# Crypto Quant — 加密貨幣量化交易訊號與回測框架

一套針對**加密貨幣特性**（24/7 交易、無漲跌停、高波動）設計的量化交易框架，
包含 **TradingView Pine Script 策略腳本** 與 **Python 訊號產生／回測引擎**。

---

## 策略：CTMA（Crypto Trend Momentum ATR）

### 設計理由

| 加密貨幣特性 | 對應設計 |
|---|---|
| 24/7 交易、無收盤跳空保護 | 停損用 **ATR 波動度自適應**，而非固定百分比 |
| 波動大、趨勢強 | 趨勢過濾用 **EMA(21/55)** 而非 SMA，對近期價格更敏感 |
| 假訊號多 | **雙重動能確認**：RSI 站中軸同側 + MACD 柱方向一致 |
| 盤整泥沼 | **ATR% 波動度門檻**，低波動時不進場 |
| 永續合約成熟 | 預設**允許做空**（可關閉） |

### 進出場邏輯

```
做多進場：EMA 快線向上穿越慢線  且 RSI > 50  且 MACD 柱 > 0  且 ATR% >= 門檻
做空進場：EMA 快線向下穿越慢線  且 RSI < 50  且 MACD 柱 < 0  且 ATR% >= 門檻
出場    ：反向交叉，或 ATR 停損 / ATR 移動停損 / 選用 ATR 停利
```

> **重要語意**：均線交叉是「市場狀態翻轉」，與「是否進場」是兩件事。
> 看多翻轉**一定**平掉空單；若空手且動能過濾通過，才建立多單。看空翻轉同理。

### 預設參數

| 參數 | 預設 | 說明 |
|---|---|---|
| `ema_fast` / `ema_slow` | 21 / 55 | 趨勢過濾 |
| `rsi_len` | 14 | 動能確認 |
| `macd` | 12 / 26 / 9 | 動能確認 |
| `atr_len` | 14 | 波動度 |
| `atr_stop_mult` | 3.0 | 初始停損距離 |
| `atr_trail_mult` | 5.0 | 移動停損距離 |
| `min_atr_pct` | 0.5 | 波動度門檻（%） |
| `sizing_mode` | `fraction` | 部位模式（見下） |

---

## TradingView Pine Script（指標版 ＋ 策略版）

`pine/` 目錄下提供兩份 **Pine Script v6** 完整腳本，共用同一套 CTMA 訊號邏輯：

| 檔案 | 型別 | 用途 | 行數 |
|---|---|---|---|
| `pine/ctma_indicator.pine` | `indicator` | 看盤：畫買賣點、停損／停利線、資訊面板；自行維護持倉狀態機 | 341 |
| `pine/ctma_strategy.pine` | `strategy` | 回測：交給 TradingView Strategy Tester 撮合，含手續費與滑價 | 280 |

### 為什麼要兩份？

指標版**不能**呼叫 `strategy.*`（拿不到部位與績效），策略版若把全套繪圖塞進去會拖慢回測。
實務分工：**看盤用指標版、驗證用策略版**。

### 使用方式

1. 打開 TradingView → 下方 **Pine Editor** → 貼上整份檔案 → 按 **Save** → **Add to chart**。
2. 指標版：在圖上會看到 ▲▼ 買賣點、紅／綠停損停利線、右上資訊面板。
3. 策略版：開啟 **Strategy Tester** 面板看淨利／MDD／勝率／Profitable Factor。
   記得把 `commission_value` 改成你交易所的實際費率。

### 參數（兩版各 31 個 input，全部可調）

分成六個群組：**① 趨勢**（EMA 快慢線／Supertrend）、**② 動能**（RSI／MACD）、
**③ 波動度與風控**（ATR 停損／移動停損／停利）、**④ 部位與方向**、**⑤ 顯示**、**⑥ 通知**。
完整表格見報告網頁第 13 章。

### 靜態檢查

```bash
python3 pine_static_check.py pine/ctma_indicator.pine pine/ctma_strategy.pine
```

兩份檔案各 23 項、合計 **46/46 全部 PASS**，檢查涵蓋：
`var` 是否全在全域、tuple 解構、`hline`／`plotstyle` 用法、
`alertcondition` 是否在全域、所有 input 是否被使用、
是否使用 `request.security`／`lookahead`（本專案完全未使用）、
歷史引用是否以 `nz()` 保護、以及常見錯誤 lint
（裸 `na` 布林運算、區塊內型別宣告、`location.absolute` 型別、序列當純量條件）。

