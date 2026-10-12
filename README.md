# 加密貨幣量化交易框架 (Crypto Quant Trading)

一套完整的加密貨幣量化交易研究框架，包含 **TradingView Pine Script 指標／策略雙版**、**Python 回測與 walk-forward 驗證**、**即時訊號引擎**、**市場掃描**、**自動化監控**，以及**瀏覽器直連即時 API 的可視化儀表板**。

> ⚠️ **本專案僅供研究與教育用途，不構成任何投資建議。** 詳見文末[免責聲明](#免責聲明)。

---

## 🌐 線上瀏覽（GitHub Pages）

| 頁面 | 說明 |
|---|---|
| [**儀表板（即時）**](https://chg983.github.io/crypto-quant-trading/dashboard.html) | 瀏覽器直連幣安 API，自動刷新 |
| [完整研究報告](https://chg983.github.io/crypto-quant-trading/) | 策略邏輯、回測、walk-forward、參數說明 |
| [Pine 編譯驗證報告](https://chg983.github.io/crypto-quant-trading/pine-verification.html) | Pine Script 靜態檢查與 PineTS 編譯證據 |

---

## 📁 專案結構

```
.
├── index.html                  # Pages 首頁：完整研究報告
├── dashboard.html              # Pages：即時儀表板（瀏覽器直連 API）
├── pine-verification.html      # Pages：Pine 編譯驗證報告
├── images/                     # 報告用圖表（權益曲線、回撤、參數熱圖…）
├── img/                        # Pine 驗證截圖
│
├── crypto_quant/               # 核心 Python 套件
│   ├── data_fetch.py           #   資料抓取（幣安）
│   ├── indicators.py           #   EMA / RSI / MACD / ATR / Supertrend
│   ├── strategy.py             #   策略邏輯與訊號產生
│   ├── backtest.py             #   回測引擎
│   ├── metrics.py              #   績效指標（Sharpe / MDD / 勝率…）
│   ├── optimize.py             #   參數網格搜尋
│   ├── walkforward.py          #   滾動 walk-forward 驗證
│   ├── wfo.py                  #   WFO 模式比較
│   ├── live.py                 #   即時報價與 K 線抓取
│   ├── signal_engine.py        #   即時訊號引擎
│   ├── scanner.py              #   市場掃描
│   └── monitor.py              #   自動化監控與狀態變化偵測
│
├── live_web/                   # 即時儀表板前端
│   ├── live_engine.js          #   前端策略引擎（JS 版，與 Python 數值等價）
│   ├── dashboard_template.html #   頁面模板
│   ├── build_dashboard_v6.py   #   建置腳本（注入引擎與歷史資料）
│   ├── serve_proxy.py          #   CORS 備援代理（直連失敗時使用）
│   ├── engine_test.mjs         #   Python ↔ JS 數值等價性測試
│   └── make_baseline.py        #   產生等價性測試基準
│
├── pine/                       # TradingView Pine Script（v6）
│   ├── ctma_indicator.pine     #   指標版（看盤畫買賣點、停損線、資訊面板）
│   ├── ctma_strategy.pine      #   策略版（Strategy Tester 回測）
│   └── crypto_trend_momentum_atr.pine
├── pine_verify/                # Pine 驗證工具（PineTS 編譯＋負向對照）
├── pine_static_check.py        # Pine 靜態檢查器
│
├── output/                     # 實際執行輸出
│   ├── live_signals.json       #   即時訊號
│   ├── scan_*.json / *.csv     #   市場掃描排行
│   ├── monitor_snapshot.json   #   監控快照
│   ├── monitor_run.json        #   本輪監控結果
│   ├── signals_history.jsonl   #   訊號歷史（逐行 JSON）
│   ├── state.json              #   狀態機（跨執行比對用）
│   └── monitor.log             #   執行日誌
│
├── docs/                       # 補充文件
├── run_*.py                    # CLI 進入點
├── verify_*.py                 # 驗證腳本
└── requirements.txt
```

---

## 🚀 快速開始

```bash
pip install -r requirements.txt

# 即時訊號（BTC / ETH / SOL）
python3 run_live.py

# 市場掃描（24h 成交量前 25 名，日線）
python3 run_scan.py --top 25 --interval 1d

# 自動化監控（輸出狀態變化、寫入歷史）
python3 run_monitor.py --top 20 --interval 1h

# 查詢某標的最近一次訊號
python3 run_monitor.py --query BTCUSDT

# 回測
python3 run_walkforward.py --symbol BTCUSDT --interval 1d
python3 run_wfo.py --symbol BTCUSDT
```

### 即時儀表板

```bash
cd live_web
python3 -m http.server 8787
# 開啟 http://127.0.0.1:8787/dashboard.html
```

> **為何需要 HTTP 伺服器？** 瀏覽器對 `file://` 來源的 `fetch()` 有安全限制。用 HTTP 開啟才能正常抓取即時資料。

### 排程（cron 範例）

```cron
# 每小時整點執行監控
0 * * * * cd /path/to/repo && /usr/bin/python3 run_monitor.py --top 20 --interval 1h >> output/monitor.log 2>&1

# 每天早上 8 點掃描
0 8 * * * cd /path/to/repo && /usr/bin/python3 run_scan.py --top 25 --interval 1d >> output/monitor.log 2>&1
```

---

## 📐 策略設計

### 三層過濾架構

```
【趨勢層】EMA 快慢線交叉 或 Supertrend 翻轉
   ↓
【動能層】RSI 站中軸同側 ＋ MACD 柱方向一致   ← 過濾假突破
   ↓
【波動層】ATR% ≥ 門檻（可關閉）                ← 過濾盤整泥沼
   ↓
進場 → 初始停損 3×ATR ／ 移動停損 5×ATR ／ 可選停利 6×ATR
```

| 層級 | 指標 | 作用 |
|---|---|---|
| 趨勢 | EMA 快線／慢線交叉、Supertrend | 判斷方向 |
| 動能 | RSI、MACD | 確認動能一致，過濾假突破 |
| 波動 | ATR、ATR% | 停損距離、部位大小、波動過濾 |
| 風控 | ATR 停損 / 移動停損 / 停利 | 出場條件 |

**所有參數皆可透過 `input`（Pine）或 CLI 參數／`StrategyParams`（Python）調整。**

---

## 🧪 驗證狀態（誠實說明）

| 項目 | 方法 | 結果 |
|---|---|---|
| Pine 靜態檢查 | 官方文件規則比對 | **46 PASS / 0 WARN / 0 FAIL** |
| Pine 編譯（第三方） | PineTS（開源 Pine v6 編譯器） | **COMPILED_AND_EXECUTED_OK** |
| Pine 編譯（TradingView） | 真實 TradingView Pine Editor | ⚠️ **未取得**（沙盒無帳號憑證） |
| Python ↔ JS 等價性 | 逐值比對同一份 K 線 | **34/34 PASS**，最大殘差 5.09e-11 |
| 瀏覽器行為驗證 | 真實 Chromium（桌面 1280 / 手機 390） | **22/22 PASS**，橫向溢出 0px、破圖 0 |
| 監控邏輯單元測試 | `verify_monitor.py` | **22/22 PASS** |

### ⚠️ 關於 Pine Script 「可編譯」

沙盒**無法執行 TradingView 的 Pine 編譯器**（需登入帳號）。
- **已做的**：靜態檢查 46/46 ＋ 開源 Pine v6 引擎 PineTS 實際編譯並執行成功
- **未做的**：TradingView 官方編譯（無憑證，未嘗試登入或破解驗證碼）
- **已知限制**：PineTS 只抓語法與未知符號（實測抓到 2/5 種故意植入的錯誤），**不檢查型別／作用域**

👉 **請自行把 `pine/*.pine` 貼進 [TradingView Pine Editor](https://www.tradingview.com/pine/) 編譯確認一次。** 若有錯誤，TradingView 會直接指出行號。

### 資料來源與延遲

- 全部市場資料來自 **幣安公開 API**（`data-api.binance.vision`），**非模擬資料**
- `api.binance.com` 在沙盒地區受限，故使用 `data-api.binance.vision`
- 儀表板驗證時與幣安伺服器時間偏移 **+0.085 秒**
- 掃描排行依據為 **24h 成交量**（非市值；幣安公開 API 不提供市值）

---

## ⚠️ 已知限制

1. **Pine 腳本未經 TradingView 官方編譯器驗證**（見上）
2. **訊號強度是啟發式排序分數，不是機率**，未經校準
3. **未模擬流動性限制**，假設任何價位都能成交
4. **walk-forward 已證實此策略 Sharpe 低於買進持有** —— 價值在回撤控制，非報酬最大化
5. **通知功能未經真實發送驗證**（無 webhook / Telegram 憑證，僅寫入本機檔案）
6. **監控只記錄程式啟動後的變化**；時間軸標示「回補」的事件為歷史重放，非當時即時偵測
7. **已收盤日線的收盤價在同一交易日內不會變** —— 儀表板盤中重複刷新時，主口徑價格相同是正確行為，非快取

---

## 📜 授權

MIT License — 見 [LICENSE](LICENSE)。

---

## 免責聲明

本專案所有內容（含指標腳本、策略邏輯、即時訊號、掃描排行、監控事件與參數）僅供**研究與教育用途**，**不構成任何投資建議、要約或推薦**。訊號基於歷史價格計算，**無法預測未來走勢**；歷史績效不代表未來表現。回測與訊號系統存在過度擬合、存活者偏差、交易成本估計誤差、流動性假設等固有限制，實際交易結果可能與之顯著不同。即時資料可能因網路、端點或交易所因素延遲、中斷或失真，**不可作為唯一交易依據**。加密貨幣波動極大，可能導致**全部本金損失**。任何交易決策應由使用者自行判斷並承擔風險。

All content in this project (including indicator scripts, strategy logic, real-time signals, ranking scans, monitored events, and parameters) is intended solely for research and educational purposes and does not constitute investment advice, an offer, or a recommendation. Signals are calculated based on historical prices and cannot predict future trends; past performance is not indicative of future results. Backtesting and signal systems are subject to inherent limitations—such as overfitting, survivorship bias, errors in estimating transaction costs, and liquidity assumptions—meaning actual trading results may differ significantly. Real-time data may be subject to delays, interruptions, or inaccuracies due to network, endpoint, or exchange issues and should not serve as the sole basis for trading decisions. Cryptocurrency markets are highly volatile and carry the risk of a total loss of principal. Users are responsible for making their own trading decisions and assuming all associated risks.
