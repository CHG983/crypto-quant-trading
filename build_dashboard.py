#!/usr/bin/env python3
"""由實跑輸出的 JSON 產生可視化儀表板 HTML（資料全部內嵌，無外部請求）。

資料來源（全部為實際執行產物）
------------------------------
* ``output/live_signals.json``      —— `run_live.py`（即時訊號）
* ``output/scan_*.json``            —— `run_scan.py`（市場掃描排行）
* ``output/monitor_snapshot.json``  —— `run_monitor.py`（監控狀態與時間軸）

設計原則
--------
1. **資料內嵌**：``<script>const DATA = {...}</script>``，不依賴外部 JSON 請求，
   離線可開、不會因 CORS 或路徑問題空白。
2. **不捏造數字**：所有數值皆取自 JSON。缺少的欄位顯示「—」而非補 0。
3. **誠實標示**：即時 vs 快照 vs 回補歷史，以及「未對外發送通知」都寫在頁面上。
4. **響應式**：CSS Grid + media query，表格包在可橫向捲動容器內。
"""
from __future__ import annotations

import argparse
import glob
import html
import json
from pathlib import Path

LIVE_BAR = 24.0  # 強度條滿格對應的分數

CSS = """
:root{
  --bg:#0b1020; --panel:#131a2e; --panel2:#1a2238; --line:#2a3554;
  --tx:#e8ecf7; --dim:#93a0c2; --dim2:#6b7899;
  --long:#2ec27e; --short:#f66151; --warn:#e5a50a; --info:#62a0ea; --pur:#c061cb;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--tx);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",
    "PingFang TC","Microsoft JhengHei",sans-serif;
  font-size:15px;line-height:1.6;
}
.wrap{max-width:1180px;margin:0 auto;padding:28px 18px 64px}
header.top{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:26px}
h1{margin:0 0 8px;font-size:clamp(1.35rem,4.2vw,2rem);line-height:1.3;letter-spacing:.2px}
h2{margin:38px 0 14px;font-size:clamp(1.05rem,3vw,1.35rem);
   border-left:4px solid var(--info);padding-left:11px;line-height:1.35}
h3{margin:22px 0 10px;font-size:1.02rem;color:var(--tx)}
p{margin:9px 0;color:var(--dim)}
a{color:var(--info)}
code{background:#0a0f1e;border:1px solid var(--line);border-radius:5px;
     padding:1px 6px;font-size:.88em;word-break:break-word}
.meta{display:flex;flex-wrap:wrap;gap:8px 10px;margin-top:12px}
.chip{background:var(--panel2);border:1px solid var(--line);border-radius:999px;
      padding:4px 11px;font-size:.79rem;color:var(--dim);white-space:nowrap}
.chip b{color:var(--tx);font-weight:600}
.badge{display:inline-block;border-radius:6px;padding:2px 8px;font-size:.75rem;
       font-weight:700;letter-spacing:.3px}
.b-long{background:rgba(46,194,126,.16);color:var(--long);border:1px solid rgba(46,194,126,.4)}
.b-short{background:rgba(246,97,81,.16);color:var(--short);border:1px solid rgba(246,97,81,.4)}
.b-neut{background:rgba(147,160,194,.14);color:var(--dim);border:1px solid var(--line)}
.b-warn{background:rgba(229,165,10,.16);color:var(--warn);border:1px solid rgba(229,165,10,.4)}
.b-info{background:rgba(98,160,234,.16);color:var(--info);border:1px solid rgba(98,160,234,.4)}
.b-pur{background:rgba(192,97,203,.16);color:var(--pur);border:1px solid rgba(192,97,203,.4)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
      padding:16px;min-width:0}
.card .sym{font-size:1.18rem;font-weight:700;letter-spacing:.4px}
.card .row{display:flex;justify-content:space-between;gap:10px;padding:3px 0;
           font-size:.86rem;color:var(--dim)}
.card .row b{color:var(--tx);font-weight:600;text-align:right;word-break:break-word}
.hr{height:1px;background:var(--line);margin:11px 0}
.bar{height:7px;border-radius:4px;background:#0a0f1e;overflow:hidden;margin-top:7px}
.bar > i{display:block;height:100%;border-radius:4px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:11px;margin:14px 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 13px}
.kpi .k{font-size:.72rem;color:var(--dim2);text-transform:uppercase;letter-spacing:.7px}
.kpi .v{font-size:1.3rem;font-weight:700;margin-top:3px;line-height:1.2}
.kpi .s{font-size:.74rem;color:var(--dim2);margin-top:2px}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch;
           border:1px solid var(--line);border-radius:10px;background:var(--panel)}
table{border-collapse:collapse;width:100%;min-width:640px;font-size:.85rem}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);
      white-space:nowrap}
th{background:var(--panel2);color:var(--dim);font-weight:600;font-size:.76rem;
   text-transform:uppercase;letter-spacing:.5px;position:sticky;top:0}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:rgba(98,160,234,.05)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.sym{font-weight:600}
.tl{list-style:none;padding:0;margin:12px 0}
.tl li{position:relative;padding:0 0 16px 22px;border-left:1px solid var(--line);
       margin-left:6px}
.tl li:last-child{border-left-color:transparent;padding-bottom:0}
.tl li::before{content:"";position:absolute;left:-5px;top:6px;width:9px;height:9px;
               border-radius:50%;background:var(--info);border:2px solid var(--bg)}
.tl li.flip::before{background:var(--warn)}
.tl li.cross::before{background:var(--pur)}
.tl li.base::before{background:var(--dim2)}
.tl .t{font-size:.8rem;color:var(--dim2);font-variant-numeric:tabular-nums}
.tl .m{font-size:.9rem}
.note{background:var(--panel2);border:1px solid var(--line);border-left:4px solid var(--warn);
      border-radius:9px;padding:13px 15px;margin:16px 0;font-size:.87rem;color:var(--dim)}
.note.ok{border-left-color:var(--long)}
.note.info{border-left-color:var(--info)}
.note b{color:var(--tx)}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;
        padding:12px 15px;margin:14px 0}
summary{cursor:pointer;font-weight:600;font-size:.92rem}
details p{font-size:.87rem}
footer{margin-top:48px;padding-top:22px;border-top:1px solid var(--line);
       color:var(--dim2);font-size:.82rem}
.disc{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--warn);
      border-radius:10px;padding:16px 18px;margin-top:22px;font-size:.86rem;color:var(--dim)}
.disc b{color:var(--tx)}
@media (max-width:520px){
  .wrap{padding:20px 13px 52px}
  body{font-size:14.5px}
  .cards{grid-template-columns:1fr}
  .kpis{grid-template-columns:repeat(2,1fr)}
  table{min-width:560px;font-size:.8rem}
  th,td{padding:7px 8px}
}
"""