> ⚠️ **誠實聲明**：本沙盒**無法執行 TradingView 的 Pine 編譯器**。
> 上述 46/46 是**靜態分析**（依 Pine v6 官方文件規則做結構與模式檢查），
> **不等同於編譯通過**。強烈建議貼進 Pine Editor 編譯一次確認；
> 若有錯誤，TradingView 會直接指出行號，修正成本極低。

開發過程中被靜態檢查抓出並已修正的 3 個真實問題：
`location.absolute` 誤傳布林值（需價格序列）、同作用域重複宣告變數、
以及裸 `na` 參與布林運算（Pine v5+ 的 `bool` 不可為 na）。

---

## 安裝

```bash
pip install -r requirements.txt
```

僅需 `pandas`、`numpy`、`matplotlib`，**無需付費或難以安裝的套件**。

---

## 執行

### 基本回測

```bash
python3 run_signals.py --symbol BTCUSDT --start 2019-01-01
```

### 常用選項

```bash
# 換標的
python3 run_signals.py --symbol ETHUSDT --start 2019-01-01

# 關閉移動停損（長線趨勢較友善）
python3 run_signals.py --symbol BTCUSDT --no-trailing --atr-stop 3.0

# 啟用停利
python3 run_signals.py --symbol BTCUSDT --take-profit --atr-tp 6.0

# 只用多單
python3 run_signals.py --symbol BTCUSDT --no-short

# 改用 Supertrend 過濾趨勢
python3 run_signals.py --symbol BTCUSDT --use-supertrend

# 網格搜尋 + 樣本內外切分
python3 run_signals.py --symbol BTCUSDT --optimize

# 改用 ATR 風險比例部位（每筆只虧固定比例）
python3 run_signals.py --symbol BTCUSDT --sizing risk --risk 0.01
```

### 驗證腳本

```bash
python3 verify_strategy.py
```

檢查：無未來函數、訊號觸發 K 棒、指標數值範圍、停損方向、
成本計入、部位模式差異、已知答案合成序列、真實資料完整性。

---

## 滾動前進驗證（Walk-Forward Validation）

### 為什麼不用單次樣本內／外切分

單次切分只有**一個**樣本外區段，切點是人為選的 —— 換個切點結論就可能不同。
更關鍵的是它無法回答「**這組參數在未來還有效嗎？**」，因為它只被檢驗過一次。

Walk-forward 把時間軸切成連續的 (訓練窗 → 測試窗) 配對：

```
|---- train 1 ----|-- test 1 --|
          |---- train 2 ----|-- test 2 --|
                    |---- train 3 ----|-- test 3 --|
```

每個窗口**只用該窗口訓練窗的資料**做網格搜尋，選出最佳參數後，
**只用那一組參數**在緊接的測試窗上回測。所有測試窗串接起來，
就是一條完全由樣本外資料構成的權益曲線 —— **這是唯一可信的績效來源**。

### 執行

```bash
# 預設：rolling 模式、訓練 730 根、測試 180 根、以 Sharpe 選參
python3 run_walkforward.py --symbol BTCUSDT

# 擴張窗（anchored）：訓練窗起點固定、長度遞增
python3 run_walkforward.py --symbol ETHUSDT --mode anchored --train 900 --test 180

# 改用總報酬選參、每次前進 90 根（測試窗重疊 50%）
python3 run_walkforward.py --symbol BTCUSDT --metric total_return --step 90

# 驗證切窗正確性與無未來函數
python3 verify_walkforward.py
```

### 可調參數

| 參數 | 預設 | 說明 |
|---|---|---|
| `--train` | 730 | 訓練窗長度（根） |
| `--test` | 180 | 測試窗長度（根） |
| `--step` | = `--test` | 每次前進根數（預設測試窗無縫相接） |
| `--mode` | `rolling` | `rolling`（滾動）或 `anchored`（擴張） |
| `--metric` | `sharpe` | 訓練窗選參目標：`sharpe`／`sortino`／`calmar`／`total_return` |
| `--min-trades` | 3 | 訓練窗最少交易筆數，不足則不納入評選 |

### 避免未來函數的三道防線

1. **訓練窗與測試窗絕不重疊**：`train_end == test_start`，以索引嚴格切分。
2. **參數只能用訓練窗決定**：網格搜尋只在 `train_df` 上執行，測試窗資料在選參時完全不可見。
3. **暖機期禁止進場**：測試窗回測時往前接一段訓練窗尾端（純過去資料）暖機指標，
   並以 `trade_start` 參數禁止在暖機期開新倉 —— 否則測試窗績效會被暖機期交易污染。

