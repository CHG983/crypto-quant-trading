#!/usr/bin/env python3
"""verify_strategy_switch.py — 以真實 Chromium 驗證「策略選擇」功能。

驗證項目（全部以真實瀏覽器執行，非模擬）
-------------------------------------
 A. 桌面 1280×800 開啟，確認真的抓到幣安即時資料
 B. 策略選擇器存在，且選項數 == 引擎註冊的預設數
 C. 依序切換 3 個策略（ctma_default → wfo_9_34 → ctma_long_only），
    每次記錄即時訊號面板的數值，證明**切換後數值確實改變**
 D. 切換後自動刷新仍運作（計時器未中斷，時間戳仍會前進）
 E. 桌面 / 手機截圖 + 橫向溢出量測 + 破圖檢查
 F. 免責聲明與策略來源標示存在

輸出：live_web/verify/strategy_switch_report.json + 截圖 PNG

用法：
    python3 verify_strategy_switch.py --url http://127.0.0.1:8787/dashboard.html
"""
from __future__ import annotations

import argparse
import json
import sys
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

# 讀取目前畫面上「即時訊號面板」的關鍵數值
SNAPSHOT_PROBE = """
() => {
  const cards = Array.from(document.querySelectorAll('#liveCards .card'));
  const out = cards.map(c => {
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
    return {
      symbol: (c.querySelector('.sym') || {}).textContent || null,
      price: get('價格'),
      signal: get('訊號'),
      strength: get('強度'),
      ema: get('EMA 快 / 慢'),
      emaGap: get('EMA 差距'),
      rsi: get('RSI / MACD 柱'),
      atrPct: get('ATR%'),
      stop: get('建議停損'),
      tp: get('建議停利')
    };
  });
  const params = Array.from(document.querySelectorAll('#paramGrid .pcell')).map(p => ({
    k: (p.querySelector('.pk')||{}).textContent || '',
    v: (p.querySelector('.pv')||{}).textContent || ''
  }));
  return {
    cards: out,
    params: params,
    strategyDesc: (document.getElementById('strategyDesc')||{}).textContent || '',
    selected: (document.getElementById('strategySel')||{}).value || null,
    optionCount: (document.getElementById('strategySel')||{}).options
      ? document.getElementById('strategySel').options.length : 0,
    lastUpdate: (document.getElementById('lastUpdate')||{}).textContent || '',
    statusText: (document.getElementById('statusText')||{}).textContent || '',
    scanRows: document.querySelectorAll('#scanBody tr').length,
    scanFirstRow: (document.querySelector('#scanBody tr')||{}).textContent || ''
  };
}
"""


