"""Walk-forward 專用繪圖（含中文字型處理，避免亂碼）。

四張圖
------
1. ``plot_wf_equity``       樣本外權益曲線 vs 買進持有 vs 單次切分
2. ``plot_wf_window_returns`` 各窗口樣本外報酬分布（長條 + 累積）
3. ``plot_wf_param_evolution`` 參數隨時間變化（看是否穩定）
4. ``plot_wf_train_vs_test``  訓練窗 vs 測試窗績效對照（看樣本外衰退）
"""
from __future__ import annotations

from typing import List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .plots import setup_cjk_font  # noqa: E402


def _L(has_cjk: bool) -> dict:
    if has_cjk:
        return {
            "date": "日期", "equity": "權益 (USDT)",
            "t_wf": "Walk-forward 樣本外權益曲線",
            "wf": "Walk-forward OOS", "bh": "買進持有", "single": "單次切分 OOS",
            "t_win": "各窗口樣本外報酬",
            "win_ret": "窗口報酬 (%)", "win_no": "窗口編號",
            "t_param": "參數隨時間變化（穩定度檢視）",
            "t_tt": "訓練窗 vs 測試窗績效（樣本外衰退）",
            "train": "訓練窗", "test": "測試窗", "sharpe": "Sharpe",
            "t_dd": "Walk-forward 樣本外回撤",
            "dd": "回撤 (%)",
        }
    return {
        "date": "Date", "equity": "Equity (USDT)",
        "t_wf": "Walk-Forward Out-of-Sample Equity",
        "wf": "Walk-forward OOS", "bh": "Buy & Hold", "single": "Single-split OOS",
        "t_win": "Per-Window Out-of-Sample Return",
        "win_ret": "Window return (%)", "win_no": "Window #",
        "t_param": "Parameter Evolution Over Time (stability check)",
        "t_tt": "Train vs Test Performance (out-of-sample decay)",
        "train": "Train", "test": "Test", "sharpe": "Sharpe",
        "t_dd": "Walk-Forward Out-of-Sample Drawdown",
        "dd": "Drawdown (%)",
    }


def plot_wf_equity(
    oos: pd.Series,
    bh: Optional[pd.Series],
    out_path: str,
    single_split: Optional[pd.Series] = None,
    has_cjk: bool = True,
) -> str:
    L = _L(has_cjk)
    fig, ax = plt.subplots(figsize=(12, 5.4), dpi=110)
    ax.plot(oos.index, oos.values, lw=2.0, color="#2563eb", label=L["wf"])
    if bh is not None and len(bh):
        ax.plot(bh.index, bh.values, lw=1.4, color="#94a3b8", ls="--", label=L["bh"])
    if single_split is not None and len(single_split):
        ax.plot(single_split.index, single_split.values, lw=1.3, color="#f59e0b",
                ls=":", label=L["single"])
    ax.set_title(L["t_wf"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["date"]); ax.set_ylabel(L["equity"])
    ax.grid(alpha=0.25); ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_wf_window_returns(windows_df: pd.DataFrame, out_path: str, has_cjk: bool = True) -> str:
    L = _L(has_cjk)
    fig, ax = plt.subplots(figsize=(12, 4.6), dpi=110)
    x = windows_df["window"].to_numpy()
    y = windows_df["test_return"].to_numpy() * 100.0
    colors = ["#16a34a" if v >= 0 else "#dc2626" for v in y]
    ax.bar(x, y, color=colors, alpha=0.85, edgecolor="white", linewidth=0.5)
    ax.axhline(0, color="#334155", lw=1.0)
    ax.set_title(L["t_win"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["win_no"]); ax.set_ylabel(L["win_ret"])
    ax.grid(alpha=0.25, axis="y")
    n_pos = int((y > 0).sum())
    ax.text(0.99, 0.97, f"{n_pos}/{len(y)} positive", transform=ax.transAxes,
            ha="right", va="top", fontsize=9, color="#475569")
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_wf_param_evolution(windows_df: pd.DataFrame, out_path: str, has_cjk: bool = True) -> str:
    L = _L(has_cjk)
    fig, axes = plt.subplots(3, 1, figsize=(12, 8.0), dpi=110, sharex=True)
    x = windows_df["window"].to_numpy()
    specs = [
        ("ema_fast", "EMA fast", "#2563eb"),
        ("ema_slow", "EMA slow", "#8b5cf6"),
        ("atr_stop", "ATR stop multiple", "#dc2626"),
    ]
    for ax, (col, label, color) in zip(axes, specs):
        if col not in windows_df:
            continue
        ax.plot(x, windows_df[col].to_numpy(), marker="o", ms=4, lw=1.4, color=color)
        ax.set_ylabel(label, fontsize=10)
        ax.grid(alpha=0.25)
        vals = windows_df[col].to_numpy()
        uniq = len(np.unique(vals))
        ax.text(0.99, 0.9, f"unique={uniq}", transform=ax.transAxes,
                ha="right", va="top", fontsize=9, color="#475569")
    axes[0].set_title(L["t_param"], fontsize=13, fontweight="bold")
    axes[-1].set_xlabel(L["win_no"])
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_wf_train_vs_test(windows_df: pd.DataFrame, out_path: str, has_cjk: bool = True) -> str:
    L = _L(has_cjk)
    fig, ax = plt.subplots(figsize=(12, 4.8), dpi=110)
    x = windows_df["window"].to_numpy()
    w = 0.38
    ax.bar(x - w / 2, windows_df["train_metric"].to_numpy(), width=w,
           color="#93c5fd", label=L["train"], edgecolor="white", linewidth=0.5)
    ax.bar(x + w / 2, windows_df["test_sharpe"].to_numpy(), width=w,
           color="#1d4ed8", label=L["test"], edgecolor="white", linewidth=0.5)
    ax.axhline(0, color="#334155", lw=1.0)
    ax.set_title(L["t_tt"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["win_no"]); ax.set_ylabel(L["sharpe"])
    ax.grid(alpha=0.25, axis="y"); ax.legend(loc="upper right")
    fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path


def plot_wf_drawdown(oos: pd.Series, out_path: str, has_cjk: bool = True) -> str:
    L = _L(has_cjk)
    dd = (oos / oos.cummax() - 1.0) * 100.0
    fig, ax = plt.subplots(figsize=(12, 3.6), dpi=110)
    ax.fill_between(dd.index, dd.values, 0, color="#ef4444", alpha=0.35)
    ax.plot(dd.index, dd.values, lw=1.0, color="#b91c1c")
    ax.set_title(L["t_dd"], fontsize=13, fontweight="bold")
    ax.set_xlabel(L["date"]); ax.set_ylabel(L["dd"])
    ax.grid(alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out_path); plt.close(fig)
    return out_path
