"""市場掃描：對一組加密貨幣標的計算「目前訊號狀態」並排行。

標的清單來源
------------
三種模式，優先序如下：
1. ``--symbols BTCUSDT,ETHUSDT,...``：完全自訂。
2. ``--top N``：先抓 ``/api/v3/ticker/24hr`` 全部交易對，以 **USDT 計價成交量
   （``quoteVolume``）** 排序取前 N 名。用成交量而非市值，因為幣安公開 API
   不提供市值；成交量是可實際取得的流動性代理指標，且對交易本身更相關。
3. 預設內建清單（下方程式碼中的 ``DEFAULT_SYMBOLS``）。

> ⚠️ **這不是「市值前 20 大」**。CoinGecko 的市值排行需要額外端點且在沙盒中
> 可能受限。本模組一律以**成交量**排行，並在輸出中明確標示排行依據。

每個標的輸出
------------
價格、EMA 差距（% 與 ATR 倍數）、RSI、MACD 柱、ATR%、訊號方向、
訊號強度、是否接近觸發、建議停損／停利。

成本控制
--------
每個標的都需要一段 K 線歷史（預設 400 根日線）。為避免對公開端點造成壓力，
掃描時採**序列 + 小延遲**（``--sleep``，預設 0.12 秒），並可用 ``--top``
控制數量。做空訊號預設也會納入統計（``allow_short`` 由策略參數控制）。
"""
from __future__ import annotations

import time
from typing import Dict, Iterable, List, Optional

import pandas as pd

from .backtest import BacktestConfig
from .live import fetch_klines, fetch_tickers, server_time
from .signal_engine import analyze_symbol
from .strategy import StrategyParams

# 內建清單：以 USDT 現貨交易對為主的主流幣（非市值排行，僅為可用預設）
DEFAULT_SYMBOLS: List[str] = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "MATICUSDT", "LTCUSDT", "TRXUSDT", "ATOMUSDT", "NEARUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "FILUSDT", "INJUSDT",
]


# 穩定幣基準資產：與美元掛鉤，ATR% 通常 < 0.1%。
# 若不過濾，它們會因為「成交量極大」而霸佔排行榜與「接近觸發」清單
# （EMA 差距 0.00x%），把真正可交易的標的擠出去，且訊號毫無意義。
STABLE_BASES = {
    "USDC", "USD1", "RLUSD", "TUSD", "FDUSD", "BUSD", "DAI", "USDP",
    "PYUSD", "USDE", "SUSDE", "USDD", "USTC", "USDX", "XUSD", "AEUR",
    "EURI", "EUR", "USDY", "USDM", "USDR", "GUSD", "LUSD", "FRAX",
    "USDS", "USDG", "USDQ", "USD0", "SUSD", "CRVUSD", "GHO", "DOLA",
    "UST", "MIM", "ALUSD", "USDF", "USDZ", "BUIDL",
}

# 最低波動度門檻（ATR%）：低於此值的標的訊號幾乎不可能成立，掃描時直接略過。
MIN_SCAN_ATR_PCT = 0.30


def is_stablecoin(symbol: str) -> bool:
    """判斷是否為 USDT 計價的穩定幣交易對。"""
    if not symbol.endswith("USDT"):
        return False
    return symbol[:-4] in STABLE_BASES


def top_symbols_by_volume(
    n: int = 25,
    min_quote_volume: float = 5_000_000.0,
    exclude_stable: bool = True,
) -> List[str]:
    """以 24 小時 USDT 計價成交量排序取前 ``n`` 名。

    ``min_quote_volume`` 用來剔除流動性過低的標的（成交太少容易被滑價吃掉）。
    ``exclude_stable`` 剔除穩定幣——它們成交量極大但無趨勢可言。
    """
    tickers = fetch_tickers(None)  # 一次抓全部，省請求數
    rows = []
    for sym, t in tickers.items():
        if not sym.endswith("USDT"):
            continue
        if exclude_stable and is_stablecoin(sym):
            continue
        # 剔除槓桿代幣（UP/DOWN/BULL/BEAR）——它們的走勢是合成出來的
        base = sym[:-4]
        if any(tag in base for tag in ("UP", "DOWN", "BULL", "BEAR")):
            continue
        try:
            qv = float(t.get("quoteVolume", 0.0))
            last = float(t.get("lastPrice", 0.0))
        except (TypeError, ValueError):
            continue
        if qv < min_quote_volume or last <= 0:
            continue
        rows.append((sym, qv, last, float(t.get("priceChangePercent", 0.0) or 0.0)))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [r[0] for r in rows[:n]]


