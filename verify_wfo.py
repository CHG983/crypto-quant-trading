#!/usr/bin/env python3
"""WFO 雙模式對照驗證腳本：證明切窗正確、無未來函數、無資料洩漏。

驗證項目
--------
1.  **訓練／測試窗不重疊**：逐窗口比對索引，斷言 train_end < test_start。
2.  **測試窗無縫相接**：相鄰測試窗不重疊且依序前進。
3.  **無未來函數（截斷一致性）**：把資料截斷到某窗口測試窗結束後重跑，
    該窗口選出的參數與樣本外權益必須與全量執行**完全一致**。
4.  **參數只由訓練窗決定**：只擾動測試窗價格（×3），選出參數必須不變。
5.  **暖機期不進場**：窗口邊界權益必須連續（暖機期零交易）。
6.  **樣本外權益連續**：全為正、索引嚴格遞增、長度等於所有測試窗之和。
7.  **彙總指標可重現**：由串接權益重算的指標與引擎回報值一致。
8.  **rolling 訓練窗固定**：所有窗口訓練窗長度相同。
9.  **anchored 訓練窗遞增**：起點固定、長度嚴格遞增。
10. **兩模式測試窗一致**：rolling 與 anchored 的測試窗切點完全相同
    （否則兩者不可直接比較）。
11. **資料完整性**：無 NaN、日期嚴格遞增、無重複、high >= low。
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from crypto_quant.backtest import BacktestConfig  # noqa: E402
from crypto_quant.data_fetch import fetch_ohlcv  # noqa: E402
from crypto_quant.metrics import max_drawdown, sharpe, total_return  # noqa: E402
from crypto_quant.strategy import StrategyParams  # noqa: E402
from crypto_quant.walkforward import WalkForwardConfig, run_walk_forward  # noqa: E402
from crypto_quant.wfo import compare_modes  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, PASS if ok else FAIL, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    return ok


def main() -> int:
    print("=" * 100)
    print("  WFO 雙模式對照驗證")
    print("=" * 100)

    df, source, _ = fetch_ohlcv(symbol="BTCUSDT", interval="1d", start="2019-01-01")
    print(f"\n[data] 來源={source}  {len(df)} 根  "
          f"{df['date'].min().date()} ~ {df['date'].max().date()}")

    print("\n--- 11) 資料完整性 ---")
    check("無 NaN", not df[["open", "high", "low", "close"]].isna().any().any())
    check("日期嚴格遞增", df["date"].is_monotonic_increasing)
    check("無重複日期", not df["date"].duplicated().any())
    check("high >= low", bool((df["high"] >= df["low"]).all()))

    base_params = StrategyParams()
    base_cfg = BacktestConfig()
    # 小網格以加速驗證（驗證的是「機制」而非「最佳參數」）
    small_grid = {"ema_fast": (9, 21), "ema_slow": (34, 55),
                  "atr_stop": (2.0, 3.0), "atr_trail": (5.0,)}
    wf = WalkForwardConfig(train_bars=400, test_bars=120, mode="rolling",
                           metric="sharpe", min_trades=2)

    print("\n--- 執行 WFO 雙模式（全量）---")
    cmp = compare_modes(df, wf=wf, base_cfg=base_cfg, base_params=base_params,
                        grid=small_grid, symbol="BTCUSDT", source=source)
    roll, anch = cmp.rolling, cmp.anchored
    print(f"  rolling 窗口數={len(roll.windows)}  anchored 窗口數={len(anch.windows)}")

    dates = pd.to_datetime(df["date"]).to_numpy()

    # ---------------- 1) 不重疊 ----------------
    print("\n--- 1) 訓練窗與測試窗不重疊 ---")
    for label, res in (("rolling", roll), ("anchored", anch)):
        ok = True
        for w in res.windows:
            tr_end = int(np.where(dates == np.datetime64(w.train_end))[0][0])
            te_start = int(np.where(dates == np.datetime64(w.test_start))[0][0])
            if not (tr_end < te_start):
                ok = False
                break
        check(f"{label}：所有窗口 train_end < test_start", ok, f"{len(res.windows)} 個窗口")

    # ---------------- 2) 測試窗無縫相接 ----------------
    print("\n--- 2) 測試窗無縫相接 ---")
    for label, res in (("rolling", roll), ("anchored", anch)):
        ok = all(pd.Timestamp(a.test_end) < pd.Timestamp(b.test_start)
                 for a, b in zip(res.windows, res.windows[1:]))
        check(f"{label}：相鄰測試窗不重疊且依序前進", ok)

    # ---------------- 3) 無未來函數：截斷一致性 ----------------
    print("\n--- 3) 無未來函數（截斷一致性）---")
    for label, res in (("rolling", roll), ("anchored", anch)):
        k = min(3, len(res.windows) - 1)
        target = res.windows[k]
        cut_idx = int(np.where(dates == np.datetime64(target.test_end))[0][0]) + 1
        df_trunc = df.iloc[:cut_idx].reset_index(drop=True)
        trunc = run_walk_forward(df_trunc, wf=replace(wf, mode=label), base_cfg=base_cfg,
                                 base_params=base_params, grid=small_grid)
        tw = trunc.windows[k]
        check(f"{label}：窗口 {k} 選出參數與全量一致",
              tw.best_params == target.best_params,
              f"{target.best_params} vs {tw.best_params}")
        a_eq, b_eq = target.test_equity, tw.test_equity
        same_len = len(a_eq) == len(b_eq)
        md = float(np.max(np.abs(a_eq.to_numpy() - b_eq.to_numpy()))) if same_len else float("nan")
        check(f"{label}：窗口 {k} 樣本外權益逐點一致", same_len and md < 1e-6,
              f"最大差異={md:.3e}")

    # ---------------- 4) 參數只由訓練窗決定 ----------------
    print("\n--- 4) 參數只由訓練窗決定（擾動測試窗）---")
    n_single = 400 + 120
    df_single = df.iloc[:n_single].reset_index(drop=True)
    for label in ("rolling", "anchored"):
        wf1 = replace(wf, mode=label)
        r1 = run_walk_forward(df_single, wf=wf1, base_cfg=base_cfg,
                              base_params=base_params, grid=small_grid)
        df_pert = df_single.copy()
        df_pert.loc[400:, ["open", "high", "low", "close"]] *= 3.0
        r2 = run_walk_forward(df_pert, wf=wf1, base_cfg=base_cfg,
                              base_params=base_params, grid=small_grid)
        check(f"{label}：擾動測試窗後選出參數不變",
              r1.windows[0].best_params == r2.windows[0].best_params,
              f"{r1.windows[0].best_params} vs {r2.windows[0].best_params}")
        check(f"{label}：擾動測試窗後訓練窗績效不變",
              abs(r1.windows[0].train_metric - r2.windows[0].train_metric) < 1e-9)

    # ---------------- 5) 暖機期不進場 ----------------
    print("\n--- 5) 暖機期不進場（權益連續）---")
    for label, res in (("rolling", roll), ("anchored", anch)):
        worst = 0.0
        for a, b in zip(res.windows, res.windows[1:]):
            gap = abs(float(b.test_equity.iloc[0]) - float(a.test_equity.iloc[-1]))
            worst = max(worst, gap / max(abs(float(a.test_equity.iloc[-1])), 1e-9))
        check(f"{label}：窗口邊界權益無跳動", worst < 1e-6, f"最大相對落差={worst:.3e}")

    # ---------------- 6) 樣本外權益連續 ----------------
    print("\n--- 6) 樣本外權益曲線 ---")
    for label, res in (("rolling", roll), ("anchored", anch)):
        eq = res.oos_equity
        check(f"{label}：權益全為正", bool((eq > 0).all()))
        check(f"{label}：索引嚴格遞增", eq.index.is_monotonic_increasing)
        check(f"{label}：無重複索引", not eq.index.duplicated().any())
        check(f"{label}：長度等於所有測試窗之和",
              len(eq) == sum(w.test_bars for w in res.windows),
              f"{len(eq)} vs {sum(w.test_bars for w in res.windows)}")

    # ---------------- 7) 彙總指標可重現 ----------------
    print("\n--- 7) 彙總指標可重現 ---")
    for label, res in (("rolling", roll), ("anchored", anch)):
        agg, eq = res.aggregate, res.oos_equity
        check(f"{label}：總報酬可重現", abs(agg["total_return"] - total_return(eq)) < 1e-9)
        check(f"{label}：最大回撤可重現", abs(agg["max_drawdown"] - max_drawdown(eq)) < 1e-9)
        check(f"{label}：Sharpe 可重現", abs(agg["sharpe"] - sharpe(eq)) < 1e-9)

    # ---------------- 8) rolling 訓練窗固定 ----------------
    print("\n--- 8) rolling 訓練窗固定 ---")
    lens = {w.train_bars for w in roll.windows}
    check("rolling：所有窗口訓練窗長度相同", len(lens) == 1, f"長度集合={lens}")

    # ---------------- 9) anchored 訓練窗遞增 ----------------
    print("\n--- 9) anchored 訓練窗遞增 ---")
    starts = {w.train_start for w in anch.windows}
    lens_a = [w.train_bars for w in anch.windows]
    check("anchored：訓練窗起點固定", len(starts) == 1, f"起點={list(starts)[0].date()}")
    check("anchored：訓練窗長度嚴格遞增",
          all(b > a for a, b in zip(lens_a, lens_a[1:])),
          f"{lens_a[0]} → {lens_a[-1]} 根")

    # ---------------- 10) 兩模式測試窗一致 ----------------
    print("\n--- 10) 兩模式測試窗切點一致 ---")
    same = (len(roll.windows) == len(anch.windows) and
            all(pd.Timestamp(a.test_start) == pd.Timestamp(b.test_start) and
                pd.Timestamp(a.test_end) == pd.Timestamp(b.test_end)
                for a, b in zip(roll.windows, anch.windows)))
    check("rolling 與 anchored 測試窗完全相同（可直接比較）", same,
          f"{len(roll.windows)} 個窗口")

    # ---------------- 總結 ----------------
    n_pass = sum(1 for _, s, _ in _results if s == PASS)
    n_fail = len(_results) - n_pass
    print("\n" + "=" * 100)
    print(f"  驗證結果：{n_pass}/{len(_results)} 通過，{n_fail} 失敗")
    print("=" * 100)
    if n_fail:
        print("\n失敗項目：")
        for name, s, detail in _results:
            if s == FAIL:
                print(f"  - {name}  {detail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