def esc(x) -> str:
    return html.escape(str(x))


def fmt(x, nd=4, dash="—"):
    if x is None or x == "":
        return dash
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, (int, float)):
        try:
            return f"{x:,.{nd}f}"
        except (ValueError, TypeError):
            return str(x)
    return str(x)


def trend_badge(t: str) -> str:
    cls = {"LONG": "b-long", "SHORT": "b-short"}.get(t, "b-neut")
    label = {"LONG": "多頭", "SHORT": "空頭", "NEUTRAL": "中性"}.get(t, t or "—")
    return f'<span class="badge {cls}">{esc(label)}</span>'


def strength_color(s: float) -> str:
    if s is None:
        return "var(--dim2)"
    if s >= 75:
        return "var(--long)"
    if s >= 60:
        return "#7fd6a8"
    if s >= 45:
        return "var(--warn)"
    if s >= 30:
        return "#e08a5a"
    return "var(--short)"


def build_live_panel(live: dict) -> str:
    syms = live.get("symbols") or []
    if not syms:
        return '<p>尚無即時訊號資料（請先執行 <code>python run_live.py</code>）。</p>'
    cards = []
    for r in syms:
        c = r.get("confirmed") or r.get("provisional") or {}
        st = c.get("strength") or {}
        lv = r.get("levels") or {}
        score = st.get("total")
        col = strength_color(score)
        pct = 0.0 if score is None else max(0.0, min(100.0, score / LIVE_BAR * 100.0))
        prog = "未收盤" if r.get("bar_is_in_progress") else "已收盤"
        prog_cls = "b-warn" if r.get("bar_is_in_progress") else "b-info"
        repaint = ""
        if r.get("repaint"):
            repaint = ('<div class="note" style="margin:10px 0 0;font-size:.8rem">'
                       '⚠ 進行中與確認版訊號不一致，可能 repaint</div>')
        pos = r.get("position")
        pos_html = ""
        if pos:
            pos_html = (
                f'<div class="hr"></div>'
                f'<div class="row"><span>理論持倉</span>'
                f'<b>{esc(pos.get("side"))} @ {fmt(pos.get("entry_price"))}</b></div>'
                f'<div class="row"><span>未實現損益</span>'
                f'<b>{fmt(pos.get("unrealized_pnl"), 2)} '
                f'({fmt(pos.get("unrealized_pct"), 2)}%)</b></div>'
            )
        cards.append(f"""
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
          <span class="sym">{esc(r.get('symbol'))}</span>
          {trend_badge(c.get('trend_label'))}
        </div>
        <div style="display:flex;gap:7px;margin-top:8px;flex-wrap:wrap">
          <span class="badge {prog_cls}">K 棒 {prog}</span>
          <span class="badge b-neut">{esc(r.get('interval'))}</span>
          <span class="badge b-pur">{esc(c.get('signal_label') or '—')}</span>
        </div>
        <div class="hr"></div>
        <div class="row"><span>價格</span><b>{fmt(c.get('price'), 6)}</b></div>
        <div class="row"><span>EMA 快 / 慢</span>
          <b>{fmt(c.get('ema_fast'), 4)} / {fmt(c.get('ema_slow'), 4)}</b></div>
        <div class="row"><span>EMA 差距</span>
          <b>{fmt(c.get('ema_gap_pct'), 3)}%</b></div>
        <div class="row"><span>RSI(14)</span><b>{fmt(c.get('rsi'), 1)}</b></div>
        <div class="row"><span>ATR%</span><b>{fmt(c.get('atr_pct'), 2)}%</b></div>
        <div class="row"><span>Supertrend</span>
          <b>{'多' if c.get('st_dir') == 1 else ('空' if c.get('st_dir') == -1 else '—')}</b></div>
        <div class="hr"></div>
        <div class="row"><span>訊號強度</span>
          <b style="color:{col}">{fmt(score, 1)} · {esc(st.get('label') or '—')}</b></div>
        <div class="bar"><i style="width:{pct:.1f}%;background:{col}"></i></div>
        <div class="row" style="margin-top:8px;font-size:.76rem;color:var(--dim2)">
          <span>趨勢 {fmt(st.get('trend'), 2)}・動能 {fmt(st.get('momentum'), 2)}・
                波動 {fmt(st.get('volatility'), 2)}・ST {fmt(st.get('supertrend'), 2)}</span>
        </div>
        <div class="hr"></div>
        <div class="row"><span>建議停損</span><b>{fmt(lv.get('stop'), 6)}
          <span style="color:var(--dim2)">({fmt(lv.get('risk_pct'), 2)}%)</span></b></div>
        <div class="row"><span>建議停利</span><b>{fmt(lv.get('take_profit'), 6)}
          <span style="color:var(--dim2)">({fmt(lv.get('reward_pct'), 2)}%)</span></b></div>
        <div class="row"><span>風險報酬比</span><b>1 : {fmt(lv.get('rr_ratio'), 2)}</b></div>
        {pos_html}
        {repaint}
        <div class="hr"></div>
        <div class="row" style="font-size:.76rem;color:var(--dim2)">
          <span>K 棒收盤 (UTC)</span><b>{esc((r.get('bar_close_utc') or '—')[:19])}</b></div>
        <div class="row" style="font-size:.76rem;color:var(--dim2)">
          <span>資料延遲</span><b>{esc(r.get('data_delay_human') or '—')}</b></div>
      </div>""")
    return '<div class="cards">' + "".join(cards) + "</div>"


