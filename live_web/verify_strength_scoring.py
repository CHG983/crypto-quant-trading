#!/usr/bin/env python3
"""verify_strength_scoring.py — 以真實 Chromium 驗證「布林／卡爾特專屬強度評分」。

驗證目標（全部以真實瀏覽器 + 真實幣安即時資料執行，非模擬）
------------------------------------------------------------
 A. 桌面 1280：頁面載入、策略選擇器 9 選項、即時卡片與掃描表有資料
 B. 核心：切換 bb_reversion 與 keltner，確認
      - 兩者對同一標的的「強度」值 **不再相同**
      - 兩者的強度評分方法標籤不同（反轉評分 / 突破評分）
      - 策略說明依策略切換
      - 掃描排行、停損值皆正確重算
 C. 回歸：ctma_default 仍使用「動能評分」，強度值與切換前一致（未被新評分影響）
 D. 自動刷新在切換策略後仍運作（時間戳前進 + 請求數增加）
 E. 桌面 / 手機：無橫向溢出、無破圖、無文字重疊
 F. 免責聲明仍存在（中英文）

輸出：verify/strength_scoring_report.json + 截圖 PNG

用法：
    python3 verify_strength_scoring.py --url http://127.0.0.1:8787/dashboard.html
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

OVERFLOW_PROBE = """
() => {
  const de = document.documentElement;
  const offenders = [];
  const vw = window.innerWidth;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    let inScroller = false, p = el.parentElement;
    while (p) {
      const pcs = getComputedStyle(p);
      if ((pcs.overflowX === 'auto' || pcs.overflowX === 'scroll') && p.scrollWidth > p.clientWidth) {
        inScroller = true; break;
      }
      p = p.parentElement;
    }
    if (inScroller) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (cs.position === 'fixed' || cs.position === 'sticky') continue;
    if (r.right > vw + 1 || r.left < -1) {
      offenders.push({tag: el.tagName.toLowerCase(),
        cls: (el.className && String(el.className).slice(0,70)) || '',
        left: Math.round(r.left), right: Math.round(r.right)});
    }
  }
  return {docScrollWidth: de.scrollWidth, docClientWidth: de.clientWidth,
    innerWidth: vw, horizontalOverflowPx: Math.max(0, de.scrollWidth - de.clientWidth),
    offenders: offenders.slice(0,12), offenderCount: offenders.length};
}
"""

BROKEN_IMG_PROBE = """
() => {
  const imgs = Array.from(document.querySelectorAll('img'));
  const broken = imgs.filter(i => i.complete && i.naturalWidth === 0);
  return {total: imgs.length, broken: broken.length,
          brokenSrc: broken.slice(0,5).map(i => i.getAttribute('src'))};
}
"""

OVERLAP_PROBE = """
() => {
  const inClosedDetails = (el) => {
    let n = el;
    while (n && n !== document.body) {
      if (n.tagName === 'DETAILS' && !n.open) return true;
      n = n.parentElement;
    }
    return false;
  };
  const els = Array.from(document.querySelectorAll('body *')).filter(el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'inline' || cs.display === 'contents') return false;
    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    if (inClosedDetails(el)) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return false;
    if (el.children.length > 0 && el.textContent.trim().length === 0) return false;
    return true;
  });
  const overlaps = [];
  for (let i = 0; i < els.length; i++) {
    for (let j = i + 1; j < els.length; j++) {
      const a = els[i], b = els[j];
      if (a.contains(b) || b.contains(a)) continue;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const ox = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const oy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (ox > 4 && oy > 4) {
        overlaps.push({a: a.tagName.toLowerCase() + '.' + String(a.className).slice(0,40),
                       b: b.tagName.toLowerCase() + '.' + String(b.className).slice(0,40),
                       ox: Math.round(ox), oy: Math.round(oy)});
      }
    }
  }
  return {count: overlaps.length, samples: overlaps.slice(0,8)};
}
"""

SNAP = """
() => {
  const rowsOf = (c) => Array.from(c.querySelectorAll('.row'));
  const get = (c, label) => {
    for (const r of rowsOf(c)) {
      const sp = r.querySelector('span');
      if (sp && sp.textContent.trim() === label) {
        const b = r.querySelector('b');
        return b ? b.textContent.trim() : null;
      }
    }
    return null;
  };
  const cards = Array.from(document.querySelectorAll('#liveCards .card')).map(c => ({
    symbol: (c.querySelector('.sym')||{}).textContent||null,
    price: get(c,'\u50f9\u683c'), signal: get(c,'\u8a0a\u865f'), strength: get(c,'\u5f37\u5ea6'),
    strengthMethod: get(c,'\u5f37\u5ea6\u8a55\u5206'),
    stop: get(c,'\u5efa\u8b70\u505c\u640d'), stratMetric: get(c,'\u5e03\u6797 %B') || get(c,'\u5361\u723e\u7279 %B')
  }));
  const stf = Array.from(document.querySelectorAll('.stf')).map(el => ({
    kind: el.getAttribute('data-kind'), hidden: !!el.hidden,
    text: (el.textContent||'').replace(/\\s+/g,' ').trim().slice(0,160)
  }));
  const scan = Array.from(document.querySelectorAll('#scanBody tr')).slice(0,6).map(tr => {
    const td = Array.from(tr.querySelectorAll('td')).map(x => (x.textContent||'').trim());
    return td;
  });
  return {
    selected: (document.getElementById('strategySel')||{}).value||null,
    optionCount: document.querySelectorAll('#strategySel option').length,
    metricHeader: (document.getElementById('thStrat')||{}).textContent||'',
    stratDesc: (document.getElementById('strategyDesc')||{}).textContent||'',
    docSummary: (document.getElementById('stDocSummary')||{}).textContent||'',
    stfBlocks: stf,
    visibleStfKinds: stf.filter(x => !x.hidden).map(x => x.kind),
    cards: cards,
    statusText: (document.getElementById('statusText')||{}).textContent||'',
    lastUpdate: (document.getElementById('lastUpdate')||{}).textContent||'',
    scanRows: document.querySelectorAll('#scanBody tr').length,
    scanTop: scan,
    engineVersion: (window.CTMA && window.CTMA.VERSION) || null,
    hasDisclaimer: document.body.textContent.includes('\u6295\u8cc7\u514d\u8cac\u8072\u660e'),
    hasEnglishDisclaimer: document.body.textContent.includes('All content on this page (including real-time signals')
  };
}
"""


def wait_settled(page, timeout_s=90):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            busy = page.evaluate("() => window.__CTMA_DASH__ ? window.__CTMA_DASH__.state.busy : true")
            txt = page.evaluate("() => (document.getElementById('statusText')||{}).textContent || ''")
            rows = page.evaluate("() => document.querySelectorAll('#scanBody tr').length")
            if not busy and '\u6293\u53d6\u4e2d' not in txt and '\u521d\u59cb\u5316' not in txt and rows > 1:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def strength_map(snap):
    return {c["symbol"]: c["strength"] for c in snap["cards"] if c.get("symbol")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", default="verify")
    ap.add_argument("--wait-seconds", type=int, default=90)
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def chk(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])

        # ---------------- 桌面 1280 ----------------
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        reqs = []
        page.on("request", lambda r: reqs.append(r.url) if "binance" in r.url else None)
        page.goto(args.url, wait_until="domcontentloaded")
        chk("\u684c\u9762\uff1arefreshAll \u5b8c\u6210", wait_settled(page, args.wait_seconds))

        base = page.evaluate(SNAP)
        chk("\u684c\u9762\uff1a\u5f15\u64ce\u7248\u672c\u70ba 1.4.0", base["engineVersion"] == "1.4.0",
            f"version={base['engineVersion']}")
        chk("\u684c\u9762\uff1a\u7b56\u7565\u9078\u64c8\u5668\u542b 9 \u500b\u9078\u9805", base["optionCount"] == 9,
            f"options={base['optionCount']}")
        chk("\u684c\u9762\uff1a\u771f\u7684\u5c0d\u5e63\u5b89\u767c\u51fa\u8acb\u6c42", len(reqs) > 0, f"requests={len(reqs)}")
        chk("\u684c\u9762\uff1a\u5373\u6642\u5361\u7247\u6709\u8cc7\u6599", len(base["cards"]) >= 3, f"cards={len(base['cards'])}")
        chk("\u684c\u9762\uff1a\u6383\u63cf\u8868\u5df2\u6e32\u67d3", base["scanRows"] >= 5, f"rows={base['scanRows']}")
        chk("\u684c\u9762\uff1a\u72c0\u614b\u6b63\u5e38", base["statusText"] == "\u5373\u6642\u8cc7\u6599\u6b63\u5e38",
            f"status={base['statusText']}")
        chk("\u684c\u9762\uff1aCTMA \u9810\u8a2d\u986f\u793a\u300c\u52d5\u80fd\u8a55\u5206\u300d",
            all("\u52d5\u80fd\u8a55\u5206" in (c["strengthMethod"] or "") for c in base["cards"]),
            f"{(base['cards'] or [{}])[0].get('strengthMethod')}")
        chk("\u684c\u9762\uff1a\u8aaa\u660e\u5340\u53ea\u986f\u793a ctma \u516c\u5f0f",
            base["visibleStfKinds"] == ["ctma"], f"{base['visibleStfKinds']}")
        chk("\u684c\u9762\uff1a\u514d\u8cac\u8072\u660e\u5b58\u5728\uff08\u4e2d\u82f1\u6587\uff09",
            base["hasDisclaimer"] and base["hasEnglishDisclaimer"])
        page.screenshot(path=str(outdir / "strength-desktop-ctma.png"), full_page=True)

        # ---------------- 切換 bb_reversion / keltner ----------------
        snaps = {"ctma_default": base}
        for pid, fname in (("bb_reversion", "strength-desktop-bb.png"),
                           ("keltner", "strength-desktop-keltner.png")):
            page.select_option("#strategySel", pid)
            time.sleep(0.4)
            wait_settled(page, args.wait_seconds)
            time.sleep(0.8)
            s = page.evaluate(SNAP)
            snaps[pid] = s
            page.screenshot(path=str(outdir / fname), full_page=True)
            chk(f"{pid}\uff1a\u9078\u64c8\u5668\u5df2\u5207\u63db", s["selected"] == pid, f"selected={s['selected']}")
            chk(f"{pid}\uff1a\u5373\u6642\u5361\u7247\u6709\u8cc7\u6599", len(s["cards"]) >= 3, f"cards={len(s['cards'])}")
            chk(f"{pid}\uff1a\u6383\u63cf\u8868\u5df2\u6e32\u67d3", s["scanRows"] >= 5, f"rows={s['scanRows']}")
            chk(f"{pid}\uff1a\u72c0\u614b\u6b63\u5e38", s["statusText"] == "\u5373\u6642\u8cc7\u6599\u6b63\u5e38",
                f"status={s['statusText']}")

        bb, kc = snaps["bb_reversion"], snaps["keltner"]
        mbb, mkc = strength_map(bb), strength_map(kc)

        # ===== 核心斷言：同一標的，兩個強度值不同 =====
        common = [s for s in mbb if s in mkc]
        diffs = [(s, mbb[s], mkc[s]) for s in common if mbb[s] != mkc[s]]
        chk("\u5f37\u5ea6\uff1a\u5e03\u6797\u8207\u5361\u723e\u7279\u5c0d\u540c\u4e00\u6a19\u7684\u5f37\u5ea6\u503c\u4e0d\u518d\u76f8\u540c",
            len(common) >= 3 and len(diffs) == len(common),
            f"common={len(common)} differing={len(diffs)} :: " +
            "; ".join(f"{s}: BB={a} vs KC={b}" for s, a, b in diffs[:5]))
        chk("\u5f37\u5ea6\uff1a\u5e03\u6797\u4f7f\u7528\u300c\u53cd\u8f49\u8a55\u5206\u300d\u6a19\u7c64",
            all("\u53cd\u8f49\u8a55\u5206" in (c["strengthMethod"] or "") for c in bb["cards"]),
            f"{(bb['cards'] or [{}])[0].get('strengthMethod')}")
        chk("\u5f37\u5ea6\uff1a\u5361\u723e\u7279\u4f7f\u7528\u300c\u7a81\u7834\u8a55\u5206\u300d\u6a19\u7c64",
            all("\u7a81\u7834\u8a55\u5206" in (c["strengthMethod"] or "") for c in kc["cards"]),
            f"{(kc['cards'] or [{}])[0].get('strengthMethod')}")
        chk("\u8aaa\u660e\u5340\uff1a\u5e03\u6797\u6642\u53ea\u986f\u793a bb \u516c\u5f0f", bb["visibleStfKinds"] == ["bb"],
            f"{bb['visibleStfKinds']}")
        chk("\u8aaa\u660e\u5340\uff1a\u5361\u723e\u7279\u6642\u53ea\u986f\u793a keltner \u516c\u5f0f",
            kc["visibleStfKinds"] == ["keltner"], f"{kc['visibleStfKinds']}")
        chk("\u8aaa\u660e\u5340\uff1a\u6a19\u984c\u6703\u8aaa\u660e\u76ee\u524d\u4f7f\u7528\u54ea\u5957\u8a55\u5206",
            "\u53cd\u8f49\u8a55\u5206" in bb["docSummary"] and "\u7a81\u7834\u8a55\u5206" in kc["docSummary"],
            f"bb='{bb['docSummary']}' kc='{kc['docSummary']}'")

        # 掃描排行 / 停損 / 策略指標欄重算
        chk("\u6383\u63cf\u6392\u884c\uff1a\u5207\u63db\u5e03\u6797\u5f8c\u8207 CTMA \u4e0d\u540c",
            bb["scanTop"] != base["scanTop"])
        chk("\u505c\u640d\uff1a\u5207\u63db\u5e03\u6797\u5f8c\u505c\u640d\u503c\u6539\u8b8a",
            [c["stop"] for c in bb["cards"]] != [c["stop"] for c in base["cards"]],
            f"{[c['stop'] for c in base['cards']]} \u2192 {[c['stop'] for c in bb['cards']]}")
        chk("\u7b56\u7565\u6307\u6a19\u6b04\uff1a\u5e03\u6797\u986f\u793a %B", "\u5e03\u6797 %B" in bb["metricHeader"],
            bb["metricHeader"])
        chk("\u7b56\u7565\u6307\u6a19\u6b04\uff1a\u5361\u723e\u7279\u986f\u793a %B", "\u5361\u723e\u7279 %B" in kc["metricHeader"],
            kc["metricHeader"])

        # ---------------- C. 回歸：切回 CTMA ----------------
        page.select_option("#strategySel", "ctma_default")
        time.sleep(0.4)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.8)
        back = page.evaluate(SNAP)
        snaps["ctma_default_back"] = back
        chk("\u56de\u6b78\uff1a\u5207\u56de CTMA \u5f8c\u4ecd\u70ba\u300c\u52d5\u80fd\u8a55\u5206\u300d",
            all("\u52d5\u80fd\u8a55\u5206" in (c["strengthMethod"] or "") for c in back["cards"]),
            f"{(back['cards'] or [{}])[0].get('strengthMethod')}")
        chk("\u56de\u6b78\uff1aCTMA \u5f37\u5ea6\u8207\u5207\u63db\u524d\u4e00\u81f4\uff08\u672a\u88ab\u65b0\u8a55\u5206\u5f71\u97ff\uff09",
            strength_map(back) == strength_map(base),
            f"{strength_map(base)} vs {strength_map(back)}")

        # ---------------- D. 自動刷新 ----------------
        t1, n1 = back["lastUpdate"], len(reqs)
        page.click("#refreshBtn")
        time.sleep(1.0)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.8)
        aff = page.evaluate(SNAP)
        chk("\u81ea\u52d5\u5237\u65b0\uff1a\u6642\u9593\u6233\u524d\u9032", t1 != aff["lastUpdate"],
            f"{t1} \u2192 {aff['lastUpdate']}")
        chk("\u81ea\u52d5\u5237\u65b0\uff1a\u4ecd\u6301\u7e8c\u6293\u53d6\uff08\u8acb\u6c42\u6578\u589e\u52a0\uff09", len(reqs) > n1,
            f"{n1} \u2192 {len(reqs)}")

        # ---------------- E. 桌面版面 ----------------
        ov_d = page.evaluate(OVERFLOW_PROBE)
        bi_d = page.evaluate(BROKEN_IMG_PROBE)
        ol_d = page.evaluate(OVERLAP_PROBE)
        chk("\u684c\u9762\uff1a\u7121\u6a6b\u5411\u6ea2\u51fa", ov_d["horizontalOverflowPx"] == 0,
            f"overflow={ov_d['horizontalOverflowPx']}px offenders={ov_d['offenderCount']}")
        chk("\u684c\u9762\uff1a\u7121\u7834\u5716", bi_d["broken"] == 0, f"broken={bi_d['broken']}/{bi_d['total']}")
        chk("\u684c\u9762\uff1a\u7121\u6587\u5b57\u91cd\u758a", ol_d["count"] == 0, f"overlaps={ol_d['count']}")
        page.close()

        # ---------------- 手機 390 ----------------
        m = browser.new_page(viewport={"width": 390, "height": 844})
        m.goto(args.url, wait_until="domcontentloaded")
        wait_settled(m, args.wait_seconds)
        m.select_option("#strategySel", "bb_reversion")
        time.sleep(0.4)
        wait_settled(m, args.wait_seconds)
        time.sleep(0.8)
        ms = m.evaluate(SNAP)
        m.screenshot(path=str(outdir / "strength-mobile-bb.png"), full_page=True)
        m.select_option("#strategySel", "keltner")
        time.sleep(0.4)
        wait_settled(m, args.wait_seconds)
        time.sleep(0.8)
        mk = m.evaluate(SNAP)
        m.screenshot(path=str(outdir / "strength-mobile-keltner.png"), full_page=True)
        ov_m = m.evaluate(OVERFLOW_PROBE)
        bi_m = m.evaluate(BROKEN_IMG_PROBE)
        ol_m = m.evaluate(OVERLAP_PROBE)

        chk("\u624b\u6a5f\uff1a\u7b56\u7565\u9078\u64c8\u5668\u53ef\u7528\uff089 \u9078\u9805\uff09", ms["optionCount"] == 9,
            f"options={ms['optionCount']}")
        chk("\u624b\u6a5f\uff1a\u53ef\u5207\u63db\u81f3 bb_reversion", ms["selected"] == "bb_reversion",
            f"selected={ms['selected']}")
        chk("\u624b\u6a5f\uff1a\u53ef\u5207\u63db\u81f3 keltner", mk["selected"] == "keltner",
            f"selected={mk['selected']}")
        chk("\u624b\u6a5f\uff1a\u5373\u6642\u8cc7\u6599\u6709\u8f09\u5165", len(ms["cards"]) >= 3, f"cards={len(ms['cards'])}")
        mob_common = [s for s in strength_map(ms) if s in strength_map(mk)]
        mob_diff = [s for s in mob_common if strength_map(ms)[s] != strength_map(mk)[s]]
        chk("\u624b\u6a5f\uff1a\u5e03\u6797\u8207\u5361\u723e\u7279\u5f37\u5ea6\u503c\u4e5f\u4e0d\u540c",
            len(mob_common) >= 3 and len(mob_diff) == len(mob_common),
            f"{len(mob_diff)}/{len(mob_common)} differing")
        chk("\u624b\u6a5f\uff1a\u7121\u6a6b\u5411\u6ea2\u51fa", ov_m["horizontalOverflowPx"] == 0,
            f"overflow={ov_m['horizontalOverflowPx']}px offenders={ov_m['offenderCount']}")
        chk("\u624b\u6a5f\uff1a\u7121\u7834\u5716", bi_m["broken"] == 0, f"broken={bi_m['broken']}/{bi_m['total']}")
        chk("\u624b\u6a5f\uff1a\u7121\u6587\u5b57\u91cd\u758a", ol_m["count"] == 0, f"overlaps={ol_m['count']}")
        m.close()
        browser.close()

    passed = sum(1 for c in checks if c["ok"])
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "total_checks": len(checks),
        "passed_checks": passed,
        "failed_checks": len(checks) - passed,
        "status": "ALL PASS" if passed == len(checks) else "HAS FAILURES",
        "checks": checks,
        "strength_comparison": {
            "bb_reversion": strength_map(snaps["bb_reversion"]),
            "keltner": strength_map(snaps["keltner"]),
            "ctma_default": strength_map(snaps["ctma_default"]),
            "differing_symbols": [{"symbol": s, "bb": a, "keltner": b} for s, a, b in diffs],
        },
        "mobile_strength_comparison": {
            "bb_reversion": strength_map(ms), "keltner": strength_map(mk),
        },
        "visible_formula_block": {
            "ctma": snaps["ctma_default"]["visibleStfKinds"],
            "bb": snaps["bb_reversion"]["visibleStfKinds"],
            "keltner": snaps["keltner"]["visibleStfKinds"],
        },
        "scan_top_ctma": snaps["ctma_default"]["scanTop"],
        "scan_top_bb": snaps["bb_reversion"]["scanTop"],
        "scan_top_keltner": snaps["keltner"]["scanTop"],
        "overflow_desktop": ov_d, "overflow_mobile": ov_m,
        "overlap_desktop": ol_d, "overlap_mobile": ol_m,
    }
    (outdir / "strength_scoring_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== {report['status']}  {passed}/{len(checks)} ===")
    print(f"report \u2192 {outdir / 'strength_scoring_report.json'}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
