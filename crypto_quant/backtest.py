"""事件驅動回測引擎（逐根 K 棒模擬，無未來函數）。

關鍵設計
--------
* **訊號 → 次根開盤成交**：第 t 根收盤後才得知的訊號，在第 t+1 根開盤執行，
  徹底避免 look-ahead bias。
* **狀態翻轉語意**：看多翻轉（bull_cross）無條件平掉空單；若空手且動能過濾
  通過才建立多單。看空翻轉同理。這修正了「進場訊號被出場訊號覆蓋」的舊 bug。
* **成本模型**：taker 手續費（預設 0.04%）、滑價（預設 0.05%），進出各收一次。
  永續合約資金費率以 ``funding_rate_daily`` 近似（預設 0.01%/日，僅持倉時扣）。
* **風控優先序**：同一根 K 棒內若同時觸及停損與停利，保守假設**先觸停損**。
* **進場當根即檢查停損**：進場後立刻以該根 low/high 檢查是否已觸停損，
  避免高估績效。
* **部位大小**：以「帳戶風險比例 / 停損距離」反推名目部位，並受最大槓桿限制。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from .strategy import StrategyParams, generate_signals


@dataclass
class BacktestConfig:
    initial_capital: float = 10_000.0
    fee_rate: float = 0.0004            # taker 0.04%
    slippage_rate: float = 0.0005       # 0.05%
    funding_rate_daily: float = 0.0001  # 0.01%/日（僅持倉時）
    risk_pct: float = 0.01              # 每筆風險佔權益 1%（sizing_mode="risk" 時使用）
    max_leverage: float = 3.0
    sizing_mode: str = "fraction"       # "fraction"=固定名目比例, "risk"=ATR 風險比例
    fraction: float = 1.0               # sizing_mode="fraction" 時，名目部位佔權益比例
    allow_short: bool = True
    use_trailing: bool = True
    use_take_profit: bool = False
    atr_stop_mult: float = 3.0
    atr_trail_mult: float = 5.0
    atr_tp_mult: float = 6.0
    # 即時訊號用：EMA 快慢線差距（%）小於此值視為「接近觸發」
    near_trigger_pct: float = 0.5


@dataclass
class Trade:
    side: str
    entry_date: pd.Timestamp
    entry_price: float
    qty: float
    notional: float
    stop_price: float
    initial_stop: float = 0.0
    entry_index: int = 0
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0
    mae: float = 0.0  # 最大不利波動（以價格計）

    def to_row(self) -> dict:
        return {
            "side": self.side,
            "entry_date": self.entry_date.date() if self.entry_date is not None else None,
            "entry_price": round(self.entry_price, 4),
            "qty": round(self.qty, 8),
            "notional": round(self.notional, 2),
            "stop_price": round(self.stop_price, 4),
            "initial_stop": round(self.initial_stop, 4),
            "exit_date": self.exit_date.date() if self.exit_date is not None else None,
            "exit_price": round(self.exit_price, 4) if self.exit_price is not None else None,
            "exit_reason": self.exit_reason,
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 4),
            "bars_held": self.bars_held,
        }


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: List[Trade]
    signals: pd.DataFrame
    config: BacktestConfig
    params: StrategyParams
    source: str = ""
    symbol: str = ""
    interval: str = "1d"

    @property
    def trades_df(self) -> pd.DataFrame:
        return pd.DataFrame([t.to_row() for t in self.trades])


def _position_size(equity: float, price: float, stop_dist: float, cfg: BacktestConfig) -> float:
    """計算部位數量（單位：幣）。

    兩種模式：
      * ``"fraction"``（預設）：名目部位 = 權益 × fraction，再受最大槓桿限制。
        與買進持有基準的資金投入程度相當，適合做策略 vs B&H 的公平比較。
      * ``"risk"``：名目部位 = (權益 × risk_pct) / 停損距離，即「每筆只虧固定比例」。
        波動越大部位越小，風控更嚴謹，但 ATR 停損很寬時投入資金會非常少
        （實測 BTC 日線僅約 10% 權益），報酬因此被大幅稀釋。
    """
    if price <= 0 or equity <= 0:
        return 0.0
    max_notional = equity * cfg.max_leverage
    if cfg.sizing_mode == "risk":
        if stop_dist <= 0:
            return 0.0
        qty = (equity * cfg.risk_pct) / stop_dist
    else:  # fraction
        qty = (equity * cfg.fraction) / price
    if qty * price > max_notional:
        qty = max_notional / price
    return max(qty, 0.0)


def run_backtest(
    df: pd.DataFrame,
    params: Optional[StrategyParams] = None,
    cfg: Optional[BacktestConfig] = None,
    source: str = "",
    symbol: str = "",
    interval: str = "1d",
    trade_start: Optional[pd.Timestamp] = None,
) -> BacktestResult:
    """執行回測。``df`` 需含 date/open/high/low/close/volume。

    ``trade_start``（walk-forward 用）
    ---------------------------------
    指定「最早可進場日期」。早於此日期的 K 棒僅用於**指標暖機**，
    不得開新倉。這讓 walk-forward 的測試窗可以往前接一段訓練窗尾端的
    歷史資料來暖機指標，同時保證暖機期不會產生任何交易 ——
    否則測試窗的績效會被暖機期的交易污染，且那些交易其實用到了
    訓練窗的資訊。
    """
    params = params or StrategyParams()
    cfg = cfg or BacktestConfig()
    # 讓 config 的風控參數與策略參數一致
    params.atr_stop_mult = cfg.atr_stop_mult
    params.atr_trail_mult = cfg.atr_trail_mult
    params.atr_tp_mult = cfg.atr_tp_mult
    params.use_trailing = cfg.use_trailing
    params.use_take_profit = cfg.use_take_profit
    params.allow_short = cfg.allow_short
    params.risk_pct = cfg.risk_pct
    params.max_leverage = cfg.max_leverage
    params.sizing_mode = cfg.sizing_mode
    params.fraction = cfg.fraction

    sig = generate_signals(df, params)
    n = len(sig)
    equity = np.zeros(n)
    cash = cfg.initial_capital
    equity[0] = cash

    trades: List[Trade] = []
    pos: Optional[Trade] = None
    pending: Optional[int] = None          # 待執行的翻轉方向（次根開盤成交）
    pending_entry_ok: bool = False         # 該翻轉是否允許開新倉

    o = sig["open"].to_numpy(dtype=float)
    h = sig["high"].to_numpy(dtype=float)
    l = sig["low"].to_numpy(dtype=float)
    c = sig["close"].to_numpy(dtype=float)
    a = sig["atr"].to_numpy(dtype=float)
    dates = pd.to_datetime(sig["date"]).to_numpy()

    # walk-forward：暖機期（trade_start 之前）禁止開新倉
    trade_start_ts = pd.Timestamp(trade_start) if trade_start is not None else None

    def _can_trade(i: int) -> bool:
        return trade_start_ts is None or pd.Timestamp(dates[i]) >= trade_start_ts

    def close_position(price: float, i: int, reason: str):
        nonlocal pos, cash
        if pos is None:
            return
        exit_px = price * (1 - cfg.slippage_rate) if pos.side == "LONG" else price * (1 + cfg.slippage_rate)
        gross = (exit_px - pos.entry_price) * pos.qty if pos.side == "LONG" else (pos.entry_price - exit_px) * pos.qty
        fee = exit_px * pos.qty * cfg.fee_rate
        pnl = gross - fee
        cash += pnl
        pos.exit_date = pd.Timestamp(dates[i])
        pos.exit_price = exit_px
        pos.exit_reason = reason
        pos.pnl = pnl
        pos.pnl_pct = pnl / (pos.entry_price * pos.qty) if pos.entry_price * pos.qty else 0.0
        pos.bars_held = i - pos.entry_index
        trades.append(pos)
        pos = None

    def open_position(side: str, i: int, atr_ref: float):
        nonlocal pos, cash
        if np.isnan(atr_ref) or atr_ref <= 0:
            return
        if side == "LONG":
            entry_px = o[i] * (1 + cfg.slippage_rate)
            stop_dist = cfg.atr_stop_mult * atr_ref
            stop = entry_px - stop_dist
        else:
            entry_px = o[i] * (1 - cfg.slippage_rate)
            stop_dist = cfg.atr_stop_mult * atr_ref
            stop = entry_px + stop_dist
        qty = _position_size(cash, entry_px, stop_dist, cfg)
        if qty <= 0:
            return
        cash -= entry_px * qty * cfg.fee_rate
        pos = Trade(
            side=side, entry_date=pd.Timestamp(dates[i]), entry_price=entry_px,
            qty=qty, notional=entry_px * qty, stop_price=stop, initial_stop=stop,
            entry_index=i,
        )
        # 進場當根即檢查是否已觸停損（保守，避免高估）
        if side == "LONG" and l[i] <= stop:
            close_position(stop, i, "ATR_STOP")
        elif side == "SHORT" and h[i] >= stop:
            close_position(stop, i, "ATR_STOP")

    for i in range(1, n):
        # ---------- 1) 既有持倉的風控（用本根 high/low）----------
        if pos is not None:
            atr_ref = a[i - 1] if not np.isnan(a[i - 1]) else a[i]
            if pos.side == "LONG":
                pos.mae = min(pos.mae, l[i] - pos.entry_price)
                if l[i] <= pos.stop_price:
                    close_position(pos.stop_price, i, "ATR_STOP")
                elif cfg.use_take_profit and not np.isnan(atr_ref) and h[i] >= pos.entry_price + cfg.atr_tp_mult * atr_ref:
                    close_position(pos.entry_price + cfg.atr_tp_mult * atr_ref, i, "ATR_TAKE_PROFIT")
                elif cfg.use_trailing and not np.isnan(atr_ref):
                    pos.stop_price = max(pos.stop_price, c[i] - cfg.atr_trail_mult * atr_ref)
            else:  # SHORT
                pos.mae = max(pos.mae, h[i] - pos.entry_price)
                if h[i] >= pos.stop_price:
                    close_position(pos.stop_price, i, "ATR_STOP")
                elif cfg.use_take_profit and not np.isnan(atr_ref) and l[i] <= pos.entry_price - cfg.atr_tp_mult * atr_ref:
                    close_position(pos.entry_price - cfg.atr_tp_mult * atr_ref, i, "ATR_TAKE_PROFIT")
                elif cfg.use_trailing and not np.isnan(atr_ref):
                    pos.stop_price = min(pos.stop_price, c[i] + cfg.atr_trail_mult * atr_ref)

        # ---------- 2) 執行前一根排定的翻轉（本根開盤）----------
        if pending is not None:
            atr_ref = a[i - 1]
            if pending == 1:  # 看多翻轉
                if pos is not None and pos.side == "SHORT":
                    close_position(o[i], i, "SIGNAL_EXIT")
                if pos is None and pending_entry_ok and _can_trade(i):
                    open_position("LONG", i, atr_ref)
            elif pending == -1:  # 看空翻轉
                if pos is not None and pos.side == "LONG":
                    close_position(o[i], i, "SIGNAL_EXIT")
                if pos is None and pending_entry_ok and cfg.allow_short and _can_trade(i):
                    open_position("SHORT", i, atr_ref)
            pending = None
            pending_entry_ok = False

        # ---------- 3) 資金費率（持倉成本）----------
        if pos is not None:
            cash -= pos.notional * cfg.funding_rate_daily

        # ---------- 4) 記錄權益 ----------
        if pos is not None:
            unreal = (c[i] - pos.entry_price) * pos.qty if pos.side == "LONG" else (pos.entry_price - c[i]) * pos.qty
            equity[i] = cash + unreal
        else:
            equity[i] = cash

        # ---------- 5) 依本根收盤訊號排定次根動作 ----------
        s = int(sig["signal"].iloc[i])
        if s != 0 and _can_trade(i):
            pending = s
            pending_entry_ok = bool(
                sig["long_entry_ok"].iloc[i] if s == 1 else sig["short_entry_ok"].iloc[i]
            )

    # 收尾：最後一根強制平倉
    if pos is not None:
        close_position(c[-1], n - 1, "END_OF_DATA")
        equity[-1] = cash

    eq = pd.Series(equity, index=pd.to_datetime(sig["date"]))
    return BacktestResult(
        equity=eq, trades=trades, signals=sig, config=cfg, params=params,
        source=source, symbol=symbol, interval=interval,
    )


def buy_and_hold(df: pd.DataFrame, cfg: Optional[BacktestConfig] = None) -> pd.Series:
    """買進持有基準（同樣計入手續費與滑價，進場一次）。"""
    cfg = cfg or BacktestConfig()
    px = df["close"].to_numpy(dtype=float)
    entry = px[0] * (1 + cfg.slippage_rate)
    qty = (cfg.initial_capital * (1 - cfg.fee_rate)) / entry
    eq = qty * px
    return pd.Series(eq, index=pd.to_datetime(df["date"]))
