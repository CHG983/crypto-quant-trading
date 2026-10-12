#!/usr/bin/env python3
"""verify_live_dashboard.py — 以真實 Chromium 驗證「瀏覽器直連即時版」儀表板。

驗證項目（全部以真實瀏覽器執行，非模擬）
-------------------------------------
 A. 桌面 1280×800 開啟頁面，確認**真的抓到幣安資料**（不是內嵌快照）
 B. 桌面截圖 + 橫向溢出量測
 C. 手機 390×844 開啟、截圖、溢出量測
 D. 記錄「最後更新時間」與 BTC 價格 → 等待 / 手動刷新 → 再記錄，
    證明**自動刷新真的會更新數值**（時間戳必變，價格則視市場是否變動）
 E. 破圖檢查（naturalWidth == 0 的 <img> 數量）
 F. 免責聲明與資料性質標示存在性

輸出：live_web/verify/dashboard_live_report.json + 截圖 PNG

用法：
    python3 verify_live_dashboard.py --url http://127.0.0.1:8787/dashboard.html
    python3 verify_live_dashboard.py --url ... --wait-seconds 40
"""
from __future__ import annotations

import argparse
import json
import re
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
  const all = document.querySelectorAll('body *');
  for (const el of all) {
    const cs = getComputedStyle(el);
    // 跳過本來就設計為可橫向捲動之容器內的元素
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
      offenders.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className && String(el.className).slice(0, 70)) || '',
        left: Math.round(r.left), right: Math.round(r.right)
      });
    }
  }
  return {
    docScrollWidth: de.scrollWidth,
    docClientWidth: de.clientWidth,
    innerWidth: vw,
    horizontalOverflowPx: Math.max(0, de.scrollWidth - de.clientWidth),
    offenders: offenders.slice(0, 12),
    offenderCount: offenders.length
  };
}
"""

BROKEN_IMG_PROBE = """
() => {
  const imgs = Array.from(document.querySelectorAll('img'));
  const broken = imgs.filter(i => i.complete && i.naturalWidth === 0);
  return { total: imgs.length, broken: broken.length,
           brokenSrc: broken.slice(0,5).map(i => i.getAttribute('src')) };
}
"""

SNAPSHOT_PROBE = """
() => {
  const q = (s) => { const e = document.querySelector(s); return e ? e.textContent : null; };
  const cards = Array.from(document.querySelectorAll('#liveCards .card'));
  const first = cards.length ? cards[0].textContent : null;
  const m = first ? first.match(/BTCUSDT/) : null;
  // 抓第一張卡的全部數字，作為「值是否變化」的指紋
  const nums = first ? (first.match(/-?[\\d,]+\\.?\\d*/g) || []).slice(0, 30) : [];
  return {
    statusText: q('#statusText'),
    lastUpdate: q('#lastUpdate'),
    countdown: q('#countdown'),
    liveMeta: q('#liveMeta'),
    scanMeta: q('#scanMeta'),
    fetchNote: q('#fetchNoteText'),
    cardCount: cards.length,
    scanRowCount: document.querySelectorAll('#scanBody tr').length,
    timelineCount: document.querySelectorAll('#monTimeline li').length,
    trackedRowCount: document.querySelectorAll('#monBody tr').length,
    firstCardText: first,
    firstCardNums: nums,
    hasDisclaimer: document.body.textContent.includes('投資免責聲明'),
    hasEnglishDisclaimer: document.body.textContent.includes('All content on this page (including real-time signals, ranking scans, monitored events, and parameters) is intended solely for research and educational purposes'),
    hasEnglishDisclaimerTail: document.body.textContent.includes('Users are responsible for making their own trading decisions and assuming all associated risks.'),
    hasOldEnglishDisclaimer: document.body.textContent.includes('As an AI Agent, I cannot provide legally protected'),
    hasLiveSection: document.body.textContent.includes('即時訊號面板'),
    hasHistoryNotice: document.body.textContent.includes('本區塊為歷史資料（非即時）'),
    hasParity: (document.querySelector('#parityText') || {}).textContent || null,
    engineVersion: (window.CTMA && window.CTMA.VERSION) || null,
    lastLive: (window.__CTMA_DASH__ && window.__CTMA_DASH__.lastLive) || null,
    lastScanMeta: (window.__CTMA_DASH__ && window.__CTMA_DASH__.lastScanMeta) || null
  };
}
"""


def probe(page):
    return {
        "snapshot": page.evaluate(SNAPSHOT_PROBE),
        "overflow": page.evaluate(OVERFLOW_PROBE),
        "images": page.evaluate(BROKEN_IMG_PROBE),
    }


def wait_live(page, timeout_ms=45000):
    """等到即時卡片出現（>0 張）或狀態顯示失敗。"""
    page.wait_for_function(
        "() => document.querySelectorAll('#liveCards .card').length > 0",
        timeout=timeout_ms,
    )


def wait_settled(page, timeout_ms=90000):
    """等到**整輪刷新完成**：狀態離開『抓取中…』且掃描表格已渲染。

    為什麼需要：即時卡片（3 檔）先完成，市場掃描（25 檔、併發 6）還在跑。
    若在掃描中途量測，會誤判為「狀態異常／表格未渲染」——那是探針太早，
    不是頁面缺陷。此函式讓量測對齊「一輪結束」的穩定狀態。
    """
    try:
        page.wait_for_function(
            "() => {"
            "  const s = document.getElementById('statusText');"
            "  if (!s) return false;"
            "  const t = s.textContent || '';"
            "  const settled = t !== '抓取中…' && t !== '初始化中…';"
            "  const rows = document.querySelectorAll('#scanBody tr').length;"
            "  const hasData = document.querySelector('#scanBody tr td.muted') === null;"
            "  return settled && rows > 1 && hasData;"
            "}",
            timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787/dashboard.html")
    ap.add_argument("--outdir", default="verify")
    ap.add_argument("--wait-seconds", type=int, default=38,
                    help="首次與第二次觀測之間的等待秒數（需大於自動刷新間隔）")
    ap.add_argument("--refresh-mode", choices=["auto", "button"], default="button",
                    help="用等自動刷新或用『立即刷新』按鈕觸發第二次觀測")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "checks": [],
    }

    def check(name, ok, detail=None):
        report["checks"].append({"name": name, "passed": bool(ok), "detail": detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return bool(ok)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])

        # ---------------- 桌面 1280×800 ----------------
        print("\n=== 桌面 1280×800 ===")
        ctx = browser.new_context(viewport={"width": 1280, "height": 800},
                                  device_scale_factor=1)
        page = ctx.new_page()
        req_log, resp_log, failed = [], [], []
        page.on("request", lambda r: req_log.append(r.url))
        page.on("response", lambda r: resp_log.append((r.url, r.status)))
        page.on("requestfailed",
                lambda r: failed.append(f"{r.url} :: {r.failure}"))

        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        try:
            wait_live(page, 45000)
            live_ok = True
        except Exception as exc:  # noqa: BLE001
            live_ok = False
            print(f"  [warn] 等待即時卡片逾時：{exc}")

        # 等「整輪刷新」結束（含 25 檔市場掃描）再量測，避免量到中途狀態
        settled1 = wait_settled(page)
        report["desktop_settled"] = settled1
        if not settled1:
            print("  [warn] 首輪未在時限內完成掃描")
        time.sleep(2.0)  # 讓倒數與最後更新時間穩定
        desk1 = probe(page)
        page.screenshot(path=str(outdir / "live-desktop-1280.png"), full_page=True)

        binance_reqs = [u for u in req_log if "binance" in u]
        kline_reqs = [u for u in binance_reqs if "/api/v3/klines" in u]
        ticker_reqs = [u for u in binance_reqs if "/api/v3/ticker/24hr" in u]
        report["desktop_first"] = desk1
        report["network"] = {
            "total_requests": len(req_log),
            "binance_requests": len(binance_reqs),
            "klines_requests": len(kline_reqs),
            "ticker24hr_requests": len(ticker_reqs),
            "failed_requests": failed[:10],
            "sample_urls": binance_reqs[:4],
        }

        check("桌面：頁面成功載入並出現即時卡片",
              live_ok and desk1["snapshot"]["cardCount"] >= 3,
              f"cards={desk1['snapshot']['cardCount']}")
        check("桌面：確實對幣安端點發出請求（證明即時抓取）",
              len(kline_reqs) >= 3,
              f"klines={len(kline_reqs)} ticker24hr={len(ticker_reqs)}")
        check("桌面：狀態顯示即時資料正常",
              desk1["snapshot"]["statusText"] == "即時資料正常",
              f"status={desk1['snapshot']['statusText']}")
        check("桌面：無橫向溢出",
              desk1["overflow"]["horizontalOverflowPx"] == 0,
              f"overflow={desk1['overflow']['horizontalOverflowPx']}px "
              f"offenders={desk1['overflow']['offenderCount']}")
        check("桌面：無破圖",
              desk1["images"]["broken"] == 0,
              f"{desk1['images']['broken']}/{desk1['images']['total']}")
        check("桌面：中文免責聲明存在", desk1["snapshot"]["hasDisclaimer"])
        check("桌面：新英文免責聲明開頭逐字存在",
              desk1["snapshot"]["hasEnglishDisclaimer"],
              "All content on this page (including real-time signals, …)")
        check("桌面：新英文免責聲明結尾逐字存在",
              desk1["snapshot"]["hasEnglishDisclaimerTail"],
              "…assuming all associated risks.")
        check("桌面：舊英文免責聲明已移除",
              not desk1["snapshot"]["hasOldEnglishDisclaimer"],
              "As an AI Agent … 應已不存在")
        check("桌面：歷史區塊標示存在", desk1["snapshot"]["hasHistoryNotice"])
        check("桌面：掃描表格已渲染",
              desk1["snapshot"]["scanRowCount"] >= 10,
              f"rows={desk1['snapshot']['scanRowCount']}")
        check("桌面：歷史時間軸已渲染",
              desk1["snapshot"]["timelineCount"] >= 10,
              f"items={desk1['snapshot']['timelineCount']}")

        # ---- 內部一致性：顯示的停損必須依「顯示的價格」與 ATR 算出 ----
        ll = desk1["snapshot"].get("lastLive") or []
        if ll:
            bad = []
            for x in ll:
                if x.get("stop") is None or x.get("atr") is None:
                    continue
                exp = x["price"] - x["stop_mult"] * x["atr"] if x["trend"] > 0 \
                    else x["price"] + x["stop_mult"] * x["atr"]
                rel = abs(exp - x["stop"]) / max(abs(x["price"]), 1e-9)
                if rel > 1e-9:
                    bad.append({"symbol": x["symbol"], "expected": exp, "got": x["stop"]})
            report["stop_consistency"] = {"checked": len(ll), "mismatches": bad}
            check("桌面：顯示的停損與價格/ATR 內部一致（無口徑混用）",
                  not bad,
                  f"checked={len(ll)} mismatches={len(bad)}"
                  + (f" {bad[:2]}" if bad else ""))
        else:
            check("桌面：結構化即時資料可讀取", False, "lastLive 為空")

        # ---------------- 自動刷新驗證 ----------------
        print(f"\n=== 自動刷新驗證（{args.refresh_mode}，等待 {args.wait_seconds}s）===")
        t1 = desk1["snapshot"]["lastUpdate"]
        n1 = desk1["snapshot"]["firstCardNums"]
        ll1 = desk1["snapshot"].get("lastLive") or []
        lp1 = [(x["symbol"], x.get("live_price")) for x in ll1]
        time.sleep(args.wait_seconds)
        if args.refresh_mode == "button":
            try:
                page.click("#refreshBtn")
            except Exception as exc:  # noqa: BLE001
                print(f"  [warn] 點擊刷新失敗：{exc}")
        # 等第二輪刷新完成（含掃描）再量測
        settled2 = wait_settled(page)
        report["desktop_second_settled"] = settled2
        time.sleep(2.5)
        desk2 = probe(page)
        page.screenshot(path=str(outdir / "live-desktop-1280-refresh.png"), full_page=True)

        t2 = desk2["snapshot"]["lastUpdate"]
        n2 = desk2["snapshot"]["firstCardNums"]
        ll2 = desk2["snapshot"].get("lastLive") or []
        lp2 = [(x["symbol"], x.get("live_price")) for x in ll2]
        cp1 = [(x["symbol"], x.get("price")) for x in ll1]
        cp2 = [(x["symbol"], x.get("price")) for x in ll2]
        report["desktop_second"] = desk2
        report["refresh"] = {
            "last_update_before": t1, "last_update_after": t2,
            "nums_before": n1, "nums_after": n2,
            "numbers_changed": n1 != n2,
            "timestamp_changed": t1 != t2,
            "live_price_before": lp1, "live_price_after": lp2,
            "live_price_changed": lp1 != lp2,
            "confirmed_price_before": cp1, "confirmed_price_after": cp2,
            "confirmed_price_changed": cp1 != cp2,
            "confirmed_price_note": (
                "已收盤日線收盤價在該交易日內不變，故兩次刷新相同屬預期行為；"
                "真正會變動的是未收盤 K 棒的 live_price 與時間戳。"
            ),
        }

        check("刷新：最後更新時間確實變動", t1 != t2, f"{t1} → {t2}")
        check("刷新：第二次仍抓到即時資料",
              desk2["snapshot"]["statusText"] == "即時資料正常"
              and desk2["snapshot"]["cardCount"] >= 3)
        check("刷新：數值指紋有變化", n1 != n2,
              "changed" if n1 != n2 else "identical")
        check("刷新：即時（未收盤）價格確實隨市價變動", lp1 != lp2,
              f"{lp1} → {lp2}")
        report["checked_confirmed_stability"] = cp1 == cp2
        check("刷新：請求次數增加（證明重新抓取而非重繪快取）",
              len([u for u in req_log if "binance" in u]) > len(binance_reqs),
              f"{len(binance_reqs)} → {len([u for u in req_log if 'binance' in u])}")
        check("刷新後：無橫向溢出",
              desk2["overflow"]["horizontalOverflowPx"] == 0,
              f"overflow={desk2['overflow']['horizontalOverflowPx']}px")

        # ---------------- 手機 390×844 ----------------
        print("\n=== 手機 390×844 ===")
        mctx = browser.new_context(viewport={"width": 390, "height": 844},
                                   device_scale_factor=2, is_mobile=True,
                                   has_touch=True)
        mpage = mctx.new_page()
        mpage.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        try:
            wait_live(mpage, 45000)
            mlive = True
        except Exception:  # noqa: BLE001
            mlive = False
        msettled = wait_settled(mpage)
        report["mobile_settled"] = msettled
        time.sleep(2.0)
        mob = probe(mpage)
        mpage.screenshot(path=str(outdir / "live-mobile-390.png"), full_page=True)
        report["mobile"] = mob

        check("手機：出現即時卡片", mlive and mob["snapshot"]["cardCount"] >= 3,
              f"cards={mob['snapshot']['cardCount']}")
        check("手機：無橫向溢出",
              mob["overflow"]["horizontalOverflowPx"] == 0,
              f"overflow={mob['overflow']['horizontalOverflowPx']}px "
              f"offenders={mob['overflow']['offenderCount']}")
        check("手機：無破圖", mob["images"]["broken"] == 0,
              f"{mob['images']['broken']}/{mob['images']['total']}")
        check("手機：狀態正常",
              mob["snapshot"]["statusText"] == "即時資料正常",
              f"status={mob['snapshot']['statusText']}")
        check("手機：中文免責聲明存在", mob["snapshot"]["hasDisclaimer"])
        check("手機：新英文免責聲明逐字存在",
              mob["snapshot"]["hasEnglishDisclaimer"]
              and mob["snapshot"]["hasEnglishDisclaimerTail"])
        check("手機：舊英文免責聲明已移除",
              not mob["snapshot"]["hasOldEnglishDisclaimer"])

        # 直連是否有 CORS 錯誤
        cors_err = [f for f in failed if "binance" in f]
        report["cors"] = {"failed_binance_requests": cors_err,
                          "mode": "direct" if not cors_err else "proxy-needed"}
        check("CORS：瀏覽器可直連幣安（無失敗請求）", len(cors_err) == 0,
              f"failed={len(cors_err)}")

        ctx.close()
        mctx.close()
        browser.close()

    passed = sum(1 for c in report["checks"] if c["passed"])
    total = len(report["checks"])
    report["summary"] = {"passed": passed, "total": total,
                         "status": "ALL PASS" if passed == total else "HAS FAILURES"}
    (outdir / "dashboard_live_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== SUMMARY: {passed}/{total} PASS ({report['summary']['status']}) ===")
    print(f"[report] {outdir/'dashboard_live_report.json'}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
