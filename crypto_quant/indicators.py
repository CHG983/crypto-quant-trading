"""技術指標（純 pandas/numpy 實作，與 TradingView 的 ta.* 語意對齊）。

所有函式皆為「僅使用當根與過去資料」的因果計算，不含未來函數。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    """指數移動平均（adjust=False，等同 TradingView ta.ema）。"""
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder RSI（等同 TradingView ta.rsi）。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 且 avg_gain > 0 → RSI = 100
    out = out.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return out


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
):
    """回傳 (macd_line, signal_line, histogram)。"""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder ATR（等同 TradingView ta.atr）。"""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 10,
    multiplier: float = 3.0,
):
    """Supertrend 指標。

    回傳 ``(supertrend, direction)``，``direction`` 為 +1（多頭）或 -1（空頭）。
    以迴圈逐根計算，確保與 TradingView 的 ta.supertrend 行為一致。
    """
    hl2 = (high + low) / 2.0
    a = atr(high, low, close, length)
    upper_basic = hl2 + multiplier * a
    lower_basic = hl2 - multiplier * a

    n = len(close)
    st = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)

    for i in range(n):
        if np.isnan(a.iloc[i]):
            continue
        if i == 0 or np.isnan(final_upper[i - 1]):
            final_upper[i] = upper_basic.iloc[i]
            final_lower[i] = lower_basic.iloc[i]
            direction[i] = 1
            st[i] = final_lower[i]
            continue

        # 帶狀線的「棘輪」邏輯
        final_upper[i] = (
            upper_basic.iloc[i]
            if (upper_basic.iloc[i] < final_upper[i - 1] or close.iloc[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            lower_basic.iloc[i]
            if (lower_basic.iloc[i] > final_lower[i - 1] or close.iloc[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )

        if direction[i - 1] == 1:
            direction[i] = -1 if close.iloc[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if close.iloc[i] > final_upper[i] else -1
        st[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return pd.Series(st, index=close.index), pd.Series(direction, index=close.index)


def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    """a 向上穿越 b（當根 a>b 且前一根 a<=b）。"""
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    """a 向下穿越 b。"""
    return (a < b) & (a.shift(1) >= b.shift(1))


def add_indicators(df: pd.DataFrame, p) -> pd.DataFrame:
    """依策略參數把全部指標欄位附加到 DataFrame。"""
    out = df.copy()
    out["ema_fast"] = ema(out["close"], p.ema_fast)
    out["ema_slow"] = ema(out["close"], p.ema_slow)
    out["rsi"] = rsi(out["close"], p.rsi_len)
    macd_line, signal_line, hist = macd(
        out["close"], p.macd_fast, p.macd_slow, p.macd_signal
    )
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist
    out["atr"] = atr(out["high"], out["low"], out["close"], p.atr_len)
    out["atr_pct"] = out["atr"] / out["close"] * 100.0
    st, st_dir = supertrend(out["high"], out["low"], out["close"], p.st_len, p.st_mult)
    out["supertrend"] = st
    out["st_dir"] = st_dir
    return out
