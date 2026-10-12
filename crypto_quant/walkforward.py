"""滾動前進驗證（Walk-Forward Validation）引擎。

為什麼需要 walk-forward
----------------------
單次「樣本內 / 樣本外」切分只有**一個**樣本外區段，而且切點是人為選的：
只要切點換一個位置，結論就可能完全不同。更糟的是，它無法回答一個關鍵問題 ——
**「這組參數在未來還有效嗎？」** 因為它只被檢驗過一次。

Walk-forward 把時間軸切成連續的 (訓練窗 → 測試窗) 配對：

    |---- train 1 ----|-- test 1 --|
              |---- train 2 ----|-- test 2 --|
                        |---- train 3 ----|-- test 3 --|
    ...

每個窗口都**只用該窗口訓練窗的資料**做參數網格搜尋，選出最佳參數後，
**只用那一組參數**在緊接的測試窗上跑回測。所有測試窗的結果串接起來，
就是一條「完全由樣本外資料構成」的權益曲線 —— 這是唯一可信的績效來源。

兩種窗口模式
------------
* ``rolling``（滾動）：訓練窗長度固定，隨時間向前滑動。
  適合市場結構會改變的情況（舊資料不再代表現在）。
* ``anchored``（擴張）：訓練窗起點固定，長度隨時間遞增。
  適合「越多歷史越好」的假設。

避免未來函數的三道防線
----------------------
1. **訓練窗與測試窗絕不重疊**：``train_end == test_start``，且以索引嚴格切分。
2. **參數只能用訓練窗決定**：網格搜尋只在 ``train_df`` 上執行，
   測試窗的資料在選參數時完全不可見。
3. **指標暖機只用過去資料**：測試窗回測時往前接一段暖機區間，
   該區間取自訓練窗尾端（純過去資料），並以 ``trade_start`` 禁止在暖機期進場。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .backtest import BacktestConfig, run_backtest
from .metrics import summarize
from .strategy import StrategyParams

# ---------------------------------------------------------------------------
# 預設網格：EMA 快慢週期 × ATR 停損倍數（可再加入移動停損倍數）
# ---------------------------------------------------------------------------
DEFAULT_GRID: Dict[str, Sequence] = {
    "ema_fast": (9, 13, 21, 34),
    "ema_slow": (34, 55, 89),
    "atr_stop": (1.5, 2.0, 2.5, 3.0, 4.0),
    "atr_trail": (5.0,),
}


@dataclass
class WalkForwardConfig:
    """Walk-forward 切窗設定。"""

    train_bars: int = 730          # 訓練窗長度（根）
    test_bars: int = 180           # 測試窗長度（根）
    mode: str = "rolling"          # "rolling"（滾動）| "anchored"（擴張）
    step_bars: Optional[int] = None  # 每次前進幾根；預設 = test_bars（測試窗無縫相接）
    metric: str = "sharpe"         # 選參數的目標：sharpe | total_return | calmar | sortino
    min_trades: int = 3            # 訓練窗至少要有幾筆交易才納入評選
    warmup_bars: int = 0           # 0 = 自動依指標週期推算

    def resolved_step(self) -> int:
        return int(self.step_bars or self.test_bars)


@dataclass
class WindowResult:
    """單一 walk-forward 窗口的結果。"""

    index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train_bars: int
    test_bars: int
    best_params: Dict[str, float]
    train_metric: float
    train_summary: Dict
    test_summary: Dict
    test_equity: pd.Series
    test_trades: List
    n_candidates: int
    n_candidates_valid: int

    def to_row(self) -> Dict:
        return {
            "window": self.index,
            "train_start": self.train_start.date(),
            "train_end": self.train_end.date(),
            "test_start": self.test_start.date(),
            "test_end": self.test_end.date(),
            "train_bars": self.train_bars,
            "test_bars": self.test_bars,
            "ema_fast": self.best_params.get("ema_fast"),
            "ema_slow": self.best_params.get("ema_slow"),
            "atr_stop": self.best_params.get("atr_stop"),
            "atr_trail": self.best_params.get("atr_trail"),
            "train_metric": round(float(self.train_metric), 4),
            "test_return": round(float(self.test_summary["total_return"]), 4),
            "test_sharpe": round(float(self.test_summary["sharpe"]), 4),
            "test_mdd": round(float(self.test_summary["max_drawdown"]), 4),
            "test_trades": int(self.test_summary["trades"]),
            "test_win_rate": round(float(self.test_summary["win_rate"]), 4),
            "candidates": self.n_candidates,
            "candidates_valid": self.n_candidates_valid,
        }


@dataclass
class WalkForwardResult:
    """整體 walk-forward 結果。"""

    oos_equity: pd.Series
    windows: List[WindowResult]
    aggregate: Dict
    param_stability: Dict
    wf_config: WalkForwardConfig
    base_config: BacktestConfig
    symbol: str = ""
    source: str = ""

    @property
    def windows_df(self) -> pd.DataFrame:
        return pd.DataFrame([w.to_row() for w in self.windows])

    @property
    def all_oos_trades(self) -> List:
        out: List = []
        for w in self.windows:
            out.extend(w.test_trades)
        return out


# ---------------------------------------------------------------------------
# 內部工具
# ---------------------------------------------------------------------------
def _score(summary: Dict, metric: str) -> float:
    """從績效摘要取出評分。非有限值一律視為不可用。"""
    key = {
        "sharpe": "sharpe",
        "sortino": "sortino",
        "calmar": "calmar",
        "total_return": "total_return",
    }.get(metric, "sharpe")
    val = summary.get(key, float("nan"))
    try:
        val = float(val)
    except (TypeError, ValueError):
        return float("nan")
    return val if np.isfinite(val) else float("nan")


def _auto_warmup(params: StrategyParams) -> int:
    """依指標週期推算暖機根數（取最長者再加緩衝）。"""
    need = max(
        params.ema_slow,
        params.atr_len,
        params.rsi_len,
        params.macd_slow + params.macd_signal,
        params.st_len if params.use_supertrend else 0,
    )
    return int(need) + 5


def _grid_combos(grid: Dict[str, Sequence]) -> List[Dict[str, float]]:
    """展開網格，並過濾掉 ema_fast >= ema_slow 的無效組合。"""
    combos: List[Dict[str, float]] = []
    for ef in grid.get("ema_fast", (21,)):
        for es in grid.get("ema_slow", (55,)):
            if ef >= es:
                continue
            for am in grid.get("atr_stop", (3.0,)):
                for tm in grid.get("atr_trail", (5.0,)):
                    combos.append(
                        {"ema_fast": int(ef), "ema_slow": int(es),
                         "atr_stop": float(am), "atr_trail": float(tm)}
                    )
    return combos


def _search_on_train(
    train_df: pd.DataFrame,
    base_params: StrategyParams,
    base_cfg: BacktestConfig,
    combos: List[Dict[str, float]],
    metric: str,
    min_trades: int,
) -> tuple[Optional[Dict], float, int, List[Dict]]:
    """在訓練窗上做網格搜尋，回傳 (最佳參數, 最佳分數, 有效組合數, 全部結果列)。"""
    best: Optional[Dict] = None
    best_score = float("-inf")
    valid = 0
    rows: List[Dict] = []

    for combo in combos:
        p = replace(base_params, ema_fast=combo["ema_fast"], ema_slow=combo["ema_slow"])
        c = replace(base_cfg, atr_stop_mult=combo["atr_stop"], atr_trail_mult=combo["atr_trail"])
        try:
            res = run_backtest(train_df, p, c)
            s = summarize(res.equity, res.trades, "train")
        except Exception:  # noqa: BLE001 — 單一組合失敗不應中斷整個搜尋
            continue

        sc = _score(s, metric)
        row = {**combo, "score": sc, "trades": s["trades"],
               "total_return": s["total_return"], "sharpe": s["sharpe"]}
        rows.append(row)

        if s["trades"] < min_trades:
            continue
        if not np.isfinite(sc):
            continue
        valid += 1
        # 同分時以交易筆數較多者優先（樣本較充分），再以總報酬決勝
        if sc > best_score or (
            sc == best_score and best is not None
            and (s["trades"], s["total_return"]) > (best["_trades"], best["_ret"])
        ):
            best_score = sc
            best = {**combo, "_trades": s["trades"], "_ret": s["total_return"]}

    if best is not None:
        best = {k: v for k, v in best.items() if not k.startswith("_")}
    return best, best_score, valid, rows


def _build_windows(n: int, wf: WalkForwardConfig) -> List[tuple[int, int, int, int]]:
    """產生 (train_start, train_end, test_start, test_end) 索引清單（半開區間）。

    保證 ``train_end == test_start``（不重疊）且 ``test_end <= n``。
    """
    step = wf.resolved_step()
    out: List[tuple[int, int, int, int]] = []
    test_start = wf.train_bars
    while test_start + wf.test_bars <= n:
        train_start = 0 if wf.mode == "anchored" else test_start - wf.train_bars
        train_end = test_start
        test_end = test_start + wf.test_bars
        out.append((train_start, train_end, test_start, test_end))
        test_start += step
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_walk_forward(
    df: pd.DataFrame,
    wf: Optional[WalkForwardConfig] = None,
    base_cfg: Optional[BacktestConfig] = None,
    base_params: Optional[StrategyParams] = None,
    grid: Optional[Dict[str, Sequence]] = None,
    symbol: str = "",
    source: str = "",
) -> WalkForwardResult:
    """執行 walk-forward 驗證。

    ``df`` 需含 date/open/high/low/close/volume，且已依日期升冪排序。
    """
    wf = wf or WalkForwardConfig()
    base_cfg = base_cfg or BacktestConfig()
    base_params = base_params or StrategyParams()
    grid = grid or DEFAULT_GRID

    df = df.reset_index(drop=True)
    n = len(df)
    dates = pd.to_datetime(df["date"]).to_numpy()

    warmup = wf.warmup_bars or _auto_warmup(base_params)
    combos = _grid_combos(grid)
    windows_idx = _build_windows(n, wf)

    if not windows_idx:
        raise ValueError(
            f"資料長度 {n} 不足以切出任何窗口"
            f"（需要至少 train_bars+test_bars = {wf.train_bars + wf.test_bars} 根）"
        )

    running_equity = float(base_cfg.initial_capital)
    windows: List[WindowResult] = []
    oos_parts: List[pd.Series] = []

    for k, (tr_s, tr_e, te_s, te_e) in enumerate(windows_idx):
        train_df = df.iloc[tr_s:tr_e].reset_index(drop=True)
        test_df = df.iloc[te_s:te_e].reset_index(drop=True)

        # --- 1) 只用訓練窗選參數 ---
        best, best_score, n_valid, _rows = _search_on_train(
            train_df, base_params, base_cfg, combos, wf.metric, wf.min_trades
        )
        if best is None:
            # 訓練窗找不到合格參數 → 沿用基準參數（誠實記錄，不偷偷跳過）
            best = {"ema_fast": base_params.ema_fast, "ema_slow": base_params.ema_slow,
                    "atr_stop": base_cfg.atr_stop_mult, "atr_trail": base_cfg.atr_trail_mult}
            best_score = float("nan")

        p_best = replace(base_params, ema_fast=best["ema_fast"], ema_slow=best["ema_slow"])
        c_best = replace(base_cfg, atr_stop_mult=best["atr_stop"],
                         atr_trail_mult=best["atr_trail"])

        # --- 2) 測試窗回測：往前接暖機區間（純過去資料），並禁止暖機期進場 ---
        ctx_start = max(0, te_s - warmup)
        ctx_df = df.iloc[ctx_start:te_e].reset_index(drop=True)
        test_start_ts = pd.Timestamp(dates[te_s])

        c_test = replace(c_best, initial_capital=running_equity)
        res = run_backtest(
            ctx_df, p_best, c_test,
            source=source, symbol=symbol, interval="1d",
            trade_start=test_start_ts,
        )

        eq = res.equity[res.equity.index >= test_start_ts]
        if len(eq) == 0:
            continue
        # 暖機期禁止進場 → 測試窗起點權益應恰等於 running_equity；
        # 若因浮點或邊界仍有些微落差，以比例校正確保曲線連續。
        if eq.iloc[0] > 0 and abs(eq.iloc[0] - running_equity) > 1e-9:
            eq = eq * (running_equity / eq.iloc[0])

        test_trades = [t for t in res.trades if pd.Timestamp(t.entry_date) >= test_start_ts]
        test_summary = summarize(eq, test_trades, f"WF-test-{k}")
        # 訓練窗績效（同一組最佳參數）——只跑一次，避免重複計算
        train_res = run_backtest(train_df, p_best, c_best)
        train_summary = summarize(train_res.equity, train_res.trades, f"WF-train-{k}")

        windows.append(WindowResult(
            index=k,
            train_start=pd.Timestamp(dates[tr_s]),
            train_end=pd.Timestamp(dates[tr_e - 1]),
            test_start=pd.Timestamp(dates[te_s]),
            test_end=pd.Timestamp(dates[te_e - 1]),
            train_bars=tr_e - tr_s,
            test_bars=te_e - te_s,
            best_params=best,
            train_metric=best_score,
            train_summary=train_summary,
            test_summary=test_summary,
            test_equity=eq,
            test_trades=test_trades,
            n_candidates=len(combos),
            n_candidates_valid=n_valid,
        ))

        oos_parts.append(eq)
        running_equity = float(eq.iloc[-1])

    if not oos_parts:
        raise ValueError("所有窗口皆無法產生樣本外權益曲線")

    oos_equity = pd.concat(oos_parts)
    oos_equity = oos_equity[~oos_equity.index.duplicated(keep="first")].sort_index()

    all_trades = [t for w in windows for t in w.test_trades]
    aggregate = summarize(oos_equity, all_trades, "Walk-Forward OOS")
    aggregate["windows"] = len(windows)
    aggregate["train_bars"] = wf.train_bars
    aggregate["test_bars"] = wf.test_bars
    aggregate["mode"] = wf.mode
    aggregate["metric"] = wf.metric
    aggregate["warmup_bars"] = warmup
    aggregate["n_combos"] = len(combos)

    stability = analyze_param_stability(windows)

    return WalkForwardResult(
        oos_equity=oos_equity,
        windows=windows,
        aggregate=aggregate,
        param_stability=stability,
        wf_config=wf,
        base_config=base_cfg,
        symbol=symbol,
        source=source,
    )


def analyze_param_stability(windows: List[WindowResult]) -> Dict:
    """分析每個窗口選出的參數是否穩定。

    若同一參數在不同窗口間不斷跳動（眾數占比低、標準差大），
    代表策略對參數極度敏感 —— 這是**不穩健**的直接證據，
    也意味著「最佳參數」很可能只是雜訊。
    """
    keys = ["ema_fast", "ema_slow", "atr_stop", "atr_trail"]
    out: Dict = {"per_param": {}, "overall_stability": 0.0, "verdict": ""}
    if not windows:
        return out

    shares: List[float] = []
    for key in keys:
        vals = [w.best_params.get(key) for w in windows if w.best_params.get(key) is not None]
        if not vals:
            continue
        arr = np.array(vals, dtype=float)
        uniq, counts = np.unique(arr, return_counts=True)
        mode_idx = int(np.argmax(counts))
        mode_val = float(uniq[mode_idx])
        mode_share = float(counts[mode_idx] / len(arr))
        shares.append(mode_share)
        out["per_param"][key] = {
            "values": [float(v) for v in vals],
            "n_unique": int(len(uniq)),
            "mode": mode_val,
            "mode_share": round(mode_share, 4),
            "mean": round(float(arr.mean()), 4),
            "std": round(float(arr.std()), 4),
            "min": round(float(arr.min()), 4),
            "max": round(float(arr.max()), 4),
            "range": round(float(arr.max() - arr.min()), 4),
        }

    overall = float(np.mean(shares)) if shares else 0.0
    out["overall_stability"] = round(overall, 4)
    if overall >= 0.6:
        out["verdict"] = "相對穩定：多數窗口選出相同參數，策略對此參數不敏感。"
    elif overall >= 0.35:
        out["verdict"] = "中度不穩定：參數在數個值之間游移，需留意過度擬合。"
    else:
        out["verdict"] = "高度不穩定：幾乎每個窗口都選出不同參數，最佳參數極可能只是雜訊。"
    return out
