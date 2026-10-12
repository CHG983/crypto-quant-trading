"""績效指標計算。"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 365  # 加密貨幣 24/7，年化用 365 天


def total_return(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] == 0:
        return 0.0
    return equity.iloc[-1] / equity.iloc[0] - 1.0


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    days = (equity.index[-1] - equity.index[0]).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    if years <= 0 or equity.iloc[0] <= 0:
        return 0.0
    ratio = equity.iloc[-1] / equity.iloc[0]
    if ratio <= 0:
        return -1.0
    return ratio ** (1.0 / years) - 1.0


def max_drawdown(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    return float(dd.min())


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def sharpe(equity: pd.Series, rf: float = 0.0) -> float:
    if len(equity) < 3:
        return 0.0
    rets = equity.pct_change().dropna()
    if rets.std() == 0 or np.isnan(rets.std()):
        return 0.0
    excess = rets - rf / TRADING_DAYS
    return float(excess.mean() / rets.std() * np.sqrt(TRADING_DAYS))


def sortino(equity: pd.Series, rf: float = 0.0) -> float:
    if len(equity) < 3:
        return 0.0
    rets = equity.pct_change().dropna()
    downside = rets[rets < 0]
    if len(downside) == 0 or downside.std() == 0:
        return 0.0
    excess = rets.mean() - rf / TRADING_DAYS
    return float(excess / downside.std() * np.sqrt(TRADING_DAYS))


def calmar(equity: pd.Series) -> float:
    mdd = abs(max_drawdown(equity))
    if mdd == 0:
        return 0.0
    return cagr(equity) / mdd


def trade_stats(trades) -> dict:
    if not trades:
        return {
            "trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "avg_bars": 0.0,
            "best": 0.0, "worst": 0.0, "long_trades": 0, "short_trades": 0,
        }
    pnls = np.array([t.pnl for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_win = wins.sum() if len(wins) else 0.0
    gross_loss = abs(losses.sum()) if len(losses) else 0.0
    return {
        "trades": len(trades),
        "win_rate": float(len(wins) / len(pnls)) if len(pnls) else 0.0,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "avg_bars": float(np.mean([t.bars_held for t in trades])),
        "best": float(pnls.max()),
        "worst": float(pnls.min()),
        "long_trades": int(sum(1 for t in trades if t.side == "LONG")),
        "short_trades": int(sum(1 for t in trades if t.side == "SHORT")),
    }


def summarize(equity: pd.Series, trades, label: str = "Strategy") -> dict:
    ts = trade_stats(trades)
    return {
        "label": label,
        "total_return": total_return(equity),
        "cagr": cagr(equity),
        "max_drawdown": max_drawdown(equity),
        "sharpe": sharpe(equity),
        "sortino": sortino(equity),
        "calmar": calmar(equity),
        "final_equity": float(equity.iloc[-1]) if len(equity) else 0.0,
        **ts,
    }


def format_summary(s: dict) -> str:
    pf = s["profit_factor"]
    pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
    return (
        f"{s['label']:<14} 總報酬 {s['total_return']*100:>9.2f}%  "
        f"年化 {s['cagr']*100:>7.2f}%  MDD {s['max_drawdown']*100:>8.2f}%  "
        f"Sharpe {s['sharpe']:>5.2f}  Sortino {s['sortino']:>5.2f}  "
        f"交易 {s['trades']:>3}  勝率 {s['win_rate']*100:>5.1f}%  盈虧比 {pf_str:>5}"
    )