def build_scan(scan: dict) -> str:
    rk = scan.get("rankings") or {}
    rows = rk.get("all_rows") or []
    if not rows:
        return '<p>尚無掃描資料（請先執行 <code>python run_scan.py</code>）。</p>'

    meta = scan.get("scan") or {}
    kpis = [
        ("掃描標的", rk.get("total_scanned", 0), f"成功 {meta.get('ok', 0)}／失敗 {meta.get('failed', 0)}"),
        ("多頭排列", rk.get("long_count", 0), "EMA 快線 &gt; 慢線"),
        ("空頭排列", rk.get("short_count", 0), "EMA 快線 &lt; 慢線"),
        ("接近觸發", rk.get("near_trigger_count", 0),
         "EMA 差距 &lt; 0.5%"),
        ("本次交叉訊號", rk.get("fresh_signal_count", 0), "非 HOLD"),
        ("略過低波動", meta.get("skipped_low_vol", 0),
         f"ATR% &lt; {fmt(meta.get('min_scan_atr_pct'), 2)}"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="k">{esc(a)}</div>'
        f'<div class="v">{esc(str(b))}</div><div class="s">{c}</div></div>'
        for a, b, c in kpis
    )

    def table(items: list, title: str, note: str = "") -> str:
        if not items:
            return f"<h3>{esc(title)}</h3><p>無符合條件的標的。</p>"
        body = []
        for r in items:
            s = r.get("strength")
            body.append(
                f'<tr><td class="sym">{esc(r.get("symbol"))}</td>'
                f'<td>{trend_badge(r.get("trend"))}</td>'
                f'<td class="num" style="color:{strength_color(s)}">'
                f'{fmt(s, 1)} {esc(r.get("strength_label") or "")}</td>'
                f'<td class="num">{fmt(r.get("price"), 6)}</td>'
                f'<td class="num">{fmt(r.get("ema_gap_pct"), 3)}%</td>'
                f'<td class="num">{fmt(r.get("ema_spread_atr"), 3)}</td>'
                f'<td class="num">{fmt(r.get("rsi"), 1)}</td>'
                f'<td class="num">{fmt(r.get("macd_hist"), 5)}</td>'
                f'<td class="num">{fmt(r.get("atr_pct"), 2)}%</td>'
                f'<td class="num">{fmt(r.get("stop"), 6)}</td>'
                f'<td>{esc(r.get("signal"))}</td>'
                f'<td>{"是" if r.get("near_trigger") else "—"}</td></tr>'
            )
        return (
            f"<h3>{esc(title)}</h3>"
            + (f'<p style="font-size:.84rem">{note}</p>' if note else "")
            + '<div class="tablewrap"><table><thead><tr>'
            '<th>標的</th><th>趨勢</th><th>強度</th><th>價格</th><th>EMA 差距</th>'
            '<th>ATR 倍數</th><th>RSI</th><th>MACD 柱</th><th>ATR%</th>'
            '<th>建議停損</th><th>訊號</th><th>接近觸發</th>'
            "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
        )

    parts = [
        f'<div class="kpis">{kpi_html}</div>',
        table(rk.get("top_strength") or [], "綜合強度排行",
              "依加權分數（趨勢 35%／動能 30%／波動 20%／Supertrend 15%）排序。"),
        table(rk.get("top_long") or [], "多頭排列標的"),
        table(rk.get("top_short") or [], "空頭排列標的"),
    ]
    if rk.get("top_near"):
        parts.append(
            table(rk["top_near"], "接近觸發",
                  "EMA 快慢線差距最小者，代表最接近交叉的標的。")
        )
    return "".join(parts)


def build_monitor(snap: dict) -> str:
    if not snap:
        return '<p>尚無監控資料（請先執行 <code>python run_monitor.py</code>）。</p>'
    notif = snap.get("notification") or {}
    tracked = snap.get("tracked_symbols") or {}
    timeline = snap.get("history_recent") or []

    mode = notif.get("mode", "—")
    mode_cls = "b-warn" if mode == "file_only" else "b-long"
    kpis = [
        ("追蹤標的", snap.get("symbol_count", 0), f"清單：{snap.get('list_source') or '—'}"),
        ("週期", snap.get("interval") or "—", "監控最近一次執行"),
        ("歷史事件總數", snap.get("history_total", 0), "signals_history.jsonl"),
        ("時間軸顯示", len(timeline), "最近事件（新到舊）"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="k">{esc(a)}</div>'
        f'<div class="v">{esc(str(b))}</div><div class="s">{esc(c)}</div></div>'
        for a, b, c in kpis
    )

    items = []
    for e in timeline:
        ct = e.get("change_type", "")
        cls = {"TREND_FLIP": "flip", "NEW_CROSS": "cross", "BASELINE": "base"}.get(ct, "")
        label = {"TREND_FLIP": "趨勢翻轉", "NEW_CROSS": "新交叉訊號",
                 "APPROACHING": "接近觸發", "BASELINE": "基準建立"}.get(ct, ct)
        replayed = ('<span class="badge b-pur">回補</span>'
                    if e.get("replayed") else "")
        frm = e.get("from_trend") or "—"
        to = e.get("to_trend") or "—"
        strength = e.get("strength")
        items.append(f"""
        <li class="{cls}">
          <div class="t">{esc((e.get('detected_at_utc') or '')[:19])} UTC
            &nbsp;·&nbsp; {esc(e.get('symbol'))}
            &nbsp;·&nbsp; <span class="badge b-neut">{esc(e.get('interval'))}</span>
            {replayed}</div>
          <div class="m">{esc(label)}：{esc(frm)} → {esc(to)}
            &nbsp;|&nbsp; 分數 {fmt(strength, 1)}
            &nbsp;|&nbsp; 價格 {fmt(e.get('price'), 6)}
            &nbsp;|&nbsp; ATR% {fmt(e.get('atr_pct'), 2)}</div>
        </li>""")
    tl = ('<ul class="tl">' + "".join(items) + "</ul>") if items \
        else '<p>尚無事件記錄。</p>'

    return f"""
    <div class="kpis">{kpi_html}</div>
    <div class="note info"><b>通知模式：</b>
      <span class="badge {mode_cls}">{esc(mode)}</span>
      &nbsp;{esc(notif.get('note') or '')}</div>
    <div class="row" style="font-size:.82rem;color:var(--dim2);margin:-6px 0 0">
      上次執行 (UTC)：{esc((snap.get('last_run_utc') or '—')[:19])}
      &nbsp;·&nbsp; 快照模式：{esc(snap.get('snapshot_mode') or 'live_run')}</div>
    <h3>最近訊號時間軸</h3>
    {tl}
    """


def build_table_tracked(snap: dict) -> str:
    tracked = snap.get("tracked_symbols") or {}
    if not tracked:
        return ""
    rows = sorted(tracked.items(), key=lambda kv: -(kv[1].get("strength") or 0))
    body = []
    for key, v in rows:
        s = v.get("strength")
        body.append(
            f'<tr><td class="sym">{esc(key)}</td>'
            f'<td>{trend_badge(v.get("trend_label"))}</td>'
            f'<td class="num" style="color:{strength_color(s)}">{fmt(s, 1)}</td>'
            f'<td class="num">{fmt(v.get("price"), 6)}</td>'
            f'<td class="num">{fmt(v.get("atr_pct"), 3)}%</td>'
            f'<td>{esc(v.get("signal_label"))}</td>'
            f'<td>{esc((v.get("seen_at_utc") or "")[:19])}</td></tr>'
        )
    return (
        "<h3>監控中標的的當前狀態</h3>"
        '<div class="tablewrap"><table><thead><tr>'
        "<th>標的（含週期）</th><th>趨勢</th><th>強度</th><th>價格</th>"
        "<th>ATR%</th><th>訊號</th><th>最後更新 (UTC)</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="產生儀表板 HTML")
    ap.add_argument("--data", default="output", help="輸出資料目錄")
    ap.add_argument("--out", default="../webpages/crypto-quant-report_v5/dashboard.html")
    args = ap.parse_args()

    data_dir = Path(args.data)
    live = {}
    lp = data_dir / "live_signals.json"
    if lp.exists():
        live = json.loads(lp.read_text(encoding="utf-8"))

    scan_files = sorted(glob.glob(str(data_dir / "scan_*.json")))
    scan = {}
    if scan_files:
        scan = json.loads(Path(scan_files[-1]).read_text(encoding="utf-8"))

    snap = {}
    sp = data_dir / "monitor_snapshot.json"
    if sp.exists():
        snap = json.loads(sp.read_text(encoding="utf-8"))

    # 內嵌用的精簡資料（保留所有呈現欄位，去掉巨型原始 results）
    embed = {
        "generated_at_utc": __import__("pandas").Timestamp.utcnow().tz_localize(None).isoformat(),
        "source": {
            "live": "Binance /api/v3/klines（公開端點，免金鑰）",
            "endpoint": "https://data-api.binance.vision",
            "scan_source": scan.get("list_source"),
            "scan_interval": scan.get("interval"),
        },
        "live": live,
        "scan": {
            "list_source": scan.get("list_source"),
            "interval": scan.get("interval"),
            "scan": {k: v for k, v in (scan.get("scan") or {}).items()
                     if k not in ("results",)},
            "rankings": scan.get("rankings") or {},
        },
        "monitor": snap,
    }

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>加密貨幣量化交易 · 即時儀表板</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <h1>加密貨幣量化交易 · 即時儀表板</h1>
  <p>即時訊號 · 市場掃描 · 自動化監控 — 由實際執行 <code>run_live.py</code> /
     <code>run_scan.py</code> / <code>run_monitor.py</code> 的輸出直接產生，資料內嵌於本頁。</p>
  <div class="meta">
    <span class="chip">資料來源 <b>Binance 公開 API</b></span>
    <span class="chip">端點 <b>data-api.binance.vision</b></span>
    <span class="chip">免金鑰 <b>是</b></span>
    <span class="chip">頁面產生於 <b>{esc((embed['generated_at_utc'] or '')[:19])} UTC</b></span>
  </div>
</header>

<div class="note">
  <b>資料性質揭露（請務必先讀）：</b>
  <ul style="margin:8px 0 0;padding-left:22px">
    <li><b>即時訊號</b>：子章節 1 的價格與指標為 <b>實際抓取</b> 幣安公開端點的即時／近即時資料；
        最後一根 K 棒若未收盤則標示「K 棒 未收盤」。</li>
    <li><b>市場掃描</b>：為<b>特定期間的一次快照</b>（見各表上方資訊），非持續更新的即時值。</li>
    <li><b>監控與時間軸</b>：狀態為最後一次執行結果；時間軸中標示
        <span class="badge b-pur">回補</span> 者為<b>由歷史 K 線回補產生</b>的事件
        （事件時間為真實 K 棒時間，但並非當時即時偵測）。</li>
    <li><b>通知</b>：本專案<b>未提供</b> webhook / Telegram 憑證，因此通知僅寫入本機檔案，
        <b>並未對外發送任何訊息</b>。</li>
  </ul>
</div>

<h2>1 · 即時訊號面板</h2>
{build_live_panel(live)}

<h2>2 · 市場掃描排行</h2>
{build_scan(scan)}

<h2>3 · 自動化監控狀態與訊號時間軸</h2>
{build_monitor(snap)}
{build_table_tracked(snap)}

<h2>4 · 方法與參數</h2>
<details open>
<summary>訊號強度計分（0–100）</summary>
<p><code>分數 = 100 × ( 0.35×趨勢 + 0.30×動能 + 0.20×波動健康度 + 0.15×Supertrend 一致性 )</code></p>
<p>趨勢以 ATR 正規化（<code>(EMA快−EMA慢)/ATR / 1.5</code>），使不同波動度的幣種可互相比較；
動能取 RSI 距中軸與 MACD 柱的平均，方向須與趨勢一致；波動健康度在 ATR% 落於
<code>min_atr_pct</code>～8% 時最高分，過低視為盤整、過高視為狂野。</p>
<p><b>這是啟發式排序分數，不是機率，也未經校準。</b>用途僅在於把「哪些標的訊號較乾淨」
排出先後，不可當成勝率估計。</p>
</details>
<details>
<summary>策略邏輯（CTMA）</summary>
<p>趨勢：EMA 21/55 交叉；動能：RSI(14) 站中軸同側 + MACD 柱方向一致；
波動度：ATR(14) 用於停損距離與 ATR% 過濾；風控：3×ATR 初始停損、5×ATR 移動停損、選用停利。</p>
<p>做多進場：EMA 快線上穿慢線 且 RSI&gt;50 且 MACD 柱&gt;0 且 ATR%≥門檻；
做空進場：EMA 快線下穿慢線 且 RSI&lt;50 且 MACD 柱&lt;0 且 ATR%≥門檻；
出場：反向交叉 或 ATR 停損／移動停損／停利。</p>
</details>
<details>
<summary>監控變化判定與排程</summary>
<p>變化判定優先序：<code>TREND_FLIP</code>（趨勢翻轉）&gt; <code>NEW_CROSS</code>（新交叉訊號）
&gt; <code>APPROACHING</code>（接近觸發）。分數升降不觸發，避免噪音洗板。
首次執行只建立 <code>BASELINE</code>。狀態鍵含週期（<code>BTCUSDT@1d</code> vs <code>BTCUSDT@1h</code>），
避免切換週期時產生偽變化事件。</p>
<p>排程範例（cron）：<code>0 * * * * cd /path &amp;&amp; python run_monitor.py --top 20 --interval 1h --out output</code></p>
</details>

<div class="disc">
  <b>⚠ 投資免責聲明</b>
  <p>本頁所有內容（含即時訊號、掃描排行、監控事件與參數）僅供<b>研究與教育用途</b>，
  <b>不構成任何投資建議、要約或推薦</b>。訊號基於歷史價格計算，<b>無法預測未來走勢</b>；
  歷史績效不代表未來表現。回測與訊號系統存在過度擬合、存活者偏差、交易成本估計誤差、
  流動性假設等固有限制，實際交易結果可能與之顯著不同。加密貨幣波動極大，
  可能導致<b>全部本金損失</b>。任何交易決策應由使用者自行判斷並承擔風險。</p>
  <p style="margin-bottom:0">As an AI Agent, I cannot provide legally protected, personalized
  investment advice. This analysis is for informational purposes only and does not constitute
  investment advice or recommendations. Consult a licensed financial advisor before making
  investment decisions.</p>
</div>

<footer>
  資料來源：Binance 公開 API（<code>data-api.binance.vision</code>，免金鑰）。
  頁面由 <code>build_dashboard.py</code> 依實跑輸出產生，所有數字皆為實際執行結果，未經人工調整。
</footer>

</div>
<script>
const DATA = {json.dumps(embed, ensure_ascii=False)};
</script>
</body>
</html>
"""

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    kb = len(html_doc.encode("utf-8")) / 1024
    print(f"[dashboard] 已產生 {out_path}  ({kb:.0f} KB)")
    print(f"[dashboard] 即時訊號 {len((live.get('symbols') or []))} 檔、"
          f"掃描 {((scan.get('rankings') or {}).get('total_scanned') or 0)} 檔、"
          f"時間軸 {len((snap.get('history_recent') or []))} 筆、"
          f"歷史 {snap.get('history_total', 0)} 筆")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
