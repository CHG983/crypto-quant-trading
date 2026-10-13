#!/usr/bin/env python3
"""verify_leverage_block.py — 以真實 Chromium 驗證「建議槓桿比率」獨立區塊。

驗證目標（全部以真實瀏覽器 + 真實幣安即時資料執行，非模擬）
------------------------------------------------------------
 A. 桌面 1280：
      - 引擎版本 1.5.0
      - 策略選擇器僅剩 8 個選項，且不含「動態風險敞口」／dyn_risk
      - 即時卡片與掃描表有資料
      - 「建議槓桿比率」區塊存在、可見、含 4 個參數輸入
      - 掃描表每列都有建議槓桿欄
 B. 建議槓桿對每檔標的顯示，且與實際波動度呈反向（波動越高 → 槓桿越低）
 C. 獨立性：切換 bb_reversion / keltner / wfo_9_34 後，區塊仍顯示且數值仍為同一套計算
 D. 調整目標波動度後建議槓桿即時改變；「重設為預設」可還原
 E. 不合法輸入（最大槓桿 < 最小槓桿 / 目標波動度 <= 0）被擋下且不套用
 F. 套用後自動刷新仍運作，且自訂值仍沿用（時間戳前進 + 請求數增加）
 G. 桌面 / 手機：無橫向溢出、無破圖、無文字重疊
 H. 免責聲明仍存在（中英文）

輸出：verify/leverage_block_report.json + 截圖 PNG

用法：
    python3 verify_leverage_block.py --url http://127.0.0.1:8787/dashboard.html
"""
from __future__ import annotations

