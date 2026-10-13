#!/usr/bin/env python3
"""visual_strength.py — 視覺檢查用的裁切截圖（策略列+卡片、以及展開後的強度公式區塊）。

策略選擇列與強度公式區塊都在頁面上方/中段，全頁截圖過長，故裁切：
  1. 策略列 + 即時卡片（裁切，桌面）
  2. 展開「訊號強度計分」details 後，僅截該區塊（元素截圖）
分別在 bb_reversion 與 keltner 各拍一次，用來肉眼確認
「強度評分」欄位文字與公式文字都正確、無截斷、無重疊。
"""
import argparse, time
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", default="verify")
    args = ap.parse_args()
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = b.new_page(viewport={"width": 1280, "height": 900})
        page.goto(args.url, wait_until="domcontentloaded")
        # 等刷新完成
        for _ in range(90):
            try:
                busy = page.evaluate("() => window.__CTMA_DASH__ ? window.__CTMA_DASH__.state.busy : true")
                rows = page.evaluate("() => document.querySelectorAll('#scanBody tr').length")
                if not busy and rows > 1:
                    break
            except Exception:
                pass
            time.sleep(1)

        for pid in ("bb_reversion", "keltner"):
            page.select_option("#strategySel", pid)
            time.sleep(0.4)
            for _ in range(60):
                try:
                    if not page.evaluate("() => window.__CTMA_DASH__ ? window.__CTMA_DASH__.state.busy : true"):
                        break
                except Exception:
                    pass
                time.sleep(1)
            time.sleep(1.0)
            # 1) 策略列 + 卡片：裁切 y=0..980
            page.screenshot(path=str(outdir / f"vis-{pid}-top.png"),
                            clip={"x": 0, "y": 0, "width": 1280, "height": 980})
            # 2) 展開強度公式 details，只截該區塊
            page.evaluate("""() => {
                const s = document.getElementById('stDocSummary');
                if (s) { const d = s.closest('details'); if (d) d.open = true; s.scrollIntoView({block:'center'}); }
            }""")
            time.sleep(0.6)
            el = page.query_selector("#stDocSummary")
            if el:
                d = page.evaluate_handle("() => document.getElementById('stDocSummary').closest('details')")
                d.as_element().screenshot(path=str(outdir / f"vis-{pid}-formula.png"))
        b.close()


if __name__ == "__main__":
    main()
