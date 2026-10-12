"""Walk-forward optimization（WFO）雙模式對照引擎。

本模組把 walk-forward 從「單一模式驗證」提升為「最佳化流程的雙模式對照」：

* **rolling（滾動窗）**：訓練窗長度固定，隨時間向前滑動。
  假設「近期市場結構比遠期更有代表性」。
* **anchored（擴張窗）**：訓練窗起點固定，長度隨時間遞增。
  假設「越多歷史越好」。

兩者使用**完全相同**的測試窗切點、參數網格、評分指標與成本模型，
唯一差異是訓練窗的取法 —— 因此效能差異可完全歸因於「訓練資料的取用方式」。

輸出四組對照
------------
1. rolling vs anchored 的樣本外績效對照表
2. 四方對照（rolling / anchored / 單次切分 / 買進持有）
3. 兩種模式各自的逐窗最佳參數
4. 兩種模式各自的參數穩定度與樣本外衰退
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .backtest import BacktestConfig, buy_and_hold, run_backtest
from .metrics import summarize
from .strategy import StrategyParams
from .walkforward import (
    DEFAULT_GRID, WalkForwardConfig, WalkForwardResult, run_walk_forward,
)

# 對照表要呈現的指標（順序即欄位順序）
COMPARE_KEYS: List[str] = [
    "total_return", "cagr", "max_drawdown", "sharpe", "sortino", "calmar",
    "win_rate", "profit_factor", "trades", "avg_bars",
]

# 中文欄名（報告與終端輸出共用）
KEY_LABELS: Dict[str, str] = {
    "total_return": "總報酬",
    "cagr": "年化報酬",
    "max_drawdown": "最大回撤",
    "sharpe": "Sharpe",
    "sortino": "Sortino",
    "calmar": "Calmar",
    "win_rate": "勝率",
    "profit_factor": "盈虧比",
    "trades": "交易次數",
    "avg_bars": "平均持有(根)",
}


@dataclass
class WFOComparison:
    """WFO 雙模式對照結果。"""

    rolling: WalkForwardResult
    anchored: WalkForwardResult
    single_split: Dict
    buy_hold: Dict
    comparison: pd.DataFrame
    four_way: pd.DataFrame
    verdict: Dict
    symbol: str = ""
    source: str = ""

    def to_summary(self) -> Dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "rolling": self.rolling.aggregate,
            "anchored": self.anchored.aggregate,
            "single_split": self.single_split,
            "buy_and_hold": self.buy_hold,
            "comparison": self.comparison.to_dict(orient="records"),
            "four_way": self.four_way.to_dict(orient="records"),
            "verdict": self.verdict,
            "rolling_stability": self.rolling.param_stability,
            "anchored_stability": self.anchored.param_stability,
            "rolling_decay": _decay(self.rolling.windows_df),
            "anchored_decay": _decay(self.anchored.windows_df),
            "rolling_windows": self.rolling.windows_df.to_dict(orient="records"),
            "anchored_windows": self.anchored.windows_df.to_dict(orient="records"),
        }


def _row(label: str, s: Dict) -> Dict:
    out: Dict = {"label": label}
    for k in COMPARE_KEYS:
        out[k] = s.get(k)
    return out


def _decay(windows_df: pd.DataFrame) -> Dict:
    """訓練窗 → 測試窗的績效衰退（以 Sharpe 為主）。"""
    tr = float(windows_df["train_metric"].mean())
    te = float(windows_df["test_sharpe"].mean())
    pct = (te - tr) / abs(tr) * 100.0 if tr else float("nan")
    return {"train_mean": tr, "test_mean": te, "pct_change": pct}


def _verdict(roll: WalkForwardResult, anch: WalkForwardResult) -> Dict:
    """判定哪一種模式在此資料上較穩健，並說明理由。

    評分方式：對 OOS Sharpe / Sortino / Calmar 三項「風險調整後報酬」各給一票，
    再對「參數穩定度」給一票，最後對「最大回撤較小」給一票。票數多者勝。
    """
    ra, aa = roll.aggregate, anch.aggregate
    votes = {"rolling": 0, "anchored": 0}
    detail: List[str] = []

    def _cmp(name: str, rv: float, av: float, higher_better: bool = True) -> None:
        if not (np.isfinite(rv) and np.isfinite(av)):
            return
        if abs(rv - av) < 1e-12:
            detail.append(f"{name}：兩者相同（{rv:.3f}）")
            return
        win = "rolling" if ((rv > av) == higher_better) else "anchored"
        votes[win] += 1
        detail.append(f"{name}：rolling {rv:.3f} vs anchored {av:.3f} → {win} 較佳")

    _cmp("OOS Sharpe", ra["sharpe"], aa["sharpe"])
    _cmp("OOS Sortino", ra["sortino"], aa["sortino"])
    _cmp("OOS Calmar", ra["calmar"], aa["calmar"])
    _cmp("最大回撤（絕對值越小越好）",
         abs(ra["max_drawdown"]), abs(aa["max_drawdown"]), higher_better=False)

    rs = roll.param_stability.get("overall_stability", 0.0)
    as_ = anch.param_stability.get("overall_stability", 0.0)
    _cmp("參數穩定度", rs, as_)

    winner = "rolling" if votes["rolling"] > votes["anchored"] else (
        "anchored" if votes["anchored"] > votes["rolling"] else "tie")

    if winner == "tie":
        text = "兩種模式在此資料上難分高下（票數相同），建議以參數穩定度較高者為準。"
    else:
        text = (
            f"在此資料上 **{winner}** 模式較穩健（票數 {votes[winner]} : "
            f"{votes['rolling' if winner == 'anchored' else 'anchored']}）。"
        )
    return {"winner": winner, "votes": votes, "detail": detail, "text": text}


def compare_modes(
    df: pd.DataFrame,
    wf: Optional[WalkForwardConfig] = None,
    base_cfg: Optional[BacktestConfig] = None,
    base_params: Optional[StrategyParams] = None,
    grid: Optional[Dict[str, Sequence]] = None,
    symbol: str = "",
    source: str = "",
) -> WFOComparison:
    """執行 rolling 與 anchored 兩種模式的完整對照。

    兩種模式共用同一組測試窗切點（由 ``wf.train_bars`` / ``wf.test_bars`` /
    ``wf.step_bars`` 決定），因此樣本外期間完全一致，可直接比較。
    """
    wf = wf or WalkForwardConfig()
    base_cfg = base_cfg or BacktestConfig()
    base_params = base_params or StrategyParams()
    grid = grid or DEFAULT_GRID

    # --- 1) 兩種模式各跑一次 walk-forward ---
    roll = run_walk_forward(
        df, wf=replace(wf, mode="rolling"), base_cfg=base_cfg,
        base_params=base_params, grid=grid, symbol=symbol, source=source,
    )
    anch = run_walk_forward(
        df, wf=replace(wf, mode="anchored"), base_cfg=base_cfg,
        base_params=base_params, grid=grid, symbol=symbol, source=source,
    )

    # --- 2) 對照組：單次切分（切點對齊 WF 樣本外起點，確保期間一致）---
    oos_start = roll.oos_equity.index[0]
    cut = int(wf.train_bars)
    single_df = df.iloc[cut:].reset_index(drop=True)
    single_res = run_backtest(single_df, base_params, base_cfg)
    single_sum = summarize(single_res.equity, single_res.trades, "Single-split OOS")

    # --- 3) 對照組：買進持有（同一段樣本外期間）---
    bh_full = buy_and_hold(df, base_cfg)
    bh = bh_full[bh_full.index >= oos_start]
    if len(bh):
        bh = bh * (base_cfg.initial_capital / bh.iloc[0])
    bh_sum = summarize(bh, [], "Buy & Hold (OOS)")

    # --- 4) 組表 ---
    comparison = pd.DataFrame([
        _row("rolling", roll.aggregate),
        _row("anchored", anch.aggregate),
    ])
    four_way = pd.DataFrame([
        _row("rolling WF", roll.aggregate),
        _row("anchored WF", anch.aggregate),
        _row("single split", single_sum),
        _row("buy & hold", bh_sum),
    ])

    return WFOComparison(
        rolling=roll, anchored=anch, single_split=single_sum, buy_hold=bh_sum,
        comparison=comparison, four_way=four_way, verdict=_verdict(roll, anch),
        symbol=symbol, source=source,
    )


def format_comparison(df: pd.DataFrame) -> str:
    """把對照表格式化成終端可讀字串。"""
    lines: List[str] = []
    header = f"{'模式':<12}" + "".join(f"{KEY_LABELS[k]:>14}" for k in COMPARE_KEYS)
    lines.append(header)
    lines.append("-" * len(header))
    for _, r in df.iterrows():
        cells: List[str] = []
        for k in COMPARE_KEYS:
            v = r[k]
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                cells.append(f"{'n/a':>14}")
            elif k in ("total_return", "cagr", "max_drawdown", "win_rate"):
                cells.append(f"{v * 100:>13.2f}%")
            elif k == "trades":
                cells.append(f"{int(v):>14}")
            elif k == "avg_bars":
                cells.append(f"{v:>14.1f}")
            else:
                cells.append(f"{v:>14.2f}")
        lines.append(f"{str(r['label']):<12}" + "".join(cells))
    return "\n".join(lines)
