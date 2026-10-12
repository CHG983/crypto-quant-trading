#!/usr/bin/env python3
"""市場掃描 CLI：掃描一組標的 → 輸出排行 CSV/JSON。

範例::

    # 掃描成交量前 25 大（USDT 計價）
    python run_scan.py --top 25

    # 自訂清單 + 小時線
    python run_scan.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --interval 1h

    # 內建預設清單
    python run_scan.py
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from crypto_quant.backtest import BacktestConfig
from crypto_quant.scanner import (
    DEFAULT_SYMBOLS,
    build_rankings,
    scan,
    summarize_text,
    top_symbols_by_volume,
)
from crypto_quant.strategy import StrategyParams

CSV_COLS = [
    "symbol", "trend", "signal", "strength", "strength_label", "price",
    "ema_gap_pct", "ema_spread_atr", "rsi", "macd_hist", "atr_pct",
    "near_trigger", "repaint", "stop", "take_profit", "rr_ratio", "position",
]


def write_csv(path: Path, rows: list) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="加密貨幣市場掃描")
    ap.add_argument("--symbols", default=None,
                    help="逗號分隔的交易對；不給則依 --top 或內建清單")
    ap.add_argument("--top", type=int, default=None,
                    help="以 24h USDT 成交量取前 N 名")
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--out", default="output")
    ap.add_argument("--sleep", type=float, default=0.12, help="每個標的間隔秒數")
    ap.add_argument("--min-quote-volume", type=float, default=5_000_000.0,
                    help="成交量門檻（USDT），低於此值剔除")
    ap.add_argument("--ema-fast", type=int, default=21)
    ap.add_argument("--ema-slow", type=int, default=55)
    ap.add_argument("--atr-stop-mult", type=float, default=3.0)
    ap.add_argument("--min-atr-pct", type=float, default=0.5)
    ap.add_argument("--no-short", action="store_true")
    args = ap.parse_args(argv)

    params = StrategyParams(
        ema_fast=args.ema_fast, ema_slow=args.ema_slow,
        atr_stop_mult=args.atr_stop_mult, min_atr_pct=args.min_atr_pct,
        allow_short=not args.no_short,
    )
    cfg = BacktestConfig(
        atr_stop_mult=args.atr_stop_mult, allow_short=not args.no_short,
    )

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        list_source = "custom"
    elif args.top:
        print(f"[scan] 取得 24h 成交量前 {args.top} 名…")
        syms = top_symbols_by_volume(args.top, min_quote_volume=args.min_quote_volume)
        list_source = f"top{args.top}_by_quote_volume"
    else:
        syms = list(DEFAULT_SYMBOLS)
        list_source = "builtin_default"
    print(f"[scan] 清單來源 {list_source}，共 {len(syms)} 個標的；週期 {args.interval}")

    res = scan(
        syms, interval=args.interval, limit=args.limit,
        params=params, cfg=cfg, sleep=args.sleep,
    )
    rankings = build_rankings(res)
    print()
    print(summarize_text(res, rankings))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"scan_{args.interval}_{list_source}"
    (out_dir / f"{tag}.json").write_text(
        json.dumps(
            {"list_source": list_source, "interval": args.interval,
             "scan": {k: v for k, v in res.items() if k != "results"},
             "rankings": rankings, "results": res["results"]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    write_csv(out_dir / f"{tag}.csv", rankings["all_rows"])
    print(f"\n[scan] 已寫入 {out_dir}/{tag}.json 與 .csv")
    if res["failures"]:
        print(f"[scan] {len(res['failures'])} 個標的失敗（詳見 JSON 的 failures）")
        for f in res["failures"][:5]:
            print(f"    {f['symbol']}: {f['error'][:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
