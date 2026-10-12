#!/usr/bin/env python3
"""產生 Python 端基準資料，供前端 live_engine.js 做逐值等價性比對。

把真實幣安 K 線存成 CSV，並用 crypto_quant 的 Python 實作算出
每個指標序列與訊號，輸出成 baseline JSON。engine_test.mjs 會讀同一份
CSV，用 live_engine.js 重算，然後逐值比對。

用法：
    python3 make_baseline.py --symbol BTCUSDT --interval 1d --limit 400
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crypto_quant.indicators import add_indicators, ema, rsi, atr, macd, supertrend  # noqa: E402
from crypto_quant.live import fetch_klines, server_time  # noqa: E402
from crypto_quant.signal_engine import analyze_symbol  # noqa: E402
from crypto_quant.strategy import StrategyParams, generate_signals  # noqa: E402
from crypto_quant.backtest import BacktestConfig  # noqa: E402


def series_to_list(s: pd.Series, nd: int = 10) -> list:
    """轉成 JSON 可序列化的 list；NaN → null。"""
    out = []
    for v in s.to_numpy(dtype=float):
        out.append(None if not np.isfinite(v) else round(float(v), nd))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="產生 Python 基準（供 JS 引擎比對）")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--outdir", default="baseline")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # --- 抓真實資料 ---
    df = fetch_klines(args.symbol, interval=args.interval, limit=args.limit)
    try:
        st_ms = server_time()
    except Exception:  # noqa: BLE001
        st_ms = None

    # --- 存 CSV（JS 端讀同一份，確保輸入逐位元相同）---
    csv_path = outdir / f"{args.symbol}_{args.interval}_klines.csv"
    df.to_csv(csv_path, index=False)

    # --- Python 端指標與訊號 ---
    params = StrategyParams()
    sig = generate_signals(df, params)

    baseline = {
        "symbol": args.symbol,
        "interval": args.interval,
        "limit": args.limit,
        "rows": int(len(df)),
        "csv": csv_path.name,
        "server_time_ms": st_ms,
        "params": params.to_dict(),
        "series": {
            "close": series_to_list(sig["close"]),
            "high": series_to_list(sig["high"]),
            "low": series_to_list(sig["low"]),
            "ema_fast": series_to_list(sig["ema_fast"]),
            "ema_slow": series_to_list(sig["ema_slow"]),
            "rsi": series_to_list(sig["rsi"]),
            "macd": series_to_list(sig["macd"]),
            "macd_signal": series_to_list(sig["macd_signal"]),
            "macd_hist": series_to_list(sig["macd_hist"]),
            "atr": series_to_list(sig["atr"]),
            "atr_pct": series_to_list(sig["atr_pct"]),
            "supertrend": series_to_list(sig["supertrend"]),
            "st_dir": [int(v) for v in sig["st_dir"].to_numpy()],
        },
        "flags": {
            "bull_cross": [bool(v) for v in sig["bull_cross"].to_numpy()],
            "bear_cross": [bool(v) for v in sig["bear_cross"].to_numpy()],
            "long_entry_ok": [bool(v) for v in sig["long_entry_ok"].to_numpy()],
            "short_entry_ok": [bool(v) for v in sig["short_entry_ok"].to_numpy()],
            "signal": [int(v) for v in sig["signal"].to_numpy()],
        },
    }

    # --- 完整 analyze_symbol 輸出（趨勢/強度/關卡）供第二層比對 ---
    try:
        full = analyze_symbol(
            df, args.symbol, interval=args.interval, params=params,
            cfg=BacktestConfig(), source="binance:data-api.binance.vision",
            server_time_ms=st_ms,
        )
        for k in ("provisional", "confirmed"):
            if full.get(k):
                full[k].pop("_sig", None)
        baseline["analyze_symbol"] = full
    except Exception as exc:  # noqa: BLE001
        baseline["analyze_symbol_error"] = str(exc)

    out_path = outdir / f"{args.symbol}_{args.interval}_baseline.json"
    out_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    print(f"[baseline] {args.symbol} {args.interval} rows={len(df)} -> {out_path}")
    print(f"[baseline]   csv -> {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