def scan(
    symbols: Iterable[str],
    interval: str = "1d",
    limit: int = 400,
    params: Optional[StrategyParams] = None,
    cfg: Optional[BacktestConfig] = None,
    sleep: float = 0.12,
    verbose: bool = True,
) -> dict:
    """逐一掃描標的，回傳完整結果（含失敗清單，不隱藏錯誤）。"""
    params = params or StrategyParams()
    cfg = cfg or BacktestConfig()
    symbols = list(dict.fromkeys(symbols))
    min_atr_pct = MIN_SCAN_ATR_PCT

    try:
        st_ms: Optional[int] = server_time()
    except Exception:  # noqa: BLE001
        st_ms = None

    results: List[dict] = []
    failures: List[dict] = []
    skipped: List[dict] = []
    for i, sym in enumerate(symbols, 1):
        try:
            df = fetch_klines(sym, interval=interval, limit=limit)
            if len(df) < 60:
                raise RuntimeError(f"K 線不足（{len(df)} 根）")
            r = analyze_symbol(
                df, sym, interval=interval, params=params, cfg=cfg,
                source="binance:data-api.binance.vision", server_time_ms=st_ms,
            )
            # 波動度過低的標的（穩定幣、掛鉤資產）不納入排行——訊號無意義
            _c = r["confirmed"] or r["provisional"]
            _ap = _c.get("atr_pct")
            if _ap is not None and _ap < min_atr_pct:
                skipped.append({
                    "symbol": sym, "reason": f"ATR% {_ap:.3f} < 門檻 {min_atr_pct}",
                    "atr_pct": _ap,
                })
                if verbose:
                    print(f"[scan {i:>3}/{len(symbols)}] {sym:<12} 略過（低波動 ATR% {_ap:.3f}）")
                if sleep:
                    time.sleep(sleep)
                continue
            results.append(r)
            if verbose:
                c = r["confirmed"] or r["provisional"]
                print(
                    f"[scan {i:>3}/{len(symbols)}] {sym:<12} "
                    f"{r['trend_label']:<8} 分數 {c['strength']['total']:>5.1f} "
                    f"({c['strength']['label']})  ATR% {c['atr_pct'] if c['atr_pct'] is not None else float('nan'):>6.2f}  "
                    f"{'(未收盤)' if r['bar_is_in_progress'] else ''}"
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"symbol": sym, "error": str(exc)[:200]})
            if verbose:
                print(f"[scan {i:>3}/{len(symbols)}] {sym:<12} 失敗：{str(exc)[:100]}")
        if sleep:
            time.sleep(sleep)

    return {
        "scanned": len(symbols),
        "ok": len(results),
        "failed": len(failures),
        "skipped_low_vol": len(skipped),
        "skipped": skipped,
        "failures": failures,
        "results": results,
        "min_scan_atr_pct": min_atr_pct,
    }


