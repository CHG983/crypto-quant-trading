#!/usr/bin/env python3
"""歷史訊號回補：掃描過去 N 根 K 棒，把真實發生過的狀態變化寫入訊號歷史。

為什麼需要
----------
監控只記錄「程式啟動之後」的變化。全新安裝時 ``signals_history.jsonl`` 是空的，
網頁時間軸就沒東西可看。本腳本用與即時監控**完全相同的判定邏輯**
（``monitor.detect_change``）重播歷史，把真實發生過的交叉與趨勢翻轉補進歷史。

⚠️ 這些事件的 ``detected_at_utc`` 是**實際 K 棒時間**，但 ``recorded_at_utc``
是**本次回補的執行時間**。它們是「事後回補的歷史事件」，**不是當時發出的
即時通知**。每筆都帶 ``replayed: true`` 與 ``source_kind: "backfilled_history"``。

冪等
----
以 ``(state_key, change_type, detected_at_utc)`` 去重，重複執行不會產生重複事件。

範例::

    python backfill_history.py --top 20 --interval 1d --lookback 120 --out output
    python backfill_history.py --symbols BTCUSDT,ETHUSDT --lookback 200 --out output
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from crypto_quant.live import fetch_klines
from crypto_quant.monitor import (
    MonitorPaths,
    append_history,
    last_signal_per_symbol,
    load_history,
    replay_transitions,
)
from crypto_quant.scanner import DEFAULT_SYMBOLS, top_symbols_by_volume
from crypto_quant.strategy import StrategyParams


def dedupe_key(rec: dict) -> tuple:
    return (
        rec.get("state_key") or f"{rec.get('symbol')}@{rec.get('interval')}",
        rec.get("change_type"),
        rec.get("detected_at_utc"),
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="歷史訊號回補")
    ap.add_argument("--symbols", default=None, help="逗號分隔；不給則依 --top 或內建清單")
    ap.add_argument("--top", type=int, default=None, help="以 24h 成交量取前 N 名")
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--limit", type=int, default=400, help="抓取 K 線根數")
    ap.add_argument("--lookback", type=int, default=120, help="回補回溯幾根 K 棒")
    ap.add_argument("--out", default="output")
    ap.add_argument("--sleep", type=float, default=0.12)
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    paths = MonitorPaths.from_dir(out_dir)

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        src = "custom"
    elif args.top:
        syms = top_symbols_by_volume(args.top)
        src = f"top{args.top}_by_quote_volume"
    else:
        syms = list(DEFAULT_SYMBOLS)
        src = "builtin_default"
    print(f"[backfill] 清單來源 {src}，{len(syms)} 個標的；週期 {args.interval}，"
          f"回溯 {args.lookback} 根")

    # 既有歷史（用來去重）
    existing = load_history(paths.history)
    seen = {dedupe_key(r) for r in existing}
    print(f"[backfill] 既有歷史 {len(existing)} 筆")

    params = StrategyParams()
    new_records: list = []
    per_symbol: list = []
    failed = 0

    for i, sym in enumerate(syms, 1):
        try:
            df = fetch_klines(sym, interval=args.interval, limit=args.limit)
            if len(df) < int(params.ema_slow) + 10:
                raise RuntimeError(f"K 線不足（{len(df)} 根）")
            evs = replay_transitions(
                df, sym, interval=args.interval, params=params,
                lookback_bars=args.lookback,
            )
            added = 0
            for e in evs:
                k = dedupe_key(e)
                if k in seen:
                    continue
                seen.add(k)
                new_records.append(e)
                added += 1
            per_symbol.append((sym, len(evs), added))
            print(f"[backfill {i:>3}/{len(syms)}] {sym:<12} 重播事件 {len(evs):>2}，"
                  f"新增 {added:>2}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[backfill {i:>3}/{len(syms)}] {sym:<12} 失敗：{str(exc)[:110]}")
        if args.sleep:
            time.sleep(args.sleep)

    # 依時間排序後寫入，讓 JSONL 時間軸好讀
    new_records.sort(key=lambda r: (r.get("detected_at_utc") or "", r.get("symbol") or ""))
    append_history(paths.history, new_records)

    total = len(load_history(paths.history))
    print()
    print(f"[backfill] 新增 {len(new_records)} 筆，歷史總計 {total} 筆"
          f"（失敗 {failed} 個標的）")
    print(f"[backfill] 歷史檔 {paths.history}")

    # 統計摘要（供回報）
    by_type: dict = {}
    for r in new_records:
        by_type[r["change_type"]] = by_type.get(r["change_type"], 0) + 1
    print(f"[backfill] 新增事件類型：{by_type}")

    latest = last_signal_per_symbol(paths.history)
    print(f"[backfill] 有訊號記錄的標的 {len(latest)} 個")
    for sym in list(latest)[:5]:
        r = latest[sym]
        print(f"    {sym:<12} 最近一次 {r.get('detected_at_utc', '')[:16]}  "
              f"{r.get('change_type')} → {r.get('to_trend')}")

    summary = {
        "list_source": src,
        "interval": args.interval,
        "lookback_bars": args.lookback,
        "symbols": syms,
        "new_records": len(new_records),
        "history_total": total,
        "failed": failed,
        "by_type": by_type,
        "per_symbol": [
            {"symbol": s, "replayed": a, "added": b} for s, a, b in per_symbol
        ],
    }
    (out_dir / "backfill_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[backfill] 摘要 {out_dir}/backfill_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
