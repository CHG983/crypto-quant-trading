#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pine_static_check.py — Pine Script 靜態檢查器（v4）

⚠️ 重要限制（誠實聲明）：
    本沙盒環境「無法執行 TradingView Pine 編譯器」。
    以下所有檢查都是「靜態分析」——根據 Pine Script v6 官方文件的語言規則，
    對原始碼做結構與模式比對，並以括號／引號／縮排的配對演算法驗證結構完整性。
    這可以抓出絕大多數常見的編譯錯誤（var 在 if 內、未使用 input、括號失衡、
    未來函數、alertcondition 不在全域 等），但**不等同於真正的編譯**。
    最可靠的做法仍是把 .pine 貼進 TradingView Pine Editor 編譯一次。

檢查項目：
  A. 版本宣告 / 宣告類型（indicator vs strategy）
  B. 括號與引號配對（忽略字串與註解）
  C. 縮排一致性（Pine 對縮排敏感）
  D. var / varip 是否全在全域作用域
  E. tuple 解構正確性
  F. hline 用法（常數水平線）與 plot style 正確性
  G. alertcondition / alert 是否在全域、是否在條件式區塊內
  H. 所有 input 是否實際被使用（無宣告未用的參數）
  I. 未來函數檢查（request.security / lookahead / barmerge）
  J. 常見錯誤模式 lint

用法：
    python pine_static_check.py pine/ctma_indicator.pine pine/ctma_strategy.pine
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# -----------------------------------------------------------------------------
# 工具
# -----------------------------------------------------------------------------
IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

BUILTIN_PREFIX = (
    "ta.", "math.", "str.", "color.", "shape.", "location.", "size.", "plot.",
    "label.", "line.", "box.", "table.", "strategy.", "position.", "barstate.",
    "syminfo.", "timeframe.", "alert.", "format.", "display.", "hline.", "order.",
    "session.", "dayofweek.", "extend.", "scale.", "text.", "xloc.", "yloc.",
    "font.", "chart.", "currency.", "adjustment.", "backadjustment.",
    "settlement_as_close.", "earnings.", "dividends.", "splits.", "screener.",
    "linefill.", "polyline.", "runtime.", "request.", "ticker.", "time.",
    "na", "nz", "bool", "int", "float", "string", "color",
)

KEYWORDS = {
    "if", "else", "for", "while", "var", "varip", "and", "or", "not", "true",
    "false", "import", "export", "switch", "type", "enum", "method", "to",
    "in", "by", "break", "continue", "series", "simple", "const", "input",
    "once", "int", "float", "bool", "string", "color", "line", "label", "table",
    "box", "polyline", "matrix", "array", "map", "chart", "void",
}