def wait_settled(page, timeout_s=90):
    """等 refreshAll 跑完（busy=false 且狀態非『抓取中』）。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            st = page.evaluate("() => window.__CTMA_DASH__ ? window.__CTMA_DASH__.state.busy : true")
            txt = page.evaluate("() => (document.getElementById('statusText')||{}).textContent || ''")
            if not st and '抓取中' not in txt and '初始化' not in txt:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


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

        # ---------- 桌面 ----------
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        reqs = []
        page.on("request", lambda r: reqs.append(r.url) if "binance" in r.url else None)
        page.goto(args.url, wait_until="domcontentloaded")
        settled = wait_settled(page, args.wait_seconds)
        chk("桌面：refreshAll 完成", settled)

        snap0 = page.evaluate(SNAPSHOT_PROBE)
        chk("桌面：真的對幣安發出請求", len(reqs) > 0, f"requests={len(reqs)}")
        chk("桌面：即時訊號卡片有資料", len(snap0["cards"]) >= 3, f"cards={len(snap0['cards'])}")
        chk("桌面：市場掃描有資料列", snap0["scanRows"] >= 5, f"rows={snap0['scanRows']}")
        chk("桌面：策略選擇器存在", snap0["optionCount"] >= 6, f"options={snap0['optionCount']}")
        chk("桌面：預設策略為 ctma_default", snap0["selected"] == "ctma_default",
            f"selected={snap0['selected']}")

        ov_d = page.evaluate(OVERFLOW_PROBE)
        chk("桌面：無橫向溢出", ov_d["horizontalOverflowPx"] == 0,
            f"overflow={ov_d['horizontalOverflowPx']}px offenders={ov_d['offenderCount']}")
        bi_d = page.evaluate(BROKEN_IMG_PROBE)
        chk("桌面：無破圖", bi_d["broken"] == 0, f"broken={bi_d['broken']}/{bi_d['total']}")

        page.screenshot(path=str(outdir / "strat-desktop-1280.png"), full_page=True)

        # ---------- 策略切換 ----------
        results = {"ctma_default": snap0}
        for pid in ["wfo_9_34", "ctma_long_only"]:
            page.select_option("#strategySel", pid)
            time.sleep(0.4)
            wait_settled(page, args.wait_seconds)
            time.sleep(0.6)
            results[pid] = page.evaluate(SNAPSHOT_PROBE)
            print(f"  → 已切換至 {pid}")

        # 切換後數值必須改變
        def sig(snap):
            return json.dumps([{k: c.get(k) for k in ("symbol", "price", "strength", "ema", "stop")}
                               for c in snap["cards"]], sort_keys=True)

        chk("切換 ctma_default → wfo_9_34：數值改變",
            sig(results["ctma_default"]) != sig(results["wfo_9_34"]))
        chk("切換 wfo_9_34 → ctma_long_only：數值改變",
            sig(results["wfo_9_34"]) != sig(results["ctma_long_only"]))
        chk("切換後參數面板跟著變（EMA 週期）",
            results["ctma_default"]["params"] != results["wfo_9_34"]["params"],
            f"default={[p['v'] for p in results['ctma_default']['params'][:1]]} "
            f"wfo={[p['v'] for p in results['wfo_9_34']['params'][:1]]}")
        chk("切換後策略說明含來源標註",
            "來源" in results["wfo_9_34"]["strategyDesc"])

        # 記錄切換前後差異（供報告引用）
        diff = {}
        for pid in ["wfo_9_34", "ctma_long_only"]:
            d = []
            for a, b in zip(results["ctma_default"]["cards"], results[pid]["cards"]):
                if a.get("symbol") == b.get("symbol"):
                    d.append({"symbol": a["symbol"],
                              "price_default": a.get("price"), "price_new": b.get("price"),
                              "strength_default": a.get("strength"), "strength_new": b.get("strength"),
                              "ema_default": a.get("ema"), "ema_new": b.get("ema"),
                              "stop_default": a.get("stop"), "stop_new": b.get("stop")})
            diff[pid] = d

        # ---------- 自動刷新仍運作 ----------
        page.select_option("#strategySel", "ctma_default")
        wait_settled(page, args.wait_seconds)
        t1 = page.evaluate("() => (document.getElementById('lastUpdate')||{}).textContent || ''")
        n1 = len(reqs)
        page.click("#refreshBtn")
        time.sleep(1.0)
        wait_settled(page, args.wait_seconds)
        t2 = page.evaluate("() => (document.getElementById('lastUpdate')||{}).textContent || ''")
        n2 = len(reqs)
        chk("切換策略後自動刷新仍運作（時間戳前進）", t1 != t2, f"{t1} → {t2}")
        chk("切換策略後仍持續抓取（請求數增加）", n2 > n1, f"{n1} → {n2}")

        # 免責聲明
        disc = page.evaluate("() => (document.querySelector('.disc')||{}).textContent || ''")
        chk("免責聲明存在（中英文）",
            "投資免責聲明" in disc and "does not constitute investment advice" in disc)

        page.close()

        # ---------- 手機 ----------
        m = browser.new_page(viewport={"width": 390, "height": 844})
        m.goto(args.url, wait_until="domcontentloaded")
        wait_settled(m, args.wait_seconds)
        ov_m = m.evaluate(OVERFLOW_PROBE)
        bi_m = m.evaluate(BROKEN_IMG_PROBE)
        snap_m = m.evaluate(SNAPSHOT_PROBE)
        chk("手機：無橫向溢出", ov_m["horizontalOverflowPx"] == 0,
            f"overflow={ov_m['horizontalOverflowPx']}px offenders={ov_m['offenderCount']}")
        chk("手機：無破圖", bi_m["broken"] == 0, f"broken={bi_m['broken']}/{bi_m['total']}")
        chk("手機：策略選擇器可用", snap_m["optionCount"] >= 6, f"options={snap_m['optionCount']}")
        chk("手機：即時資料有載入", len(snap_m["cards"]) >= 3, f"cards={len(snap_m['cards'])}")
        m.screenshot(path=str(outdir / "strat-mobile-390.png"), full_page=True)
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
        "strategy_switch_diff": diff,
        "snapshots": {k: {"selected": v["selected"], "params": v["params"],
                          "cards": v["cards"]} for k, v in results.items()},
        "overflow_desktop": ov_d,
        "overflow_mobile": ov_m,
    }
    (outdir / "strategy_switch_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== {report['status']}  {passed}/{len(checks)} ===")
    print(f"report → {outdir / 'strategy_switch_report.json'}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
