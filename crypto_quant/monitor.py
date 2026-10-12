"""自動化監控：可重複執行、偵測狀態變化、記錄訊號歷史、發送通知。

設計
----
* **狀態檔** ``state.json``：保存上一次執行時每個標的的趨勢與訊號，用來偵測「變化」。
* **訊號歷史** ``signals_history.jsonl``：每筆訊號一行 JSON（append-only），
  可查詢「某標的最近一次訊號是何時」。
* **執行 log** ``monitor.log``：每次執行的摘要。
* **通知**：
  - 一律寫入 log 與 JSON（本機檔案，**這是實際可用的**）
  - ``webhook``：若提供 ``--webhook-url``（或環境變數 ``CRYPTO_ALERT_WEBHOOK``）才啟用
  - ``telegram``：若提供 ``--telegram-token`` 與 ``--telegram-chat`` 才啟用
  - **沒有憑證時不假裝送出**：腳本會在輸出的 ``notification`` 區塊標示
    ``mode: "file_only"`` 並附上說明，不會宣稱已發送通知。

用法
----
::

    # 執行一輪監控
    python -m crypto_quant.monitor --symbols BTCUSDT,ETHUSDT --interval 1d

    # 查詢某標的最近一次訊號
    python -m crypto_quant.monitor --query BTCUSDT

排程（cron）
-----------
每小時執行一次（加密貨幣 24/7，小時線有意義）::

    0 * * * * cd /path/to/crypto-quant_v5 && python -m crypto_quant.monitor \\
        --top 20 --interval 1h >> monitor_cron.log 2>&1

每天 00:10（日線收盤後）::

    10 0 * * * cd /path/to/crypto-quant_v5 && python -m crypto_quant.monitor \\
        --top 25 --interval 1d >> monitor_cron.log 2>&1

避免重複通知
------------
只有在**趨勢方向改變**或**出現新的交叉訊號**時才記錄為「變化事件」。
單純分數升降不觸發，以免噪音洗版。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from .backtest import BacktestConfig
from .live import fetch_klines, server_time
from .scanner import DEFAULT_SYMBOLS, top_symbols_by_volume
from .signal_engine import analyze_symbol
from .strategy import StrategyParams

STATE_VERSION = 1


@dataclass
class MonitorPaths:
    base: Path
    state: Path
    history: Path
    log: Path

    @staticmethod
    def from_dir(d: Path) -> "MonitorPaths":
        d = Path(d)
        d.mkdir(parents=True, exist_ok=True)
        return MonitorPaths(
            base=d,
            state=d / "state.json",
            history=d / "signals_history.jsonl",
            log=d / "monitor.log",
        )


def _load_state(p: Path) -> dict:
    if not p.exists():
        return {"version": STATE_VERSION, "symbols": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("version") != STATE_VERSION:
            return {"version": STATE_VERSION, "symbols": {}}
        return data
    except Exception:  # noqa: BLE001
        return {"version": STATE_VERSION, "symbols": {}}


def _save_state(p: Path, state: dict) -> None:
    p.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def append_history(p: Path, records: List[dict]) -> None:
    if not records:
        return
    with p.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_history(p: Path) -> List[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def query_symbol(history_path: Path, symbol: str) -> Optional[dict]:
    """查詢某標的最近一次訊號。"""
    recs = [r for r in load_history(history_path) if r.get("symbol") == symbol]
    if not recs:
        return None
    recs.sort(key=lambda r: r.get("detected_at_utc", ""))
    return recs[-1]


def query_symbol_all(history_path: Path, symbol: str, limit: int = 10) -> List[dict]:
    recs = [r for r in load_history(history_path) if r.get("symbol") == symbol]
    recs.sort(key=lambda r: r.get("detected_at_utc", ""), reverse=True)
    return recs[:limit]


# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------
def send_webhook(url: str, payload: dict, timeout: int = 10) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status, "body": resp.read(400).decode("utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": None, "error": str(exc)[:200]}


def send_telegram(token: str, chat_id: str, text: str, timeout: int = 10) -> dict:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": exc.read(300).decode("utf-8", "replace")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": None, "error": str(exc)[:200]}


def detect_change(prev: Optional[dict], cur: dict) -> tuple:
    """比較兩個狀態，回傳 ``(改變?, 變化類型)``。

    抽成獨立函式有兩個作用：
      1. 執行時與歷史重放（``replay_transitions``）共用**同一套**判定邏輯，
         不會出現「線上用的規則」與「重播用的規則」不一致。
      2. 可獨立測試，不必真的去抓資料。

    優先序：趨勢翻轉 > 新交叉訊號 > 接近觸發。
    僅比對「方向性」欄位，分數升降不觸發，避免噪音洗版。
    """
    if prev is None:
        return False, ""
    if prev.get("trend_label") != cur.get("trend_label"):
        return True, "TREND_FLIP"
    if prev.get("signal_label") != cur.get("signal_label"):
        return True, "NEW_CROSS"
    if (
        prev.get("near_trigger") is False
        and cur.get("near_trigger") is True
        and cur.get("trend_label") != "NEUTRAL"
    ):
        return True, "APPROACHING"
    return False, ""


def format_alert_text(events: List[dict]) -> str:
    lines = [f"【加密貨幣訊號】偵測到 {len(events)} 筆狀態變化"]
    for e in events:
        lines.append(
            f"• {e['symbol']} [{e['interval']}] {e['change_type']}："
            f"{e.get('from_trend')} → {e.get('to_trend')}"
            f"　分數 {e.get('strength')}　價格 {e.get('price')}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run_monitor(
    symbols: Optional[Iterable[str]] = None,
    interval: str = "1d",
    limit: int = 400,
    top: Optional[int] = None,
    out_dir: str = "monitor_output",
    params: Optional[StrategyParams] = None,
    cfg: Optional[BacktestConfig] = None,
    webhook_url: Optional[str] = None,
    telegram_token: Optional[str] = None,
    telegram_chat: Optional[str] = None,
    sleep: float = 0.12,
    verbose: bool = True,
) -> dict:
    """執行一輪監控。回傳本輪的完整結果（含變化事件）。"""
    paths = MonitorPaths.from_dir(Path(out_dir))
    state = _load_state(paths.state)

    # --- 決定標的清單 ---
    if symbols:
        syms = list(dict.fromkeys(symbols))
        list_source = "custom"
    elif top:
        syms = top_symbols_by_volume(top)
        list_source = f"top{top}_by_quote_volume"
    else:
        syms = list(DEFAULT_SYMBOLS)
        list_source = "builtin_default"

    params = params or StrategyParams()
    cfg = cfg or BacktestConfig()
    try:
        st_ms: Optional[int] = server_time()
    except Exception:  # noqa: BLE001
        st_ms = None

    events: List[dict] = []
    baseline_events: List[dict] = []
    now_iso = pd.Timestamp.utcnow().tz_localize(None).isoformat()
    scanned, failed = 0, 0

    for sym in syms:
        try:
            df = fetch_klines(sym, interval=interval, limit=limit)
            if len(df) < 60:
                raise RuntimeError(f"K 線不足（{len(df)} 根）")
            r = analyze_symbol(
                df, sym, interval=interval, params=params, cfg=cfg,
                source="binance:data-api.binance.vision", server_time_ms=st_ms,
            )
            scanned += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if verbose:
                print(f"[monitor] {sym} 失敗：{str(exc)[:120]}")
            if sleep:
                time.sleep(sleep)
            continue

        c = r["confirmed"] or r["provisional"]
        # 狀態鍵含週期：否則「先跑 1h 再跑 1d」會拿不同週期的狀態互相比較，
        # 產生偽變化事件。
        skey = f"{sym}@{interval}"
        cur = {
            "trend_label": r["trend_label"],
            "trend_int": r["trend"],
            "signal_label": r["signal_label"],
            "strength": c["strength"]["total"],
            "price": c["price"],
            "atr_pct": c["atr_pct"],
            "near_trigger": r["near_trigger"],
            "bar_is_in_progress": r["bar_is_in_progress"],
            "seen_at_utc": now_iso,
        }

        prev = (state["symbols"] or {}).get(skey)
        changed, change_type = detect_change(prev, cur)
        if prev is None:
            # 第一次看到：不發「變化」通知（避免首次執行爆量），
            # 但**仍記錄一筆基準記錄**到歷史。
            # 原因：若只在「有變化」時寫歷史，全新安裝在第一次真正的
            # 訊號出現前，signals_history.jsonl 根本不會被建立，
            # 「查詢最近一次訊號」與網頁時間軸都會是空的。
            baseline_events.append({
                "symbol": sym,
                "interval": interval,
                "change_type": "BASELINE",
                "from_trend": None,
                "to_trend": cur["trend_label"],
                "from_signal": None,
                "to_signal": cur["signal_label"],
                "strength": cur["strength"],
                "price": cur["price"],
                "atr_pct": cur["atr_pct"],
                "stop": (r["levels"] or {}).get("stop"),
                "take_profit": (r["levels"] or {}).get("take_profit"),
                "detected_at_utc": now_iso,
                "bar_open_utc": r["bar_open_utc"],
                "bar_close_utc": r["bar_close_utc"],
                "bar_is_in_progress": r["bar_is_in_progress"],
                "data_delay_human": r["data_delay_human"],
                "position_side": (r["position"] or {}).get("side"),
                "state_key": skey,
                "note": "首次建立基準，非狀態變化",
            })

        if changed:
            ev = {
                "symbol": sym,
                "interval": interval,
                "change_type": change_type,
                "from_trend": (prev or {}).get("trend_label"),
                "to_trend": cur["trend_label"],
                "from_signal": (prev or {}).get("signal_label"),
                "to_signal": cur["signal_label"],
                "strength": cur["strength"],
                "price": cur["price"],
                "atr_pct": cur["atr_pct"],
                "stop": (r["levels"] or {}).get("stop"),
                "take_profit": (r["levels"] or {}).get("take_profit"),
                "detected_at_utc": now_iso,
                "bar_open_utc": r["bar_open_utc"],
                "bar_close_utc": r["bar_close_utc"],
                "bar_is_in_progress": r["bar_is_in_progress"],
                "data_delay_human": r["data_delay_human"],
                "position_side": (r["position"] or {}).get("side"),
                "state_key": skey,
            }
            events.append(ev)

        state["symbols"][skey] = cur
        if sleep:
            time.sleep(sleep)

    state["last_run_utc"] = now_iso
    state["last_interval"] = interval
    state["list_source"] = list_source
    state["symbol_count"] = len(syms)
    _save_state(paths.state, state)
    # 歷史記錄：變化事件 + 首次基準（基準不觸發通知，但讓查詢與時間軸有資料）
    append_history(paths.history, events + baseline_events)

    # --- 通知 ---
    notif = {
        "mode": "file_only",
        "file_written": True,
        "webhook": None,
        "telegram": None,
        "note": (
            "未提供 webhook 或 Telegram 憑證，通知僅寫入本機 state.json / "
            "signals_history.jsonl / monitor.log。**並未對外發送任何訊息。**"
        ),
    }
    wh = webhook_url or os.environ.get("CRYPTO_ALERT_WEBHOOK")
    tg_token = telegram_token or os.environ.get("CRYPTO_ALERT_TELEGRAM_TOKEN")
    tg_chat = telegram_chat or os.environ.get("CRYPTO_ALERT_TELEGRAM_CHAT")
    if events:
        if wh:
            notif["webhook"] = send_webhook(wh, {"events": events, "run_at": now_iso})
            notif["mode"] = "webhook"
        if tg_token and tg_chat:
            notif["telegram"] = send_telegram(tg_token, tg_chat, format_alert_text(events))
            notif["mode"] = "webhook+telegram" if wh else "telegram"
        if notif["mode"] != "file_only":
            notif["note"] = "已嘗試對外發送通知（結果見 webhook / telegram 欄位）。"

    # --- log ---
    log_line = (
        f"{now_iso}  掃描={scanned} 失敗={failed} 變化事件={len(events)} "
        f"清單={list_source} 週期={interval} 通知={notif['mode']}"
    )
    with paths.log.open("a", encoding="utf-8") as fh:
        fh.write(log_line + "\n")
        for e in events:
            fh.write(
                f"    → {e['symbol']} {e['change_type']} "
                f"{e['from_trend']}→{e['to_trend']} 分數={e['strength']} "
                f"價格={e['price']}\n"
            )
    if verbose:
        print(f"[monitor] {log_line}")

    return {
        "run_at_utc": now_iso,
        "interval": interval,
        "list_source": list_source,
        "symbols": list(syms),
        "scanned": scanned,
        "failed": failed,
        "events": events,
        "baseline_events": baseline_events,
        "notification": notif,
        "paths": {
            "state": str(paths.state),
            "history": str(paths.history),
            "log": str(paths.log),
        },
        "state": state,
    }


def history_summary(history_path: Path, limit: int = 20) -> List[dict]:
    """最近 ``limit`` 筆訊號事件（含未看過的），供網頁時間軸使用。"""
    recs = load_history(history_path)
    recs.sort(key=lambda r: r.get("detected_at_utc", ""), reverse=True)
    return recs[:limit]


def last_signal_per_symbol(history_path: Path) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for rec in load_history(history_path):
        sym = rec.get("symbol")
        if not sym:
            continue
        if sym not in out or rec.get("detected_at_utc", "") > out[sym].get("detected_at_utc", ""):
            out[sym] = rec
    return out


# --------------------------------------------------------------------------
# 歷史重放（backfill）
# --------------------------------------------------------------------------
def replay_transitions(
    df,
    symbol: str,
    interval: str = "1d",
    params: Optional[StrategyParams] = None,
    lookback_bars: int = 90,
) -> List[dict]:
    """在一段歷史 K 線上重放訊號，產生**真實發生過**的狀態變化事件。

    為什麼需要它
    ------------
    監控只記錄「監控程式啟動之後」的變化。全新安裝時歷史是空的，
    網頁時間軸就沒東西可看。本函式掃描過去 ``lookback_bars`` 根 K 棒，
    找出每一次「趨勢翻轉」與「新交叉訊號」，並用**與即時監控相同的
    ``detect_change`` 判定邏輯**輸出事件，欄位一律來自真實歷史資料。

    ⚠️ 誠實標示：這些事件的 ``detected_at_utc`` 是**實際的 K 棒時間**，
    但 ``recorded_at_utc`` 是**本次回補的執行時間**（不是當時就偵測到的）。
    因此它們是「事後回補的歷史事件」，不是「當時發出的即時通知」。
    輸出中一律帶 ``replayed: true`` 與 ``source_kind: "backfilled_history"``
    以資區別。

    無未來函數
    ----------
    第 t 根的狀態只用第 t 根（含）以前的資料，與即時監控完全一致。
    """
    from .strategy import generate_signals

    params = params or StrategyParams()
    sig = generate_signals(df, params)
    n = len(sig)
    start = max(int(params.ema_slow) + 2, n - int(lookback_bars))

    events: List[dict] = []
    prev = None
    rec_at = pd.Timestamp.utcnow().tz_localize(None).isoformat()

    for i in range(start, n):
        row = sig.iloc[i]
        ema_f = row["ema_fast"]
        ema_s = row["ema_slow"]
        if not (pd.notna(ema_f) and pd.notna(ema_s)):
            continue
        trend = 1 if ema_f > ema_s else (-1 if ema_f < ema_s else 0)
        trend_label = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}[trend]
        sig_label = {1: "BULL_CROSS", -1: "BEAR_CROSS", 0: "HOLD"}[int(row["signal"])]
        cur = {"trend_label": trend_label, "signal_label": sig_label,
               "near_trigger": False}

        changed, change_type = detect_change(prev, cur)
        if changed:
            bar_ts = pd.Timestamp(row["date"]).isoformat()
            pos_side = None
            if change_type in ("TREND_FLIP", "NEW_CROSS") and trend != 0:
                pos_side = "LONG" if trend > 0 else "SHORT"
            events.append({
                "symbol": symbol,
                "interval": interval,
                "change_type": change_type,
                "from_trend": (prev or {}).get("trend_label"),
                "to_trend": trend_label,
                "from_signal": (prev or {}).get("signal_label"),
                "to_signal": sig_label,
                "strength": None,
                "price": round(float(row["close"]), 8),
                "atr_pct": round(float(row["atr_pct"]), 4)
                if pd.notna(row["atr_pct"]) else None,
                "stop": round(float(row["close"] - 3.0 * row["atr"]), 8)
                if (trend > 0 and pd.notna(row["atr"])) else
                (round(float(row["close"] + 3.0 * row["atr"]), 8)
                 if (trend < 0 and pd.notna(row["atr"])) else None),
                "take_profit": None,
                "detected_at_utc": bar_ts,            # 實際 K 棒時間
                "bar_open_utc": bar_ts,
                "bar_close_utc": None,
                "bar_is_in_progress": False,
                "data_delay_human": None,
                "position_side": pos_side,
                "state_key": f"{symbol}@{interval}",
                # --- 誠實標示 ---
                "replayed": True,
                "source_kind": "backfilled_history",
                "recorded_at_utc": rec_at,
                "note": "由歷史 K 線回補產生（非當時即時偵測）",
            })
        prev = {**cur, "trend_label": trend_label, "signal_label": sig_label}
    return events

