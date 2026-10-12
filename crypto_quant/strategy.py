"""策略：CryptoTrend Momentum ATR（CTMA）。

設計理由（針對加密貨幣特性）
--------------------------
* **24/7、無漲跌停** → 沒有收盤跳空保護，必須用「波動度自適應」的停損，
  因此以 ATR 而非固定百分比計算停損距離。
* **波動大且趨勢強** → 趨勢過濾用 EMA 快慢線（21/55）而非 SMA，
  EMA 對近期價格更敏感，能較早跟上加密貨幣的快速趨勢。
* **假訊號多** → 加兩道確認：
    1. 動能確認：RSI 必須站在中軸同側（多單 RSI>50、空單 RSI<50），
       且 MACD 柱狀體方向一致，避免在逆勢反彈中進場。
    2. 波動度過濾：ATR/Close 必須高於門檻，濾掉低波動的盤整泥沼
       （盤整時均線交叉幾乎都是假訊號）。
* **可做空** → 加密貨幣有成熟的永續合約，做空是常態，故預設允許雙向。

訊號語意（重要）
--------------
均線交叉是「市場狀態翻轉」，與「是否進場」是兩件事：

* ``bull_cross``（快線向上穿越慢線）：**一定**要平掉空單；若空手且動能過濾
  通過，才建立多單。
* ``bear_cross``（快線向下穿越慢線）：**一定**要平掉多單；若空手且動能過濾
  通過，才建立空單。

因此本模組輸出三個層次的欄位：
  - ``bull_cross`` / ``bear_cross``：狀態翻轉（無條件觸發平倉）
  - ``long_entry_ok`` / ``short_entry_ok``：動能＋波動度過濾是否通過（決定是否開新倉）
  - ``signal``：1 = 看多翻轉、-1 = 看空翻轉、0 = 無

> 早期版本把「進場」與「出場」寫進同一個 signal 欄位，導致同一根 K 棒上
> 進場訊號被出場訊號覆蓋（crossUp 同時是 long_entry 與 short_exit），
> 造成 0 筆交易。現已拆分為上述三層，語意明確且不再互相覆蓋。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

from .indicators import add_indicators, crossover, crossunder


@dataclass
class StrategyParams:
    # --- 趨勢 ---
    ema_fast: int = 21
    ema_slow: int = 55
    # --- 動能 ---
    rsi_len: int = 14
    rsi_long_min: float = 50.0
    rsi_short_max: float = 50.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    # --- 波動度 / 風控 ---
    atr_len: int = 14
    atr_stop_mult: float = 3.0
    atr_trail_mult: float = 5.0
    atr_tp_mult: float = 6.0
    use_trailing: bool = True
    use_take_profit: bool = False
    min_atr_pct: float = 0.5
    # --- 部位 ---
    risk_pct: float = 0.01
    max_leverage: float = 3.0
    sizing_mode: str = "fraction"
    fraction: float = 1.0
    allow_short: bool = True
    # --- Supertrend（可選的替代趨勢過濾）---
    use_supertrend: bool = False
    st_len: int = 10
    st_mult: float = 3.0

    def to_dict(self) -> dict:
        return asdict(self)


def generate_signals(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """在已含指標的 DataFrame 上產生訊號欄位。

    新增欄位
    --------
    bull_cross / bear_cross : bool   狀態翻轉（無條件平倉）
    long_entry_ok / short_entry_ok : bool  動能＋波動度過濾是否通過
    signal : int  1=看多翻轉, -1=看空翻轉, 0=無
    """
    out = add_indicators(df, p)

    cross_up = crossover(out["ema_fast"], out["ema_slow"]).fillna(False)
    cross_dn = crossunder(out["ema_fast"], out["ema_slow"]).fillna(False)

    if p.use_supertrend:
        # 以 Supertrend 方向作為額外的趨勢一致性過濾
        cross_up = cross_up & (out["st_dir"] == 1)
        cross_dn = cross_dn & (out["st_dir"] == -1)

    vol_ok = (out["atr_pct"] >= p.min_atr_pct).fillna(False)
    warm = (out["ema_slow"].notna() & out["atr"].notna() & out["rsi"].notna()).fillna(False)

    long_entry_ok = (
        cross_up
        & (out["rsi"] > p.rsi_long_min)
        & (out["macd_hist"] > 0)
        & vol_ok
        & warm
    ).fillna(False)

    short_entry_ok = (
        cross_dn
        & (out["rsi"] < p.rsi_short_max)
        & (out["macd_hist"] < 0)
        & vol_ok
        & warm
        & p.allow_short
    ).fillna(False)

    out["bull_cross"] = cross_up
    out["bear_cross"] = cross_dn
    out["long_entry_ok"] = long_entry_ok
    out["short_entry_ok"] = short_entry_ok

    sig = pd.Series(0, index=out.index, dtype=int)
    sig[cross_up] = 1
    sig[cross_dn] = -1
    out["signal"] = sig
    return out


def signal_label(code: int) -> str:
    return {1: "BULL_CROSS", -1: "BEAR_CROSS", 0: "HOLD"}.get(int(code), "HOLD")