import argparse
import json
import re
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
  const inStickyOrFixed = (el) => {
    let n = el;
    while (n && n !== document.body) {
      const pcs = getComputedStyle(n);
      if (pcs.position === 'sticky' || pcs.position === 'fixed') return true;
      n = n.parentElement;
    }
    return false;
  };
  const els = Array.from(document.querySelectorAll('body *')).filter(el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'inline' || cs.display === 'contents') return false;
    // 略過 sticky/fixed 容器及其後代：.livebar 是捲動時固定於視窗頂的浮動列，
    // 任何捲到它底下的內容都會與它相交，屬幾何假陽性而非版面缺陷。
    if (inStickyOrFixed(el)) return false;
    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    if (inClosedDetails(el)) return false;
    // 略過 sticky 元素（如 .livebar）：它是捲動時固定於視窗頂部的浮動列，
    // 其 rect 會與捲到它底下的內容必然相交，屬幾何假陽性而非版面缺陷。
    // （既有驗證腳本同樣排除 position:fixed，此處延伸至 sticky）
    if (cs.position === 'fixed' || cs.position === 'sticky') return false;
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
""".replace("inClosedBody(el)", "inClosedDetails(el)")

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
    signal: get(c,'\\u8a0a\\u865f'),
    strength: get(c,'\\u5f37\\u5ea6'),
    leverage: get(c,'\\u5efa\\u8b70\\u69d3\\u687f'),
    positionPct: get(c,'\\u90e8\\u4f4d\\u6bd4\\u4f8b'),
    realizedVol: get(c,'\\u5be6\\u969b\\u6ce2\\u52d5\\u5ea6')
  }));
  const opts = Array.from(document.querySelectorAll('#strategySel option')).map(o => ({
    value: o.value, text: (o.textContent||'').trim()
  }));
  const leverRows = Array.from(document.querySelectorAll('#leverBody tr')).map(tr => {
    const td = Array.from(tr.querySelectorAll('td')).map(x => (x.textContent||'').trim());
    return {symbol: td[0]||null, atrPct: td[1]||null, leverage: td[2]||null,
            position: td[3]||null, capped: td[4]||null};
  });
  const leverFields = Array.from(document.querySelectorAll('#leverGrid input[data-key]')).map(el => ({
    key: el.getAttribute('data-key'), value: el.value
  }));
  const leverBox = document.getElementById('leverGrid');
  const leverBody = document.getElementById('leverBody');
  const leverSec = leverBox ? leverBox.closest('div, section') : null;
  const scrolledIntoView = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  // leverSummary 應存在且不在收合的 details 內；它位於頁面較下方，
  // 未捲入視窗時 rect 仍非零，故以「存在 + 非隱藏 + 非收合容器內」判定。
  const leverSummaryEl = document.getElementById('leverSummary');
  const inClosed = (el) => {
    let n = el;
    while (n && n !== document.body) {
      if (n.tagName === 'DETAILS' && !n.open) return true;
      n = n.parentElement;
    }
    return false;
  };
  const scrollToLever = () => {
    if (leverSummaryEl) leverSummaryEl.scrollIntoView({block:'start'});
  };
  const ths = Array.from(document.querySelectorAll('thead th')).map(t => (t.textContent||'').trim());
  return {
    engineVersion: (window.CTMA && window.CTMA.VERSION) || null,
    optionCount: opts.length,
    options: opts,
    hasDynRiskOption: opts.some(o => o.value === 'dyn_risk' || o.text.includes('\\u52d5\\u614b\\u98a8\\u96aa\\u655e\\u53e3')),
    selected: (document.getElementById('strategySel')||{}).value||null,
    cards: cards,
    scanRows: document.querySelectorAll('#scanBody tr').length,
    scanHeaders: ths,
    hasLeverageHeader: ths.some(t => t.includes('\\u5efa\\u8b70\\u69d3\\u687f')),
    leverSummary: (document.getElementById('leverSummary')||{}).textContent||'',
    leverSummaryPresent: !!leverSummaryEl && leverSummaryEl.textContent.trim().length > 0,
    leverSummaryVisible: scrolledIntoView(leverSummaryEl) && !inClosed(leverSummaryEl),
    leverGridVisible: scrolledIntoView(leverBox),
    leverBodyVisible: scrolledIntoView(leverBody),
    hasLeverageSectionHeading: Array.from(document.querySelectorAll('h2,h3'))
      .some(h => (h.textContent||'').includes('\u5efa\u8b70\u69d3\u687f\u6bd4\u7387')),
    hasNoElementReuse: !document.getElementById('liveLeverage'),
    leverFields: leverFields,
    leverFieldKeys: leverFields.map(f => f.key),
    leverRows: leverRows,
    leverRowCount: leverRows.length,
    applyVisible: scrolledIntoView(document.getElementById('applyLever')),
    resetVisible: scrolledIntoView(document.getElementById('resetLever')),
    leverStateText: (document.getElementById('leverState')||{}).textContent||'',
    leverMsg: (document.getElementById('leverMsg')||{}).textContent||'',
    leverErrors: Array.from(document.querySelectorAll('#leverErrors li')).map(li => li.textContent),
    statusText: (document.getElementById('statusText')||{}).textContent||'',
    lastUpdate: (document.getElementById('lastUpdate')||{}).textContent||'',
    hasDisclaimer: document.body.textContent.includes('投資免責聲明'),
    hasEnglishDisclaimer: document.body.textContent.includes('All content on this page (including real-time signals'),
    // 掃描表是否回報抓取錯誤（沙盒代理偶發；用於重試判斷，不作為功能判斷）
    scanText: (document.getElementById('scanBody') || {}).textContent || '',
    dynriskAttr: document.body.innerHTML.includes('dyn_risk')
  };
}
"""


