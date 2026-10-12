#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_dashboard.py — 以真實 Chromium（Playwright）在桌面 1280 與手機 390
量測儀表板的實際版面，並輸出截圖。

檢查項目
--------
1. 頁面層級橫向溢出（documentElement.scrollWidth - innerWidth）
2. 圖片解碼狀態（naturalWidth > 0）
3. 右緣超出視窗的元素（排除位於可橫向捲動/裁切容器內者 —— 那是設計允許的）
4. 文字重疊（同層兄弟中同時水平與垂直交集、且兩者皆有直接文字者）
5. 關鍵內容存在性（各章節、免責聲明、資料來源揭露）
6. 內嵌資料非空（即時訊號／掃描／時間軸筆數 > 0）

用法::

    python verify_dashboard.py <page_url> --outdir verify_dashboard
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

MEASURE_JS = r"""
(() => {
  const W = window.innerWidth, H = window.innerHeight;
  const de = document.documentElement;
  const pageOverflow = Math.max(0, de.scrollWidth - W);

  const imgs = [...document.images].map(i => ({
    src: (i.getAttribute('src')||'').slice(-60),
    complete: i.complete, nw: i.naturalWidth,
    broken: i.complete && i.naturalWidth === 0
  }));

  // 排除位於可橫向捲動 / 裁切容器內的元素
  const inScrollable = el => {
    let p = el.parentElement;
    while (p && p !== document.body) {
      const cs = getComputedStyle(p);
      const ox = cs.overflowX;
      if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true;
      p = p.parentElement;
    }
    return false;
  };

  const offenders = [];
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') return;
    if (r.right > W + 1 && !inScrollable(el)) {
      offenders.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className && typeof el.className === 'string') ? el.className.slice(0,60) : '',
        right: Math.round(r.right), width: Math.round(r.width),
        text: (el.textContent||'').trim().replace(/\s+/g,' ').slice(0,50)
      });
    }
  });

  const overlaps = [];
  const rectOf = el => { const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)]; };
  document.querySelectorAll('*').forEach(parent => {
    const kids = [...parent.children].filter(k => {
      const r = k.getBoundingClientRect();
      const cs = getComputedStyle(k);
      // 排除純行內元素（display:inline，例如 <b>/<strong>/<code>）：
      // 行內元素的 getBoundingClientRect 是「所有換行片段的聯集」，同一段文字
      // 換行時兩個相鄰行內標籤的邊界框必然互相重疊。這是規範定義的行為，
      // 不是版面缺陷。真正會造成文字疊字的排版缺陷必為 block / inline-block /
      // flex / grid 等原子盒，因此只檢查這些。
      if (cs.display === 'inline') return false;
      return r.width > 2 && r.height > 2 && cs.visibility !== 'hidden' &&
             cs.display !== 'none' && cs.position !== 'absolute' && cs.position !== 'fixed';
    });
    for (let i=0;i<kids.length;i++) for (let j=i+1;j<kids.length;j++) {
      const a = kids[i].getBoundingClientRect(), b = kids[j].getBoundingClientRect();
      const ox = Math.min(a.right,b.right) - Math.max(a.left,b.left);
      const oy = Math.min(a.bottom,b.bottom) - Math.max(a.top,b.top);
      if (ox > 2 && oy > 2) {
        overlaps.push({
          parent: parent.tagName.toLowerCase()+'.'+String(parent.className||'').slice(0,30),
          a: kids[i].outerHTML.slice(0,70), b: kids[j].outerHTML.slice(0,70),
          aRect: rectOf(kids[i]), bRect: rectOf(kids[j]),
          overlap: [Math.round(ox), Math.round(oy)]
        });
      }
    }
  });

  const wrappers = [...document.querySelectorAll('.tablewrap')].map(w => ({
    scrollW: w.scrollWidth, clientW: w.clientWidth,
    scrollable: w.scrollWidth > w.clientWidth + 1
  }));

  const txt = document.body.innerText || '';
  const data = (typeof DATA !== 'undefined') ? DATA : null;
  const counts = {
    live: data && data.live && data.live.symbols ? data.live.symbols.length : 0,
    scanRows: data && data.scan && data.scan.rankings && data.scan.rankings.all_rows
              ? data.scan.rankings.all_rows.length : 0,
    timeline: data && data.monitor && data.monitor.history_recent
              ? data.monitor.history_recent.length : 0,
    historyTotal: data && data.monitor && data.monitor.history_total
              ? data.monitor.history_total : 0
  };
  const bands = [...document.querySelectorAll('.badge')].map(b => ({
    cls: b.className, r: rectOf(b)
  }));
  // 徽章是否被裁切（寬度小於其 scrollWidth）
  const clippedBadges = bands.filter(b => {
    const el = [...document.querySelectorAll('.badge')].find(x =>
      x.className === b.cls && rectOf(x).join(',') === b.r.join(','));
    return el ? el.scrollWidth > el.clientWidth + 2 : false;
  }).length;

  return JSON.stringify({
    vw: W, vh: H, pageOverflow,
    imgTotal: imgs.length, imgBroken: imgs.filter(i=>i.broken).length,
    offendersTotal: offenders.length, offenders: offenders.slice(0,12),
    overlapTotal: overlaps.length, overlaps: overlaps.slice(0,8),
    tablewrapTotal: wrappers.length,
    tablewrapScrollable: wrappers.filter(w=>w.scrollable).length,
    clippedBadges,
    counts,
    has: {
      live: txt.includes('即時訊號面板'),
      scan: txt.includes('市場掃描排行'),
      monitor: txt.includes('自動化監控狀態'),
      params: txt.includes('方法與參數'),
      disclaimer: txt.includes('投資免責聲明'),
      source: txt.includes('data-api.binance.vision'),
      honesty: txt.includes('並未對外發送'),
      replayed: txt.includes('回補')
    },
    tables: document.querySelectorAll('table').length,
    details: document.querySelectorAll('details').length
  });
})()
"""