### 輸出檔案

| 檔案 | 內容 |
|---|---|
| `{symbol}_wf_windows.csv` | 每個窗口的訓練／測試區間與選出的最佳參數 |
| `{symbol}_wf_oos_equity.csv` | 樣本外串接權益曲線 |
| `{symbol}_wf_trades.csv` | 樣本外交易明細 |
| `{symbol}_wf_summary.json` | 完整結果（含參數穩定度、單次切分對照、樣本外衰退） |
| `{symbol}_wf_equity.png` | OOS 權益 vs 買進持有 vs 單次切分 |
| `{symbol}_wf_window_returns.png` | 各窗口樣本外報酬分布 |
| `{symbol}_wf_params.png` | 參數隨時間變化（穩定度檢視） |
| `{symbol}_wf_train_test.png` | 訓練窗 vs 測試窗績效（樣本外衰退） |
| `{symbol}_wf_drawdown.png` | OOS 回撤曲線 |

### 參數穩定度分析

引擎會統計每個參數在各窗口的**眾數占比**與**唯一值個數**：

* 眾數占比 ≥ 60% → 相對穩定
* 35% ~ 60% → 中度不穩定，需留意過度擬合
* < 35% → 高度不穩定，最佳參數極可能只是雜訊

> ⚠️ 若每個窗口選出的參數差異很大，代表策略**不穩健** ——
> 這通常才是真相，不要因為「某個窗口表現很好」就忽略它。

---

## Walk-forward optimization（WFO）雙模式對照

`run_wfo.py` 把 walk-forward 從「單一模式驗證」升級為**雙模式對照**：
同一組測試窗切點、同一組參數網格、同一組成本模型，**唯一差異是訓練窗的取法**。

| 模式 | 訓練窗取法 | 假設 |
|---|---|---|
| `rolling`（滾動窗） | 長度固定（730 根），隨時間向前滑動 | 近期市場結構比遠期更有代表性 |
| `anchored`（擴張窗） | 起點固定（2019-01-01），長度遞增（730 → 2530 根） | 越多歷史越好 |

### 執行

```bash
# 雙模式對照（預設 rolling + anchored 各跑一次）
python3 run_wfo.py --symbol BTCUSDT
python3 run_wfo.py --symbol ETHUSDT

# 調整切窗與選參指標
python3 run_wfo.py --symbol BTCUSDT --train 730 --test 180 --metric sharpe
python3 run_wfo.py --symbol BTCUSDT --step 90 --min-trades 2

# 驗證雙模式切窗正確性與無未來函數（36 項檢查）
python3 verify_wfo.py
```

### 輸出檔案

| 檔案 | 內容 |
|---|---|
| `{symbol}_wfo_compare.csv` | rolling vs anchored 對照表 |
| `{symbol}_wfo_fourway.csv` | 四方對照（+ 單次切分 + 買進持有） |
| `{symbol}_wfo_rolling_windows.csv` | rolling 逐窗最佳參數 |
| `{symbol}_wfo_anchored_windows.csv` | anchored 逐窗最佳參數 |
| `{symbol}_wfo_rolling_equity.csv` | rolling 樣本外權益曲線 |
| `{symbol}_wfo_anchored_equity.csv` | anchored 樣本外權益曲線 |
| `{symbol}_wfo_summary.json` | 完整結果（含穩定度、衰退、結論） |
| `{symbol}_wfo_mode_equity.png` | rolling vs anchored 權益疊圖 |
| `{symbol}_wfo_window_returns.png` | 各窗口報酬（雙模式） |
| `{symbol}_wfo_params.png` | 參數隨時間變化（雙模式） |
| `{symbol}_wfo_train_test.png` | 訓練 vs 測試（雙模式） |
| `{symbol}_wfo_drawdown.png` | 回撤（雙模式） |

### 實測結果（BTCUSDT / ETHUSDT，2019-01-01 ~ 2026-10-01）

| 指標 | BTC rolling | BTC anchored | ETH rolling | ETH anchored |
|---|---|---|---|---|
| 樣本外總報酬 | +94.55% | +97.85% | **+15.59%** | **−35.59%** |
| 最大回撤 | **−37.38%** | −50.81% | **−53.25%** | −70.31% |
| Sharpe | **0.52** | 0.51 | **0.29** | 0.13 |
| 參數穩定度 | 61.4% | 65.9% | 68.2% | 88.6% |
| 樣本外衰退 | −61.9% | −67.6% | −64.3% | −80.8% |

