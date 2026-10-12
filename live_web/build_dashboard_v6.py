#!/usr/bin/env python3
"""build_dashboard_v6.py — 產生「瀏覽器直連即時 API」版的儀表板。

與前一版（build_dashboard.py）的根本差異
---------------------------------------
前一版把 Python 算好的結果用 `const DATA = {...}` 內嵌進 HTML，頁面是
產生當下的快照。本版 HTML 只內嵌：

  1. `live_engine.js` 的**原文**（前端策略引擎，與受測版本逐字元相同）
  2. 歷史快照（僅第 3 節「監控與時間軸」使用，明文標示為歷史）
  3. 引擎等價性驗證摘要（供報告引用）

即時訊號與市場掃描的**數字全部在瀏覽器端抓取與計算**，HTML 內沒有任何
寫死的價格。建置時會比對內嵌引擎與磁碟上 `live_engine.js` 的 SHA-256，
不符即中止 —— 避免「受測的程式碼」與「上線的程式碼」不一致。

用法：
    python3 build_dashboard_v6.py \
        --template live_web/dashboard_template.html \
        --engine live_web/live_engine.js \
        --history output/monitor_snapshot.json \
        --parity verify/engine_parity.json \
        --out ../webpages/crypto-quant-report_v6/dashboard.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="產生瀏覽器直連即時版儀表板")
    ap.add_argument("--template", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--history", required=True)
    ap.add_argument("--parity", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    tpl_path = Path(args.template)
    eng_path = Path(args.engine)
    tpl = tpl_path.read_text(encoding="utf-8")
    engine_src = eng_path.read_text(encoding="utf-8")

    # --- 1) 內嵌策略引擎（原文）---
    if "/*__LIVE_ENGINE__*/" not in tpl:
        print("[build] ERROR: 模板缺少 /*__LIVE_ENGINE__*/ 佔位符", file=sys.stderr)
        return 2
    html = tpl.replace("/*__LIVE_ENGINE__*/", engine_src)

    # 驗證替換後頁面中所含引擎與磁碟版本逐位元相同
    embedded = html[html.index("/* ===================== 內嵌策略引擎")
                    : html.index("/* ===================== 內嵌歷史資料")]
    engine_hash = sha256_text(engine_src)
    # 取出內嵌區塊中的引擎本體（去掉前後標題註解行）
    m = re.search(re.escape(engine_src), embedded)
    if not m:
        print("[build] ERROR: 內嵌引擎與 live_engine.js 不一致（雜湊驗證失敗）",
              file=sys.stderr)
        return 3
    print(f"[build] engine embedded OK  sha256={engine_hash[:16]}…  "
          f"bytes={len(engine_src)}")

    # --- 2) 歷史快照（僅第 3 節；明確標示為歷史，不隨刷新更新）---
    # monitor_snapshot.json 的「頂層鍵」即為監控資料（run_at_utc /
    # tracked_symbols / history_recent / symbol_count / history_total / …），
    # 包成 {"monitor": {...}} 以便與引擎等價性摘要（parity）並存於 HISTORY。
    hist_path = Path(args.history)
    raw = json.loads(hist_path.read_text(encoding="utf-8"))
    history: dict = {"monitor": raw}
    mon = history["monitor"]
    print(f"[build] history loaded  run_at={mon.get('run_at_utc')}  "
          f"interval={mon.get('interval')}  tracked={mon.get('symbol_count')}  "
          f"events_recent={len(mon.get('history_recent') or [])}  "
          f"history_total={mon.get('history_total')}")

    # --- 3) 引擎等價性驗證摘要（先併入 history，再一次性注入頁面）---
    if args.parity and Path(args.parity).exists():
        par = json.loads(Path(args.parity).read_text(encoding="utf-8"))
        total = par.get("total_checks", 0)
        failed = par.get("failed_checks", 0)
        summary = {
            "status": par.get("status"),
            "summary": (
                f"{total - failed}/{total} 項檢查通過"
                f"（最大殘差 {par.get('max_abs_delta', 0):.2e}"
                f"，位於 {par.get('max_abs_delta_at')}）"
            ),
        }
        history["parity"] = summary
        print(f"[build] parity embedded  {summary['status']} — {summary['summary']}")
    else:
        print("[build] parity 檔不存在，跳過（頁面會顯示提示文字）")

    # --- 3b) 一次性注入 HISTORY（含 monitor 與 parity）---
    if "/*__HISTORY_JSON__*/" not in html:
        print("[build] ERROR: 模板缺少 history 佔位符", file=sys.stderr)
        return 2
    html = html.replace(
        "/*__HISTORY_JSON__*/",
        json.dumps(history, ensure_ascii=False, separators=(",", ":")),
    )

    # --- 4) 建置時間註記 ---
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    html = html.replace(
        "<footer>",
        f"<!-- built_by=build_dashboard_v6.py  built_at={stamp}  "
        f"engine_sha256={engine_hash} -->\n<footer>",
        1,
    )

    # --- 5) 寫出 ---
    # 路徑防呆：輸出必須落在 webpages/ 之下。若相對路徑解析到
    # documents/webpages/… 之類的意外位置（曾發生：--out ../webpages/… 從
    # documents/crypto-quant_v7 出發會落到 documents/webpages/…），直接中止，
    # 避免靜默寫到錯的地方而讓瀏覽器驗證看到舊檔。
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()
    if "documents" in out_path.parts and "webpages" in out_path.parts:
        print(f"[build] ERROR: 輸出路徑看起來不對：{out_path}\n"
              f"        預期落在 <repo>/webpages/<name>/dashboard.html。"
              f"請改用絕對路徑或正確的相對路徑。", file=sys.stderr)
        return 4
    if "webpages" not in out_path.parts:
        print(f"[build] ERROR: 輸出路徑不在 webpages/ 之下：{out_path}",
              file=sys.stderr)
        return 4
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"[build] wrote {out_path}  ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