def _row_of(r: dict, rank_by: str) -> dict:
    """把掃描結果攤平成表格用的一列。以『確認版』為主。"""
    c = r["confirmed"] or r["provisional"]
    return {
        "symbol": r["symbol"],
        "trend": c["trend_label"],
        "signal": c["signal_label"],
        "strength": c["strength"]["total"],
        "strength_label": c["strength"]["label"],
        "price": c["price"],
        "ema_gap_pct": c["ema_gap_pct"],
        "ema_spread_atr": c["ema_spread_atr"],
        "rsi": c["rsi"],
        "macd_hist": c["macd_hist"],
        "atr_pct": c["atr_pct"],
        "near_trigger": r["near_trigger"],
        "repaint": r["repaint"],
        "stop": (r["levels"] or {}).get("stop"),
        "take_profit": (r["levels"] or {}).get("take_profit"),
        "rr_ratio": (r["levels"] or {}).get("rr_ratio"),
        "position": (r["position"] or {}).get("side"),
        "trend_int": c["trend"],
    }


def build_rankings(scan_result: dict, top_n: int = 8) -> dict:
    """產生四組排行：做多、做空、接近觸發、綜合強度。"""
    rows = [_row_of(r, "") for r in scan_result["results"]]

    longs = sorted([x for x in rows if x["trend_int"] == 1],
                   key=lambda x: x["strength"], reverse=True)
    shorts = sorted([x for x in rows if x["trend_int"] == -1],
                    key=lambda x: x["strength"], reverse=True)
    near = sorted([x for x in rows if x["near_trigger"]],
                  key=lambda x: abs(x["ema_gap_pct"]) if x["ema_gap_pct"] is not None else 9e9)
    strongest = sorted(rows, key=lambda x: x["strength"], reverse=True)

    fresh = [x for x in rows if x["signal"] != "HOLD"]

    return {
        "total_scanned": len(rows),
        "long_count": len(longs),
        "short_count": len(shorts),
        "neutral_count": len(rows) - len(longs) - len(shorts),
        "near_trigger_count": len(near),
        "fresh_signal_count": len(fresh),
        "top_long": longs[:top_n],
        "top_short": shorts[:top_n],
        "top_near": near[:top_n],
        "top_strength": strongest[:top_n],
        "all_rows": rows,
    }


def rankings_to_frame(rankings: dict) -> pd.DataFrame:
    return pd.DataFrame(rankings["all_rows"])


def summarize_text(scan_result: dict, rankings: dict) -> str:
    lines = [
        f"掃描 {rankings['total_scanned']} 個標的（成功 {scan_result['ok']} / 失敗 {scan_result['failed']}）",
        f"  略過低波動標的 {scan_result.get('skipped_low_vol', 0)} 個"
        f"（ATR% < {scan_result.get('min_scan_atr_pct', 0)}）",
        f"  多頭排列 {rankings['long_count']}　空頭排列 {rankings['short_count']}　"
        f"中性 {rankings['neutral_count']}",
        f"  接近觸發 {rankings['near_trigger_count']}　本次交叉訊號 {rankings['fresh_signal_count']}",
        "",
        "多頭強度前 5：",
    ]
    for x in rankings["top_long"][:5]:
        lines.append(
            f"  {x['symbol']:<12} 分數 {x['strength']:>5.1f} ({x['strength_label']})  "
            f"RSI {x['rsi'] if x['rsi'] is not None else float('nan'):>5.1f}  "
            f"ATR% {x['atr_pct'] if x['atr_pct'] is not None else float('nan'):>5.2f}"
        )
    lines.append("空頭強度前 5：")
    for x in rankings["top_short"][:5]:
        lines.append(
            f"  {x['symbol']:<12} 分數 {x['strength']:>5.1f} ({x['strength_label']})  "
            f"RSI {x['rsi'] if x['rsi'] is not None else float('nan'):>5.1f}  "
            f"ATR% {x['atr_pct'] if x['atr_pct'] is not None else float('nan'):>5.2f}"
        )
    if rankings["top_near"]:
        lines.append("接近觸發（EMA 差距最小）：")
        for x in rankings["top_near"][:5]:
            lines.append(
                f"  {x['symbol']:<12} EMA 差距 {x['ema_gap_pct']:>7.4f}%  "
                f"（{x['ema_spread_atr']:>6.3f} ATR）"
            )
    return "\n".join(lines)