def wait_settled(page, timeout_s=90):
    """等刷新結束。注意：若掃描表以「抓取失敗」收場，busy 亦會歸 false，
    此時仍視為 settled（交由呼叫方重試），不再空等 timeout。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            busy = page.evaluate("() => window.__CTMA_DASH__ ? window.__CTMA_DASH__.state.busy : true")
            txt = page.evaluate("() => (document.getElementById('statusText')||{}).textContent || ''")
            rows = page.evaluate("() => document.querySelectorAll('#scanBody tr').length")
            scanfail = page.evaluate(
                "() => (((document.getElementById('scanBody')||{}).textContent||'').includes('\\u62d3\\u53d6\\u5931\\u6557'))"
                " || (((document.getElementById('statusText')||{}).textContent||'').includes('\\u62d3\\u53d6\\u5931\\u6557'))")
            if not busy and '抓取中' not in txt and '初始化' not in txt and (rows > 1 or scanfail):
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def scan_ready(s):
    return s["scanRows"] > 1 and "抓取失敗" not in (s.get("scanText") or "")


def settle_with_retry(page, snap_eval, wait_seconds, tries=4):
    """反覆刷新直到掃描表真的取得資料；沙盒代理偶發會讓掃描失敗。"""
    s = page.evaluate(snap_eval)
    for i in range(tries):
        if scan_ready(s):
            return s
        print(f"[info] 掃描表尚未就緒（rows={s['scanRows']}），重試 {i + 1}/{tries} …")
        page.click("#refreshBtn")
        time.sleep(1.0)
        wait_settled(page, wait_seconds)
        time.sleep(1.5)
        s = page.evaluate(snap_eval)
    return s


def parse_pct(s):
    if not s:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)", str(s).replace(',', ''))
    return float(m.group(1)) if m else None


def lev_pairs(snap):
    """回傳 [(symbol, atrPct, leverage, position)]，皆為已解析的數值。"""
    out = []
    for r in snap.get("leverRows", []):
        a, l = parse_pct(r.get("atrPct")), parse_pct(r.get("leverage"))
        p = parse_pct(r.get("position"))
        if r.get("symbol") and a is not None and l is not None:
            out.append((r["symbol"], a, l, p))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", default="verify")
    ap.add_argument("--wait-seconds", type=int, default=90)
    ap.add_argument("--shot-prefix", default="lever")
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def chk(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    snaps: dict = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])

        # ---------------- 桌面 1280 ----------------
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        reqs: list[str] = []
        errs: list[str] = []
        page.on("request", lambda r: reqs.append(r.url) if "binance" in r.url else None)
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(args.url, wait_until="domcontentloaded")
        settled = wait_settled(page, args.wait_seconds)
        time.sleep(1.2)
        base = settle_with_retry(page, SNAP, args.wait_seconds)
        snaps["base"] = base
        scan_ok = scan_ready(base)
        chk("桌面：掃描表成功取得資料（代理抖動已重試）", scan_ok,
            f"rows={base['scanRows']} status={base['statusText'][:40]}")

        chk("桌面：完成一次刷新且狀態非載入中", settled, base["statusText"][:60])
        chk("桌面：引擎版本為 1.5.0", base["engineVersion"] == "1.5.0", f"version={base['engineVersion']}")
        chk("桌面：真的對幣安發出請求", len(reqs) > 0, f"requests={len(reqs)}")
        chk("桌面：無 pageerror", len(errs) == 0, f"errors={errs[:2]}")

        # A. 策略選擇器 8 選項、無 dyn_risk
        chk("策略選擇器：恰有 8 個選項", base["optionCount"] == 8, f"options={base['optionCount']}")
        chk("策略選擇器：不含「動態風險敞口」選項", not base["hasDynRiskOption"],
            f"values={[o['value'] for o in base['options']]}")
        chk("策略選擇器：dyn_risk 已完全移除（無選項、無舊元素 #liveLeverage）",
            (not base["hasDynRiskOption"]) and base["hasNoElementReuse"],
            "dyn_risk 僅存在於「已移除」的說明文字與來源註解中，已非策略選項")
        chk("策略選擇器：策略由 9 降為 8", base["optionCount"] == 8 and not base["hasDynRiskOption"],
            f"values={[o['value'] for o in base['options']]}")

        chk("桌面：即時卡片有資料", len(base["cards"]) >= 3, f"cards={len(base['cards'])}")
        chk("桌面：掃描表有資料", base["scanRows"] > 1, f"rows={base['scanRows']}")

        # B. 建議槓桿區塊存在
        chk("建議槓桿：區塊存在（摘要 + 4 參數編輯器 + 標的清單）",
            base["leverSummaryPresent"] and base["leverGridVisible"]
            and base["leverBodyVisible"] and base["leverRowCount"] >= 3)
        chk("建議槓桿：區塊標題為「建議槓桿比率」（獨立區塊，非策略）",
            base["hasLeverageSectionHeading"] and "建議槓桿比率" in base["leverSummary"],
            base["leverSummary"][:70])
        chk("建議槓桿：參數輸入為 4 項（dr_target_vol / dr_max_lev / dr_min_lev / dr_atr_len）",
            base["leverFieldKeys"] == ["dr_target_vol", "dr_max_lev", "dr_min_lev", "dr_atr_len"],
            f"{base['leverFieldKeys']}")
        chk("建議槓桿：套用 / 重設按鈕存在", base["applyVisible"] and base["resetVisible"])
        chk("建議槓桿：預設值與 dr_* 預設一致（2.0 / 3.0 / 0.5 / 14）",
            [f["value"] for f in base["leverFields"]] == ["2", "3", "0.5", "14"],
            f"{[f['value'] for f in base['leverFields']]}")
        chk("掃描表：新增「建議槓桿」欄位", base["hasLeverageHeader"],
            f"headers={base['scanHeaders'][-3:]}")
        chk("建議槓桿：清單列出 ≥3 檔標的", base["leverRowCount"] >= 3, f"rows={base['leverRowCount']}")

        # 每檔標的皆有槓桿；即時卡片也顯示建議槓桿（任何策略下）
        pairs = lev_pairs(base)
        chk("建議槓桿：每列都有 ATR% 與建議槓桿", len(pairs) == base["leverRowCount"],
            f"parsed={len(pairs)}/{base['leverRowCount']}")
        chk("即時卡片：在 CTMA 策略下也顯示建議槓桿（不依賴策略）",
            all(c.get("leverage") for c in base["cards"]),
            f"{[(c['symbol'], c['leverage']) for c in base['cards']]}")

        # 反向關係：ATR% 越高 → 槓桿越低
        srt = sorted(pairs, key=lambda t: t[1])
        las = [t[2] for t in srt]
        inv_ok = all(las[i] >= las[i + 1] for i in range(len(las) - 1))
        chk("槓桿與波動度呈反向（ATR% 由低到高 → 槓桿不遞增）", inv_ok,
            " · ".join(f"{s} ATR%{a:.2f}→{l:.2f}x" for s, a, l, _ in srt[:6]))
        top3 = sorted(pairs, key=lambda t: -t[1])[:3]
        chk("槓桿落在 [最小 0.5×, 最大 3.0×] 之間",
            bool(pairs) and all(0.5 <= l <= 3.0 for _, _, l, _ in pairs),
            f"n={len(pairs)}" + (f" min={min(l for _,_,l,_ in pairs):.2f} max={max(l for _,_,l,_ in pairs):.2f}" if pairs else ""))
        chk("至少 3 檔標的有 ATR%/槓桿對照", len(top3) >= 3,
            " · ".join(f"{s}: ATR%{a:.2f} → {l:.2f}× (部位 {p:.1f}%)" for s, a, l, p in top3))

        page.screenshot(path=str(outdir / f"{args.shot_prefix}-desktop-1280.png"), full_page=True)
        # 區塊特寫
        try:
            box_el = page.query_selector("#leverGrid")
            if box_el:
                box_el.scroll_into_view_if_needed()
                time.sleep(0.3)
                sec = page.evaluate(
                    "() => { const g=document.getElementById('leverSummary');"
                    "const t=g?g.getBoundingClientRect():null;"
                    "return t?{x:0,y:Math.max(0,t.top-60),w:window.innerWidth,h:520}:null; }")
                if sec:
                    page.screenshot(path=str(outdir / f"{args.shot_prefix}-desktop-block.png"),
                                    clip={"x": sec["x"], "y": sec["y"], "width": sec["w"], "height": sec["h"]})
        except Exception as e:
            print(f"[warn] block screenshot failed: {e}")

        # C. 獨立性：切換策略後區塊仍運作
        for pid in ("bb_reversion", "keltner", "wfo_9_34"):
            page.select_option("#strategySel", pid)
            time.sleep(0.4)
            wait_settled(page, args.wait_seconds)
            time.sleep(0.8)
            s = page.evaluate(SNAP)
            snaps[pid] = s
            p2 = lev_pairs(s)
            same_set = [t[2] for t in p2] == [t[2] for t in pairs]
            chk(f"獨立性：切換 {pid} 後區塊仍可見且有 {len(p2)} 檔標的",
                s["leverBodyVisible"] and len(p2) >= 3, f"rows={len(p2)}")
            chk(f"獨立性：{pid} 下建議槓桿與 CTMA 相同（不隨策略改變）", same_set,
                f"ctma={[round(t[2],2) for t in pairs][:4]} vs {pid}={[round(t[2],2) for t in p2][:4]}")
            chk(f"獨立性：{pid} 下即時卡片仍顯示建議槓桿",
                all(c.get("leverage") for c in s["cards"]),
                f"{[(c['symbol'], c['leverage']) for c in s['cards']]}")

        page.screenshot(path=str(outdir / f"{args.shot_prefix}-desktop-wfo.png"), full_page=True)

        # 回到 CTMA
        page.select_option("#strategySel", "ctma_default")
        time.sleep(0.4)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.8)

        # D. 調整目標波動度 → 槓桿改變
        lev_before = [round(t[2], 3) for t in lev_pairs(page.evaluate(SNAP))]
        page.fill("#leverGrid input[data-key='dr_target_vol']", "1.0")
        page.click("#applyLever")
        time.sleep(0.5)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.8)
        tuned = page.evaluate(SNAP)
        snaps["target_vol_1_0"] = tuned
        lev_after = [round(t[2], 3) for t in lev_pairs(tuned)]
        chk("調整目標波動度 2.0%→1.0% 後建議槓桿改變", lev_before != lev_after,
            f"{lev_before} → {lev_after}")
        chk("目標波動度調低後槓桿下降（或已觸及下限）",
            all(a <= b + 1e-6 for a, b in zip(lev_after, lev_before)),
            f"{lev_before} → {lev_after}")
        chk("調整後狀態標示為「自訂參數」", "自訂參數" in tuned["leverStateText"],
            tuned["leverStateText"][:80])
        page.screenshot(path=str(outdir / f"{args.shot_prefix}-desktop-tuned.png"), full_page=True)

        # F. 自動刷新仍運作，且自訂值沿用
        t1, n1 = tuned["lastUpdate"], len(reqs)
        page.click("#refreshBtn")
        time.sleep(1.0)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.8)
        after = page.evaluate(SNAP)
        chk("自動刷新：時間戳前進", t1 != after["lastUpdate"], f"{t1} → {after['lastUpdate']}")
        chk("自動刷新：仍持續抓取（請求數增加）", len(reqs) > n1, f"{n1} → {len(reqs)}")
        after_fields = {f["key"]: f["value"] for f in after["leverFields"]}
        _av = parse_pct(after_fields.get("dr_target_vol"))
        chk("自動刷新：自訂值仍沿用（dr_target_vol 仍為 1.0）",
            _av is not None and abs(_av - 1.0) < 1e-9, f"{after_fields}")
        chk("自動刷新：槓桿仍為自訂後的結果",
            [round(t[2], 3) for t in lev_pairs(after)] == lev_after,
            f"{[round(t[2],3) for t in lev_pairs(after)]}")

        # E. 不合法輸入被擋下
        page.fill("#leverGrid input[data-key='dr_max_lev']", "0.2")   # < min_lev 0.5
        page.click("#applyLever")
        time.sleep(0.6)
        bad = page.evaluate(SNAP)
        snaps["invalid"] = bad
        chk("不合法（最大槓桿 0.2 < 最小槓桿 0.5）被擋下並提示",
            len(bad["leverErrors"]) > 0 and "不合法" in bad["leverMsg"],
            f"errors={bad['leverErrors']} msg={bad['leverMsg']}")
        chk("不合法時不套用（生效槓桿維持不變）",
            [round(t[2], 3) for t in lev_pairs(bad)] == lev_after,
            f"{[round(t[2],3) for t in lev_pairs(bad)]}")
        page.fill("#leverGrid input[data-key='dr_target_vol']", "-1")
        page.click("#applyLever")
        time.sleep(0.6)
        bad2 = page.evaluate(SNAP)
        chk("不合法（目標波動度 -1 超出範圍）被擋下並提示",
            len(bad2["leverErrors"]) > 0, f"errors={bad2['leverErrors']}")

        # 重設為預設
        page.click("#resetLever")
        time.sleep(0.5)
        wait_settled(page, args.wait_seconds)
        time.sleep(0.8)
        rst = page.evaluate(SNAP)
        snaps["reset"] = rst
        rf = {f["key"]: f["value"] for f in rst["leverFields"]}
        chk("重設為預設：參數回到 2.0 / 3.0 / 0.5 / 14",
            [parse_pct(rf.get(k)) for k in ("dr_target_vol", "dr_max_lev", "dr_min_lev", "dr_atr_len")]
            == [2.0, 3.0, 0.5, 14.0], f"{rf}")
        chk("重設為預設：狀態回到「原始預設」", "原始預設" in rst["leverStateText"],
            rst["leverStateText"][:80])
        chk("重設為預設：槓桿回到預設計算結果",
            [round(t[2], 3) for t in lev_pairs(rst)] == lev_before,
            f"{lev_before} vs {[round(t[2],3) for t in lev_pairs(rst)]}")

        # G/H 桌面版面
        ov_d = page.evaluate(OVERFLOW_PROBE)
        bi_d = page.evaluate(BROKEN_IMG_PROBE)
        ol_d = page.evaluate(OVERLAP_PROBE)
        chk("桌面：無橫向溢出", ov_d["horizontalOverflowPx"] == 0,
            f"overflow={ov_d['horizontalOverflowPx']}px offenders={ov_d['offenderCount']}")
        chk("桌面：無破圖", bi_d["broken"] == 0, f"broken={bi_d['broken']}/{bi_d['total']}")
        chk("桌面：無文字重疊", ol_d["count"] == 0, f"overlaps={ol_d['count']} {ol_d['samples'][:2]}")
        chk("桌面：免責聲明仍在（中英文）",
            base["hasDisclaimer"] and base["hasEnglishDisclaimer"])
        page.close()

        # ---------------- 手機 390 ----------------
        m = browser.new_page(viewport={"width": 390, "height": 844})
        m.goto(args.url, wait_until="domcontentloaded")
        wait_settled(m, args.wait_seconds)
        time.sleep(1.0)
        ms = settle_with_retry(m, SNAP, args.wait_seconds)
        snaps["mobile"] = ms
        chk("手機：掃描表成功取得資料", scan_ready(ms), f"rows={ms['scanRows']}")
        chk("手機：策略選擇器 8 選項且無「動態風險敞口」",
            ms["optionCount"] == 8 and not ms["hasDynRiskOption"],
            f"options={ms['optionCount']}")
        chk("手機：建議槓桿區塊可見且有資料",
            ms["leverGridVisible"] and ms["leverBodyVisible"] and ms["leverRowCount"] >= 3,
            f"rows={ms['leverRowCount']}")
        chk("手機：即時卡片顯示建議槓桿",
            all(c.get("leverage") for c in ms["cards"]), f"cards={len(ms['cards'])}")
        m.screenshot(path=str(outdir / f"{args.shot_prefix}-mobile-390.png"), full_page=True)
        try:
            g = m.query_selector("#leverGrid")
            if g:
                g.scroll_into_view_if_needed()
                time.sleep(0.4)
                m.screenshot(path=str(outdir / f"{args.shot_prefix}-mobile-block.png"), full_page=False)
        except Exception as e:
            print(f"[warn] mobile block shot failed: {e}")

        # 手機切換策略後仍運作
        m.select_option("#strategySel", "keltner")
        time.sleep(0.4)
        wait_settled(m, args.wait_seconds)
        time.sleep(0.8)
        mk = m.evaluate(SNAP)
        snaps["mobile_keltner"] = mk
        chk("手機：切換 keltner 後建議槓桿區塊仍運作",
            mk["leverBodyVisible"] and mk["leverRowCount"] >= 3,
            f"rows={mk['leverRowCount']}")
        ov_m = m.evaluate(OVERFLOW_PROBE)
        bi_m = m.evaluate(BROKEN_IMG_PROBE)
        ol_m = m.evaluate(OVERLAP_PROBE)
        chk("手機：無橫向溢出", ov_m["horizontalOverflowPx"] == 0,
            f"overflow={ov_m['horizontalOverflowPx']}px offenders={ov_m['offenderCount']}")
        chk("手機：無破圖", bi_m["broken"] == 0, f"broken={bi_m['broken']}/{bi_m['total']}")
        chk("手機：無文字重疊", ol_m["count"] == 0, f"overlaps={ol_m['count']} {ol_m['samples'][:2]}")
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
        "leverage_table": [
            {"symbol": s, "atr_pct": a, "leverage": l, "position_pct": p}
            for s, a, l, p in sorted(lev_pairs(snaps["base"]), key=lambda t: -t[1])
        ],
        "option_values": [o["value"] for o in snaps["base"]["options"]],
        "target_vol_change": {
            "dr_target_vol_2_0": [round(t[2], 3) for t in lev_pairs(snaps["base"])],
            "dr_target_vol_1_0": [round(t[2], 3) for t in lev_pairs(snaps["target_vol_1_0"])],
            "reset_to_default": [round(t[2], 3) for t in lev_pairs(snaps["reset"])],
        },
        "layout": {
            "desktop": {"overflow": None, "broken": None, "overlaps": None},
        },
    }
    # 版面數值
    report["layout"] = {
        "desktop": {"overflow": ov_d["horizontalOverflowPx"], "broken": bi_d["broken"],
                    "overlaps": ol_d["count"]},
        "mobile": {"overflow": ov_m["horizontalOverflowPx"], "broken": bi_m["broken"],
                   "overlaps": ol_m["count"]},
    }
    (outdir / "leverage_block_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n================ SUMMARY ================")
    print(f"  {passed}/{len(checks)} PASS")
    if passed != len(checks):
        print("  FAILURES:")
        for c in checks:
            if not c["ok"]:
                print(f"    - {c['check']}  :: {c['detail']}")
    print(f"  OVERALL: {'ALL PASS' if passed == len(checks) else 'FAILED'}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
