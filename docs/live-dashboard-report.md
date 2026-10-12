# 即時儀表板改造 — 驗證報告（瀏覽器直連版）

本檔記錄「把儀表板從內嵌靜態快照改為瀏覽器直接抓取即時 API」的**實際驗證結果**，
並明確區分「即時資料」與「歷史快照」。

- 引擎：`live_engine.js` v1.0.0
- 儀表板：`webpages/crypto-quant-report_v6/dashboard.html`（由 `build_dashboard_v6.py` 產生）
- 驗證時間：2026-10-12（UTC）

---

## 1. 結論摘要

| 項目 | 結果 |
|---|---|
| Python ↔ JS 引擎等價性 | **34/34 PASS**，最大殘差 `5.09e-11`（float 雜訊等級） |
| 瀏覽器端行為驗證 | **22/22 PASS（ALL PASS）** |
| CORS | **不需代理**，瀏覽器可直連 `data-api.binance.vision` |
| 效能 | 首輪 3 檔即時 + 25 檔掃描，約 25–35 秒完成 |

---

## 2. CORS：採用哪一種方案

**採用「瀏覽器直連」，不需要代理。**

實測回應標頭：

```
$ curl -sD- -H "Origin: https://static.teamily.ai" \
    "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=2"
access-control-allow-origin: *
```

因此 `fetch()` 可從任何來源（含 `file://` 以外的 http 來源）直接呼叫。
`serve_proxy.py` 仍隨附，作為**備援**（公司網路／地區限制／擴充套件攔截時使用），
其反向代理與 allow-list 已實測：

```
GET /__health                        → {"ok": true, ...}
GET /dashboard.html                  → 200
GET /binance/api/v3/klines?...       → 正常回傳 K 線
GET /binance/api/v3/secret           → {"error": "endpoint not allowed by proxy"}
```

頁面本身的**主要路徑完全走直連**；只有在直連失敗時才會改打 `/binance/...`。

---

## 3. 瀏覽器端行為驗證（22/22）

以真實 Chromium（Playwright）執行 `verify_live_dashboard.py`。

### 桌面 1280×800
| 檢查 | 結果 | 證據 |
|---|---|---|
| 頁面載入並出現即時卡片 | PASS | cards=3 |
| **確實對幣安端點發出請求** | PASS | klines=28、ticker24hr=1 |
| 狀態顯示即時資料正常 | PASS | status=即時資料正常 |
| 無橫向溢出 | PASS | overflow=0px、offenders=0 |
| 無破圖 | PASS | 0/0 |
| 免責聲明存在 | PASS | — |
| 歷史區塊標示存在 | PASS | — |
| 掃描表格已渲染 | PASS | rows=25 |
| 歷史時間軸已渲染 | PASS | items=30 |
| **停損與價格/ATR 內部一致** | PASS | checked=3、mismatches=0 |

### 自動刷新（等待 36 秒後按「立即刷新」）
| 檢查 | 結果 | 證據 |
|---|---|---|
| 最後更新時間確實變動 | PASS | `07:15:43` → `07:16:24` UTC |
| 第二次仍抓到即時資料 | PASS | status=即時資料正常 |
| 數值指紋有變化 | PASS | changed |
| **即時（未收盤）價格確實隨市價變動** | PASS | BTC `84260.48 → 84208.78`、ETH `2574.70 → 2572.39`、SOL `112.99 → 112.87` |
| 請求次數增加（證明重新抓取） | PASS | 30 → 90 次 |
| 刷新後無橫向溢出 | PASS | 0px |

### 手機 390×844
| 檢查 | 結果 |
|---|---|
| 即時卡片出現 | PASS（cards=3） |
| 無橫向溢出 | PASS（0px、offenders=0） |
| 無破圖 | PASS |
| 狀態正常 | PASS |
| 免責聲明存在 | PASS |
| CORS 無失敗請求 | PASS（failed=0） |

### 視覺判讀
桌面與手機截圖以視覺模型判讀，**兩者皆回報「NO rendering defects found」**
（無裁切、無溢出、無破圖、無 □ 亂碼，狀態列／按鈕／倒數／KPI／寬表格／時間軸皆完整）。

---

## 4. 即時 vs 歷史 — 資料性質對照

| 區塊 | 資料性質 | 來源 | 會隨自動刷新更新？ |
|---|---|---|---|
| 1 · 即時訊號面板 | **即時** | 瀏覽器 → `/api/v3/klines` | ✅ 是（每 30 秒，可調） |
| 2 · 市場掃描排行 | **即時** | 瀏覽器 → `/api/v3/ticker/24hr` + 逐檔 `/klines` | ✅ 是 |
| 3 · 監控狀態與時間軸 | **歷史快照** | `run_monitor.py` 產出的 `monitor_snapshot.json`，內嵌於 HTML | ❌ 否，需重跑 `build_dashboard_v6.py` |
| 時間軸中標「回補」者 | **歷史重放** | 由歷史 K 線重放產生 | ❌ 否 |

第 3 節在頁面上有**明文標示**「本區塊為歷史資料（非即時）」。

---

## 5. 實測數值（2026-10-12 抓取）

| 標的 | 主口徑價格 | ATR | ATR% | 建議停損 | 風險% | 趨勢 | 強度 |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 83,773.12 | 2,064.797 | 2.46% | 77,578.73 | 7.39% | 多頭 | 67.5 |
| ETHUSDT | 2,536.94 | 84.143 | 3.32% | 2,284.51 | 9.95% | 多頭 | 47.3 |
| SOLUSDT | 111.35 | 4.710 | 4.23% | 97.22 | 12.69% | 多頭 | 66.0 |

