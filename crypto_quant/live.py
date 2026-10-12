"""即時／近即時市場資料抓取（Binance 公開端點，免金鑰）。

只用標準函式庫的 ``urllib`` 發 HTTP，避免額外套件依賴。

端點
----
* ``GET /api/v3/time``            —— 交易所伺服器時間（用來量測時鐘偏移）
* ``GET /api/v3/ticker/24hr``     —— 24 小時滾動統計（可一次查多個標的）
* ``GET /api/v3/klines``          —— K 線；**最後一根可能是「進行中」的未收盤 K 棒**

「進行中 K 棒」的判定
--------------------
Binance 回傳的每一根 K 線第 7 個欄位（index 6）是 ``closeTime``（毫秒）。
若 ``closeTime >= 現在時間``，代表該根還沒收盤。這點非常重要：
未收盤 K 棒上的交叉訊號**可能在收盤前消失**（repaint），因此本模組一律
同時回報「進行中訊號」與「已收盤確認訊號」兩者。
"""
from __future__ import annotations

import json
import time
import urllib.parse
from typing import Dict, Iterable, List, Optional

import pandas as pd

from .data_fetch import _get_json  # 重用已測試過的帶重試 GET

# 主端點：資料端點無地區限制（見 data_fetch.py 的說明）
LIVE_BASE = "https://data-api.binance.vision"
# 備援端點（依序嘗試）
LIVE_BASES = [
    "https://data-api.binance.vision",
    "https://api.binance.us",
]

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _bases(primary: Optional[str] = None) -> List[str]:
    out = list(LIVE_BASES)
    if primary and primary in out:
        out.remove(primary)
        out.insert(0, primary)
    return out


def server_time(base: Optional[str] = None) -> int:
    """交易所伺服器時間（毫秒，UTC epoch）。"""
    last = None
    for b in _bases(base):
        try:
            payload = _get_json(f"{b}/api/v3/time", retries=2)
            return int(payload["serverTime"])
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"無法取得交易所時間：{last}")


def fetch_tickers(
    symbols: Optional[Iterable[str]] = None,
    base: Optional[str] = None,
) -> Dict[str, dict]:
    """取得 24 小時滾動統計。

    ``symbols=None`` 時查詢**全部**交易對（單次請求約 1.5 MB，但只需一次）。
    回傳 ``{symbol: {...}}``。
    """
    last = None
    for b in _bases(base):
        try:
            if symbols is None:
                url = f"{b}/api/v3/ticker/24hr"
            else:
                payload_arg = urllib.parse.quote(json.dumps(sorted(set(symbols))))
                url = f"{b}/api/v3/ticker/24hr?symbols={payload_arg}"
            payload = _get_json(url, retries=2)
            if isinstance(payload, dict):
                payload = [payload]
            return {row["symbol"]: row for row in payload}
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"無法取得 24h 統計：{last}")


def fetch_exchange_info(base: Optional[str] = None) -> dict:
    last = None
    for b in _bases(base):
        try:
            return _get_json(f"{b}/api/v3/exchangeInfo?permissions=SPOT", retries=2)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"無法取得 exchangeInfo：{last}")


def tradable_usdt_symbols(base: Optional[str] = None) -> List[str]:
    """所有「交易中」的 USDT 現貨交易對。"""
    info = fetch_exchange_info(base)
    out = []
    for s in info.get("symbols", []):
        if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT":
            out.append(s["symbol"])
    return out


def fetch_klines(
    symbol: str,
    interval: str = "1d",
    limit: int = 400,
    base: Optional[str] = None,
) -> pd.DataFrame:
    """抓取**含進行中 K 棒**的最近 ``limit`` 根 K 線。

    回傳欄位：``date, open, high, low, close, volume, close_time_ms, is_closed``。
    ``date`` 為該根 K 棒**開盤**時間（UTC, tz-naive）、``close_time_ms`` 為收盤毫秒時間戳。
    """
    last = None
    for b in _bases(base):
        try:
            url = f"{b}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            rows = _get_json(url, retries=2)
            if not isinstance(rows, list) or not rows:
                raise RuntimeError("回傳空資料")
            df = pd.DataFrame(
                {
                    "date": pd.to_datetime(
                        [r[0] for r in rows], unit="ms", utc=True
                    ).tz_localize(None),
                    "open": [float(r[1]) for r in rows],
                    "high": [float(r[2]) for r in rows],
                    "low": [float(r[3]) for r in rows],
                    "close": [float(r[4]) for r in rows],
                    "volume": [float(r[5]) for r in rows],
                    "close_time_ms": [int(r[6]) for r in rows],
                }
            )
            now_ms = int(time.time() * 1000)
            df["is_closed"] = df["close_time_ms"] < now_ms
            return df.reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"無法取得 {symbol} 的 K 線：{last}")


def fetch_klines_closed(
    symbol: str,
    interval: str = "1d",
    limit: int = 400,
    base: Optional[str] = None,
) -> pd.DataFrame:
    """只保留**已收盤** K 棒（去掉進行中的最後一根）。"""
    df = fetch_klines(symbol, interval=interval, limit=limit, base=base)
    return df[df["is_closed"]].reset_index(drop=True)


def ms_to_utc(ms: int) -> pd.Timestamp:
    return pd.to_datetime(ms, unit="ms", utc=True).tz_localize(None)


def human_delay(seconds: float) -> str:
    """把秒數轉成好讀的延遲描述。"""
    s = abs(float(seconds))
    if s < 1:
        return f"{s:.2f} 秒"
    if s < 90:
        return f"{s:.1f} 秒"
    if s < 5400:
        return f"{s/60:.1f} 分鐘"
    if s < 172800:
        return f"{s/3600:.2f} 小時"
    return f"{s/86400:.2f} 天"
