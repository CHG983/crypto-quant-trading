"""WFO 雙模式對照專用繪圖（含中文字型處理，避免亂碼）。

五張圖
------
1. ``plot_wfo_mode_equity``          rolling vs anchored 樣本外權益曲線疊圖（+ B&H + 單次切分）
2. ``plot_wfo_window_returns``       各窗口報酬分布（兩種模式對照）
3. ``plot_wfo_param_compare``        參數隨時間變化（兩種模式對照）
4. ``plot_wfo_train_test``           訓練窗 vs 測試窗績效（兩種模式對照）
5. ``plot_wfo_drawdown``             回撤圖（兩種模式對照）
"""
from __future__ import annotations

from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def _L(has_cjk: bool) -> dict:
    if has_cjk:
        return {
            "date": "日期", "equity": "權益 (USDT)",
            "t_eq": "WFO 樣本外權益曲線：rolling vs anchored",
            "roll": "rolling（滾動窗）", "anch": "anchored（擴張窗）",
            "bh": "買進持有", "single": "單次切分 OOS",
            "t_win": "各窗口樣本外報酬：rolling vs anchored",
            "win_ret": "窗口報酬 (%)", "win_no": "窗口編號",
            "t_param": "參數隨時間變化：rolling vs anchored",
            "t_tt": "訓練窗 vs 測試窗 Sharpe：rolling vs anchored",
            "train": "訓練窗", "test": "測試窗", "sharpe": "Sharpe",
            "t_dd": "樣本外回撤：rolling vs anchored", "dd": "回撤 (%)",
        }
    return {
        "date": "Date", "equity": "Equity (USDT)",
        "t_eq": "WFO Out-of-Sample Equity: rolling vs anchored",
        "roll": "rolling", "anch": "anchored",
        "bh": "Buy & Hold", "single": "Single-split OOS",
        "t_win": "Per-Window OOS Return: rolling vs anchored",
        "win_ret": "Window return (%)", "win_no": "Window #",
        "t_param": "Parameter Evolution: rolling vs anchored",
        "t_tt": "Train vs Test Sharpe: rolling vs anchored",
        "train": "Train", "test": "Test", "sharpe": "Sharpe",
        "t_dd": "Out-of-Sample Drawdown: rolling vs anchored", "dd": "Drawdown (%)",
    }


