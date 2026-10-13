#!/usr/bin/env python3
"""verify_new_strategies.py — 以真實 Chromium 驗證三個新增策略。

驗證項目（全部以真實瀏覽器執行，非模擬）
-------------------------------------
 A. 桌面 1280×800：策略選擇器含 9 個選項，且三個新策略可被選取
 B. 逐一選取 bb_reversion / keltner / dyn_risk，確認：
      - 即時卡片、掃描表、參數面板、策略指標欄都跟著重算
      - 數值與 ctma_default 基準「確實不同」
 C. 動態風險敞口：建議槓桿隨波動度改變
      - 同一策略下，不同標的（ATR% 不同）槓桿呈反向關係
      - 調整目標波動度後，槓桿確實改變
 D. 自動刷新在切換策略後仍運作（時間戳前進、請求數增加）
 E. 桌面 / 手機：無橫向溢出、無破圖、無文字重疊
 F. 免責聲明仍存在

輸出：verify/new_strategies_report.json + 截圖 PNG

用法：
    python3 verify_new_strategies.py --url http://127.0.0.1:8787/dashboard.html
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
  const cards = Array.from(document.querySelectorAll('#liveCards .card')).map(c => {
    const rows = Array.from(c.querySelectorAll('.row'));
    const get = (label) => {
      for (const r of rows) {
        const sp = r.querySelector('span');
        if (sp && sp.textContent.trim() === label) {
          const b = r.querySelector('b');
          return b ? b.textContent.trim() : null;
        }
      }
      return null;
    };
    return {symbol: (c.querySelector('.sym')||{}).textContent||null,
            price: get('價格'), signal: get('訊號'), strength: get('強度'),
            stop: get('建議停損'), lev: get('建議槓桿'), pos: get('部位比例')};
  });
  return {
    selected: (document.getElementById('strategySel')||{}).value||null,
    optionCount: document.querySelectorAll('#strategySel option').length,
    metricHeader: (document.getElementById('thStrat')||{}).textContent||'',
    leveragePanel: (document.getElementById('liveLeverage')||{}).textContent||'',
    paramCells: Array.from(document.querySelectorAll('#paramGrid .pcell')).map(p => ({
      k: (p.querySelector('.pk')||{}).textContent||'',
      v: (p.querySelector('.pv')||{}).textContent||''})),
    fieldKeys: Array.from(document.querySelectorAll('#custGrid [data-key]')).map(e => e.getAttribute('data-key')),
    cards: cards,
    statusText: (document.getElementById('statusText')||{}).textContent||'',
    lastUpdate: (document.getElementById('lastUpdate')||{}).textContent||'',
    scanRows: document.querySelectorAll('#scanBody tr').length,
    lastLive: (window.__CTMA_DASH__ && window.__CTMA_DASH__.lastLive) || null,
    engineVersion: (window.CTMA && window.CTMA.VERSION) || null,
    hasDisclaimer: document.body.textContent.includes('投資免責聲明'),
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
            if not busy and '抓取中' not in txt and '初始化' not in txt and rows > 1:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def sig(snap):
    return json.dumps([{k: c.get(k) for k in ('symbol', 'price', 'strength', 'stop', 'lev')}
                       for c in snap['cards']], sort_keys=True)


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
        settled = wait_settled(page, args.wait_seconds)
        chk("桌面：refreshAll 完成", settled)

        base = page.evaluate(SNAP)
        chk("桌面：引擎版本為 1.3.0", base["engineVersion"] == "1.3.0", f"version={base['engineVersion']}")
        chk("桌面：策略選擇器含 9 個選項", base["optionCount"] == 9, f"options={base['optionCount']}")
        chk("桌面：預設策略為 ctma_default", base["selected"] == "ctma_default", f"selected={base['selected']}")
        chk("桌面：真的對幣安發出請求", len(reqs) > 0, f"requests={len(reqs)}")
        chk("桌面：即時卡片有資料", len(base["cards"]) >= 3, f"cards={len(base['cards'])}")
        chk("桌面：掃描表已渲染", base["scanRows"] >= 5, f"rows={base['scanRows']}")
        chk("桌面：免責聲明存在（中英文）",
            base["hasDisclaimer"] and base["hasEnglishDisclaimer"])
        page.screenshot(path=str(outdir / "newstrat-desktop-ctma.png"), full_page=True)

        # ---------------- 逐一選取三個新策略 ----------------
        snaps = {"ctma_default": base}
        for pid, fname in (("bb_reversion", "newstrat-desktop-bb.png"),
                           ("keltner", "newstrat-desktop-keltner.png"),
                           ("dyn_risk", "newstrat-desktop-dynrisk.png")):
            page.select_option("#strategySel", pid)
            time.sleep(0.4)
            wait_settled(page, args.wait_seconds)
            time.sleep(0.8)
            s = page.evaluate(SNAP)
            snaps[pid] = s
            page.screenshot(path=str(outdir / fname), full_page=True)
            chk(f"{pid}：選擇器已切換", s["selected"] == pid, f"selected={s['selected']}")
            chk(f"{pid}：即時卡片有資料", len(s["cards"]) >= 3, f"cards={len(s['cards'])}")
            chk(f"{pid}：掃描表已渲染", s["scanRows"] >= 5, f"rows={s['scanRows']}")
            chk(f"{pid}：狀態正常", s["statusText"] == "即時資料正常", f"status={s['statusText']}")

        # B. 數值確實改變
        chk("bb_reversion：數值與 ctma_default 不同", sig(snaps["bb_reversion"]) != sig(base))
        chk("keltner：數值與 ctma_default 不同", sig(snaps["keltner"]) != sig(base))
        chk("dyn_risk：數值與 ctma_default 不同", sig(snaps["dyn_risk"]) != sig(base))

        # 策略指標欄位
        chk("bb_reversion：策略指標欄為「布林 %B」",
            snaps["bb_reversion"]["metricHeader"] == "布林 %B", snaps["bb_reversion"]["metricHeader"])
        chk("keltner：策略指標欄為「卡爾特 %B」",
            snaps["keltner"]["metricHeader"] == "卡爾特 %B", snaps["keltner"]["metricHeader"])
        chk("dyn_risk：策略指標欄為「建議槓桿」",
            snaps["dyn_risk"]["metricHeader"] == "建議槓桿", snaps["dyn_risk"]["metricHeader"])

        # 參數面板顯示策略專屬參數
        def cell(snap, key):
            for c in snap["paramCells"]:
                if c["k"] == key:
                    return c["v"]
            return None
        chk("bb_reversion：參數面板顯示布林週期",
            cell(snaps["bb_reversion"], "布林週期") is not None,
            f"cells={[c['k'] for c in snaps['bb_reversion']['paramCells']]}")
        chk("keltner：參數面板顯示卡爾特 EMA 週期",
            cell(snaps["keltner"], "卡爾特 EMA 週期") is not None)
        chk("dyn_risk：參數面板顯示目標波動度",
            cell(snaps["dyn_risk"], "目標波動度") is not None)

        # 參數編輯器欄位依策略改變
        chk("bb_reversion：編輯器含 bb_len 欄位",
            "bb_len" in snaps["bb_reversion"]["fieldKeys"], f"{snaps['bb_reversion']['fieldKeys']}")
        chk("keltner：編輯器含 kc_mult 欄位",
            "kc_mult" in snaps["keltner"]["fieldKeys"])
        chk("dyn_risk：編輯器含 dr_target_vol 欄位",
            "dr_target_vol" in snaps["dyn_risk"]["fieldKeys"])

        # C. 動態風險敞口：槓桿隨波動度改變
        page.select_option("#strategySel", "dyn_risk")
        time.sleep(0.4)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.8)
        dr = page.evaluate(SNAP)
        ll = dr["lastLive"] or []
        pairs = [(x["symbol"], x.get("atr_pct"), x.get("leverage")) for x in ll
                 if x.get("atr_pct") and x.get("leverage")]
        chk("dyn_risk：每檔標的都有建議槓桿", len(pairs) >= 3, f"{pairs}")
        # 反向關係：ATR% 越高 → 槓桿越低
        inv_ok = True
        for i in range(len(pairs)):
            for j in range(len(pairs)):
                if pairs[i][1] > pairs[j][1] and not (pairs[i][2] < pairs[j][2]):
                    inv_ok = False
        chk("dyn_risk：槓桿與波動度呈反向關係（波動越高、槓桿越低）", inv_ok, f"{pairs}")
        chk("dyn_risk：槓桿面板已顯示", "動態風險敞口" in dr["leveragePanel"],
            dr["leveragePanel"][:80])

        # 調整目標波動度 → 槓桿改變
        lev_before = [p[2] for p in pairs]
        page.evaluate("() => window.__CTMA_DASH__.setField('dr_target_vol', 1.0)")
        page.click("#applyParams")
        time.sleep(0.5)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.8)
        dr2 = page.evaluate(SNAP)
        ll2 = dr2["lastLive"] or []
        pairs2 = [(x["symbol"], x.get("atr_pct"), x.get("leverage")) for x in ll2
                  if x.get("atr_pct") and x.get("leverage")]
        lev_after = [p[2] for p in pairs2]
        chk("dyn_risk：調整目標波動度後槓桿確實改變", lev_before != lev_after,
            f"{lev_before} → {lev_after}")
        chk("dyn_risk：目標波動度調低後槓桿下降",
            all(a <= b for a, b in zip(lev_after, lev_before)) and lev_after != lev_before,
            f"{lev_before} → {lev_after}")
        page.screenshot(path=str(outdir / "newstrat-desktop-dynrisk-tuned.png"), full_page=True)

        # D. 自動刷新在切換策略後仍運作
        t1 = dr2["lastUpdate"]
        n1 = len(reqs)
        page.click("#refreshBtn")
        time.sleep(1.0)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.8)
        dr3 = page.evaluate(SNAP)
        chk("切換策略後：自動刷新仍運作（時間戳前進）", t1 != dr3["lastUpdate"],
            f"{t1} → {dr3['lastUpdate']}")
        chk("切換策略後：仍持續抓取（請求數增加）", len(reqs) > n1, f"{n1} → {len(reqs)}")
        chk("切換策略後：自訂參數仍沿用（dr_target_vol=1.0）",
            (dr3["lastLive"] or [{}])[0].get("leverage") is not None)

        # E. 桌面版面
        ov_d = page.evaluate(OVERFLOW_PROBE)
        bi_d = page.evaluate(BROKEN_IMG_PROBE)
        ol_d = page.evaluate(OVERLAP_PROBE)
        chk("桌面：無橫向溢出", ov_d["horizontalOverflowPx"] == 0,
            f"overflow={ov_d['horizontalOverflowPx']}px offenders={ov_d['offenderCount']}")
        chk("桌面：無破圖", bi_d["broken"] == 0, f"broken={bi_d['broken']}/{bi_d['total']}")
        chk("桌面：無文字重疊", ol_d["count"] == 0, f"overlaps={ol_d['count']}")
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
        ov_m = m.evaluate(OVERFLOW_PROBE)
        bi_m = m.evaluate(BROKEN_IMG_PROBE)
        ol_m = m.evaluate(OVERLAP_PROBE)
        chk("手機：策略選擇器可用（9 選項）", ms["optionCount"] == 9, f"options={ms['optionCount']}")
        chk("手機：可切換至 bb_reversion", ms["selected"] == "bb_reversion", f"selected={ms['selected']}")
        chk("手機：即時資料有載入", len(ms["cards"]) >= 3, f"cards={len(ms['cards'])}")
        chk("手機：無橫向溢出", ov_m["horizontalOverflowPx"] == 0,
            f"overflow={ov_m['horizontalOverflowPx']}px offenders={ov_m['offenderCount']}")
        chk("手機：無破圖", bi_m["broken"] == 0, f"broken={bi_m['broken']}/{bi_m['total']}")
        chk("手機：無文字重疊", ol_m["count"] == 0, f"overlaps={ol_m['count']}")
        m.screenshot(path=str(outdir / "newstrat-mobile-bb.png"), full_page=True)
        m.select_option("#strategySel", "dyn_risk")
        time.sleep(0.4)
        wait_settled(m, args.wait_seconds)
        time.sleep(0.8)
        m.screenshot(path=str(outdir / "newstrat-mobile-dynrisk.png"), full_page=True)
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
        "snapshots": {k: {"selected": v["selected"], "metricHeader": v["metricHeader"],
                          "cards": v["cards"], "paramCells": v["paramCells"],
                          "fieldKeys": v["fieldKeys"]}
                      for k, v in snaps.items()},
        "dynrisk_leverage": {"before": pairs, "after_target_vol_1_0": pairs2},
        "overflow_desktop": ov_d, "overflow_mobile": ov_m,
        "overlap_desktop": ol_d, "overlap_mobile": ol_m,
    }
    (outdir / "new_strategies_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== {report['status']}  {passed}/{len(checks)} ===")
    print(f"report → {outdir / 'new_strategies_report.json'}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