def measure(pw, url: str, w: int, h: int, label: str, outdir: Path) -> dict:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": w, "height": h},
                            device_scale_factor=1)
    page.goto(url, wait_until="load", timeout=60000)
    page.wait_for_timeout(900)
    raw = page.evaluate(MEASURE_JS)
    data = json.loads(raw) if isinstance(raw, str) else raw

    shot = outdir / f"{label.replace(' ', '_')}.png"
    page.screenshot(path=str(shot), full_page=True)

    print(f"\n{'=' * 72}\n  {label}   (viewport {w}x{h})\n{'=' * 72}")
    def mark(v): return "✅" if v == 0 else "❌"
    print(f"  頁面橫向溢出      : {data['pageOverflow']} px   {mark(data['pageOverflow'])}")
    print(f"  圖片解碼          : {data['imgTotal']-data['imgBroken']}/{data['imgTotal']}   "
          f"{mark(data['imgBroken'])}")
    print(f"  右緣超出元素      : {data['offendersTotal']} 個   {mark(data['offendersTotal'])}")
    for o in data["offenders"][:6]:
        print(f"      - <{o['tag']} class=\"{o['cls']}\"> right={o['right']} w={o['width']} | {o['text']}")
    print(f"  文字重疊          : {data['overlapTotal']} 組   {mark(data['overlapTotal'])}")
    for o in data["overlaps"][:4]:
        print(f"      - {o['parent']}: {o['a']} × {o['b']} 重疊 {o['overlap']}px")
    print(f"  徽章被裁切        : {data['clippedBadges']} 個   {mark(data['clippedBadges'])}")
    print(f"  表格 / details    : {data['tables']} / {data['details']}")
    print(f"  捲動容器          : {data['tablewrapTotal']} 個"
          f"（{data['tablewrapScrollable']} 個需橫向捲動）")
    print(f"  內嵌資料筆數      : 即時 {data['counts']['live']}、"
          f"掃描 {data['counts']['scanRows']}、時間軸 {data['counts']['timeline']}、"
          f"歷史 {data['counts']['historyTotal']}")
    hs = data["has"]
    print(f"  章節存在          : " + "  ".join(
        f"{k}={'✓' if v else '✗'}" for k, v in hs.items()))
    data["label"] = label
    data["screenshot"] = str(shot)
    browser.close()
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--outdir", default="verify_dashboard")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    results = []
    with sync_playwright() as pw:
        for w, h, label in ((1280, 900, "desktop 1280"), (390, 844, "mobile 390")):
            results.append(measure(pw, args.url, w, h, label, outdir))

    bad = []
    for d in results:
        if d["pageOverflow"] > 0: bad.append((d["label"], "頁面橫向溢出"))
        if d["imgBroken"] > 0: bad.append((d["label"], "破圖"))
        if d["offendersTotal"] > 0: bad.append((d["label"], "元素溢出"))
        if d["overlapTotal"] > 0: bad.append((d["label"], "文字重疊"))
        if d["clippedBadges"] > 0: bad.append((d["label"], "徽章被裁切"))
        if d["counts"]["live"] == 0 or d["counts"]["scanRows"] == 0 \
                or d["counts"]["timeline"] == 0:
            bad.append((d["label"], "內嵌資料為空"))
        for k, v in d["has"].items():
            if not v:
                bad.append((d["label"], f"缺少內容：{k}"))

    print(f"\n{'=' * 72}")
    if bad:
        print(f"  ❌ {len(bad)} 項缺陷：")
        for lbl, msg in bad:
            print(f"     - [{lbl}] {msg}")
    else:
        print("  ✅ 全部通過（溢出 0、破圖 0、重疊 0、資料齊備、內容完整）")
    print("=" * 72)
    (outdir / "measure.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
