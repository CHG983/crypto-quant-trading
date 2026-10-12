"""matplotlib 繪圖（含中文字型處理，避免亂碼）。"""
from __future__ import annotations

import os
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .metrics import drawdown_series  # noqa: E402

# ---------------------------------------------------------------------------
# 中文字型：依序尋找系統可用字型，找不到就退回英文標籤（絕不畫出亂碼方框）
# ---------------------------------------------------------------------------
_CJK_CANDIDATES = [
    "Noto Sans CJK TC", "Noto Sans CJK SC", "Noto Sans TC", "Noto Sans SC",
    "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "Microsoft JhengHei",
    "PingFang TC", "Heiti TC", "SimHei", "Arial Unicode MS",
]


def setup_cjk_font() -> Optional[str]:
    """設定中文字型，回傳實際使用的字型名稱（None 表示找不到）。"""
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name] + plt.rcParams["font.sans-serif"]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    plt.rcParams["axes.unicode_minus"] = False
    return None


def _labels(has_cjk: bool) -> dict:
    if has_cjk:
        return {
            "equity": "權益曲線", "bh": "買進持有", "strategy": "策略",
            "price": "價格", "ema_fast": "EMA 快", "ema_slow": "EMA 慢",
            "long": "做多進場", "short": "做空進場", "exit": "出場",
            "dd": "回撤", "date": "日期", "equity_axis": "權益 (USDT)",
            "price_axis": "價格 (USDT)", "dd_axis": "回撤 (%)",
            "title_eq": "權益曲線：策略 vs 買進持有",
            "title_px": "價格、均線與買賣點",
            "title_dd": "回撤曲線",
        }
    return {
        "equity": "Equity", "bh": "Buy & Hold", "strategy": "Strategy",
        "price": "Price", "ema_fast": "EMA fast", "ema_slow": "EMA slow",
        "long": "Long entry", "short": "Short entry", "exit": "Exit",
        "dd": "Drawdown", "date": "Date", "equity_axis": "Equity (USDT)",
        "price_axis": "Price (USDT)", "dd_axis": "Drawdown (%)",
        "title_eq": "Equity Curve: Strategy vs Buy & Hold",
        "title_px": "Price, Moving Averages and Trade Markers",
        "title_dd": "Drawdown Curve",
    }


def plot_equity(equity: pd.Series, bh: pd.Series, out_path: str, has_cjk: bool = True) -> str:
    L = _labels(has_cjk)
    fig, ax = plt.subplots(figsize=(12, 5.2), dpi=110)
    ax.plot(equity.index, equity.values, lw=1.8, color="#2563eb", label=L["strategy"])
    ax.plot(bh.index, bh.values, lw=1.4, color="#94a3b8", ls="--", label=L["bh"])
    ax.set_title(L["title_eq"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["date"]); ax.set_ylabel(L["equity_axis"])
    ax.grid(alpha=0.25); ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_price_trades(sig: pd.DataFrame, trades, out_path: str, has_cjk: bool = True) -> str:
    L = _labels(has_cjk)
    fig, ax = plt.subplots(figsize=(12, 5.6), dpi=110)
    ax.plot(sig["date"], sig["close"], lw=1.2, color="#0f172a", label=L["price"])
    ax.plot(sig["date"], sig["ema_fast"], lw=1.0, color="#f59e0b", label=L["ema_fast"])
    ax.plot(sig["date"], sig["ema_slow"], lw=1.0, color="#8b5cf6", label=L["ema_slow"])

    longs = sig[sig["long_entry_ok"]]
    shorts = sig[sig["short_entry_ok"]]
    ax.scatter(longs["date"], longs["close"], marker="^", s=70, color="#16a34a",
               edgecolors="white", linewidths=0.6, zorder=5, label=L["long"])
    ax.scatter(shorts["date"], shorts["close"], marker="v", s=70, color="#dc2626",
               edgecolors="white", linewidths=0.6, zorder=5, label=L["short"])

    for t in trades:
        if t.exit_date is not None:
            ax.scatter([t.exit_date], [t.exit_price], marker="x", s=45,
                       color="#64748b", zorder=5)
    ax.scatter([], [], marker="x", s=45, color="#64748b", label=L["exit"])

    ax.set_title(L["title_px"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["date"]); ax.set_ylabel(L["price_axis"])
    ax.grid(alpha=0.25); ax.legend(loc="upper left", fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_drawdown(equity: pd.Series, out_path: str, has_cjk: bool = True) -> str:
    L = _labels(has_cjk)
    dd = drawdown_series(equity) * 100.0
    fig, ax = plt.subplots(figsize=(12, 3.6), dpi=110)
    ax.fill_between(dd.index, dd.values, 0, color="#ef4444", alpha=0.35)
    ax.plot(dd.index, dd.values, lw=1.0, color="#b91c1c")
    ax.set_title(L["title_dd"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["date"]); ax.set_ylabel(L["dd_axis"])
    ax.grid(alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_optimization_heatmap(grid: pd.DataFrame, out_path: str, has_cjk: bool = True) -> str:
    """grid: index=ema_fast, columns=atr_mult, values=sharpe。"""
    fig, ax = plt.subplots(figsize=(8.5, 5.0), dpi=110)
    data = grid.values.astype(float)
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(grid.columns)))
    ax.set_xticklabels([f"{c:g}" for c in grid.columns])
    ax.set_yticks(range(len(grid.index)))
    ax.set_yticklabels([f"{i}" for i in grid.index])
    ax.set_xlabel("ATR stop multiple")
    ax.set_ylabel("EMA fast length")
    ax.set_title("Grid search: Sharpe ratio" if not has_cjk else "網格搜尋：Sharpe 比率",
                 fontsize=12, fontweight="bold")
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            if not np.isnan(data[i, j]):
                ax.text(j, i, f"{data[i, j]:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, label="Sharpe")
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path
