#!/usr/bin/env python3
"""加密貨幣量化訊號產生 + 回測主程式。

用法
----
    python3 run_signals.py --symbol BTCUSDT --start 2019-01-01
    python3 run_signals.py --symbol ETHUSDT --no-trailing --atr-stop 3.0
    python3 run_signals.py --symbol BTCUSDT --optimize

輸出（皆落在 --outdir，預設 ./output）
----
    {symbol}_daily.csv          原始 OHLCV
    {symbol}_signals.csv        逐日訊號表（日期/價格/訊號/停損/部位建議）
    {symbol}_trades.csv         交易明細
    {symbol}_summary.json       績效統計（策略 vs 買進持有）
    {symbol}_equity.png         權益曲線
    {symbol}_price_trades.png   價格、均線與買賣點
    {symbol}_drawdown.png       回撤曲線
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
from crypto_quant.optimize import grid_search, in_out_sample  # noqa: E402
from crypto_quant.plots import (  # noqa: E402
    plot_drawdown, plot_equity, plot_optimization_heatmap, plot_price_trades, setup_cjk_font,
)
from crypto_quant.strategy import StrategyParams  # noqa: E402


def build_signal_table(sig: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """輸出訊號表：日期、價格、訊號類型、停損價、部位建議。

    訊號類型語意：
      BULL_CROSS_ENTRY  看多翻轉且動能過濾通過 → 建立多單
      BULL_CROSS_EXIT   看多翻轉但過濾未過 → 僅平空單，不開新倉
      BEAR_CROSS_ENTRY  看空翻轉且動能過濾通過 → 建立空單
      BEAR_CROSS_EXIT   看空翻轉但過濾未過 → 僅平多單，不開新倉
    """
    rows = []
    for _, r in sig.iterrows():
        code = int(r["signal"])
        if code == 0:
            continue
        price = float(r["close"])
        atr_v = float(r["atr"]) if pd.notna(r["atr"]) else float("nan")
        stop = float("nan")
        size_hint = ""
        if code == 1:
            entry_ok = bool(r["long_entry_ok"])
            kind = "BULL_CROSS_ENTRY" if entry_ok else "BULL_CROSS_EXIT"
            if entry_ok and pd.notna(atr_v):
                stop = price - cfg.atr_stop_mult * atr_v
                size_hint = f"風險 {cfg.risk_pct*100:.1f}% / 停損距離 {cfg.atr_stop_mult}×ATR"
            else:
                size_hint = "僅平空單（動能過濾未通過，不開新倉）"
        else:
            entry_ok = bool(r["short_entry_ok"])
            kind = "BEAR_CROSS_ENTRY" if entry_ok else "BEAR_CROSS_EXIT"
            if entry_ok and pd.notna(atr_v):
                stop = price + cfg.atr_stop_mult * atr_v
                size_hint = f"風險 {cfg.risk_pct*100:.1f}% / 停損距離 {cfg.atr_stop_mult}×ATR"
            else:
                size_hint = "僅平多單（動能過濾未通過，不開新倉）"
        rows.append({
            "date": pd.Timestamp(r["date"]).date(),
            "close": round(price, 4),
            "signal": kind,
            "atr": round(atr_v, 4) if pd.notna(atr_v) else None,
            "atr_pct": round(float(r["atr_pct"]), 3) if pd.notna(r["atr_pct"]) else None,
            "stop_price": round(stop, 4) if pd.notna(stop) else None,
            "rsi": round(float(r["rsi"]), 2) if pd.notna(r["rsi"]) else None,
            "macd_hist": round(float(r["macd_hist"]), 4) if pd.notna(r["macd_hist"]) else None,
            "position_hint": size_hint,
        })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Crypto quant signals + backtest")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--outdir", default="output")
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--fee", type=float, default=0.0004)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--funding", type=float, default=0.0001)
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--leverage", type=float, default=3.0)
    ap.add_argument("--sizing", choices=["fraction", "risk"], default="fraction",
                    help="fraction=固定名目比例（預設，與 B&H 公平比較）; risk=ATR 風險比例")
    ap.add_argument("--fraction", type=float, default=1.0,
                    help="sizing=fraction 時，名目部位佔權益比例（預設 1.0=全額）")
    ap.add_argument("--ema-fast", type=int, default=21)
    ap.add_argument("--ema-slow", type=int, default=55)
    ap.add_argument("--atr-len", type=int, default=14)
    ap.add_argument("--atr-stop", type=float, default=3.0)
    ap.add_argument("--atr-trail", type=float, default=5.0)
    ap.add_argument("--atr-tp", type=float, default=6.0)
    ap.add_argument("--min-atr-pct", type=float, default=0.5)
    ap.add_argument("--no-trailing", action="store_true")
    ap.add_argument("--take-profit", action="store_true")
    ap.add_argument("--no-short", action="store_true")
    ap.add_argument("--use-supertrend", action="store_true")
    ap.add_argument("--optimize", action="store_true")
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
    df.to_csv(os.path.join(args.outdir, f"{args.symbol}_daily.csv"), index=False)

    # ---------------- 2) 參數 ----------------
    params = StrategyParams(
        ema_fast=args.ema_fast, ema_slow=args.ema_slow,
        atr_len=args.atr_len, min_atr_pct=args.min_atr_pct,
        use_supertrend=args.use_supertrend,
    )
    cfg = BacktestConfig(
        initial_capital=args.capital, fee_rate=args.fee, slippage_rate=args.slippage,
        funding_rate_daily=args.funding, risk_pct=args.risk, max_leverage=args.leverage,
        sizing_mode=args.sizing, fraction=args.fraction,
        allow_short=not args.no_short, use_trailing=not args.no_trailing,
        use_take_profit=args.take_profit, atr_stop_mult=args.atr_stop,
        atr_trail_mult=args.atr_trail, atr_tp_mult=args.atr_tp,
    )

    # ---------------- 3) 回測 ----------------
    res = run_backtest(df, params, cfg, source=source, symbol=args.symbol, interval=args.interval)
    bh = buy_and_hold(df, cfg)

    s_strat = summarize(res.equity, res.trades, "Strategy")
    s_bh = summarize(bh, [], "Buy & Hold")

    print("\n" + "=" * 100)
    print(f"  {args.symbol}  {df['date'].min().date()} ~ {df['date'].max().date()}  "
          f"({len(df)} 根)  來源：{source}")
    print("=" * 100)
    print(format_summary(s_strat))
    print(format_summary(s_bh))
    print("=" * 100)

    # ---------------- 4) 輸出 ----------------
    sig_tbl = build_signal_table(res.signals, cfg)
    sig_tbl.to_csv(os.path.join(args.outdir, f"{args.symbol}_signals.csv"), index=False)
    res.trades_df.to_csv(os.path.join(args.outdir, f"{args.symbol}_trades.csv"), index=False)

    summary = {
        "symbol": args.symbol,
        "interval": args.interval,
        "source": source,
        "source_attempts": [{"name": n, "status": s, "info": i} for n, s, i in attempts],
        "data_start": str(df["date"].min().date()),
        "data_end": str(df["date"].max().date()),
        "bars": int(len(df)),
        "params": params.to_dict(),
        "config": {
            "initial_capital": cfg.initial_capital, "fee_rate": cfg.fee_rate,
            "slippage_rate": cfg.slippage_rate, "funding_rate_daily": cfg.funding_rate_daily,
            "risk_pct": cfg.risk_pct, "max_leverage": cfg.max_leverage,
            "sizing_mode": cfg.sizing_mode, "fraction": cfg.fraction,
            "allow_short": cfg.allow_short, "use_trailing": cfg.use_trailing,
            "use_take_profit": cfg.use_take_profit, "atr_stop_mult": cfg.atr_stop_mult,
            "atr_trail_mult": cfg.atr_trail_mult, "atr_tp_mult": cfg.atr_tp_mult,
        },
        "strategy": s_strat,
        "buy_and_hold": s_bh,
        "signal_counts": {
            "bull_cross": int(res.signals["bull_cross"].sum()),
            "bear_cross": int(res.signals["bear_cross"].sum()),
            "long_entry_ok": int(res.signals["long_entry_ok"].sum()),
            "short_entry_ok": int(res.signals["short_entry_ok"].sum()),
        },
        "exit_reasons": (
            res.trades_df["exit_reason"].value_counts().to_dict() if len(res.trades_df) else {}
        ),
    }

    # ---------------- 5) 最佳化（可選）----------------
    if args.optimize:
        print("\n[optimize] 網格搜尋中…")
        grid = grid_search(df, cfg=cfg)
        pivot = grid.pivot_table(index="ema_fast", columns="atr_mult", values="sharpe")
        plot_optimization_heatmap(pivot, os.path.join(args.outdir, f"{args.symbol}_optimize.png"), has_cjk)
        best = grid.sort_values("sharpe", ascending=False).head(5)
        print(best.to_string(index=False))
        summary["optimization_top5"] = best.to_dict(orient="records")
        is_s, oos_s = in_out_sample(df, params, cfg)
        print("\n[optimize] 樣本內/外（同一組參數）：")
        print(format_summary(is_s))
        print(format_summary(oos_s))
        summary["in_sample"] = is_s
        summary["out_of_sample"] = oos_s

    # ---------------- 6) 圖表 ----------------
    plot_equity(res.equity, bh, os.path.join(args.outdir, f"{args.symbol}_equity.png"), has_cjk)
    plot_price_trades(res.signals, res.trades, os.path.join(args.outdir, f"{args.symbol}_price_trades.png"), has_cjk)
    plot_drawdown(res.equity, os.path.join(args.outdir, f"{args.symbol}_drawdown.png"), has_cjk)

    with open(os.path.join(args.outdir, f"{args.symbol}_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n[out] 已寫入 {args.outdir}/")
    print(f"      訊號筆數：{len(sig_tbl)}  交易筆數：{len(res.trades)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
