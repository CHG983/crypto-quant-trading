#!/usr/bin/env python3
"""Walk-forward optimization（WFO）雙模式對照主程式。

用法
----
    python3 run_wfo.py --symbol BTCUSDT
    python3 run_wfo.py --symbol ETHUSDT --train 730 --test 180 --metric sharpe
    python3 run_wfo.py --symbol BTCUSDT --step 90 --min-trades 2

輸出（皆落在 --outdir，預設 ./output）
----
    {symbol}_wfo_compare.csv          rolling vs anchored 對照表
    {symbol}_wfo_fourway.csv          四方對照（+ 單次切分 + 買進持有）
    {symbol}_wfo_rolling_windows.csv  rolling 逐窗最佳參數
    {symbol}_wfo_anchored_windows.csv anchored 逐窗最佳參數
    {symbol}_wfo_rolling_equity.csv   rolling 樣本外權益曲線
    {symbol}_wfo_anchored_equity.csv  anchored 樣本外權益曲線
    {symbol}_wfo_summary.json         完整結果
    {symbol}_wfo_mode_equity.png      rolling vs anchored 權益疊圖
    {symbol}_wfo_window_returns.png   各窗口報酬（雙模式）
    {symbol}_wfo_params.png           參數隨時間變化（雙模式）
    {symbol}_wfo_train_test.png       訓練 vs 測試（雙模式）
    {symbol}_wfo_drawdown.png         回撤（雙模式）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402

from crypto_quant.backtest import BacktestConfig  # noqa: E402
from crypto_quant.data_fetch import fetch_ohlcv  # noqa: E402
from crypto_quant.metrics import format_summary  # noqa: E402
from crypto_quant.plots import setup_cjk_font  # noqa: E402
from crypto_quant.strategy import StrategyParams  # noqa: E402
from crypto_quant.walkforward import DEFAULT_GRID, WalkForwardConfig  # noqa: E402
from crypto_quant.wfo import compare_modes, format_comparison  # noqa: E402
from crypto_quant.wfo_plots import (  # noqa: E402
    plot_wfo_drawdown, plot_wfo_mode_equity, plot_wfo_param_compare,
    plot_wfo_train_test, plot_wfo_window_returns,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward optimization (rolling vs anchored)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--outdir", default="output")
    # --- WFO 切窗 ---
    ap.add_argument("--train", type=int, default=730, help="訓練窗長度（根）")
    ap.add_argument("--test", type=int, default=180, help="測試窗長度（根）")
    ap.add_argument("--step", type=int, default=None, help="每次前進根數（預設=test）")
    ap.add_argument("--metric", choices=["sharpe", "sortino", "calmar", "total_return"],
                    default="sharpe", help="訓練窗選參數的目標指標")
    ap.add_argument("--min-trades", type=int, default=3, help="訓練窗最少交易筆數")
    # --- 回測成本與部位 ---
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--fee", type=float, default=0.0004)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--funding", type=float, default=0.0001)
    ap.add_argument("--sizing", choices=["fraction", "risk"], default="fraction")
    ap.add_argument("--fraction", type=float, default=1.0)
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--leverage", type=float, default=3.0)
    ap.add_argument("--no-trailing", action="store_true")
    ap.add_argument("--no-short", action="store_true")
    ap.add_argument("--min-atr-pct", type=float, default=0.5)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    font = setup_cjk_font()
    has_cjk = font is not None
    print(f"[plot] 中文字型：{font if font else '未找到，改用英文標籤'}")

    # ---------------- 1) 資料 ----------------
    df, source, attempts = fetch_ohlcv(
        symbol=args.symbol, interval=args.interval, start=args.start, end=args.end
    )
    print(f"[data] 實際使用來源：{source}")
    for name, status, info in attempts:
        print(f"       - {name:<16} {status:<5} {info}")

    base_params = StrategyParams(min_atr_pct=args.min_atr_pct)
    base_cfg = BacktestConfig(
        initial_capital=args.capital, fee_rate=args.fee, slippage_rate=args.slippage,
        funding_rate_daily=args.funding, risk_pct=args.risk, max_leverage=args.leverage,
        sizing_mode=args.sizing, fraction=args.fraction,
        allow_short=not args.no_short, use_trailing=not args.no_trailing,
    )
    wf = WalkForwardConfig(
        train_bars=args.train, test_bars=args.test, step_bars=args.step,
        metric=args.metric, min_trades=args.min_trades,
    )

    print(f"\n[wfo] 訓練窗={wf.train_bars}  測試窗={wf.test_bars}  前進={wf.resolved_step()}  "
          f"目標={wf.metric}  最少交易={wf.min_trades}")
    print(f"[wfo] 網格：EMA fast {DEFAULT_GRID['ema_fast']} × EMA slow {DEFAULT_GRID['ema_slow']} "
          f"× ATR stop {DEFAULT_GRID['atr_stop']}（已排除 fast>=slow）")

    # ---------------- 2) 雙模式對照 ----------------
    cmp = compare_modes(
        df, wf=wf, base_cfg=base_cfg, base_params=base_params,
        grid=DEFAULT_GRID, symbol=args.symbol, source=source,
    )

    # ---------------- 3) 終端報表 ----------------
    print("\n" + "=" * 118)
    print(f"  {args.symbol}  {df['date'].min().date()} ~ {df['date'].max().date()}  "
          f"({len(df)} 根)  來源：{source}")
    print("=" * 118)
    print("\n【rolling vs anchored 對照】")
    print(format_comparison(cmp.comparison))
    print("\n【四方對照】")
    print(format_comparison(cmp.four_way))

    for name, res in (("rolling", cmp.rolling), ("anchored", cmp.anchored)):
        wdf = res.windows_df
        st = res.param_stability
        print(f"\n【{name}】窗口數={len(wdf)}  "
              f"樣本外 {res.oos_equity.index[0].date()} ~ {res.oos_equity.index[-1].date()}")
        print(f"  訓練窗長度：{wdf['train_bars'].min()} ~ {wdf['train_bars'].max()} 根")
        print(f"  參數穩定度：整體 {st['overall_stability']*100:.1f}%  → {st['verdict']}")
        for key, info in st["per_param"].items():
            print(f"    {key:<10} 眾數={info['mode']:<6} 眾數占比={info['mode_share']*100:>5.1f}%  "
                  f"唯一值={info['n_unique']:<3} 範圍=[{info['min']}, {info['max']}]  std={info['std']}")
        tr = float(wdf["train_metric"].mean()); te = float(wdf["test_sharpe"].mean())
        dec = (te - tr) / abs(tr) * 100 if tr else float("nan")
        print(f"  樣本外衰退：訓練平均 {tr:.3f} → 測試平均 {te:.3f}（{dec:+.1f}%）")

    print("\n【結論】")
    print(f"  {cmp.verdict['text']}")
    for d in cmp.verdict["detail"]:
        print(f"    - {d}")

    # ---------------- 4) 寫檔 ----------------
    od = args.outdir
    cmp.comparison.to_csv(os.path.join(od, f"{args.symbol}_wfo_compare.csv"), index=False)
    cmp.four_way.to_csv(os.path.join(od, f"{args.symbol}_wfo_fourway.csv"), index=False)
    cmp.rolling.windows_df.to_csv(os.path.join(od, f"{args.symbol}_wfo_rolling_windows.csv"), index=False)
    cmp.anchored.windows_df.to_csv(os.path.join(od, f"{args.symbol}_wfo_anchored_windows.csv"), index=False)
    cmp.rolling.oos_equity.rename("equity").to_csv(
        os.path.join(od, f"{args.symbol}_wfo_rolling_equity.csv"))
    cmp.anchored.oos_equity.rename("equity").to_csv(
        os.path.join(od, f"{args.symbol}_wfo_anchored_equity.csv"))

    summary = {
        "symbol": args.symbol, "interval": args.interval, "source": source,
        "source_attempts": [{"name": n, "status": s, "info": i} for n, s, i in attempts],
        "data_start": str(df["date"].min().date()), "data_end": str(df["date"].max().date()),
        "bars": int(len(df)),
        "wfo_config": {
            "train_bars": wf.train_bars, "test_bars": wf.test_bars,
            "step_bars": wf.resolved_step(), "metric": wf.metric, "min_trades": wf.min_trades,
            "n_combos": cmp.rolling.aggregate["n_combos"],
            "warmup_bars": cmp.rolling.aggregate["warmup_bars"],
        },
        "backtest_config": {
            "initial_capital": base_cfg.initial_capital, "fee_rate": base_cfg.fee_rate,
            "slippage_rate": base_cfg.slippage_rate,
            "funding_rate_daily": base_cfg.funding_rate_daily,
            "sizing_mode": base_cfg.sizing_mode, "fraction": base_cfg.fraction,
            "allow_short": base_cfg.allow_short, "use_trailing": base_cfg.use_trailing,
        },
        **cmp.to_summary(),
    }
    with open(os.path.join(od, f"{args.symbol}_wfo_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    # ---------------- 5) 圖表 ----------------
    oos_start = cmp.rolling.oos_equity.index[0]
    from crypto_quant.backtest import buy_and_hold  # noqa: E402
    bh = buy_and_hold(df, base_cfg)
    bh = bh[bh.index >= oos_start]
    if len(bh):
        bh = bh * (base_cfg.initial_capital / bh.iloc[0])
    single_df = df.iloc[wf.train_bars:].reset_index(drop=True)
    from crypto_quant.backtest import run_backtest  # noqa: E402
    single_eq = run_backtest(single_df, base_params, base_cfg).equity

    plot_wfo_mode_equity(cmp.rolling.oos_equity, cmp.anchored.oos_equity, bh, single_eq,
                         os.path.join(od, f"{args.symbol}_wfo_mode_equity.png"), has_cjk)
    plot_wfo_window_returns(cmp.rolling.windows_df, cmp.anchored.windows_df,
                            os.path.join(od, f"{args.symbol}_wfo_window_returns.png"), has_cjk)
    plot_wfo_param_compare(cmp.rolling.windows_df, cmp.anchored.windows_df,
                           os.path.join(od, f"{args.symbol}_wfo_params.png"), has_cjk)
    plot_wfo_train_test(cmp.rolling.windows_df, cmp.anchored.windows_df,
                        os.path.join(od, f"{args.symbol}_wfo_train_test.png"), has_cjk)
    plot_wfo_drawdown(cmp.rolling.oos_equity, cmp.anchored.oos_equity,
                      os.path.join(od, f"{args.symbol}_wfo_drawdown.png"), has_cjk)

    print(f"\n[out] 已寫入 {od}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
