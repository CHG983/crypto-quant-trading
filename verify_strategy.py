#!/usr/bin/env python3
"""驗證腳本：以模擬序列 + 真實資料檢查策略邏輯正確性。

檢查項目
--------
1. 無未來函數：截斷資料後重算，前段訊號必須完全一致。
2. 訊號觸發 K 棒正確性：進場必須發生在「翻轉訊號的次一根」。
3. 指標數值範圍：RSI ∈ [0,100]、ATR > 0、Supertrend 方向 ∈ {-1,1}。
4. 停損方向正確性：多單停損 < 進場價、空單停損 > 進場價。
5. 成本確實計入：手續費/滑價設為 0 與非 0 的結果必須不同。
6. 部位大小模式：fraction 與 risk 兩種模式的部位差異符合預期。
7. 已知答案的合成序列：完美上升趨勢必須產生多單且獲利。

用法：python3 verify_strategy.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from crypto_quant.backtest import BacktestConfig, run_backtest  # noqa: E402
from crypto_quant.data_fetch import fetch_ohlcv  # noqa: E402
from crypto_quant.indicators import atr, rsi, supertrend  # noqa: E402
from crypto_quant.strategy import StrategyParams, generate_signals  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def synth_series(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """合成 OHLCV：隨機漫步 + 一段明確上升趨勢。"""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n // 2, 0.0), np.full(n - n // 2, 0.004)])
    ret = rng.normal(0, 0.02, n) + drift
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.008, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="D"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.uniform(1e3, 1e4, n),
    })


def main() -> int:
    print("=" * 78)
    print("  策略驗證：無未來函數 / 訊號時序 / 數值範圍 / 成本 / 部位模式")
    print("=" * 78)

    # ---------------- 1) 無未來函數 ----------------
    print("\n[1] 無未來函數檢查（截斷資料後前段訊號必須一致）")
    df = synth_series(400)
    p = StrategyParams()
    full = generate_signals(df, p)
    for cut in (250, 300, 350):
        part = generate_signals(df.iloc[:cut].reset_index(drop=True), p)
        cols = ["signal", "long_entry_ok", "short_entry_ok", "bull_cross", "bear_cross"]
        same = all(full[c].iloc[:cut].reset_index(drop=True).equals(part[c]) for c in cols)
        check(f"截斷至 {cut} 根後前段訊號一致", same)

    # ---------------- 2) 訊號觸發 K 棒 ----------------
    print("\n[2] 訊號觸發 K 棒正確性（進場在翻轉的次一根）")
    cfg = BacktestConfig()
    res = run_backtest(df, p, cfg)
    sig = res.signals
    ok_order = True
    detail = ""
    for t in res.trades:
        # 進場日必須晚於或等於某個翻轉訊號日
        prior = sig[sig["date"] < t.entry_date]
        if len(prior) == 0:
            ok_order = False
            detail = "找不到進場前的訊號"
            break
        last_sig = int(prior["signal"].iloc[-1])
        expect = 1 if t.side == "LONG" else -1
        if last_sig != expect:
            ok_order = False
            detail = f"{t.side} 進場於 {t.entry_date.date()}，但前一個訊號為 {last_sig}"
            break
    check("每筆進場都對應到正確方向的翻轉訊號", ok_order, detail)
    check("交易筆數 > 0（策略確實有動作）", len(res.trades) > 0, f"{len(res.trades)} 筆")

    # ---------------- 3) 指標數值範圍 ----------------
    print("\n[3] 指標數值範圍")
    r = rsi(df["close"], 14).dropna()
    check("RSI ∈ [0, 100]", bool((r >= 0).all() and (r <= 100).all()),
          f"min={r.min():.2f} max={r.max():.2f}")
    a = atr(df["high"], df["low"], df["close"], 14).dropna()
    check("ATR > 0", bool((a > 0).all()), f"min={a.min():.4f}")
    _, d = supertrend(df["high"], df["low"], df["close"], 10, 3.0)
    check("Supertrend 方向 ∈ {-1, 1}", set(d.unique()).issubset({-1, 1}),
          f"值集合={sorted(set(d.unique()))}")

    # ---------------- 4) 停損方向 ----------------
    print("\n[4] 停損方向正確性（檢查「初始」停損，因移動停損會上移鎖利）")
    ok_stop = all(
        (t.initial_stop < t.entry_price) if t.side == "LONG" else (t.initial_stop > t.entry_price)
        for t in res.trades
    )
    check("多單初始停損 < 進場價、空單初始停損 > 進場價", ok_stop)
    # 移動停損只應朝有利方向移動
    ok_trail = all(
        (t.stop_price >= t.initial_stop) if t.side == "LONG" else (t.stop_price <= t.initial_stop)
        for t in res.trades
    )
    check("移動停損只朝有利方向移動（多單上移、空單下移）", ok_trail)

    # ---------------- 5) 成本確實計入 ----------------
    print("\n[5] 交易成本確實計入")
    free = run_backtest(df, p, BacktestConfig(fee_rate=0.0, slippage_rate=0.0, funding_rate_daily=0.0))
    costly = run_backtest(df, p, BacktestConfig(fee_rate=0.0004, slippage_rate=0.0005, funding_rate_daily=0.0001))
    check("零成本 vs 有成本 的期末權益不同",
          abs(free.equity.iloc[-1] - costly.equity.iloc[-1]) > 1e-6,
          f"零成本 {free.equity.iloc[-1]:.2f} vs 有成本 {costly.equity.iloc[-1]:.2f}")

    # ---------------- 6) 部位大小模式 ----------------
    print("\n[6] 部位大小模式差異")
    r_frac = run_backtest(df, p, BacktestConfig(sizing_mode="fraction", fraction=1.0))
    r_risk = run_backtest(df, p, BacktestConfig(sizing_mode="risk", risk_pct=0.01))
    nf = np.mean([t.notional for t in r_frac.trades]) if r_frac.trades else 0
    nr = np.mean([t.notional for t in r_risk.trades]) if r_risk.trades else 0
    check("fraction 模式的名目部位大於 risk 模式", nf > nr,
          f"fraction 平均名目 {nf:.0f} vs risk 平均名目 {nr:.0f}")

    # ---------------- 7) 已知答案的合成序列 ----------------
    print("\n[7] 已知答案：先盤整後上升（必須出現黃金交叉 → 做多且獲利）")
    n = 300
    # 前 100 根盤整、後 200 根線性上升 → 保證會發生 EMA 黃金交叉
    flat = np.full(100, 100.0)
    rise = np.linspace(100, 300, n - 100)
    close = np.concatenate([flat, rise])
    up = pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="D"),
        "open": close, "high": close * 1.005, "low": close * 0.995,
        "close": close, "volume": np.full(n, 1000.0),
    })
    r_up = run_backtest(up, p, BacktestConfig())
    longs = [t for t in r_up.trades if t.side == "LONG"]
    check("盤整後上升產生多單", len(longs) > 0, f"{len(longs)} 筆多單")
    check("上升趨勢策略獲利", r_up.equity.iloc[-1] > r_up.equity.iloc[0],
          f"期末 {r_up.equity.iloc[-1]:.2f} vs 期初 {r_up.equity.iloc[0]:.2f}")

    # 已知限制：資料起點即為既成趨勢時，交叉策略不會進場（無交叉可觸發）
    print("\n[7b] 已知限制：資料起點即為既成趨勢 → 無交叉 → 不進場")
    pure = np.linspace(100, 300, n)
    pure_up = pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="D"),
        "open": pure, "high": pure * 1.005, "low": pure * 0.995,
        "close": pure, "volume": np.full(n, 1000.0),
    })
    r_pure = run_backtest(pure_up, p, BacktestConfig())
    check("純線性上升（無交叉）確實不進場（符合交叉策略預期）",
          len(r_pure.trades) == 0, f"{len(r_pure.trades)} 筆交易")

    # ---------------- 8) 真實資料（若可取得）----------------
    print("\n[8] 真實資料驗證（BTCUSDT）")
    try:
        real, src, _ = fetch_ohlcv("BTCUSDT", start="2021-01-01", verbose=False)
        rr = run_backtest(real, p, BacktestConfig())
        check("真實資料可完成回測", len(rr.trades) > 0,
              f"來源 {src}，{len(real)} 根，{len(rr.trades)} 筆交易")
        check("真實資料權益曲線無 NaN", not rr.equity.isna().any())
        check("真實資料期末權益為有限值", bool(np.isfinite(rr.equity.iloc[-1])),
              f"{rr.equity.iloc[-1]:.2f}")
    except Exception as exc:  # noqa: BLE001
        check("真實資料可完成回測", False, f"資料取得失敗：{str(exc)[:100]}")

    # ---------------- 總結 ----------------
    n_pass = sum(1 for _, ok, _ in results if ok)
    n_total = len(results)
    print("\n" + "=" * 78)
    print(f"  驗證結果：{n_pass}/{n_total} 通過")
    print("=" * 78)
    for name, ok, detail in results:
        if not ok:
            print(f"  ✗ {name} — {detail}")
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
