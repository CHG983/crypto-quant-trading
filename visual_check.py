#!/usr/bin/env python3
"""Visual Check：本地伺服器 + 硬性斷言 + 桌面/手機截圖。"""
import json, subprocess, sys, time
from playwright.sync_api import sync_playwright

PORT = 8099
ROOT = "/workspace/webpages/crypto-quant-report_v3"
OUT = "/workspace/documents/crypto-quant_v3"

ASSERT_JS = """() => {
  const imgs = [...document.querySelectorAll('img')];
  const broken = imgs.filter(i => !i.complete || i.naturalWidth === 0).map(i => i.getAttribute('src'));
  const de = document.documentElement;
  const overflow = de.scrollWidth - de.clientWidth;
  const vw = window.innerWidth;
  // 排除「位於可橫向捲動容器內」的元素 —— 那是刻意的設計，不是版面破損
  const inScroller = el => {
    let p = el.parentElement;
    while (p && p !== document.body) {
      const ox = getComputedStyle(p).overflowX;
      if (ox === 'auto' || ox === 'scroll') return true;
      p = p.parentElement;
    }
    return false;
  };
  const over = [...document.querySelectorAll('body *')].filter(el => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0) return false;
    if (r.right <= vw + 1 && r.left >= -1) return false;
    return !inScroller(el);
  }).slice(0, 8).map(el => el.tagName + '.' + (el.className || '').toString().slice(0, 30));
  return {
    title: document.title,
    href: location.href,
    imgTotal: imgs.length,
    imgBroken: broken,
    docOverflow: overflow,
    innerW: vw, innerH: window.innerHeight,
    tables: document.querySelectorAll('table').length,
    figures: document.querySelectorAll('figure').length,
    preBlocks: document.querySelectorAll('pre').length,
    overflowing: over,
    disclaimer: document.body.innerText.includes('投資免責聲明'),
    source: document.body.innerText.includes('data-api.binance.vision'),
    hasS12: !!document.querySelector('#s12'),
  };
}"""

def main():
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    results = {}
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--no-sandbox"])
            for label, w, h in [("desktop", 1280, 900), ("mobile", 390, 844)]:
                pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
                pg.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="networkidle", timeout=60000)
                pg.evaluate("document.documentElement.style.scrollBehavior='auto'")
                pg.wait_for_timeout(1200)
                a = pg.evaluate(ASSERT_JS)
                results[label] = a
                print(f"\n=== {label} ({w}x{h}) ===")
                print(json.dumps(a, ensure_ascii=False, indent=2))
                pg.screenshot(path=f"{OUT}/verify-{label}-{w}.png", full_page=False)
                # 免責聲明區塊特寫
                pg.evaluate("document.querySelector('.disc').scrollIntoView({block:'center'})")
                pg.wait_for_timeout(500)
                pg.screenshot(path=f"{OUT}/verify-{label}-disclaimer.png", full_page=False)
                # WFO 雙模式對照章節特寫（s12）
                pg.evaluate("document.querySelector('#s12').scrollIntoView({block:'start'})")
                pg.wait_for_timeout(500)
                pg.screenshot(path=f"{OUT}/verify-{label}-s12.png", full_page=False)
                pg.close()
            b.close()
    finally:
        srv.terminate()

    print("\n=== 判定 ===")
    ok = True
    for label, a in results.items():
        checks = [
            ("頁面識別 (title)", "加密貨幣" in a["title"]),
            ("圖片全部解碼", len(a["imgBroken"]) == 0 and a["imgTotal"] >= 6),
            ("無橫向溢出", a["docOverflow"] == 0),
            ("無元素超出視窗", len(a["overflowing"]) == 0),
            ("表格存在", a["tables"] >= 8),
            ("圖表存在", a["figures"] >= 6),
            ("WFO 章節存在", a.get("hasS12", False)),
            ("免責聲明存在", a["disclaimer"]),
            ("資料來源標示存在", a["source"]),
        ]
        for name, passed in checks:
            print(f"  [{label}] {'PASS' if passed else 'FAIL'} {name}")
            if not passed:
                ok = False
    print("\n結果:", "ALL PASS" if ok else "HAS FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
