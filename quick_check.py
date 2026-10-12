#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quick_check.py — 報告網頁 v4 交付品質閘門（Quick Check）
結構平衡 + 功能斷言；可達性由 shell 的 curl 並行處理。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

PAGE = Path("/workspace/webpages/crypto-quant-report_v4/index.html")
raw = PAGE.read_text(encoding="utf-8")

fails: list[str] = []
warns: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"  → {detail}" if detail else ""))
    if not cond:
        fails.append(label)
    return cond


print("=" * 78)
print("  A. 結構檢查（HTML 標籤平衡 / CSS 括號 / 內嵌 JS 可解析）")
print("=" * 78)

# 剝除 script / style / 註解後再數標籤
stripped = re.sub(r"<script\b.*?</script>", "", raw, flags=re.S | re.I)
stripped = re.sub(r"<style\b.*?</style>", "", stripped, flags=re.S | re.I)
stripped = re.sub(r"<!--.*?-->", "", stripped, flags=re.S)
# 連同原始檔（含 style 內容）一起數 CSS 括號
css_blocks = "\n".join(re.findall(r"<style\b.*?>(.*?)</style>", raw, flags=re.S | re.I))

VOID = {"meta", "link", "br", "hr", "img", "input", "source", "area", "base",
        "col", "embed", "param", "track", "wbr"}
for tag in ("div", "table", "thead", "tbody", "tr", "td", "th", "figure", "figcaption",
            "pre", "code", "details", "summary", "ul", "ol", "li", "p", "h2", "h3", "h4",
            "header", "footer", "html", "head", "body", "span", "strong", "em", "sup", "a"):
    op = len(re.findall(rf"<{tag}\b(?![^>]*/>)", stripped, flags=re.I))
    cl = len(re.findall(rf"</{tag}\s*>", stripped, flags=re.I))
    ok(op == cl, f"A  <{tag}> 開合平衡", f"開={op} 閉={cl}" if op == cl else f"開={op} 閉={cl} 不平衡")

css_open, css_close = css_blocks.count("{"), css_blocks.count("}")
ok(css_open == css_close, "A  CSS 大括號平衡", f"{{={css_open} }}={css_close}")

scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", raw, flags=re.S | re.I)
inline = [s for s in scripts if s.strip()]
print(f"  ℹ  內嵌 <script> 區塊數 = {len(inline)}（若為 0 則無 JS 需檢查）")

# z-index / 常見排版數值
print()
print("=" * 78)
print("  B. 功能斷言（v4 新增內容必須存在）")
print("=" * 78)

ok('id="s13"' in raw, "B1 新增第 13 章錨點 id=\"s13\"")
ok('href="#s13"' in raw, "B2 目錄含 #s13 連結")
ok("量化交易指標腳本" in raw, "B3 第 13 章標題存在")
ok("ctma_indicator.pine" in raw and "ctma_strategy.pine" in raw, "B4 兩個 .pine 檔名皆出現")
ok(raw.count("<details") == 2 and raw.count("</details>") == 2, "B5 兩個可折疊程式碼區塊",
   f"details={raw.count('<details')}")
ok(raw.count("@version=6") >= 2, "B6 兩份 .pine 程式碼已內嵌且為 v6", f"@version=6 出現 {raw.count('@version=6')} 次")
ok("var int   pos" in raw or "var int pos" in raw, "B7 指標版 var 狀態機變數已內嵌")
ok("var float longStop" in raw, "B8 策略版 var 停損變數已內嵌")
ok("46/46" in raw, "B9 靜態檢查 46/46 結果已揭露")
ok("靜態檢查" in raw and "非真正的編譯" in raw.replace("不是真正的編譯", "非真正的編譯"),
   "B10 靜態檢查 vs 實際編譯的誠實區別已說明")
ok("Pine Editor" in raw, "B11 建議貼進 Pine Editor 確認已寫入")
ok("alertcondition" in raw, "B12 alertcondition 說明存在")
ok("投資免責聲明" in raw, "B13 投資免責聲明存在")
ok("As an AI Agent, I cannot provide legally protected" in raw, "B14 英文免責聲明存在")
ok("不構成任何投資建議" in raw, "B15 中文免責聲明措辭存在")
ok('class="codebox"' in raw, "B16 程式碼區塊樣式已套用")
ok("Noto Sans TC" in re.search(r"font-family:([^;]+);", raw).group(1), "B17 中文字型已指定")
ok("tablewrap" in raw, "B18 表格橫向捲動容器存在（窄螢幕保護）")
ok(raw.count("<tr>") >= 30, "B19 參數表列數充足", f"<tr>(不含屬性)={raw.count('<tr>')}")

# 免責聲明必須在最後（footer 之前）
i_disc, i_foot = raw.find('class="disc"'), raw.find("<footer>")
ok(0 < i_disc < i_foot, "B20 免責聲明位於頁尾 footer 之前（未被新章節擠掉）")

# 第 13 章須在 disclaimer 之前
i_s13 = raw.find('id="s13"')
ok(0 < i_s13 < i_disc, "B21 第 13 章位於免責聲明之前")

print()
print("=" * 78)
print(f"  結構 + 功能：{'全部通過 ✅' if not fails else f'{len(fails)} 項未通過 ❌'}")
print("=" * 78)
for f in fails:
    print(f"    ❌ {f}")

sys.exit(1 if fails else 0)