**結論：rolling 較穩健**（BTC 3:2、ETH 4:1）。anchored 參數較穩定，但把 2019–2020 的舊市場結構
一路帶進後期決策，在 ETH 上甚至虧損 35.59%。**但兩者都打不敗買進持有** ——
WFO 的價值在把最大回撤砍掉 40–50%，不在報酬最大化。

> ⚠️ **參數穩定 ≠ 有效**：ETH 的 anchored 參數最穩定（88.6%），績效卻最差。
> 另注意 ATR 移動停損倍數 100% 穩定**不具資訊量**（網格中只有一個值）。

---

## 即時儀表板（瀏覽器直連版）

### 與前一版的根本差異

| | 前一版 | 本版 |
|---|---|---|
| 資料取得 | Python 先抓好，內嵌 `const DATA = {...}` | **瀏覽器**用 `fetch()` 直接呼叫幣安 API |
| 頁面性質 | 產生當下的**快照** | **每次開啟／刷新都是即時** |
| 策略計算 | Python 算好 | **前端 `live_engine.js` 即時重算** |
| 更新方式 | 需重跑 `build_dashboard.py` | 每 30 秒自動刷新（可調 15s–2min 或關閉） |

### 執行方式

`fetch()` 從 `file://` 來源不可靠，請用 HTTP 開啟：

```bash
# 方式 A：最簡單（僅靜態檔，資料走瀏覽器直連幣安）
cd webpages/crypto-quant-report_v6
python3 -m http.server 8787
# 瀏覽器開 http://127.0.0.1:8787/dashboard.html
```

```bash
# 方式 B：附反向代理（直連被 CORS／地區限制擋掉時使用）
python3 documents/crypto-quant_v7/live_web/serve_proxy.py --port 8787 --open
# 靜態檔 + /binance/** 反向代理，同源，無 CORS 問題
```

### 重新產生儀表板（更新第 3 節的歷史快照時）

```bash
cd documents/crypto-quant_v7

# 1) 先跑監控產生最新快照
python3 run_monitor.py --top 20 --interval 1h

# 2) 產生儀表板（會驗證內嵌引擎雜湊）
python3 live_web/build_dashboard_v6.py \
  --template live_web/dashboard_template.html \
  --engine live_web/live_engine.js \
  --history output/monitor_snapshot.json \
  --parity live_web/verify/engine_parity.json \
  --out /workspace/webpages/crypto-quant-report_v6/dashboard.html
```

> ⚠️ `--out` 請用**絕對路徑**。相對路徑曾導致靜默寫到 `documents/webpages/…`
> 而非正確位置（已加防呆，路徑不含 `webpages` 或含 `documents` 時會中止）。

### 驗證

```bash
cd documents/crypto-quant_v7/live_web

# 引擎等價性：JS 重算 vs Python 基準，逐值比對（需先 make_baseline.py）
python3 make_baseline.py --symbol BTCUSDT --interval 1d --limit 400 --outdir verify
node engine_test.mjs --glob verify --json verify/engine_parity.json

# 瀏覽器端行為（真實 Chromium：溢出、破圖、刷新確實更新）
python3 verify_live_dashboard.py --url http://127.0.0.1:8787/dashboard.html \
  --outdir verify --wait-seconds 36 --refresh-mode button
```

實測結果：引擎等價性 **34/34 PASS**（最大殘差 5.09e-11）；
瀏覽器行為 **22/22 PASS**。詳見 `live_web/REPORT.md`。

### 資料性質（重要）

- **第 1、2 節 = 即時**：每次刷新都重新抓取幣安 API 並於前端重算。
- **第 3 節 = 歷史快照**：內嵌自 `monitor_snapshot.json`，**不隨刷新更新**，頁面已明文標示。
- 標示「回補」的時間軸事件為歷史 K 線重放，非當時即時偵測。
- **已收盤日線的收盤價在同一交易日內不會變**，故盤中重複刷新可能看到相同「價格」——
  這是正確行為，不是快取。逐次變動的是未收盤 K 棒的即時市價與時間欄位。

---

## 部位大小模式（重要）

