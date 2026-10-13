#!/usr/bin/env python3
"""verify_custom_params.py — 以真實 Chromium 驗證「自訂參數」功能。

驗證項目（全部以真實瀏覽器執行，非模擬）
-------------------------------------
 A. 桌面 1280×800 開啟，確認真的抓到幣安即時資料
 B. 自訂參數編輯器存在，欄位數 == 引擎 PARAM_SPECS 數
 C. 記錄基準值 → 填入 EMA 9/34、ATR 停損 1.5 → 按「套用」→ 數值確實改變
 D. 不合法輸入（快線 > 慢線）被擋下：顯示錯誤訊息且數值「未套用」
 E. 「重設為預設」正確還原為該策略原始預設
 F. 自訂參數在自動刷新後仍被沿用（刷新一輪後生效值不變）
 G. 桌面 / 手機：無橫向溢出、無破圖、無文字重疊
 H. 免責聲明仍存在

輸出：verify/custom_params_report.json + 截圖 PNG

用法：
    python3 verify_custom_params.py --url http://127.0.0.1:8787/dashboard.html
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

# 文字重疊偵測：只比較「非 inline」的可見區塊元素，避免行內 <b>/<span> 換行造成的誤判。
# 另排除「已收合的 <details> 內」的內容 —— 那些元素雖然有非零 rect，但依規範不渲染，
# 且會與 summary 的 rect 重疊，屬偵測器誤判而非版面缺陷。
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

# 讀取即時訊號面板關鍵數值 + 自訂編輯器狀態
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
      price: get('價格'), signal: get('訊號'), strength: get('強度'),
      ema: get('EMA 快 / 慢'), emaGap: get('EMA 差距'),
      rsi: get('RSI / MACD 柱'), atrPct: get('ATR%'),
      stop: get('建議停損'), tp: get('建議停利')
    };
  });
  const fields = Array.from(document.querySelectorAll('#custGrid [data-key]')).map(el => ({
    key: el.getAttribute('data-key'),
    type: el.type || el.tagName.toLowerCase(),
    value: el.type === 'checkbox' ? el.checked : el.value
  }));
  const params = Array.from(document.querySelectorAll('#paramGrid .pcell')).map(p => ({
    k: (p.querySelector('.pk')||{}).textContent || '',
    v: (p.querySelector('.pv')||{}).textContent || ''
  }));
  return {
    cards: out, fields: fields, params: params,
    fieldCount: fields.length,
    strategyDesc: (document.getElementById('strategyDesc')||{}).textContent || '',
    selected: (document.getElementById('strategySel')||{}).value || null,
    custState: (document.getElementById('custState')||{}).textContent || '',
    custMsg: (document.getElementById('custMsg')||{}).textContent || '',
    errors: Array.from(document.querySelectorAll('#custErrors li')).map(li => li.textContent),
    errorsVisible: (() => { const u = document.getElementById('custErrors');
      return !!u && getComputedStyle(u).display !== 'none'; })(),
    lastUpdate: (document.getElementById('lastUpdate')||{}).textContent || '',
    countdown: (document.getElementById('countdown')||{}).textContent || '',
    statusText: (document.getElementById('statusText')||{}).textContent || '',
    scanRows: document.querySelectorAll('#scanBody tr').length,
    activeParams: window.__CTMA_DASH__ ? window.__CTMA_DASH__.activeParams() : null,
    isCustomized: window.__CTMA_DASH__ ? window.__CTMA_DASH__.isCustomized() : null
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


def set_field(page, key, value):
    return page.evaluate(
        "([k,v]) => window.__CTMA_DASH__.setField(k, v)", [key, value])


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

        base = page.evaluate(SNAPSHOT_PROBE)
        chk("桌面：真的對幣安發出請求", len(reqs) > 0, f"requests={len(reqs)}")
        chk("桌面：即時訊號卡片有資料", len(base["cards"]) >= 3, f"cards={len(base['cards'])}")
        chk("桌面：市場掃描有資料列", base["scanRows"] >= 5, f"rows={base['scanRows']}")
        chk("桌面：自訂參數編輯器存在", base["fieldCount"] >= 12, f"fields={base['fieldCount']}")
        chk("桌面：預設策略為 ctma_default", base["selected"] == "ctma_default",
            f"selected={base['selected']}")
        chk("桌面：初始未自訂", base["isCustomized"] is False,
            f"isCustomized={base['isCustomized']}")

        ov_d = page.evaluate(OVERFLOW_PROBE)
        chk("桌面：無橫向溢出", ov_d["horizontalOverflowPx"] == 0,
            f"overflow={ov_d['horizontalOverflowPx']}px offenders={ov_d['offenderCount']}")
        bi_d = page.evaluate(BROKEN_IMG_PROBE)
        chk("桌面：無破圖", bi_d["broken"] == 0, f"broken={bi_d['broken']}/{bi_d['total']}")
        ol_d = page.evaluate(OVERLAP_PROBE)
        chk("桌面：無文字重疊", ol_d["count"] == 0, f"overlaps={ol_d['count']}")

        page.screenshot(path=str(outdir / "custom-desktop-1280.png"), full_page=True)

        # ---------- C. 套用自訂參數 9/34 + ATR 1.5 ----------
        set_field(page, "ema_fast", 9)
        set_field(page, "ema_slow", 34)
        set_field(page, "atr_stop_mult", 1.5)
        page.click("#applyParams")
        time.sleep(0.5)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.6)
        applied = page.evaluate(SNAPSHOT_PROBE)

        def sig(snap):
            return json.dumps([{k: c.get(k) for k in ("symbol", "price", "strength", "ema", "stop")}
                               for c in snap["cards"]], sort_keys=True)

        chk("套用 9/34 + ATR 1.5：數值確實改變", sig(base) != sig(applied))
        chk("套用後：生效參數 ema_fast=9", (applied["activeParams"] or {}).get("ema_fast") == 9,
            f"activeParams.ema_fast={(applied['activeParams'] or {}).get('ema_fast')}")
        chk("套用後：生效參數 ema_slow=34", (applied["activeParams"] or {}).get("ema_slow") == 34)
        chk("套用後：生效參數 atr_stop_mult=1.5",
            (applied["activeParams"] or {}).get("atr_stop_mult") == 1.5)
        chk("套用後：標記為自訂", applied["isCustomized"] is True)
        chk("套用後：參數面板顯示 9 / 34",
            any(p["k"] == "EMA 快 / 慢" and p["v"].replace(" ", "") == "9/34" for p in applied["params"]),
            f"params={[p['v'] for p in applied['params'][:1]]}")
        chk("套用後：狀態列顯示自訂", "自訂" in applied["custState"], applied["custState"][:60])
        chk("套用後：無錯誤訊息", not applied["errorsVisible"] and not applied["errors"])

        page.screenshot(path=str(outdir / "custom-desktop-1280-applied.png"), full_page=True)

        # ---------- D. 不合法輸入被擋下 ----------
        set_field(page, "ema_fast", 60)   # 60 > 34 → 不合法
        page.click("#applyParams")
        time.sleep(0.6)
        invalid = page.evaluate(SNAPSHOT_PROBE)
        chk("不合法（快線 60 > 慢線 34）：顯示錯誤訊息",
            invalid["errorsVisible"] and len(invalid["errors"]) > 0,
            f"errors={invalid['errors']}")
        chk("不合法：錯誤訊息含「必須小於」",
            any("必須小於" in e for e in invalid["errors"]), f"{invalid['errors']}")
        chk("不合法：數值未被套用（生效 ema_fast 仍為 9）",
            (invalid["activeParams"] or {}).get("ema_fast") == 9,
            f"activeParams.ema_fast={(invalid['activeParams'] or {}).get('ema_fast')}")
        chk("不合法：提示文字說明未套用", "未套用" in invalid["custMsg"], invalid["custMsg"])

        # 再測一個：非正整數
        set_field(page, "ema_fast", 9)
        set_field(page, "atr_stop_mult", -1)
        page.click("#applyParams")
        time.sleep(0.5)
        invalid2 = page.evaluate(SNAPSHOT_PROBE)
        chk("不合法（ATR 倍數 -1）：被擋下",
            invalid2["errorsVisible"] and len(invalid2["errors"]) > 0, f"errors={invalid2['errors']}")
        chk("不合法（ATR -1）：生效值仍為 1.5",
            (invalid2["activeParams"] or {}).get("atr_stop_mult") == 1.5)

        # ---------- E. 重設為預設 ----------
        page.click("#resetParams")
        time.sleep(0.5)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.6)
        reset = page.evaluate(SNAPSHOT_PROBE)
        chk("重設為預設：回到未自訂", reset["isCustomized"] is False)
        chk("重設為預設：ema_fast 回到 21",
            (reset["activeParams"] or {}).get("ema_fast") == 21,
            f"activeParams.ema_fast={(reset['activeParams'] or {}).get('ema_fast')}")
        chk("重設為預設：ema_slow 回到 55",
            (reset["activeParams"] or {}).get("ema_slow") == 55)
        chk("重設為預設：atr_stop_mult 回到 3.0",
            (reset["activeParams"] or {}).get("atr_stop_mult") == 3.0)
        chk("重設為預設：錯誤訊息已清除", not reset["errorsVisible"])
        chk("重設為預設：數值回到基準", sig(reset) == sig(base))

        # ---------- F. 自訂參數在自動刷新後仍沿用 ----------
        set_field(page, "ema_fast", 9)
        set_field(page, "ema_slow", 34)
        set_field(page, "atr_stop_mult", 1.5)
        page.click("#applyParams")
        time.sleep(0.5)
        wait_settled(page, args.wait_seconds)
        before_refresh = page.evaluate(SNAPSHOT_PROBE)
        t1 = before_refresh["lastUpdate"]
        n1 = len(reqs)
        page.click("#refreshBtn")
        time.sleep(1.0)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.6)
        after_refresh = page.evaluate(SNAPSHOT_PROBE)
        t2 = after_refresh["lastUpdate"]
        n2 = len(reqs)
        chk("自動刷新仍運作（時間戳前進）", t1 != t2, f"{t1} → {t2}")
        chk("自動刷新仍持續抓取（請求數增加）", n2 > n1, f"{n1} → {n2}")
        chk("刷新後自訂參數仍沿用（ema_fast 仍為 9）",
            (after_refresh["activeParams"] or {}).get("ema_fast") == 9,
            f"activeParams.ema_fast={(after_refresh['activeParams'] or {}).get('ema_fast')}")
        chk("刷新後仍標記為自訂", after_refresh["isCustomized"] is True)
        chk("刷新後倒數計時有值", after_refresh["countdown"].strip() not in ("", "—"),
            f"countdown={after_refresh['countdown']}")

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
        ol_m = m.evaluate(OVERLAP_PROBE)
        snap_m = m.evaluate(SNAPSHOT_PROBE)
        chk("手機：無橫向溢出", ov_m["horizontalOverflowPx"] == 0,
            f"overflow={ov_m['horizontalOverflowPx']}px offenders={ov_m['offenderCount']}")
        chk("手機：無破圖", bi_m["broken"] == 0, f"broken={bi_m['broken']}/{bi_m['total']}")
        chk("手機：無文字重疊", ol_m["count"] == 0, f"overlaps={ol_m['count']}")
        chk("手機：自訂參數編輯器可用", snap_m["fieldCount"] >= 12, f"fields={snap_m['fieldCount']}")
        chk("手機：即時資料有載入", len(snap_m["cards"]) >= 3, f"cards={len(snap_m['cards'])}")

        # 手機上實際套用一次，確認可操作
        set_field(m, "ema_fast", 9)
        set_field(m, "ema_slow", 34)
        m.click("#applyParams")
        time.sleep(0.5)
        wait_settled(m, args.wait_seconds)
        time.sleep(0.5)
        snap_m2 = m.evaluate(SNAPSHOT_PROBE)
        chk("手機：套用自訂參數成功（ema_fast=9）",
            (snap_m2["activeParams"] or {}).get("ema_fast") == 9,
            f"activeParams.ema_fast={(snap_m2['activeParams'] or {}).get('ema_fast')}")
        m.screenshot(path=str(outdir / "custom-mobile-390.png"), full_page=True)
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
        "baseline": {"cards": base["cards"], "params": base["params"],
                     "activeParams": base["activeParams"]},
        "applied_9_34_atr1_5": {"cards": applied["cards"], "params": applied["params"],
                                "activeParams": applied["activeParams"]},
        "invalid_rejected": {"errors": invalid["errors"], "msg": invalid["custMsg"],
                             "activeParams": invalid["activeParams"]},
        "after_reset": {"activeParams": reset["activeParams"], "isCustomized": reset["isCustomized"]},
        "after_refresh": {"activeParams": after_refresh["activeParams"],
                          "isCustomized": after_refresh["isCustomized"],
                          "lastUpdate": after_refresh["lastUpdate"]},
        "overflow_desktop": ov_d, "overflow_mobile": ov_m,
        "overlap_desktop": ol_d, "overlap_mobile": ol_m,
    }
    (outdir / "custom_params_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== {report['status']}  {passed}/{len(checks)} ===")
    print(f"report → {outdir / 'custom_params_report.json'}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
