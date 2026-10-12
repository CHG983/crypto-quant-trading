#!/usr/bin/env python3
"""即時訊號 CLI：抓即時資料 → 輸出訊號 JSON。

範例::

    # 單一標的（預設 1d）
    python run_live.py BTCUSDT

    # 多標的 + 小時線
    python run_live.py BTCUSDT ETHUSDT --interval 1h --out output

    # 只看已收盤 K 棒（不會 repaint）
    python run_live.py BTCUSDT --confirmed-only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crypto_quant.backtest import BacktestConfig
from crypto_quant.live import human_delay, server_time
from crypto_quant.signal_engine import analyze_live, strength_label
from crypto_quant.strategy import StrategyParams


def _fmt(x, nd=6, dash="—"):
    if x is None:
        return dash
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    return str(x)


def print_report(r: dict) -> None:
    c = r["confirmed"] or r["provisional"]
    src = "已收盤" if r["confirmed"] is not None else "進行中"
    print("=" * 78)
    print(f"  {r['symbol']}  [{r['interval']}]   資料來源 {r['source']}")
    print("=" * 78)
    print(f"  抓取時間 (UTC)      {r['fetched_at_utc']}")
    print(f"  幣安伺服器時間      {r['binance_server_time_utc']}"
          + (f"   （本地時鐘偏移 {r['clock_offset_seconds']:+.3f} 秒）"
             if r["clock_offset_seconds"] is not None else ""))
    print(f"  最後一根 K 棒       開盤 {r['bar_open_utc']}　收盤 {r['bar_close_utc']}")
    state = "進行中（未收盤）" if r["bar_is_in_progress"] else "已收盤"
    print(f"  該根狀態           {state}　進度 {r['bar_progress_pct']}%")
    print(f"  延遲               {r['data_delay_human']}　({r['data_delay_note']})")
    print()
    print(f"  ── 趨勢（以{src} K 棒為準）──")
    print(f"  方向               {c['trend_label']}")
    _px_tag = "進行中即時價" if r["bar_is_in_progress"] else "收盤價"
    print(f"  K 棒價格({_px_tag})  {_fmt(c['price'])}")
    print(f"  EMA 快線 / 慢線     {_fmt(c['ema_fast'])} / {_fmt(c['ema_slow'])}")
    _g = c["ema_spread_atr"]
    _g_str = "—" if _g is None else ("" if _g >= 0 else "-") + f"{abs(_g):.4f}"
    print(f"  EMA 差距           {_fmt(c['ema_gap_pct'], 4)}%"
          f"　= {_g_str} × ATR")
    print(f"  Supertrend         {_fmt(c['supertrend'])}　方向 "
          f"{'多' if c['st_dir'] == 1 else '空' if c['st_dir'] == -1 else '—'}")
    print()
    print(f"  ── 動能 ──")
    print(f"  RSI(14)            {_fmt(c['rsi'], 2)}")
    print(f"  MACD 柱            {_fmt(c['macd_hist'])}")
    print()
    print(f"  ── 波動度 ──")
    print(f"  ATR(14)            {_fmt(c['atr'])}")
    print(f"  ATR%               {_fmt(c['atr_pct'], 3)}%")
    print()
    st = c["strength"]
    print(f"  ── 訊號強度 ──")
    print(f"  總分               {st['total']:.1f} / 100   【{st['label']}】")
    print(f"    趨勢分量 (35%)    {st['trend']:.3f}")
    print(f"    動能分量 (30%)    {st['momentum']:.3f}")
    print(f"    波動分量 (20%)    {st['volatility']:.3f}")
    print(f"    ST 一致  (15%)    {st['supertrend']:.3f}")
    print()
    print(f"  ── 訊號 ──")
    ep = c["entry_passes"]
    if c["last_cross"]:
        lc = c["last_cross"]
        side = "多" if lc["type"] == "BULL_CROSS" else "空"
        ok = ep["long"] if lc["type"] == "BULL_CROSS" else ep["short"]
        print(f"  最新交叉           {lc['type']}（{lc['bars_ago']} 根前）")
        print(f"  該次交叉是否成立   {side}方進場過濾 "
              + ("通過 → 訊號成立" if ok else "未通過 → 該次交叉未產生交易"))
    else:
        print(f"  最新交叉           暖機期內無交叉")
    print(f"  當根過濾（多/空）   "
          f"{'通過' if c['long_entry_ok'] else '未通過'} / "
          f"{'通過' if c['short_entry_ok'] else '未通過'}"
          f"　（最新 K 棒當根狀態，非交叉當時）")
    if r["repaint"]:
        print(f"  ⚠ 進行中與確認版訊號不一致（{r['provisional']['signal_label']} "
              f"vs {r['confirmed']['signal_label']}）—— 未收盤，可能 repaint")
    if r["levels"]:
        lv = r["levels"]
        print()
        _lv_tag = ("最後一根未收盤，此為進行中即時價" if r["bar_is_in_progress"]
                   else "最後一根已收盤")
        print(f"  ── 建議價位（以 {_fmt(lv['entry_ref'])} 為假想進場點；{_lv_tag}）──")
        print(f"  停損               {_fmt(lv['stop'])}"
              f"　（距離 {lv['risk_pct']:.2f}% = {lv['stop_mult']}×ATR）")
        print(f"  停利               {_fmt(lv['take_profit'])}"
              f"　（距離 {lv['reward_pct']:.2f}% = {lv['tp_mult']}×ATR）")
        print(f"  風險報酬比         1 : {lv['rr_ratio']}")
    if r["position"]:
        p = r["position"]
        print()
        print(f"  ── 目前理論持倉（由近期歷史模擬推導）──")
        print(f"  方向               {p['side']}　進場 {p['entry_date']} @ {_fmt(p['entry_price'])}")
        print(f"  未實現損益         {p['unrealized_pnl']:,.2f} USDT"
              f"（{p['unrealized_pct']:+.2f}%）")
        print(f"  目前停損           {_fmt(p['stop_price'])}"
              + ("（已移動）" if p["stop_moved"] else "（初始）"))
        print(f"  持有               {p['bars_held']} 根")
    print("=" * 78)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="加密貨幣即時訊號引擎")
    ap.add_argument("symbols", nargs="*", default=["BTCUSDT"], help="交易對（預設 BTCUSDT）")
    ap.add_argument("--interval", default="1d", help="K 線週期（1m/5m/15m/1h/4h/1d）")
    ap.add_argument("--limit", type=int, default=400, help="抓取 K 線根數")
    ap.add_argument("--out", default="output", help="輸出目錄")
    ap.add_argument("--confirmed-only", action="store_true",
                    help="只用已收盤 K 棒（去掉進行中的最後一根）")
    ap.add_argument("--no-save", action="store_true", help="不寫檔，只印出")
    # 策略參數
    ap.add_argument("--ema-fast", type=int, default=21)
    ap.add_argument("--ema-slow", type=int, default=55)
    ap.add_argument("--atr-stop-mult", type=float, default=3.0)
    ap.add_argument("--atr-tp-mult", type=float, default=6.0)
    ap.add_argument("--min-atr-pct", type=float, default=0.5)
    ap.add_argument("--no-short", action="store_true", help="停用做空")
    args = ap.parse_args(argv)

    params = StrategyParams(
        ema_fast=args.ema_fast, ema_slow=args.ema_slow,
        atr_stop_mult=args.atr_stop_mult, atr_tp_mult=args.atr_tp_mult,
        min_atr_pct=args.min_atr_pct, allow_short=not args.no_short,
    )
    cfg = BacktestConfig(
        atr_stop_mult=args.atr_stop_mult, atr_tp_mult=args.atr_tp_mult,
        allow_short=not args.no_short,
    )

    syms = args.symbols or ["BTCUSDT"]
    out_dir = Path(args.out)
    if not args.no_save:
        out_dir.mkdir(parents=True, exist_ok=True)

    payload = {"symbols": [], "errors": []}
    for sym in syms:
        try:
            r = analyze_live(
                sym, interval=args.interval, limit=args.limit,
                params=params, cfg=cfg,
            )
            if args.confirmed_only and r["confirmed"] is None:
                payload["errors"].append({"symbol": sym, "error": "已收盤 K 棒不足"})
                print(f"[live] {sym} 已收盤 K 棒不足，略過")
                continue
            print_report(r)
            payload["symbols"].append(r)
        except Exception as exc:  # noqa: BLE001
            payload["errors"].append({"symbol": sym, "error": str(exc)[:300]})
            print(f"[live] {sym} 失敗：{exc}", file=sys.stderr)

    if not args.no_save:
        fp = out_dir / "live_signals.json"
        fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[live] 已寫入 {fp}")
    return 0 if payload["symbols"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
