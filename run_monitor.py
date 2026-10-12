#!/usr/bin/env python3
"""自動化監控 CLI：定時執行、偵測狀態變化、記錄歷史、可選通知。

範例::

    # 執行一輪（自訂清單）
    python run_monitor.py --symbols BTCUSDT,ETHUSDT

    # 執行一輪（成交量前 20，小時線）
    python run_monitor.py --top 20 --interval 1h

    # 查詢某標的最近一次訊號
    python run_monitor.py --query BTCUSDT

    # 查詢最近 20 筆訊號事件
    python run_monitor.py --recent 20

    # 啟用 Telegram 通知（需自備憑證）
    python run_monitor.py --top 20 \\
        --telegram-token "$TG_TOKEN" --telegram-chat "$TG_CHAT"

排程請見 README.md。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from crypto_quant.backtest import BacktestConfig
from crypto_quant.monitor import (
    MonitorPaths,
    history_summary,
    last_signal_per_symbol,
    load_history,
    query_symbol_all,
    run_monitor,
)
from crypto_quant.strategy import StrategyParams


def cmd_query(out_dir: Path, symbol: str, limit: int) -> int:
    paths = MonitorPaths.from_dir(out_dir)
    recs = query_symbol_all(paths.history, symbol, limit=limit)
    if not recs:
        print(f"[monitor] {symbol} 尚無訊號記錄（{paths.history}）")
        return 1
    print(f"【{symbol}】訊號歷史（新到舊，共顯示 {len(recs)} 筆）")
    print("-" * 92)
    for r in recs:
        print(
            f"  偵測於 {r.get('detected_at_utc', '—')[:19]}  "
            f"{r.get('change_type', '—'):<12} "
            f"{r.get('from_trend')} → {r.get('to_trend')}  "
            f"分數 {r.get('strength')}  價格 {r.get('price')}"
        )
        print(f"      K 棒 {r.get('bar_open_utc', '—')[:16]} ~ "
              f"{r.get('bar_close_utc', '—')[:16]}"
              f"　週期 {r.get('interval')}")
    last = recs[0]
    print("-" * 92)
    print(f"  最近一次：{last.get('detected_at_utc', '—')[:19]}　"
          f"{last.get('change_type')}　→ {last.get('to_trend')}")
    return 0


def cmd_recent(out_dir: Path, limit: int) -> int:
    paths = MonitorPaths.from_dir(out_dir)
    recs = history_summary(paths.history, limit=limit)
    if not recs:
        print(f"[monitor] 尚無訊號記錄（{paths.history}）")
        return 1
    print(f"最近 {len(recs)} 筆訊號事件")
    print("-" * 92)
    for r in recs:
        print(f"  {r.get('detected_at_utc', '—')[:19]}  {r.get('symbol'):<12} "
              f"{r.get('change_type', '—'):<12} {r.get('from_trend')} → {r.get('to_trend')}  "
              f"分數 {r.get('strength')}")
    return 0


def cmd_status(out_dir: Path) -> int:
    paths = MonitorPaths.from_dir(out_dir)
    if not paths.state.exists():
        print(f"[monitor] 尚無狀態檔（{paths.state}）。請先執行一輪監控。")
        return 1
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    syms = state.get("symbols", {})
    print(f"上次執行：{state.get('last_run_utc', '—')}　"
          f"週期 {state.get('last_interval')}　清單來源 {state.get('list_source')}")
    print(f"追蹤標的 {len(syms)} 個")
    print("-" * 92)
    for sym, cur in sorted(syms.items(), key=lambda kv: -kv[1].get("strength", 0)):
        print(f"  {sym:<12} {cur.get('trend_label', '—'):<8} "
              f"分數 {cur.get('strength', 0):>5.1f}  "
              f"RSI 價格 {cur.get('price')}  ATR% {cur.get('atr_pct')}")
    return 0


def cmd_rebuild_snapshot(out_dir: Path) -> int:
    """依現有 state.json / signals_history.jsonl 重建網頁快照（不抓資料）。

    為什麼需要：歷史回補（backfill_history.py）會補進大量真實歷史事件，
    但 monitor_snapshot.json 是在回補**之前**產生的，時間軸會漏掉那些事件。
    本子命令讓快照與歷史保持一致，且**完全不連網**。
    """
    paths = MonitorPaths.from_dir(out_dir)
    if not paths.state.exists():
        print(f"[monitor] 找不到 {paths.state}，請先執行一輪監控。")
        return 1
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    recent = history_summary(paths.history, limit=40)
    total = len(load_history(paths.history))
    snap = {
        "run_at_utc": state.get("last_run_utc"),
        "last_run_utc": state.get("last_run_utc"),
        "list_source": state.get("list_source"),
        "interval": state.get("last_interval"),
        "scanned": state.get("symbol_count"),
        "failed": None,
        "event_count": None,
        "events": [],
        "tracked_symbols": state.get("symbols", {}),
        "symbol_count": len(state.get("symbols", {})),
        "notification": {
            "mode": "file_only",
            "file_written": True,
            "webhook": None,
            "telegram": None,
            "note": (
                "未提供 webhook 或 Telegram 憑證，通知僅寫入本機 state.json / "
                "signals_history.jsonl / monitor.log。**並未對外發送任何訊息。**"
            ),
        },
        "history_recent": recent,
        "last_signal_per_symbol": last_signal_per_symbol(paths.history),
        "history_total": total,
        "snapshot_mode": "rebuilt_from_history",
    }
    (out_dir / "monitor_snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[monitor] 已重建快照：追蹤 {snap['symbol_count']} 個標的，"
          f"歷史 {total} 筆，時間軸 {len(recent)} 筆")
    print(f"[monitor] {out_dir}/monitor_snapshot.json")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="加密貨幣自動化訊號監控")
    ap.add_argument("--symbols", default=None, help="逗號分隔；不給則依 --top 或內建清單")
    ap.add_argument("--top", type=int, default=None, help="以 24h 成交量取前 N 名")
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--out", default="monitor_output", help="狀態/歷史/log 目錄")
    ap.add_argument("--sleep", type=float, default=0.12)
    ap.add_argument("--query", metavar="SYMBOL", help="查詢該標的訊號歷史後結束")
    ap.add_argument("--query-limit", type=int, default=10)
    ap.add_argument("--recent", type=int, default=None, help="列出最近 N 筆訊號後結束")
    ap.add_argument("--status", action="store_true", help="列出上次執行的狀態後結束")
    ap.add_argument("--rebuild-snapshot", action="store_true",
                    help="不抓取資料，只依現有 state.json / signals_history.jsonl "
                         "重建 monitor_snapshot.json（歷史回補後使用）")
    ap.add_argument("--webhook-url", default=None)
    ap.add_argument("--telegram-token", default=None)
    ap.add_argument("--telegram-chat", default=None)
    ap.add_argument("--email-note", action="store_true",
                    help="只印出『無憑證時僅寫本機檔案』的說明")
    ap.add_argument("--ema-fast", type=int, default=21)
    ap.add_argument("--ema-slow", type=int, default=55)
    ap.add_argument("--atr-stop-mult", type=float, default=3.0)
    ap.add_argument("--min-atr-pct", type=float, default=0.5)
    ap.add_argument("--no-short", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)

    # --- 唯讀子命令 ---
    if args.rebuild_snapshot:
        return cmd_rebuild_snapshot(out_dir)
    if args.query:
        return cmd_query(out_dir, args.query.upper(), args.query_limit)
    if args.recent:
        return cmd_recent(out_dir, args.recent)
    if args.status:
        return cmd_status(out_dir)

    params = StrategyParams(
        ema_fast=args.ema_fast, ema_slow=args.ema_slow,
        atr_stop_mult=args.atr_stop_mult, min_atr_pct=args.min_atr_pct,
        allow_short=not args.no_short,
    )
    cfg = BacktestConfig(
        atr_stop_mult=args.atr_stop_mult, allow_short=not args.no_short,
    )

    syms = None
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    result = run_monitor(
        symbols=syms, interval=args.interval, limit=args.limit, top=args.top,
        out_dir=str(out_dir), params=params, cfg=cfg,
        webhook_url=args.webhook_url,
        telegram_token=args.telegram_token,
        telegram_chat=args.telegram_chat,
        sleep=args.sleep,
    )

    print()
    print(f"執行時間 (UTC)   {result['run_at_utc']}")
    print(f"清單來源         {result['list_source']}（{len(result['symbols'])} 個標的）")
    print(f"成功 / 失敗      {result['scanned']} / {result['failed']}")
    print(f"變化事件         {len(result['events'])}")
    for e in result["events"]:
        print(f"  • {e['symbol']:<12} {e['change_type']:<12} "
              f"{e['from_trend']} → {e['to_trend']}　分數 {e['strength']}　"
              f"價格 {e['price']}")
    n = result["notification"]
    print(f"通知模式         {n['mode']}")
    print(f"  {n['note']}")
    if n.get("webhook"):
        print(f"  webhook → {n['webhook']}")
    if n.get("telegram"):
        print(f"  telegram → {n['telegram']}")
    print(f"狀態檔           {result['paths']['state']}")
    print(f"訊號歷史         {result['paths']['history']}")
    print(f"執行 log         {result['paths']['log']}")

    # 同步輸出 dashboard 用的快照
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "monitor_run.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        paths = MonitorPaths.from_dir(out_dir)
        snap = {
            "run_at_utc": result["run_at_utc"],
            "last_run_utc": result["state"].get("last_run_utc"),
            "list_source": result["list_source"],
            "interval": result["interval"],
            "scanned": result["scanned"],
            "failed": result["failed"],
            "event_count": len(result["events"]),
            "events": result["events"],
            "tracked_symbols": result["state"].get("symbols", {}),
            "symbol_count": len(result["state"].get("symbols", {})),
            "notification": result["notification"],
            "history_recent": history_summary(paths.history, limit=25),
            "last_signal_per_symbol": last_signal_per_symbol(paths.history),
            "history_total": len(load_history(paths.history)),
        }
        (out_dir / "monitor_snapshot.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"網頁快照         {out_dir}/monitor_snapshot.json")
    except Exception as exc:  # noqa: BLE001
        print(f"[monitor] 快照輸出失敗：{exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
