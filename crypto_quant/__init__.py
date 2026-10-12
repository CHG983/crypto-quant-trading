"""crypto-quant：加密貨幣量化交易訊號與回測框架。

模組
----
data_fetch  多來源歷史資料抓取（Binance vision / Binance US / Kraken / CoinGecko）
indicators  EMA / RSI / MACD / ATR / Supertrend
strategy    趨勢 + 動能 + 波動度過濾的訊號產生
backtest    逐日事件驅動回測引擎（含 ATR 停損、移動停損、部位大小）
metrics     績效指標（CAGR / MDD / Sharpe / 勝率 / 盈虧比 …）
plots       matplotlib 繪圖（權益曲線、價格與買賣點、回撤）
walkforward 滾動前進驗證（rolling / anchored 窗、逐窗選參、樣本外串接）
wf_plots    walk-forward 專用繪圖（OOS 權益、窗口報酬、參數演化、訓練vs測試）
wfo         walk-forward optimization 雙模式對照引擎（rolling vs anchored）
wfo_plots   WFO 雙模式對照繪圖（權益疊圖、窗口報酬、參數對照、訓練vs測試、回撤）
"""

__version__ = "3.0.0"