市場掃描：**共 25 檔，25 檔多頭、0 檔空頭、0 檔當日新交叉**。
（誠實說明：全多頭是**當下市場狀態**，非策略特性。）

停損一致性已驗證：`83,773.12 − 3 × 2,064.797 = 77,578.728` ✓

---

## 6. 開發中發現並修正的三個真實缺陷

### ① 風控關卡口徑混用（0 錯誤，已修正）
`analyzeBars()` 以 `prim = confirmed || provisional` 作為主口徑（卡片顯示 confirmed 的價格），
但停損／停利卻用 `provisional.atr`、`provisional.price` 計算 —— 導致**畫面顯示的價格與停損
對不上**（實測差約 1.2%）。已改為一律以 `prim` 計算，並在驗證腳本中加入
「顯示的停損與價格/ATR 內部一致」這項斷言防止回歸。

### ② 建置輸出路徑靜默寫錯位置（已加防呆）
`--out ../webpages/…` 從 `documents/crypto-quant_v7` 出發，會解析成
`documents/webpages/…` —— 頁面表面建置成功，實際**根本沒更新到要交付的檔案**，
瀏覽器驗證因此看到舊版（無 `#statusText`）。已在 `build_dashboard_v6.py` 加入路徑防呆：
輸出路徑必須含 `webpages` 且不得含 `documents`，否則中止。

### ③ v6/v7 模組分支合併缺口（已補齊）
`crypto-quant_v7` 由 v6 複製，但 v6 的 `backtest.py` 缺少 v5 才有的 `near_trigger_pct`
欄位，導致 `analyze_symbol()` 失敗。已從 v5 取回正確版本。

### ④ 驗證探針量測時機過早（已修正）
即時卡片（3 檔）先完成，市場掃描（25 檔、併發 6）仍在跑；探針在掃描中途量測，
誤判為「狀態異常／表格未渲染」。已加入 `wait_settled()`，等**整輪刷新結束**再量測。

---

## 7. 誠實揭露的限制

1. **已收盤日線的收盤價在同一交易日內不會變** —— 卡片「價格」在主口徑為已收盤 K 棒時，
   盤中重複刷新會相同。這是正確行為（已定案的歷史值），不是快取。逐次變動的是
   未收盤 K 棒的即時市價與時間欄位；本報告已改用後者作為「確實更新」的證據。
2. **掃描排行依據是 24h 成交量，不是市值** —— 幣安公開 API 不提供市值。
3. **訊號強度是啟發式排序分數，不是機率**，也未經校準，不可當勝率估計。
4. **未在 sandbox 之外的真實網路環境測試** —— 若您的環境有代理／地區限制，
   直連會失敗，頁面會顯示明確錯誤並提示改用 `serve_proxy.py`。
5. **第 3 節仍為內嵌歷史快照**，未改為即時；這是刻意保留（監控歷史需由 Python 端產生）。
6. **通知功能仍為 file-only**，無 webhook／Telegram 憑證，未經真實發送驗證。
7. **策略層面限制沿用 v2–v7** —— walk-forward 已證實此策略 Sharpe 低於買進持有，
   價值在回撤控制而非報酬最大化。

---

## 8. 驗收對照

| 驗收條件 | 狀態 |
|---|---|
| (a) dashboard.html 能重建，無鍵名錯配錯誤 | ✅ 重建成功（90,066 bytes），鍵名已對齊 `monitor_snapshot.json` |
| (b) 桌面 1280px 與手機 390px 截圖存在且無橫向溢出 | ✅ 3 張截圖，兩者 overflow=0px |
| (c) 刷新後時間戳（且價格）確實變動 | ✅ 時間戳與即時價格皆變動 |
| (d) 免責聲明文字存在 | ✅ 桌面與手機皆偵測到 |
| (e) 報告列出即時 vs 快照區分與所有連結 | ✅ 第 4 節 + 第 9 節 |

---

## 9. 檔案連結

| 檔案 | 連結 |
|---|---|
| 儀表板（HTML） | _(見交付訊息)_ |
| `live_engine.js` | _(見交付訊息)_ |
| `dashboard_template.html` | _(見交付訊息)_ |
| `build_dashboard_v6.py` | _(見交付訊息)_ |
| `serve_proxy.py` | _(見交付訊息)_ |
| `engine_test.mjs` | _(見交付訊息)_ |
| `make_baseline.py` | _(見交付訊息)_ |
| `verify_live_dashboard.py` | _(見交付訊息)_ |
| `engine_parity.json` | _(見交付訊息)_ |
| `dashboard_live_report.json` | _(見交付訊息)_ |
| 截圖（桌面／桌面刷新後／手機） | _(見交付訊息)_ |

---

**⚠ 投資免責聲明**

本報告所有內容（含即時訊號、掃描排行、監控事件與參數）僅供**研究與教育用途**，
**不構成任何投資建議、要約或推薦**。訊號基於歷史價格計算，**無法預測未來走勢**；
歷史績效不代表未來表現。即時資料可能因網路、端點或交易所因素延遲、中斷或失真，
**不可作為唯一交易依據**。加密貨幣波動極大，可能導致**全部本金損失**。

As an AI Agent, I cannot provide legally protected, personalized investment advice.
This analysis is for informational purposes only and does not constitute investment advice
or recommendations. Consult a licensed financial advisor before making investment decisions.
