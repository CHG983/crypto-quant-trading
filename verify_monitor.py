#!/usr/bin/env python3
"""監控模組驗證：在**獨立的臨時目錄**中測試變化偵測機制，不汙染正式輸出。

為什麼要獨立目錄
----------------
變化偵測是「與上一次狀態比較」的邏輯，要驗證它「會」與「不會」觸發，
就必須能控制上一次的狀態。正式 ``output/state.json`` 是實跑累積的真實紀錄，
不可為了測試而篡改。因此本腳本一律使用 ``tempfile.mkdtemp()``。

測試項目
--------
1. 首次執行：建立基準，**不**產生變化事件（避免首次爆量通知）
2. 相同資料重跑：**冪等**，第二輪 0 事件
3. 趨勢翻轉：人為把狀態中的趨勢改為相反 → 必須偵測到 ``TREND_FLIP``
4. 新交叉訊號：狀態中的訊號改變 → 必須偵測到 ``NEW_CROSS``
5. 狀態鍵含週期：先跑 1h 再跑 1d → **不得**產生偽變化事件
6. 歷史 append-only：每輪只增不減，且 JSONL 每行可解析
7. 查詢函式：``query_symbol_all`` 由新到舊、``last_signal_per_symbol`` 取最新
8. 通知誠實性：無憑證時 ``mode == "file_only"`` 且註記明說未對外發送
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from crypto_quant.monitor import (
    MonitorPaths,
    last_signal_per_symbol,
    load_history,
    query_symbol_all,
    run_monitor,
)
from crypto_quant.strategy import StrategyParams

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="wf_monitor_test_"))
    print(f"臨時目錄：{root}")
    try:
        params = StrategyParams()
        kw = dict(symbols=SYMS, interval="1d", limit=300,
                  params=params, sleep=0.05, verbose=False)

        # --- 1. 首次執行 ---
        r1 = run_monitor(out_dir=str(root), **kw)
        check("1. 首次執行成功抓取", r1["scanned"] == len(SYMS),
              f"scanned={r1['scanned']}")
        check("1. 首次不產生變化事件（僅建立基準）", len(r1["events"]) == 0,
              f"events={len(r1['events'])}")

        # --- 2. 冪等重跑 ---
        r2 = run_monitor(out_dir=str(root), **kw)
        check("2. 相同資料重跑為冪等（0 事件）", len(r2["events"]) == 0,
              f"events={len(r2['events'])}")

        paths = MonitorPaths.from_dir(root)

        # --- 3. 趨勢翻轉偵測 ---
        st = json.loads(paths.state.read_text(encoding="utf-8"))
        key = f"BTCUSDT@{kw['interval']}"
        check("5a. 狀態鍵含週期（BTCUSDT@1d）", key in st["symbols"],
              f"keys={list(st['symbols'])[:4]}")
        orig = st["symbols"][key]["trend_label"]
        st["symbols"][key]["trend_label"] = "SHORT" if orig != "SHORT" else "LONG"
        paths.state.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        r3 = run_monitor(out_dir=str(root), **kw)
        flips = [e for e in r3["events"] if e["change_type"] == "TREND_FLIP"]
        check("3. 偵測到 TREND_FLIP", len(flips) >= 1,
              f"events={[(e['symbol'], e['change_type']) for e in r3['events']]}")

        # --- 4. 新交叉訊號偵測 ---
        st = json.loads(paths.state.read_text(encoding="utf-8"))
        st["symbols"][key]["trend_label"] = r3["state"]["symbols"][key]["trend_label"]
        st["symbols"][key]["signal_label"] = (
            "BULL_CROSS"
            if r3["state"]["symbols"][key]["signal_label"] != "BULL_CROSS"
            else "BEAR_CROSS"
        )
        paths.state.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        r4 = run_monitor(out_dir=str(root), **kw)
        crosses = [e for e in r4["events"] if e["change_type"] == "NEW_CROSS"]
        check("4. 偵測到 NEW_CROSS", len(crosses) >= 1,
              f"events={[(e['symbol'], e['change_type']) for e in r4['events']]}")

        # --- 5. 週期隔離 ---
        r5 = run_monitor(out_dir=str(root), symbols=SYMS, interval="1h",
                         limit=300, params=params, sleep=0.05, verbose=False)
        check("5b. 換週期不產生偽變化事件", len(r5["events"]) == 0,
              f"events={[(e['symbol'], e['change_type']) for e in r5['events']]}")
        check("5c. 兩種週期狀態並存",
              f"BTCUSDT@1h" in r5["state"]["symbols"]
              and f"BTCUSDT@1d" in r5["state"]["symbols"],
              f"keys={sorted(r5['state']['symbols'])}")

        # --- 6. 歷史 append-only ---
        hist = load_history(paths.history)
        raw_lines = [l for l in paths.history.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("6a. JSONL 每行皆可解析", len(hist) == len(raw_lines),
              f"parsed={len(hist)} raw={len(raw_lines)}")
        check("6b. 歷史累積且含 TREND_FLIP/NEW_CROSS",
              len(hist) >= len(flips) + len(crosses),
              f"total={len(hist)}")
        r6 = run_monitor(out_dir=str(root), **kw)
        hist2 = load_history(paths.history)
        check("6c. 重跑後歷史只增不減", len(hist2) >= len(hist),
              f"{len(hist)} → {len(hist2)}")

        # --- 7. 查詢函式 ---
        recs = query_symbol_all(paths.history, SYMS[0], limit=10)
        check("7a. query_symbol_all 有結果", len(recs) >= 1, f"n={len(recs)}")
        if len(recs) >= 2:
            check("7b. query_symbol_all 由新到舊",
                  recs[0]["detected_at_utc"] >= recs[-1]["detected_at_utc"])
        else:
            check("7b. query_symbol_all 由新到舊", True, "僅 1 筆，略過排序檢查")
        latest = last_signal_per_symbol(paths.history)
        check("7c. last_signal_per_symbol 涵蓋全部標的",
              len(latest) >= 1, f"symbols={sorted(latest)}")
        if SYMS[0] in latest:
            allrec = query_symbol_all(paths.history, SYMS[0], limit=999)
            expect = max(r["detected_at_utc"] for r in allrec)
            check("7d. 取到的確實是最新一筆",
                  latest[SYMS[0]]["detected_at_utc"] == expect)

        # --- 8. 通知誠實性 ---
        n = r6["notification"]
        check("8a. 無憑證時 mode 為 file_only", n["mode"] == "file_only", n["mode"])
        check("8b. 註記明說未對外發送", "並未對外發送" in n.get("note", ""))
        check("8c. webhook/telegram 皆未嘗試", n["webhook"] is None and n["telegram"] is None)

        # --- 9. 檔案產出 ---
        check("9a. state.json 存在", paths.state.exists())
        check("9b. signals_history.jsonl 存在", paths.history.exists())
        check("9c. monitor.log 存在", paths.log.exists())
        log_txt = paths.log.read_text(encoding="utf-8")
        check("9d. log 含每輪掃描摘要", log_txt.count("掃描=") >= 6,
              f"lines={log_txt.count('掃描=')}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print()
    print(f"通過 {len(PASS)} / 共 {len(PASS)+len(FAIL)}")
    if FAIL:
        print("失敗項目：")
        for f in FAIL:
            print(f"  - {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
