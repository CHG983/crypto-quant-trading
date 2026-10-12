"""即時訊號引擎：把既有 CTMA 策略邏輯包成「單一標的即時狀態」輸出。

輸出內容
--------
* 目前趨勢方向（EMA 快慢線相對位置 + Supertrend 方向）
* 進場／出場訊號（區分**已收盤確認**與**進行中未確認**）
* 建議停損價、建議停利價、風險報酬比
* 訊號強度（0–100，四個分量加權，權重與計分方式見下）
* 資料時間戳與延遲程度

訊號強度計分（0–100）
--------------------
四個分量各自正規化到 0–1，再加權：

```
分數 = 100 × ( 0.35×趨勢 + 0.30×動能 + 0.20×波動健康度 + 0.15×Supertrend 一致性 )
```

* **趨勢 (0.35)**：``(ema_fast − ema_slow) / ATR`` 取方向後除以 1.5 並截斷於 1。
  以 ATR 正規化而非百分比，讓不同波動度的幣種可互相比較。
* **動能 (0.30)**：RSI 距中軸的距離 ``|RSI−50|/20`` 與 MACD 柱 ``|hist|/(0.5×ATR)``
  兩者平均，且方向必須與趨勢一致。
* **波動健康度 (0.20)**：ATR% 落在合理區間（``min_atr_pct`` ～ 8%）得分最高；
  低於門檻視為盤整泥沼給 0 分，高於 8% 視為過度狂野並隨超越幅度衰減。
  （波動太大不代表好——停損距離會大到讓部位被稀釋，且滑價風險升高。）
* **Supertrend 一致性 (0.15)**：Supertrend 方向與趨勢方向一致給 1，否則 0。

分級：``>=75 極強 / >=60 強 / >=45 中 / >=30 弱 / <30 極弱``。

**此為啟發式排序分數，不是機率，也沒有經過校準**——用途僅在於把
「哪些標的的訊號比較乾淨」排出先後，不可當成勝率估計。

無未來函數
----------
指標與訊號只使用當根與過去的 K 棒。為避免「進行中 K 棒的訊號在收盤前消失」
造成誤判，本模組同時跑兩次：一次含進行中 K 棒（``provisional``）、
一次只用已收盤 K 棒（``confirmed``），並明確標示兩者差異。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .backtest import BacktestConfig, run_backtest
from .live import human_delay, ms_to_utc
from .strategy import StrategyParams, generate_signals

WEIGHTS = {"trend": 0.35, "momentum": 0.30, "volatility": 0.20, "supertrend": 0.15}

TREND_LABELS = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}

# 波動健康度上限：ATR% 超過此值視為過度狂野，分數隨超越幅度衰減
VOL_WILD_CAP_PCT = 8.0


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if x is None or not np.isfinite(x):
        return 0.0
    return float(max(lo, min(hi, x)))


def strength_label(score: float) -> str:
    if score >= 75:
        return "極強"
    if score >= 60:
        return "強"
    if score >= 45:
        return "中"
    if score >= 30:
        return "弱"
    return "極弱"


@dataclass
class Strength:
    trend: float
    momentum: float
    volatility: float
    supertrend: float
    total: float
    label: str


def compute_strength(row: pd.Series, trend: int, p: StrategyParams) -> Strength:
    """依上述權重計算訊號強度。"""
    atr = float(row.get("atr", np.nan))
    close = float(row.get("close", np.nan))
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(close) or close <= 0:
        return Strength(0.0, 0.0, 0.0, 0.0, 0.0, "N/A")

    # --- 趨勢 ---
    spread_atr = (float(row["ema_fast"]) - float(row["ema_slow"])) / atr
    if trend > 0:
        t = _clamp(spread_atr / 1.5)
    elif trend < 0:
        t = _clamp(-spread_atr / 1.5)
    else:
        t = 0.0

    # --- 動能 ---
    rsi = float(row.get("rsi", np.nan))
    hist = float(row.get("macd_hist", np.nan))
    if trend > 0:
        r = _clamp((rsi - 50.0) / 20.0)
        m = _clamp(hist / (0.5 * atr))
    elif trend < 0:
        r = _clamp((50.0 - rsi) / 20.0)
        m = _clamp(-hist / (0.5 * atr))
    else:
        r = m = 0.0
    mom = (r + m) / 2.0

    # --- 波動健康度（區間帶）---
    ap = float(row.get("atr_pct", np.nan))
    if not np.isfinite(ap):
        v = 0.0
    else:
        lo = float(p.min_atr_pct)
        if ap < lo:
            v = 0.0
        elif ap <= VOL_WILD_CAP_PCT:
            v = _clamp(0.6 + 0.4 * ((ap - lo) / max(VOL_WILD_CAP_PCT - lo, 1e-9)))
        else:
            v = _clamp(1.0 - ((ap - VOL_WILD_CAP_PCT) / VOL_WILD_CAP_PCT))
    if trend == 0:
        v *= 0.5  # 沒有方向時，波動再健康也不構成訊號

    # --- Supertrend 一致性 ---
    st_dir = row.get("st_dir", np.nan)
    if trend != 0 and np.isfinite(st_dir) and int(st_dir) == (1 if trend > 0 else -1):
        st = 1.0
    else:
        st = 0.0

    total = 100.0 * (
        WEIGHTS["trend"] * t
        + WEIGHTS["momentum"] * mom
        + WEIGHTS["volatility"] * v
        + WEIGHTS["supertrend"] * st
    )
    return Strength(
        round(t, 3), round(mom, 3), round(v, 3), round(st, 3), round(total, 1),
        strength_label(total),
    )


def _bars_since(series: pd.Series, upto: int) -> Optional[int]:
    """最後一次 True 距離 ``upto`` 幾根。"""
    idx = series.iloc[: upto + 1].to_numpy(dtype=bool)
    hits = np.flatnonzero(idx)
    if len(hits) == 0:
        return None
    return int(upto - hits[-1])


def _analyze_frame(
    df: pd.DataFrame,
    params: StrategyParams,
    cfg: BacktestConfig,
) -> dict:
    """對一個（可能含進行中 K 棒的）DataFrame 計算完整狀態。"""
    sig = generate_signals(df, params)
    last = sig.iloc[-1]
    i = len(sig) - 1

    ema_fast = float(last["ema_fast"])
    ema_slow = float(last["ema_slow"])
    close = float(last["close"])
    trend = 0
    if np.isfinite(ema_fast) and np.isfinite(ema_slow):
        trend = 1 if ema_fast > ema_slow else -1 if ema_fast < ema_slow else 0

    strength = compute_strength(last, trend, params)
    bull = _bars_since(sig["bull_cross"], i)
    bear = _bars_since(sig["bear_cross"], i)

    # 最近一次交叉（取較近者）
    last_cross = None
    if bull is not None and (bear is None or bull <= bear):
        last_cross = {"type": "BULL_CROSS", "bars_ago": bull}
    elif bear is not None:
        last_cross = {"type": "BEAR_CROSS", "bars_ago": bear}

    # 「該次交叉當下是否通過動能＋波動度過濾」——這是判斷訊號是否成立的正確依據。
    # 只看最新一根 K 棒的 *_entry_ok 會誤導：交叉發生在數十根之前時，
    # 當根狀態與交叉當時已經無關，會把「成立過的訊號」誤報為「從未成立」。
    # 因此回看交叉當根與前一根（訊號在交叉當根收盤才判定，故兩根都算）。
    crosses = {
        "long": bull,
        "short": bear,
    }
    entry_passes = {}
    for side, bars_ago in crosses.items():
        col = f"{side}_entry_ok"
        if bars_ago is None:
            entry_passes[side] = False
            continue
        j = i - int(bars_ago)
        lo = max(0, j - 1)
        entry_passes[side] = bool(
            sig[col].iloc[j] or sig[col].iloc[lo]
        )

    ema_gap_pct = (
        (ema_fast - ema_slow) / close * 100.0
        if np.isfinite(ema_fast) and np.isfinite(ema_slow) and close > 0
        else float("nan")
    )
    atr = float(last["atr"])
    spread_atr = (
        (ema_fast - ema_slow) / atr if np.isfinite(atr) and atr > 0 else float("nan")
    )

    return {
        "trend": trend,
        "trend_label": TREND_LABELS[trend],
        "price": round(close, 8),
        "ema_fast": round(ema_fast, 8) if np.isfinite(ema_fast) else None,
        "ema_slow": round(ema_slow, 8) if np.isfinite(ema_slow) else None,
        "ema_gap_pct": round(ema_gap_pct, 4) if np.isfinite(ema_gap_pct) else None,
        "ema_spread_atr": round(spread_atr, 4) if np.isfinite(spread_atr) else None,
        "rsi": round(float(last["rsi"]), 2) if np.isfinite(last["rsi"]) else None,
        "macd": round(float(last["macd"]), 6) if np.isfinite(last["macd"]) else None,
        "macd_signal": round(float(last["macd_signal"]), 6)
        if np.isfinite(last["macd_signal"])
        else None,
        "macd_hist": round(float(last["macd_hist"]), 6)
        if np.isfinite(last["macd_hist"])
        else None,
        "atr": round(atr, 8) if np.isfinite(atr) else None,
        "atr_pct": round(float(last["atr_pct"]), 4)
        if np.isfinite(last["atr_pct"])
        else None,
        "st_dir": int(last["st_dir"]) if np.isfinite(last["st_dir"]) else None,
        "supertrend": round(float(last["supertrend"]), 8)
        if np.isfinite(last["supertrend"])
        else None,
        "signal": int(last["signal"]),
        "signal_label": {1: "BULL_CROSS", -1: "BEAR_CROSS", 0: "HOLD"}[int(last["signal"])],
        "long_entry_ok": bool(last["long_entry_ok"]),
        "short_entry_ok": bool(last["short_entry_ok"]),
        "last_cross": last_cross,
        "entry_passes": entry_passes,
        "strength": asdict(strength),
        "_sig": sig,
    }


def _position_state(sig: pd.DataFrame, params: StrategyParams, cfg: BacktestConfig, df: pd.DataFrame):
    """用既有回測引擎在近期歷史上推導「目前是否持倉」。

    回測引擎在資料末端會強制平倉並標記為 ``END_OF_DATA``，
    因此若最後一筆交易的出場原因是 ``END_OF_DATA``，代表該倉位實際上仍持有。
    """
    res = run_backtest(df, params=params, cfg=cfg, symbol="", interval="")
    if not res.trades:
        return None, res
    last = res.trades[-1]
    if last.exit_reason != "END_OF_DATA":
        return None, res
    return last, res


def analyze_symbol(
    df: pd.DataFrame,
    symbol: str,
    interval: str = "1d",
    params: Optional[StrategyParams] = None,
    cfg: Optional[BacktestConfig] = None,
    source: str = "binance",
    server_time_ms: Optional[int] = None,
    near_trigger_pct: Optional[float] = None,
) -> dict:
    """完整分析一個標的。``df`` 需含 date/open/high/low/close/volume/close_time_ms/is_closed。"""
    params = params or StrategyParams()
    cfg = cfg or BacktestConfig()
    if near_trigger_pct is None:
        near_trigger_pct = cfg.near_trigger_pct

    now_ms = int(time.time() * 1000)
    st_ms = server_time_ms if server_time_ms is not None else None

    last_bar = df.iloc[-1]
    bar_open_ms = int(pd.Timestamp(last_bar["date"]).timestamp() * 1000)
    bar_close_ms = int(last_bar["close_time_ms"])
    bar_is_closed = bool(last_bar["is_closed"])

    # 進行中 / 已收盤
    provisional = _analyze_frame(df, params, cfg)
    sig_prov = provisional.pop("_sig")
    df_closed = df[df["is_closed"]]
    if len(df_closed) >= 60:
        confirmed = _analyze_frame(df_closed.reset_index(drop=True), params, cfg)
        sig_conf = confirmed.pop("_sig")
    else:
        confirmed, sig_conf = None, None

    # --- 建議價位（以現價為假想進場點）---
    atr = provisional["atr"]
    price = provisional["price"]
    trend = provisional["trend"]
    levels = None
    if atr and price and trend != 0:
        if trend > 0:
            stop = price - cfg.atr_stop_mult * atr
            tp = price + cfg.atr_tp_mult * atr
        else:
            stop = price + cfg.atr_stop_mult * atr
            tp = price - cfg.atr_tp_mult * atr
        risk = abs(price - stop)
        reward = abs(tp - price)
        levels = {
            "entry_ref": round(price, 8),
            "stop": round(stop, 8),
            "take_profit": round(tp, 8),
            "risk_distance": round(risk, 8),
            "risk_pct": round(risk / price * 100.0, 4) if price else None,
            "reward_distance": round(reward, 8),
            "reward_pct": round(reward / price * 100.0, 4) if price else None,
            "rr_ratio": round(reward / risk, 3) if risk > 0 else None,
            "stop_mult": cfg.atr_stop_mult,
            "tp_mult": cfg.atr_tp_mult,
        }

    # --- 目前持倉狀態（由近期歷史模擬推導）---
    pos, _res = _position_state(sig_prov, params, cfg, df)
    position = None
    if pos is not None:
        unreal = (
            (price - pos.entry_price) * pos.qty
            if pos.side == "LONG"
            else (pos.entry_price - price) * pos.qty
        )
        position = {
            "side": pos.side,
            "entry_date": str(pd.Timestamp(pos.entry_date).date()),
            "entry_price": round(pos.entry_price, 8),
            "qty": round(pos.qty, 8),
            "notional": round(pos.notional, 2),
            "stop_price": round(pos.stop_price, 8),
            "initial_stop": round(pos.initial_stop, 8),
            "stop_moved": bool(abs(pos.stop_price - pos.initial_stop) > 1e-12),
            "bars_held": int(len(sig_prov) - 1 - pos.entry_index),
            "unrealized_pnl": round(unreal, 2),
            "unrealized_pct": round(unreal / pos.notional * 100.0, 3)
            if pos.notional
            else None,
        }

    # --- 接近觸發判定 ---
    gap = provisional["ema_gap_pct"]
    near = False
    if gap is not None and provisional["signal"] == 0 and trend != 0:
        near = abs(gap) < near_trigger_pct

    delay_s = (now_ms - bar_close_ms) / 1000.0
    out = {
        "symbol": symbol,
        "interval": interval,
        "source": source,
        "fetched_at_utc": pd.Timestamp(now_ms, unit="ms").isoformat(),
        "local_clock_utc": ms_to_utc(now_ms).isoformat(),
        "binance_server_time_utc": ms_to_utc(st_ms).isoformat() if st_ms else None,
        "clock_offset_seconds": round((now_ms - st_ms) / 1000.0, 3) if st_ms else None,
        "bar_open_utc": ms_to_utc(bar_open_ms).isoformat(),
        "bar_close_utc": ms_to_utc(bar_close_ms).isoformat(),
        "bar_is_in_progress": (not bar_is_closed),
        "bar_progress_pct": round(
            max(0.0, min(100.0, (now_ms - bar_open_ms) / (bar_close_ms - bar_open_ms) * 100.0)), 1
        ),
        "data_delay_seconds": round(delay_s, 1),
        "data_delay_human": human_delay(delay_s),
        "data_delay_note": (
            "最後一根 K 棒尚未收盤，此延遲為『距該根收盤還有多久』"
            if not bar_is_closed
            else "最後一根 K 棒已收盤，此延遲為『距該根收盤已過多久』"
        ),
        "near_trigger": bool(near),
        "near_trigger_threshold_pct": cfg.near_trigger_pct,
        # 進行中（可能 repaint）
        "provisional": provisional,
        # 只用已收盤 K 棒（不會 repaint）
        "confirmed": confirmed,
        # 便捷欄位（以確認版為主，缺則退回進行中版）
        "trend": (confirmed or provisional)["trend"],
        "trend_label": (confirmed or provisional)["trend_label"],
        "signal": (confirmed or provisional)["signal"],
        "signal_label": (confirmed or provisional)["signal_label"],
        "strength": (confirmed or provisional)["strength"],
        "levels": levels,
        "position": position,
        "repaint": bool(
            confirmed is not None
            and confirmed["signal"] != provisional["signal"]
        ),
    }
    return out


def analyze_live(
    symbol: str,
    interval: str = "1d",
    limit: int = 400,
    params: Optional[StrategyParams] = None,
    cfg: Optional[BacktestConfig] = None,
    exchange_base: Optional[str] = None,
) -> dict:
    """抓即時資料並分析單一標的（便利函式）。"""
    from .live import fetch_klines, server_time

    df = fetch_klines(symbol, interval=interval, limit=limit, base=exchange_base)
    try:
        st_ms: Optional[int] = server_time(exchange_base)
    except Exception:  # noqa: BLE001
        st_ms = None
    return analyze_symbol(
        df, symbol, interval=interval, params=params, cfg=cfg,
        source="binance:data-api.binance.vision", server_time_ms=st_ms,
    )