def plot_wfo_mode_equity(
    roll_eq: pd.Series,
    anch_eq: pd.Series,
    bh: Optional[pd.Series],
    single: Optional[pd.Series],
    out_path: str,
    has_cjk: bool = True,
) -> str:
    L = _L(has_cjk)
    fig, ax = plt.subplots(figsize=(12, 5.6), dpi=110)
    ax.plot(roll_eq.index, roll_eq.values, lw=2.0, color="#2563eb", label=L["roll"])
    ax.plot(anch_eq.index, anch_eq.values, lw=2.0, color="#dc2626", label=L["anch"])
    if bh is not None and len(bh):
        ax.plot(bh.index, bh.values, lw=1.4, color="#94a3b8", ls="--", label=L["bh"])
    if single is not None and len(single):
        ax.plot(single.index, single.values, lw=1.3, color="#f59e0b", ls=":", label=L["single"])
    ax.set_title(L["t_eq"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["date"]); ax.set_ylabel(L["equity"])
    ax.grid(alpha=0.25); ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_wfo_window_returns(
    roll_df: pd.DataFrame, anch_df: pd.DataFrame, out_path: str, has_cjk: bool = True
) -> str:
    L = _L(has_cjk)
    fig, ax = plt.subplots(figsize=(12, 4.8), dpi=110)
    x = np.arange(len(roll_df))
    w = 0.4
    ax.bar(x - w / 2, roll_df["test_return"].to_numpy() * 100, width=w,
           color="#2563eb", alpha=0.9, label=L["roll"], edgecolor="white", linewidth=0.5)
    ax.bar(x + w / 2, anch_df["test_return"].to_numpy() * 100, width=w,
           color="#dc2626", alpha=0.9, label=L["anch"], edgecolor="white", linewidth=0.5)
    ax.axhline(0, color="#334155", lw=1.0)
    ax.set_title(L["t_win"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["win_no"]); ax.set_ylabel(L["win_ret"])
    ax.set_xticks(x); ax.grid(alpha=0.25, axis="y"); ax.legend(loc="upper right")
    rp = int((roll_df["test_return"] > 0).sum()); ap = int((anch_df["test_return"] > 0).sum())
    ax.text(0.01, 0.97, f"rolling {rp}/{len(roll_df)} positive   anchored {ap}/{len(anch_df)} positive",
            transform=ax.transAxes, ha="left", va="top", fontsize=9, color="#475569")
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_wfo_param_compare(
    roll_df: pd.DataFrame, anch_df: pd.DataFrame, out_path: str, has_cjk: bool = True
) -> str:
    L = _L(has_cjk)
    fig, axes = plt.subplots(3, 1, figsize=(12, 8.4), dpi=110, sharex=True)
    x = np.arange(len(roll_df))
    specs = [("ema_fast", "EMA fast"), ("ema_slow", "EMA slow"), ("atr_stop", "ATR stop multiple")]
    for ax, (col, label) in zip(axes, specs):
        if col in roll_df:
            ax.plot(x, roll_df[col].to_numpy(), marker="o", ms=4, lw=1.4,
                    color="#2563eb", label=L["roll"])
        if col in anch_df:
            ax.plot(x, anch_df[col].to_numpy(), marker="s", ms=4, lw=1.4,
                    color="#dc2626", label=L["anch"])
        ax.set_ylabel(label, fontsize=10); ax.grid(alpha=0.25)
        ru = len(np.unique(roll_df[col].to_numpy())) if col in roll_df else 0
        au = len(np.unique(anch_df[col].to_numpy())) if col in anch_df else 0
        ax.text(0.99, 0.9, f"unique: rolling={ru}  anchored={au}", transform=ax.transAxes,
                ha="right", va="top", fontsize=9, color="#475569")
        ax.legend(loc="upper left", fontsize=8)
    axes[0].set_title(L["t_param"], fontsize=13, fontweight="bold")
    axes[-1].set_xlabel(L["win_no"])
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_wfo_train_test(
    roll_df: pd.DataFrame, anch_df: pd.DataFrame, out_path: str, has_cjk: bool = True
) -> str:
    L = _L(has_cjk)
    fig, ax = plt.subplots(figsize=(12, 5.0), dpi=110)
    x = np.arange(len(roll_df))
    w = 0.2
    ax.bar(x - 1.5 * w, roll_df["train_metric"].to_numpy(), width=w, color="#93c5fd",
           label=f"{L['train']} (rolling)", edgecolor="white", linewidth=0.5)
    ax.bar(x - 0.5 * w, roll_df["test_sharpe"].to_numpy(), width=w, color="#1d4ed8",
           label=f"{L['test']} (rolling)", edgecolor="white", linewidth=0.5)
    ax.bar(x + 0.5 * w, anch_df["train_metric"].to_numpy(), width=w, color="#fca5a5",
           label=f"{L['train']} (anchored)", edgecolor="white", linewidth=0.5)
    ax.bar(x + 1.5 * w, anch_df["test_sharpe"].to_numpy(), width=w, color="#b91c1c",
           label=f"{L['test']} (anchored)", edgecolor="white", linewidth=0.5)
    ax.axhline(0, color="#334155", lw=1.0)
    ax.set_title(L["t_tt"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["win_no"]); ax.set_ylabel(L["sharpe"])
    ax.set_xticks(x); ax.grid(alpha=0.25, axis="y"); ax.legend(loc="upper right", fontsize=8, ncol=2)
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_wfo_drawdown(
    roll_eq: pd.Series, anch_eq: pd.Series, out_path: str, has_cjk: bool = True
) -> str:
    L = _L(has_cjk)
    fig, ax = plt.subplots(figsize=(12, 4.0), dpi=110)
    for eq, color, label in ((roll_eq, "#2563eb", L["roll"]), (anch_eq, "#dc2626", L["anch"])):
        dd = (eq / eq.cummax() - 1.0) * 100.0
        ax.plot(dd.index, dd.values, lw=1.2, color=color, label=label)
        ax.fill_between(dd.index, dd.values, 0, color=color, alpha=0.15)
    ax.set_title(L["t_dd"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["date"]); ax.set_ylabel(L["dd"])
    ax.grid(alpha=0.25); ax.legend(loc="lower left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path
