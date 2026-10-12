"""加密貨幣歷史日線資料抓取（多來源備援，HTTP 僅用標準函式庫）。

來源優先序
----------
1. Binance 公開資料端點 ``data-api.binance.vision``（無地區限制、完整 OHLCV）
2. Binance US ``api.binance.us``
3. Kraken ``api.kraken.com``
4. CoinGecko ``api.coingecko.com``（僅 OHLC，無成交量）

回傳的 DataFrame 一律具備欄位：``date, open, high, low, close, volume``，
``date`` 為 UTC 日線起點（tz-naive）。
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Optional

import pandas as pd

USER_AGENT = "Mozilla/5.0 (compatible; crypto-quant-research/1.0)"
TIMEOUT = 30
_MS_PER_DAY = 86_400_000


def _get_json(url: str, retries: int = 3, backoff: float = 1.6):
    """帶重試的 JSON GET。失敗時丟出 RuntimeError（不吞錯）。"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
    raise RuntimeError(f"HTTP 取得失敗：{url} -> {last}")


def _to_ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="UTC").timestamp() * 1000)


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["open", "high", "low", "close"]).copy()
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


# --------------------------------------------------------------------------
# 1. Binance 公開資料端點（主要來源）
# --------------------------------------------------------------------------
def fetch_binance_vision(
    symbol: str = "BTCUSDT",
    interval: str = "1d",
    start: str = "2019-01-01",
    end: Optional[str] = None,
) -> pd.DataFrame:
    """以分頁方式抓取 Binance 公開 klines（每頁上限 1000 根）。"""
    base = "https://data-api.binance.vision/api/v3/klines"
    start_ms = _to_ms(start)
    end_ms = _to_ms(end) if end else int(time.time() * 1000)
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        url = (
            f"{base}?symbol={symbol}&interval={interval}"
            f"&startTime={cursor}&endTime={end_ms}&limit=1000"
        )
        batch = _get_json(url)
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        last_open = batch[-1][0]
        if last_open <= cursor:  # 防呆：避免無限迴圈
            break
        cursor = last_open + _MS_PER_DAY
        if len(batch) < 1000:
            break
        time.sleep(0.25)  # 對公開端點客氣一點
    if not rows:
        raise RuntimeError("Binance vision 回傳空資料")
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows], unit="ms", utc=True).tz_localize(None),
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [float(r[5]) for r in rows],
        }
    )
    return _finalize(df)


# --------------------------------------------------------------------------
# 2. Binance US（備援）
# --------------------------------------------------------------------------
def fetch_binance_us(
    symbol: str = "BTCUSDT",
    interval: str = "1d",
    start: str = "2019-01-01",
    end: Optional[str] = None,
) -> pd.DataFrame:
    base = "https://api.binance.us/api/v3/klines"
    start_ms = _to_ms(start)
    end_ms = _to_ms(end) if end else int(time.time() * 1000)
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        url = (
            f"{base}?symbol={symbol}&interval={interval}"
            f"&startTime={cursor}&endTime={end_ms}&limit=1000"
        )
        batch = _get_json(url)
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        last_open = batch[-1][0]
        if last_open <= cursor:
            break
        cursor = last_open + _MS_PER_DAY
        if len(batch) < 1000:
            break
        time.sleep(0.25)
    if not rows:
        raise RuntimeError("Binance US 回傳空資料")
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows], unit="ms", utc=True).tz_localize(None),
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [float(r[5]) for r in rows],
        }
    )
    return _finalize(df)


# --------------------------------------------------------------------------
# 3. Kraken（備援）
# --------------------------------------------------------------------------
_KRAKEN_PAIRS = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD"}


def fetch_kraken(
    symbol: str = "BTCUSDT",
    interval: str = "1d",
    start: str = "2019-01-01",
    end: Optional[str] = None,
) -> pd.DataFrame:
    pair = _KRAKEN_PAIRS.get(symbol, symbol)
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440"
    payload = _get_json(url)
    if payload.get("error"):
        raise RuntimeError(f"Kraken 回報錯誤：{payload['error']}")
    result = payload.get("result", {})
    key = next((k for k in result if k != "last"), None)
    if key is None:
        raise RuntimeError("Kraken 回傳空資料")
    rows = result[key]
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows], unit="s", utc=True).tz_localize(None),
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [float(r[6]) for r in rows],
        }
    )
    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end) if end else df["date"].max()
    df = df[(df["date"] >= lo) & (df["date"] <= hi)]
    return _finalize(df)


# --------------------------------------------------------------------------
# 4. CoinGecko（最後備援，僅 OHLC、無成交量）
# --------------------------------------------------------------------------
_COINGECKO_IDS = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana"}


def fetch_coingecko(
    symbol: str = "BTCUSDT",
    interval: str = "1d",
    start: str = "2019-01-01",
    end: Optional[str] = None,
) -> pd.DataFrame:
    coin = _COINGECKO_IDS.get(symbol, "bitcoin")
    days = max(1, (pd.Timestamp.now().normalize() - pd.Timestamp(start)).days)
    url = (
        f"https://api.coingecko.com/api/v3/coins/{coin}/ohlc"
        f"?vs_currency=usd&days={min(days, 365)}"
    )
    rows = _get_json(url)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("CoinGecko 回傳空資料")
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows], unit="ms", utc=True).tz_localize(None),
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [0.0] * len(rows),
        }
    )
    return _finalize(df)


# --------------------------------------------------------------------------
# 統一入口：依序嘗試，記錄實際成功的來源
# --------------------------------------------------------------------------
SOURCES = [
    ("binance_vision", fetch_binance_vision),
    ("binance_us", fetch_binance_us),
    ("kraken", fetch_kraken),
    ("coingecko", fetch_coingecko),
]


def fetch_ohlcv(
    symbol: str = "BTCUSDT",
    interval: str = "1d",
    start: str = "2019-01-01",
    end: Optional[str] = None,
    verbose: bool = True,
):
    """依序嘗試各來源，回傳 ``(DataFrame, 來源名稱, 嘗試紀錄)``。"""
    attempts = []
    for name, fn in SOURCES:
        try:
            df = fn(symbol=symbol, interval=interval, start=start, end=end)
            if len(df) < 50:
                raise RuntimeError(f"資料筆數過少（{len(df)}）")
            attempts.append((name, "OK", f"{len(df)} 根，{df['date'].min().date()} ~ {df['date'].max().date()}"))
            if verbose:
                print(f"[data] 來源 {name} 成功：{len(df)} 根 "
                      f"({df['date'].min().date()} ~ {df['date'].max().date()})")
            return df, name, attempts
        except Exception as exc:  # noqa: BLE001
            attempts.append((name, "FAIL", str(exc)[:160]))
            if verbose:
                print(f"[data] 來源 {name} 失敗：{str(exc)[:120]}")
    raise RuntimeError(f"所有資料來源皆失敗：{attempts}")
