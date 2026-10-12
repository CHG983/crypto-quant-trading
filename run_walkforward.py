#!/usr/bin/env python3
"""Walk-forward 驗證主程式。

用法
----
    python3 run_walkforward.py --symbol BTCUSDT
    python3 run_walkforward.py --symbol ETHUSDT --mode anchored --train 900 --test 180
    python3 run_walkforward.py --symbol BTCUSDT --metric total_return --step 90

輸出（皆落在 --outdir，預設 ./output）
----
    {symbol}_wf_windows.csv        每個窗口的訓練/測試區間與選出的最佳參數
    {symbol}_wf_oos_equity.csv     樣本外串接權益曲線
    {symbol}_wf_trades.csv         樣本外交易明細
    {symbol}_wf_summary.json       完整結果（含參數穩定度、單次切分對照）
    {symbol}_wf_equity.png         OOS 權益 vs B&H vs 單次切分
    {symbol}_wf_window_returns.png 各窗口報酬分布
    {symbol}_wf_params.png         參數隨時間變化
    {symbol}_wf_train_test.png     訓練 vs 測試績效
    {symbol}_wf_drawdown.png       OOS 回撤
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402

from crypto_quant.backtest import BacktestConfig, buy_and_hold, run_backtest  # noqa: E402
from crypto_quant.data_fetch import fetch_ohlcv  # noqa: E402
from crypto_quant.metrics import format_summary, summarize  # noqa: E402
from crypto_quant.optimize import in_out_sample  # noqa: E402
from crypto_quant.plots import setup_cjk_font  # noqa: E402
from crypto_quant.strategy import StrategyParams  # noqa: E402
from crypto_quant.walkforward import (  # noqa: E402
    DEFAULT_GRID, WalkForwardConfig, run_walk_forward,
)
from crypto_quant.wf_plots import (  # noqa: E402
    plot_wf_drawdown, plot_wf_equity, plot_wf_param_evolution,
    plot_wf_train_vs_test, plot_wf_window_returns,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward validation for CTMA strategy")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--outdir", default="output")
    # --- walk-forward 切窗 ---
    ap.add_argument("--train", type=int, default=730, help="訓練窗長度（根）")
    ap.add_argument("--test", type=int, default=180, help="測試窗長度（根）")
    ap.add_argument("--step", type=int, default=None, help="每次前進根數（預設=test）")
    ap.add_argument("--mode", choices=["rolling", "anchored"], default="rolling")
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

    # ---------------- 2) Walk-forward ----------------
    wf = WalkForwardConfig(
        train_bars=args.train, test_bars=args.test, mode=args.mode,
        step_bars=args.step, metric=args.metric, min_trades=args.min_trades,
    )
    print(f"\n[wf] 模式={wf.mode}  訓練窗={wf.train_bars}  測試窗={wf.test_bars}  "
          f"前進={wf.resolved_step()}  目標={wf.metric}")
    print(f"[wf] 網格組合數={len(DEFAULT_GRID['ema_fast'])*len(DEFAULT_GRID['ema_slow'])*len(DEFAULT_GRID['atr_stop'])}"
          f"（已排除 ema_fast>=ema_slow）")

    wf_res = run_walk_forward(
        df, wf=wf, base_cfg=base_cfg, base_params=base_params,
        grid=DEFAULT_GRID, symbol=args.symbol, source=source,
    )

    # ---------------- 3) 對照組 ----------------
    # 3a) 買進持有（同一段樣本外期間，公平比較）
    oos_start = wf_res.oos_equity.index[0]
    bh_full = buy_and_hold(df, base_cfg)
    bh_oos = bh_full[bh_full.index >= oos_start]
    if len(bh_oos):
        bh_oos = bh_oos * (base_cfg.initial_capital / bh_oos.iloc[0])
    s_bh = summarize(bh_oos, [], "Buy & Hold (OOS period)")

    # 3b) 單次切分（沿用既有 in_out_sample，切點對齊 walk-forward 起點）
    cut = int(len(df) * 0.7)
    is_s, oos_s = in_out_sample(df, base_params, base_cfg, split=0.7)
    single_oos_df = df.iloc[cut:].reset_index(drop=True)
    single_res = run_backtest(single_oos_df, base_params, base_cfg)
    single_equity = single_res.equity

    # ---------------- 4) 輸出報表 ----------------
    print("\n" + "=" * 104)
    print(f"  {args.symbol}  {df['date'].min().date()} ~ {df['date'].max().date()}  "
          f"({len(df)} 根)  來源：{source}")
    print("=" * 104)
    print(format_summary(wf_res.aggregate))
    print(format_summary(s_bh))
    print(format_summary(oos_s))
    print("=" * 104)

    wdf = wf_res.windows_df
    print(f"\n[wf] 共 {len(wdf)} 個窗口，樣本外期間 "
          f"{wf_res.oos_equity.index[0].date()} ~ {wf_res.oos_equity.index[-1].date()}")
    print("\n[wf] 各窗口選出的最佳參數：")
    show_cols = ["window", "train_start", "train_end", "test_start", "test_end",
                 "ema_fast", "ema_slow", "atr_stop", "train_metric", "test_return",
                 "test_sharpe", "test_trades"]
    print(wdf[show_cols].to_string(index=False))

    st = wf_res.param_stability
    print("\n[wf] 參數穩定度分析：")
    for key, info in st["per_param"].items():
        print(f"       {key:<10} 眾數={info['mode']:<6} 眾數占比={info['mode_share']*100:>5.1f}%  "
              f"唯一值={info['n_unique']:<3} 範圍=[{info['min']}, {info['max']}]  std={info['std']}")
    print(f"       整體穩定度={st['overall_stability']*100:.1f}%  → {st['verdict']}")

    # 樣本外衰退
    tr_mean = float(wdf["train_metric"].mean())
    te_mean = float(wdf["test_sharpe"].mean())
    decay = (te_mean - tr_mean) / abs(tr_mean) * 100 if tr_mean else float("nan")
    print(f"\n[wf] 樣本外衰退：訓練平均 {tr_mean:.3f} → 測試平均 {te_mean:.3f}  "
          f"（變化 {decay:+.1f}%）")

    # ---------------- 5) 寫檔 ----------------
    wdf.to_csv(os.path.join(args.outdir, f"{args.symbol}_wf_windows.csv"), index=False)
    wf_res.oos_equity.rename("equity").to_csv(
        os.path.join(args.outdir, f"{args.symbol}_wf_oos_equity.csv"))
    trades_df = pd.DataFrame([t.to_row() for t in wf_res.all_oos_trades])
    trades_df.to_csv(os.path.join(args.outdir, f"{args.symbol}_wf_trades.csv"), index=False)

    summary = {
        "symbol": args.symbol,
        "interval": args.interval,
        "source": source,
        "source_attempts": [{"name": n, "status": s, "info": i} for n, s, i in attempts],
        "data_start": str(df["date"].min().date()),
        "data_end": str(df["date"].max().date()),
        "bars": int(len(df)),
        "walk_forward_config": {
            "train_bars": wf.train_bars, "test_bars": wf.test_bars,
            "step_bars": wf.resolved_step(), "mode": wf.mode, "metric": wf.metric,
            "min_trades": wf.min_trades, "warmup_bars": wf_res.aggregate["warmup_bars"],
            "n_combos": wf_res.aggregate["n_combos"],
        },
        "backtest_config": {
            "initial_capital": base_cfg.initial_capital, "fee_rate": base_cfg.fee_rate,
            "slippage_rate": base_cfg.slippage_rate,
            "funding_rate_daily": base_cfg.funding_rate_daily,
            "sizing_mode": base_cfg.sizing_mode, "fraction": base_cfg.fraction,
            "allow_short": base_cfg.allow_short, "use_trailing": base_cfg.use_trailing,
        },
        "walk_forward_oos": wf_res.aggregate,
        "buy_and_hold_oos_period": s_bh,
        "single_split": {"in_sample": is_s, "out_of_sample": oos_s, "split": 0.7},
        "param_stability": st,
        "oos_decay": {"train_mean_metric": tr_mean, "test_mean_sharpe": te_mean,
                      "pct_change": decay},
        "windows": wdf.to_dict(orient="records"),
        "exit_reasons": (
            trades_df["exit_reason"].value_counts().to_dict() if len(trades_df) else {}
        ),
    }

    # ---------------- 6) 圖表 ----------------
    plot_wf_equity(wf_res.oos_equity, bh_oos,
                   os.path.join(args.outdir, f"{args.symbol}_wf_equity.png"),
                   single_split=single_equity, has_cjk=has_cjk)
    plot_wf_window_returns(wdf, os.path.join(args.outdir, f"{args.symbol}_wf_window_returns.png"), has_cjk)
    plot_wf_param_evolution(wdf, os.path.join(args.outdir, f"{args.symbol}_wf_params.png"), has_cjk)
    plot_wf_train_vs_test(wdf, os.path.join(args.outdir, f"{args.symbol}_wf_train_test.png"), has_cjk)
    plot_wf_drawdown(wf_res.oos_equity, os.path.join(args.outdir, f"{args.symbol}_wf_drawdown.png"), has_cjk)

    with open(os.path.join(args.outdir, f"{args.symbol}_wf_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n[out] 已寫入 {args.outdir}/")
    print(f"      窗口數：{len(wdf)}  樣本外交易筆數：{len(trades_df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