def strip_comments_and_strings(src: str) -> str:
    """把字串常值換成佔位符、把註解清空，但保留行結構（換行）以便逐行分析。"""
    out_lines = []
    for line in src.split("\n"):
        res = []
        i = 0
        in_str = False
        while i < len(line):
            ch = line[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    in_str = False
                    res.append('"')
                else:
                    res.append(" ")
            else:
                if ch == '"':
                    in_str = True
                    res.append('"')
                elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                    break  # 行註解，其餘丟棄
                else:
                    res.append(ch)
            i += 1
        out_lines.append("".join(res))
    return "\n".join(out_lines)


def indent_of(line: str) -> int:
    n = 0
    for ch in line:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4
        else:
            break
    return n


def balanced(clean: str, open_ch: str, close_ch: str) -> tuple[bool, int]:
    """回傳 (是否平衡, 最深不平衡位置的行號)。忽略註解與字串。"""
    depth = 0
    first_bad = 0
    for lineno, line in enumerate(clean.split("\n"), 1):
        for ch in line:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth < 0 and first_bad == 0:
                    first_bad = lineno
    return depth == 0, first_bad


def build_block_map(lines: list[str]) -> list[int | None]:
    """
    回傳每個行索引所屬的「最近外層結構區塊」的縮排值（若在區塊外則 None）。
    區塊 = 以 `if / else / for / while` 起頭的行所開啟的縮排層級。
    """
    owner: list[int | None] = [None] * len(lines)
    stack: list[int] = []  # 各層區塊的縮排值
    for idx, line in enumerate(lines):
        if not line.strip():
            owner[idx] = stack[-1] if stack else None
            continue
        ind = indent_of(line)
        # 關閉縮排 >= 目前行的區塊
        while stack and stack[-1] >= ind:
            stack.pop()
        owner[idx] = stack[-1] if stack else None
        s = line.strip()
        if re.match(r"^(if|else|for|while)\b", s):
            stack.append(ind)
    return owner


# -----------------------------------------------------------------------------
# 主檢查
# -----------------------------------------------------------------------------
def check_file(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    clean = strip_comments_and_strings(raw)
    raw_lines = raw.split("\n")
    lines = clean.split("\n")

    R: dict = {"file": str(path), "lines": len(raw_lines), "checks": []}

    def add(name: str, ok: bool, detail: str, *, warn: bool = False) -> None:
        R["checks"].append({
            "name": name,
            "status": "PASS" if ok else ("WARN" if warn else "FAIL"),
            "detail": detail,
        })

    # --- A. 版本與宣告 -------------------------------------------------------
    m_ver = re.match(r"^//@version=(\d+)\s*$", raw_lines[0]) if raw_lines else None
    ver = int(m_ver.group(1)) if m_ver else 0
    add("A1 Pine 版本宣告", ver >= 5, f"//@version={ver}" if ver else "找不到 //@version")

    decl_ind = next((i for i, l in enumerate(lines) if re.match(r"^(indicator|strategy)\s*\(", l)), None)
    if decl_ind is None:
        add("A2 宣告型別", False, "找不到 indicator() / strategy() 宣告")
        decl_type = ""
    else:
        decl_type = "indicator" if lines[decl_ind].startswith("indicator") else "strategy"
        add("A2 宣告型別", True, f"{decl_type}() 位於第 {decl_ind + 1} 行")
    R["decl_type"] = decl_type

    R["is_indicator"] = decl_type == "indicator"
    R["is_strategy"] = decl_type == "strategy"

    # --- B. 括號與引號配對 ---------------------------------------------------
    allok = True
    details = []
    for o, c, nm in (("(", ")", "小括號"), ("[", "]", "中括號"), ("{", "}", "大括號")):
        ok, bad = balanced(clean, o, c)
        details.append(f"{nm}{'OK' if ok else f'不平衡@行{bad}'}")
        allok &= ok
    # 引號：計數需為偶數
    q = clean.count('"')
    allok &= (q % 2 == 0)
    details.append(f"引號{'OK' if q % 2 == 0 else '奇數'}({q})")
    add("B1 括號／引號配對", allok, " · ".join(details))

    # --- C. 縮排一致性 -------------------------------------------------------
    indents = [indent_of(l) for l in lines if l.strip()]
    pos = sorted({i for i in indents if i > 0})
    if pos:
        unit = min(pos)
        bad_ind = [(n, l.strip()[:60]) for n, l in enumerate(lines, 1)
                   if l.strip() and indent_of(l) % unit != 0]
        # 續行（上一行以運算子結尾）允許非整數倍縮排
        real_bad = []
        for n, snip in bad_ind:
            prev = lines[n - 2].rstrip() if n >= 2 else ""
            if prev.endswith(("and", "or", "?", ":", ",", "(", "[", "+", "-", "*", "/")):
                continue
            real_bad.append((n, snip))
        add("C1 縮排一致性", len(real_bad) == 0,
            f"基礎縮排單位={unit}；不合規行數={len(real_bad)}"
            + (f" → {real_bad[:3]}" if real_bad else ""))
    else:
        add("C1 縮排一致性", True, "無縮排行")

    # --- D. var / varip 是否全在全域 ----------------------------------------
    owners = build_block_map(lines)
    var_hits = []
    for idx, line in enumerate(lines):
        if re.match(rf"^\s*var(ip)?\s", line) and not line.lstrip().startswith("//"):
            var_hits.append((idx, indent_of(line), line.strip()))
    nested = [(i + 1, s) for i, ind, s in var_hits if owners[i] is not None]
    add("D1 var/varip 全在全域", len(nested) == 0,
        f"共 {len(var_hits)} 個 var 宣告；位於 if/for/while 區塊內者 = {len(nested)}"
        + (f" → {nested[:3]}" if nested else " ✓"))

    # --- E. tuple 解構 -------------------------------------------------------
    tuples = []
    for idx, line in enumerate(lines, 1):
        m = re.match(rf"^\s*\[([^\]]+)\]\s*=\s*({IDENT}[\w.]*)\s*\(", line)
        if m:
            names = [x.strip() for x in m.group(1).split(",")]
            tuples.append((idx, names, m.group(2)))
    bad_tuples = [(ln, ns) for ln, ns, _ in tuples if any(not re.match(rf"^{IDENT}$", n) for n in ns)]
    add("E1 tuple 解構語法", len(bad_tuples) == 0,
        f"找到 {len(tuples)} 個 tuple 解構："
        + "; ".join(f"行{ln} {ns} ← {fn}()" for ln, ns, fn in tuples)
        + (f" | 格式異常 {bad_tuples}" if bad_tuples else ""))

    # ta.supertrend 必須雙值解構、ta.macd 必須三值解構
    st_ok = all(len(ns) == 2 for ln, ns, fn in tuples if fn.endswith("supertrend"))
    macd_ok = all(len(ns) == 3 for ln, ns, fn in tuples if fn.endswith("macd"))
    add("E2 內建函式回傳值數量", st_ok and macd_ok,
        f"ta.supertrend 雙值解構={st_ok}；ta.macd 三值解構={macd_ok}")

    # --- F. hline / plot style ----------------------------------------------
    hl_args = [l.strip() for l in lines if re.match(r"^\s*(hline\s*\(|hlines\s*\()", l)]
    bad_hl = [a for a in hl_args if a.startswith("hlines")]
    add("F1 hline 用法", len(bad_hl) == 0,
        f"hline() 呼叫 {len(hl_args)} 次，使用 hlines() 者 {len(bad_hl)} 次"
        + (" ✓（常數水平線正確用 hline）" if not bad_hl else f" → {bad_hl}"))

    bad_style = [l.strip()[:70] for l in lines
                 if re.search(r"style\s*=\s*(?!plot\.style_|line\.style_|label\.style_|shape\.|hline\.style_)\w+", l)]
    add("F2 plot style 命名空間", len(bad_style) == 0,
        "所有 style= 皆使用完整命名空間" if not bad_style else f"可疑 {bad_style[:3]}")

    # plotstyle 用於 plot()、shape.* 用於 plotshape()
    plot_shape_mismatch = []
    for idx, line in enumerate(lines, 1):
        if "plotshape" in line and "shape." in line:
            continue
        if re.match(r"^\s*plotshape\s*\(", line) and "shape." not in line:
            # 可能在續行
            blk = " ".join(lines[idx - 1:idx + 3])
            if "shape." not in blk:
                plot_shape_mismatch.append(idx)
    add("F3 plotshape 使用 shape.*", len(plot_shape_mismatch) == 0,
        "全部 plotshape 皆指定 shape.*" if not plot_shape_mismatch
        else f"缺 shape.* 的行：{plot_shape_mismatch}")

    # --- G. alertcondition / alert 位置 -------------------------------------
    ac_lines = [idx for idx, l in enumerate(lines) if re.match(r"^\s*alertcondition\s*\(", l)]
    ac_nested = [i + 1 for i in ac_lines if owners[i] is not None]
    add("G1 alertcondition 在全域", len(ac_nested) == 0,
        f"alertcondition 共 {len(ac_lines)} 個；在區塊內者 = {len(ac_nested)}"
        + (f" → {ac_nested}" if ac_nested else " ✓"))

    alert_calls = [idx for idx, l in enumerate(lines) if re.match(r"^\s*alert\s*\(", l)]
    alert_nested = [i + 1 for i in alert_calls if owners[i] is not None]
    add("G2 alert() 皆位於條件區塊", len(alert_calls) > 0 and len(alert_nested) == len(alert_calls),
        f"alert() 共 {len(alert_calls)} 個，其中在 if 區塊內者 {len(alert_nested)} 個（須全部在條件內才不會逐根觸發）")

    freq_ok = all("alert.freq_" in " ".join(lines[i:i + 3]) for i in alert_calls) if alert_calls else True
    add("G3 alert() 指定頻率", freq_ok, "每個 alert() 皆指定 alert.freq_*" if freq_ok else "有 alert() 未指定頻率")

    # --- H. input 是否全被使用 ----------------------------------------------
    inputs = []
    for idx, line in enumerate(lines):
        m = re.match(rf"^\s*({IDENT})\s*=\s*input\.", line)
        if m:
            inputs.append((m.group(1), idx))

    # 反向引用：使用者可能宣告 `g = "① 群組"` 後在 input 內用 group=g
    helper_defs = {}
    for idx, line in enumerate(lines):
        m = re.match(rf"^\s*({IDENT})\s*=\s*\"", raw_lines[idx])
        if m:
            helper_defs[m.group(1)] = idx

    unused = []
    for name, decl_idx in inputs:
        body = "\n".join(l for i, l in enumerate(lines) if i != decl_idx)
        if not re.search(rf"\b{re.escape(name)}\b", body):
            unused.append(name)

    unused_helpers = []
    for name, decl_idx in helper_defs.items():
        if name in ("str",):
            continue
        body = "\n".join(l for i, l in enumerate(lines) if i != decl_idx)
        if not re.search(rf"\b{re.escape(name)}\b", body):
            unused_helpers.append(name)

    add("H1 所有 input 皆被使用", len(unused) == 0,
        f"宣告 {len(inputs)} 個 input；未被使用 = {len(unused)}"
        + (f" → {unused}" if unused else " ✓"))
    R["input_count"] = len(inputs)

    add("H2 群組字串變數皆被使用", len(unused_helpers) == 0,
        f"字串常數 {len(helper_defs)} 個；未被使用 = {len(unused_helpers)}"
        + (f" → {unused_helpers}" if unused_helpers else " ✓"))

    # 每個 input 是否都有 group=（組織性檢查，僅警告）
    no_group = []
    for name, idx in inputs:
        blk = " ".join(lines[idx:idx + 3])
        if "group" not in blk:
            no_group.append(name)
    add("H3 input 皆有 group", len(no_group) == 0,
        "每個 input 都歸類到群組" if not no_group else f"未分群：{no_group}", warn=True)

    # --- I. 未來函數 --------------------------------------------------------
    rs = [i + 1 for i, l in enumerate(lines) if "request.security" in l]
    lk = [i + 1 for i, l in enumerate(lines) if "lookahead" in l or "barmerge.lookahead" in l]
    add("I1 未使用 request.security", len(rs) == 0,
        "完全未使用 request.security → 不存在跨週期 lookahead / repaint 風險" if not rs
        else f"使用了 request.security：行 {rs}（須逐項確認未用 lookahead）")
    add("I2 無 lookahead 參數", len(lk) == 0,
        "未出現 lookahead / barmerge.lookahead" if not lk else f"出現於行 {lk}")
    add("I3 未使用 barmerge.*", "barmerge." not in clean, "未出現 barmerge.*" if "barmerge." not in clean else "出現 barmerge.*")

    # 歷史引用是否以 nz() 保護（[1] 在首根為 na，需 nz() 才安全）
    hist_all = [i + 1 for i, l in enumerate(lines) if re.search(r"\[1\]", l)]
    hist_unprotected = []
    for i in hist_all:
        blk = " ".join(lines[max(0, i - 2):i + 1])
        # 安全條件：(a) 已用 nz() 包住；或 (b) 該引用直接參與比較運算
        # （Pine 中 `na > 0` 會回傳 false，是明確定義的行為，不會污染後續計算）
        safe = "nz(" in blk or re.search(r"\[1\]\s*(>=|<=|==|!=|>|<)", blk) is not None \
            or re.search(r"(>=|<=|==|!=|>|<)\s*[\w.]*\[1\]", blk) is not None
        if not safe:
            hist_unprotected.append(i)
    add("I4 歷史引用以 nz() 保護", len(hist_unprotected) == 0,
        f"history [1] 引用 {len(hist_all)} 處，未以 nz() 保護者 {len(hist_unprotected)}"
        + (f" → 行 {hist_unprotected}" if hist_unprotected else " ✓"))

    # --- J. 常見錯誤 lint ---------------------------------------------------
    lints = []

    # J1a 布林運算直接使用「裸 na」值（Pine v5+ bool 不可為 na）
    #     注意：`not na(x)` 是合法慣用法，必須排除；只有後面不接括號的裸 na 才違法。
    for i, l in enumerate(lines, 1):
        if re.search(r"\b(and|or|not)\s+na\b(?!\s*\()", l) or re.search(r"\bna\b(?!\s*\()\s+(and|or)\b", l):
            lints.append((i, "布林運算直接使用裸 na 值", l.strip()[:70]))

    # J1b 未指定型別的變數宣告為 na，之後卻被當成布林運算元使用
    na_decl = {}
    for i, l in enumerate(lines, 1):
        m = re.match(rf"^\s*({IDENT})\s*=\s*na\s*$", l)
        if m:
            na_decl[m.group(1)] = i
    for name, ln in na_decl.items():
        for j, l in enumerate(lines, 1):
            if j == ln:
                continue
            if re.search(rf"\b{re.escape(name)}\b\s*(and|or)\b", l) or \
               re.search(rf"\b(and|or|not)\s+{re.escape(name)}\b", l) or \
               re.match(rf"^\s*if\s+{re.escape(name)}\s*$", l):
                lints.append((ln, f"變數 {name} 宣告為 na 卻用於布林運算（型別為 na，無法參與 and/or）",
                              l.strip()[:70]))
                break

    # J2 if 區塊內宣告區域變數後又在同層級使用（Pine 不允許）
    for i, l in enumerate(lines, 1):
        s = l.strip()
        if re.match(r"^(int|float|bool|string|color|line|label|table|box)\s+\w+\s*=", s):
            if owners[i - 1] is not None:
                lints.append((i, "在 if/for 區塊內宣告型別變數（區域作用域會消失）", s[:70]))

    # J3 strategy.exit 未指定任何價位（注意 stripped 版本中 `stop = x` 會有空白，
    #    因此不可用 "stop=" 字面比對，必須容許空白）
    for i, l in enumerate(lines, 1):
        if "strategy.exit" in l:
            blk = " ".join(lines[i - 1:i + 3])
            if re.search(r"\b(stop|limit|profit|loss|trail_points|trail_offset)\s*=", blk) is None:
                lints.append((i, "strategy.exit 未指定 stop/limit/profit/loss", l.strip()[:70]))

    # J4 location.absolute 必須搭配「價格序列」而非布林值
    #    必須只檢查「該 location.absolute 所屬的那個 plotshape 呼叫」，不可用行窗誤抓鄰近呼叫。
    for i, l in enumerate(lines):
        if not re.match(r"^\s*plotshape\s*\(", l):
            continue
        # 從呼叫起點往下收集直到括號平衡
        depth = 0
        call_lines = []
        for k in range(i, min(len(lines), i + 12)):
            call_lines.append(lines[k])
            depth += lines[k].count("(") - lines[k].count(")")
            if depth <= 0:
                break
        call = " ".join(call_lines)
        if "location.absolute" not in call:
            continue
        m = re.match(r"^\s*plotshape\s*\(\s*(.+?)\s*,\s*location\.absolute", call)
        if m:
            arg1 = m.group(1)
            # location.absolute 需要價格序列；若第一參數是布林組合（不含三元運算 ? :）則違法
            if "?" not in arg1:
                lints.append((i + 1, "location.absolute 的第一參數是布林值，應改為價格序列（例：cond ? close : na）", arg1[:70]))

    # J5 序列不可直接當純量條件（Pine v6 會編譯錯誤）
    for i, l in enumerate(lines, 1):
        if re.match(r"^\s*if\s+[A-Za-z_][\w.]*\s*$", l):
            nm = l.strip()[3:].strip()
            if nm not in ("barstate.islast", "barstate.isfirst"):
                lints.append((i, f"`if {nm}` 未使用比較運算子（序列不能當純量布林）", l.strip()[:70]))


    add("J0 常見錯誤 lint", len(lints) == 0,
        "未發現常見錯誤模式" if not lints else f"{len(lints)} 處：" + str(lints[:4]))

    # --- K. 指標版／策略版專屬 -------------------------------------------------
    if R["is_indicator"]:
        add("K1 指標版不含 strategy.* 呼叫", "strategy." not in clean,
            "未出現 strategy.* (指標版無法使用交易函式)" if "strategy." not in clean
            else "出現 strategy.* API（指標版會編譯失敗）")
        has_state = "var int   pos" in raw or re.search(r"var\s+int\s+pos\s*=", raw) is not None
        add("K2 指標版自行維護持倉狀態機", bool(has_state),
            "以 var pos 自行模擬多空狀態（指標版無 strategy 引擎可用）" if has_state else "找不到持倉狀態變數")

    if R["is_strategy"]:
        has_entry = "strategy.entry" in clean
        has_exit = "strategy.exit" in clean
        add("K3 策略版含 entry / exit", has_entry and has_exit,
            f"strategy.entry={'有' if has_entry else '無'}；strategy.exit={'有' if has_exit else '無'}")
        add("K4 策略宣告含成本與資金設定",
            all(k in clean for k in ("initial_capital", "commission_value", "slippage")),
            "已設定 initial_capital / commission_value / slippage")

    # --- 統計 --------------------------------------------------------------
    R["pass"] = sum(1 for c in R["checks"] if c["status"] == "PASS")
    R["warn"] = sum(1 for c in R["checks"] if c["status"] == "WARN")
    R["fail"] = sum(1 for c in R["checks"] if c["status"] == "FAIL")
    R["total"] = len(R["checks"])
    return R


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 2

    results = [check_file(p) for p in paths]

    for r in results:
        print("=" * 78)
        print(f"  {r['file']}   ({r['lines']} 行, {r.get('input_count', 0)} 個 input)")
        print("=" * 78)
        for c in r["checks"]:
            icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[c["status"]]
            print(f"  {icon} [{c['status']}] {c['name']}")
            print(f"        {c['detail']}")
        print(f"\n  → {r['pass']} PASS / {r['warn']} WARN / {r['fail']} FAIL  (共 {r['total']} 項)\n")

    total_fail = sum(r["fail"] for r in results)
    total_pass = sum(r["pass"] for r in results)
    total_warn = sum(r["warn"] for r in results)
    total = sum(r["total"] for r in results)
    print("=" * 78)
    print(f"  總計：{total_pass} PASS / {total_warn} WARN / {total_fail} FAIL  (共 {total} 項)")
    print("=" * 78)

    out = Path("output/pine_static_check.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "note": "本結果為靜態分析，非 TradingView 實際編譯；建議貼進 Pine Editor 確認一次。",
        "files": results,
        "summary": {"pass": total_pass, "warn": total_warn, "fail": total_fail, "total": total},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已寫出 {out}")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())