| 模式 | 公式 | 適用 |
|---|---|---|
| `fraction`（預設） | 名目部位 = 權益 × `fraction` | 與買進持有基準**公平比較** |
| `risk` | 名目部位 = (權益 × `risk_pct`) / 停損距離 | 嚴格風控，每筆只虧固定比例 |

> ⚠️ **實測發現**：`risk` 模式在 ATR 停損很寬時（BTC 日線 3×ATR ≈ 9% 價格），
> 只投入約 **10% 權益**，報酬被大幅稀釋（+82% vs `fraction` 的 +1569%）。
> 這是「風險平價」的固有特性，不是 bug —— 但做策略 vs B&H 比較時必須用 `fraction`。

---

## 資料來源

依序嘗試，自動備援，並在輸出中**誠實記錄實際使用的來源**：

1. **Binance 公開資料端點** `data-api.binance.vision`（主要，無地區限制）
2. Binance US `api.binance.us`
3. Kraken `api.kraken.com`
4. CoinGecko `api.coingecko.com`（僅 OHLC，無成交量）

> 沙盒實測：`api.binance.com` 因**地區限制**回傳 `Service unavailable from a
> restricted location`；Yahoo Finance 回傳 **429**。故實際使用 `data-api.binance.vision`。

---

## 輸出檔案

| 檔案 | 內容 |
|---|---|
| `{symbol}_daily.csv` | 原始 OHLCV |
| `{symbol}_signals.csv` | 逐日訊號表（日期／價格／訊號／停損／部位建議） |
| `{symbol}_trades.csv` | 交易明細（含出場原因、持有根數） |
| `{symbol}_summary.json` | 績效統計（策略 vs 買進持有） |
| `{symbol}_equity.png` | 權益曲線 |
| `{symbol}_price_trades.png` | 價格、均線與買賣點 |
| `{symbol}_drawdown.png` | 回撤曲線 |
| `{symbol}_optimize.png` | 網格搜尋熱圖（`--optimize` 時） |

---

## 成本模型

| 項目 | 預設 | 說明 |
|---|---|---|
| 手續費 | 0.04% | taker，進出各收一次 |
| 滑價 | 0.05% | 進出各收一次 |
| 資金費率 | 0.01%/日 | 永續合約持倉成本，僅持倉時扣 |

---

## 避免未來函數

* 第 t 根收盤後才得知的訊號，在**第 t+1 根開盤**執行。
* 所有指標（EMA/RSI/MACD/ATR/Supertrend）皆為因果計算。
* `verify_strategy.py` 以「截斷資料後前段訊號必須一致」驗證。

---

## Pine Script

`pine/crypto_trend_momentum_atr.pine` — Pine v5，可直接貼進 TradingView。

* 含 `alertcondition` × 4 與動態 `alert()`
* 圖表標示買賣點、停損線、EMA、Supertrend
* 右上角資訊面板（趨勢／RSI／MACD／ATR%／持倉／停損）
* 所有 `var` 皆在全域作用域（避免區域宣告的狀態遺失問題）
* 所有 input 皆實際被使用

---

## 已知限制

1. **僅日線**，未測試分鐘級（加密貨幣高頻特性未涵蓋）。
2. **未計入交易所提領費、借貸利率變動**。
3. **資金費率以固定值近似**，實際每 8 小時浮動。
4. **未模擬流動性限制**，假設任何價位都能成交。
5. **交叉策略的固有特性**：若資料起點即為既成趨勢，不會有交叉可觸發 → 不進場。
6. **最佳化結果不可直接採用**，樣本外表現通常顯著衰退（見報告）。
7. **Walk-forward 的窗口數受資料長度限制**：訓練窗 + 測試窗越長，可切出的窗口越少，
   統計上越不穩定。本專案 2831 根日線、730/180 配置下可切出約 11 個窗口。
8. **Walk-forward 只驗證「參數選擇流程」，不保證獲利**：它證明的是「若照此流程
   在歷史上滾動選參，結果會如何」，而非「未來會賺錢」。

---

## 投資免責聲明

本專案所有回測結果皆為**歷史模擬**，不構成任何投資建議或要約。
歷史績效不代表未來表現。回測存在過度擬合、存活者偏差、交易成本估計誤差等
固有限制，實際交易結果可能與回測顯著不同。加密貨幣波動極大，可能導致全部本金損失。

As an AI Agent, I cannot provide legally protected, personalized investment advice.
This analysis is for informational purposes only and does not constitute investment
advice or recommendations. Consult a licensed financial advisor before making
investment decisions.
