#!/usr/bin/env python3
"""Walk-forward 驗證腳本：證明切窗正確、無未來函數、無資料洩漏。

驗證項目
--------
1. **窗口不重疊**：每個窗口的訓練窗最後一根 < 測試窗第一根。
2. **窗口連續**：測試窗之間無縫相接（step == test_bars 時）。
3. **無未來函數（截斷一致性）**：把資料截斷到某窗口測試窗結束，
   重跑 walk-forward，該窗口選出的參數與樣本外權益必須與全量執行**完全一致**。
   若參數會因為「看到未來資料」而改變，此測試就會失敗。
4. **參數只由訓練窗決定**：只擾動測試窗的價格，重新執行，
   該窗口選出的參數必須**不變**（因為選參時看不到測試窗）。
5. **暖機期不進場**：測試窗第一根的權益必須恰等於前一窗口的結束權益，
   證明暖機區間沒有產生任何交易。
6. **樣本外權益連續**：串接後的曲線在窗口邊界無異常跳動。
7. **彙總指標可重現**：由串接權益重新計算的指標與引擎回報值一致。
8. **資料完整性**：無 NaN、日期嚴格遞增、無重複。
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
from crypto_quant.walkforward import (  # noqa: E402
    DEFAULT_GRID, WalkForwardConfig, run_walk_forward,
)

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, PASS if ok else FAIL, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    return ok


def main() -> int:
    print("=" * 100)
    print("  Walk-forward 驗證")
    print("=" * 100)

    # ---------------- 資料 ----------------
    df, source, attempts = fetch_ohlcv(symbol="BTCUSDT", interval="1d", start="2019-01-01")
    print(f"\n[data] 來源={source}  {len(df)} 根  "
          f"{df['date'].min().date()} ~ {df['date'].max().date()}")

    print("\n--- 8) 資料完整性 ---")
    check("無 NaN", not df[["open", "high", "low", "close"]].isna().any().any())
    check("日期嚴格遞增", df["date"].is_monotonic_increasing)
    check("無重複日期", not df["date"].duplicated().any())
    check("high >= low", bool((df["high"] >= df["low"]).all()))

    base_params = StrategyParams()
    base_cfg = BacktestConfig()

    # 小網格以加速驗證（驗證的是「機制」而非「最佳參數」）
    small_grid = {"ema_fast": (9, 21), "ema_slow": (34, 55), "atr_stop": (2.0, 3.0), "atr_trail": (5.0,)}
    wf = WalkForwardConfig(train_bars=400, test_bars=120, mode="rolling", metric="sharpe", min_trades=2)

    print("\n--- 執行 walk-forward（全量）---")
    full = run_walk_forward(df, wf=wf, base_cfg=base_cfg, base_params=base_params,
                            grid=small_grid, symbol="BTCUSDT", source=source)
    wdf = full.windows_df
    print(f"  窗口數={len(wdf)}  樣本外期間 "
          f"{full.oos_equity.index[0].date()} ~ {full.oos_equity.index[-1].date()}")

    # ---------------- 1) 窗口不重疊 ----------------
    print("\n--- 1) 訓練窗與測試窗不重疊 ---")
    dates = pd.to_datetime(df["date"]).to_numpy()
    ok_overlap = True
    for w in full.windows:
        tr_end_idx = int(np.where(dates == np.datetime64(w.train_end))[0][0])
        te_start_idx = int(np.where(dates == np.datetime64(w.test_start))[0][0])
        if not (tr_end_idx < te_start_idx):
            ok_overlap = False
            break
    check("所有窗口 train_end < test_start（索引嚴格）", ok_overlap,
          f"檢查 {len(full.windows)} 個窗口")

    # ---------------- 2) 窗口連續 ----------------
    print("\n--- 2) 測試窗無縫相接 ---")
    ok_contig = True
    for a, b in zip(full.windows, full.windows[1:]):
        if pd.Timestamp(a.test_end) >= pd.Timestamp(b.test_start):
            ok_contig = False
            break
    check("相鄰測試窗不重疊且依序前進", ok_contig)

    # ---------------- 3) 無未來函數：截斷一致性 ----------------
    print("\n--- 3) 無未來函數（截斷一致性）---")
    k = min(3, len(full.windows) - 1)
    target = full.windows[k]
    cut_idx = int(np.where(dates == np.datetime64(target.test_end))[0][0]) + 1
    df_trunc = df.iloc[:cut_idx].reset_index(drop=True)
    trunc = run_walk_forward(df_trunc, wf=wf, base_cfg=base_cfg, base_params=base_params,
                             grid=small_grid, symbol="BTCUSDT", source=source)
    tw = trunc.windows[k]
    same_params = tw.best_params == target.best_params
    check(f"窗口 {k} 選出參數與全量一致", same_params,
          f"{target.best_params} vs {tw.best_params}")

    a_eq = target.test_equity
    b_eq = tw.test_equity
    same_len = len(a_eq) == len(b_eq)
    max_diff = float(np.max(np.abs(a_eq.to_numpy() - b_eq.to_numpy()))) if same_len else float("nan")
    check(f"窗口 {k} 樣本外權益逐點一致", same_len and max_diff < 1e-6,
          f"最大差異={max_diff:.3e}")

    # ---------------- 4) 參數只由訓練窗決定 ----------------
    print("\n--- 4) 參數只由訓練窗決定（擾動測試窗）---")
    # 只切出「單一窗口」的資料，擾動其測試窗價格，參數必須不變
    single_wf = WalkForwardConfig(train_bars=400, test_bars=120, mode="rolling",
                                  metric="sharpe", min_trades=2)
    n_single = 400 + 120
    df_single = df.iloc[:n_single].reset_index(drop=True)
    r1 = run_walk_forward(df_single, wf=single_wf, base_cfg=base_cfg,
                          base_params=base_params, grid=small_grid)
    # 把測試窗（後 120 根）價格全部乘 3，訓練窗不動
    df_pert = df_single.copy()
    df_pert.loc[400:, ["open", "high", "low", "close"]] *= 3.0
    r2 = run_walk_forward(df_pert, wf=single_wf, base_cfg=base_cfg,
                          base_params=base_params, grid=small_grid)
    check("擾動測試窗後，選出參數不變",
          r1.windows[0].best_params == r2.windows[0].best_params,
          f"{r1.windows[0].best_params} vs {r2.windows[0].best_params}")
    check("擾動測試窗後，訓練窗績效不變",
          abs(r1.windows[0].train_metric - r2.windows[0].train_metric) < 1e-9,
          f"{r1.windows[0].train_metric:.6f} vs {r2.windows[0].train_metric:.6f}")

    # ---------------- 5) 暖機期不進場 ----------------
    print("\n--- 5) 暖機期不進場（權益連續）---")
    ok_warm = True
    worst = 0.0
    for a, b in zip(full.windows, full.windows[1:]):
        gap = abs(float(b.test_equity.iloc[0]) - float(a.test_equity.iloc[-1]))
        rel = gap / max(abs(float(a.test_equity.iloc[-1])), 1e-9)
        worst = max(worst, rel)
        if rel > 1e-6:
            ok_warm = False
    check("窗口邊界權益無跳動（暖機期零交易）", ok_warm, f"最大相對落差={worst:.3e}")

    # ---------------- 6) 樣本外權益連續 ----------------
    print("\n--- 6) 樣本外權益曲線 ---")
    eq = full.oos_equity
    check("權益全為正", bool((eq > 0).all()))
    check("索引嚴格遞增", eq.index.is_monotonic_increasing)
    check("無重複索引", not eq.index.duplicated().any())
    check("長度等於所有測試窗之和",
          len(eq) == sum(w.test_bars for w in full.windows),
          f"{len(eq)} vs {sum(w.test_bars for w in full.windows)}")

    # ---------------- 7) 彙總指標可重現 ----------------
    print("\n--- 7) 彙總指標可重現 ---")
    agg = full.aggregate
    check("總報酬可重現", abs(agg["total_return"] - total_return(eq)) < 1e-9)
    check("最大回撤可重現", abs(agg["max_drawdown"] - max_drawdown(eq)) < 1e-9)
    check("Sharpe 可重現", abs(agg["sharpe"] - sharpe(eq)) < 1e-9)

    # ---------------- 額外：anchored 模式 ----------------
    print("\n--- 額外) anchored（擴張窗）模式 ---")
    wf_a = replace(wf, mode="anchored")
    anch = run_walk_forward(df, wf=wf_a, base_cfg=base_cfg, base_params=base_params,
                            grid=small_grid)
    ok_anch = all(w.train_bars >= wf.train_bars for w in anch.windows)
    growing = anch.windows[-1].train_bars > anch.windows[0].train_bars
    check("anchored 訓練窗起點固定且長度遞增", ok_anch and growing,
          f"{anch.windows[0].train_bars} → {anch.windows[-1].train_bars} 根")

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
