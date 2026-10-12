"""簡單網格搜尋（含樣本內/外切分，用於示範過度擬合風險）。"""
from __future__ import annotations

from dataclasses import replace
from typing import List, Tuple

import numpy as np
import pandas as pd

from .backtest import BacktestConfig, run_backtest
from .metrics import sharpe, total_return
from .strategy import StrategyParams


def grid_search(
    df: pd.DataFrame,
    ema_fast_list: List[int] = (9, 13, 21, 34),
    ema_slow_list: List[int] = (34, 55, 89),
    atr_mult_list: List[float] = (1.5, 2.0, 2.5, 3.0, 4.0),
    metric: str = "sharpe",
    cfg: BacktestConfig | None = None,
) -> pd.DataFrame:
    """回傳 DataFrame，index=(ema_fast, ema_slow)，columns=atr_mult，值為指定指標。"""
    cfg = cfg or BacktestConfig()
    rows = []
    for ef in ema_fast_list:
        for es in ema_slow_list:
            if ef >= es:
                continue
            for am in atr_mult_list:
                p = StrategyParams(ema_fast=ef, ema_slow=es)
                c = replace(cfg, atr_stop_mult=am)
                try:
                    res = run_backtest(df, p, c)
                    val = sharpe(res.equity) if metric == "sharpe" else total_return(res.equity)
                except Exception:  # noqa: BLE001
                    val = np.nan
                rows.append({"ema_fast": ef, "ema_slow": es, "atr_mult": am, metric: val})
    return pd.DataFrame(rows)


def in_out_sample(
    df: pd.DataFrame,
    params: StrategyParams,
    cfg: BacktestConfig,
    split: float = 0.7,
) -> Tuple[dict, dict]:
    """以時間順序切分樣本內/外，回傳兩段各自的績效摘要。"""
    from .metrics import summarize

    cut = int(len(df) * split)
    is_df = df.iloc[:cut].reset_index(drop=True)
    oos_df = df.iloc[cut:].reset_index(drop=True)
    r_is = run_backtest(is_df, params, cfg)
    r_oos = run_backtest(oos_df, params, cfg)
    return (
        summarize(r_is.equity, r_is.trades, "In-sample"),
        summarize(r_oos.equity, r_oos.trades, "Out-of-sample"),
    )
