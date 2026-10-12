#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_s13.py — 產生報告第 13 章（Pine Script 雙版本）並插入 index.html。

以程式產生而非手寫，是為了讓 .pine 的完整程式碼以 html.escape 正確轉義，
避免 < > & 破壞 HTML 結構。
"""
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = Path("/workspace/documents/crypto-quant_v4")
PAGE = Path("/workspace/webpages/crypto-quant-report_v4/index.html")

IND = (DOCS / "pine/ctma_indicator.pine").read_text(encoding="utf-8")
STR = (DOCS / "pine/ctma_strategy.pine").read_text(encoding="utf-8")
IND_LINES, STR_LINES = IND.count("\n") + 1, STR.count("\n") + 1


def esc(s: str) -> str:
    return html.escape(s, quote=False)


# -----------------------------------------------------------------------------
# 靜態檢查項目（與 pine_static_check.py 輸出一致）
# -----------------------------------------------------------------------------
CHECKS = [
    ("A1", "Pine 版本宣告", "確認首行為 //@version=6（最新穩定版）"),
    ("A2", "宣告型別", "indicator() / strategy() 存在且型別正確"),
    ("B1", "括號／引號配對", "() [] {} 三者深度歸零、引號為偶數"),
    ("C1", "縮排一致性", "所有縮排為基礎單位的整數倍（Pine 對縮排敏感）"),
    ("D1", "<strong>var/varip 全在全域</strong>", "9 個（指標版）/ 7 個（策略版）var 宣告，無一在 if/for/while 區塊內"),
    ("E1", "tuple 解構語法", "[a, b] = f() 形式正確、識別字合法"),
    ("E2", "內建函式回傳值數量", "ta.supertrend 雙值、ta.macd 三值解構"),
    ("F1", "hline 用法", "常數水平線用 hline()，未誤用 hlines()"),
    ("F2", "plot style 命名空間", "所有 style= 皆為 plot.style_* / line.style_* / shape.*"),
    ("F3", "plotshape 使用 shape.*", "每個 plotshape 都指定合法 shape.*"),
    ("G1", "<strong>alertcondition 在全域</strong>", "6 個（指標版）/ 4 個（策略版）皆在全域作用域"),
    ("G2", "alert() 皆位於條件區塊", "4 個（指標版）/ 3 個（策略版）皆包在 if 內，不會逐根觸發"),
    ("G3", "alert() 指定頻率", "每個 alert() 皆帶 alert.freq_* 參數"),
    ("H1", "<strong>所有 input 皆被使用</strong>", "31 個 input，未被使用 = 0（無宣告未用參數）"),
    ("H2", "群組字串變數皆被使用", "6 個 grpX 字串常數全數被 group= 引用"),
    ("H3", "input 皆有 group", "每個 input 都歸類到群組，選單可折疊"),
    ("I1", "<strong>未使用 request.security</strong>", "完全未使用 → 不存在跨週期 lookahead / repaint 風險"),
    ("I2", "無 lookahead 參數", "未出現 lookahead / barmerge.lookahead"),
    ("I3", "未使用 barmerge.*", "未出現任何 barmerge.* 呼叫"),
    ("I4", "歷史引用以 nz() 保護", "pos[1] 等歷史引用皆以 nz() 或比較運算保護"),
    ("J0", "常見錯誤 lint", "裸 na 布林運算 / 區塊內型別宣告 / strategy.exit 缺價位 / location.absolute 型別 / 序列當純量條件 → 全數 0 處"),
    ("K（指標版）", "不含 strategy.* 呼叫", "指標版不得呼叫交易 API，已確認無"),
    ("K（策略版）", "含 entry/exit 與成本設定", "strategy.entry / strategy.exit / commission / slippage 皆已設定"),
]

# -----------------------------------------------------------------------------
# 參數表（合併兩版本，標註差異）
# -----------------------------------------------------------------------------
PARAMS = [
    ("① 趨勢", "trendMode", "EMA 交叉", "EMA 交叉 / Supertrend", "趨勢判斷來源，可切換 EMA 交叉或 Supertrend 翻轉"),
    ("① 趨勢", "emaFastLen", "21", "2 – 400", "EMA 快線週期"),
    ("① 趨勢", "emaSlowLen", "55", "3 – 500", "EMA 慢線週期"),
    ("① 趨勢", "stAtrLen", "10", "1 – 100", "Supertrend 的 ATR 週期"),
    ("① 趨勢", "stFactor", "3.0", "0.5 – 10.0", "Supertrend 倍數（越大越不易翻轉）"),
    ("② 動能", "momMode", "RSI + MACD", "RSI+MACD / 僅 RSI / 僅 MACD", "動能確認方式，可只留一種"),
    ("② 動能", "rsiLen", "14", "2 – 100", "RSI 週期"),
    ("② 動能", "rsiBull", "50", "0 – 100", "做多要求 RSI 高於此值（站中軸同側）"),
    ("② 動能", "rsiBear", "50", "0 – 100", "做空要求 RSI 低於此值"),
    ("② 動能", "macdFast", "12", "1 – 200", "MACD 快線週期"),
    ("② 動能", "macdSlow", "26", "2 – 300", "MACD 慢線週期"),
    ("② 動能", "macdSigLen", "9", "1 – 100", "MACD 訊號線週期"),
    ("③ 波動度", "atrLen", "14", "1 – 200", "ATR 週期（停損距離與部位大小的基準）"),
    ("③ 波動度", "minAtrPct", "0.0", "0 – 50", "最低 ATR% 波動度門檻；0 = 關閉過濾"),
    ("③ 波動度", "stopAtrMult", "3.0", "0.5 – 20.0", "初始停損距離 = N × ATR"),
    ("③ 波動度", "useTrail", "true", "開 / 關", "啟用 ATR 移動停損（只朝有利方向移動）"),
    ("③ 波動度", "trailAtrMult", "5.0", "0.5 – 30.0", "移動停損距離 = N × ATR"),
    ("③ 波動度", "useTP", "false", "開 / 關", "啟用 ATR 停利"),
    ("③ 波動度", "tpAtrMult", "6.0", "0.5 – 50.0", "停利距離 = N × ATR"),
    ("④ 部位", "allowLong", "true", "開 / 關", "允許做多"),
    ("④ 部位", "allowShort", "true", "開 / 關", "允許做空（加密貨幣永續合約適用）"),
    ("④ 部位", "riskPct", "1.0", "0.05 – 10.0", "每筆風險 %；指標版用於面板部位建議，策略版用於 ATR 風險比例模式"),
    ("④ 部位", "<em>equityRef</em><sup>指</sup>", "10000", "≥ 100", "指標版專用：帳戶名目資金，用於換算部位建議單位數"),
    ("④ 部位", "<em>sizingMode</em><sup>策</sup>", "權益比例", "權益比例 / ATR 風險比例", "策略版專用：部位大小模式"),
    ("④ 部位", "<em>qtyPct</em><sup>策</sup>", "100", "1 – 100", "策略版專用：權益比例模式下的投入 %"),
    ("⑤ 顯示", "showEma", "true", "開 / 關", "顯示 EMA 快慢線"),
    ("⑤ 顯示", "showSt", "true", "開 / 關", "顯示 Supertrend 線"),
    ("⑤ 顯示", "showStop", "true", "開 / 關", "顯示停損／停利線（僅持倉期間）"),
    ("⑤ 顯示", "showMarkers", "true", "開 / 關", "顯示買賣點標記與進場標籤"),
    ("⑤ 顯示", "showPanel", "true", "開 / 關", "顯示資訊面板"),
    ("⑤ 顯示", "<em>panelPos</em><sup>指</sup>", "右上", "右上 / 左上 / 右下 / 左下", "指標版專用：面板位置"),
    ("⑥ 通知", "alertEntry", "true", "開 / 關", "進場時發出 alert()"),
    ("⑥ 通知", "alertExit", "true", "開 / 關", "出場／停損／停利時發出 alert()"),
]

# -----------------------------------------------------------------------------
def build() -> str:
    p_check = "\n".join(
        f'    <tr><td><code>{c[0]}</code></td><td>{c[1]}</td><td>{c[2]}</td>'
        f'<td><span class="tag t-ok">PASS</span></td></tr>' for c in CHECKS)

    p_params = "\n".join(
        f'    <tr><td><code>{g}</code></td><td><code>{n}</code></td><td class="num">{d}</td>'
        f'<td>{rng}</td><td>{desc}</td></tr>' for g, n, d, rng, desc in PARAMS)

    return f'''
<!-- ================= 13 ================= -->
<h2 id="s13"><span class="n">13</span>量化交易指標腳本（Pine Script 指標版 ＋ 策略版）</h2>

<p>本章交付一套以 <strong>TradingView Pine Script v6</strong> 為主的量化指標腳本，
同時提供 <strong>指標版（indicator）</strong> 與 <strong>策略版（strategy）</strong> 兩份完整可貼上的檔案：
指標版用於看盤（畫買賣點、停損線、指標面板），策略版用於回測（交給 TradingView 的 Strategy Tester 撮合與統計）。
兩者<strong>共用同一套訊號邏輯</strong>，只是執行引擎不同。</p>

<div class="grid g3">
  <div class="stat"><div class="lbl">指標版行數</div><div class="val neu">{IND_LINES}</div><div class="note">indicator，只做運算與繪圖</div></div>
  <div class="stat"><div class="lbl">策略版行數</div><div class="val neu">{STR_LINES}</div><div class="note">strategy，交由回測引擎撮合</div></div>
  <div class="stat"><div class="lbl">可調參數</div><div class="val neu">31</div><div class="note">每個版本各 31 個 input</div></div>
  <div class="stat"><div class="lbl">靜態檢查</div><div class="val pos">46/46</div><div class="note">兩檔各 23 項，全部 PASS</div></div>
</div>

<h3>13.1 為何要「指標版 ＋ 策略版」兩份？</h3>
<div class="tablewrap"><table>
  <thead><tr><th>面向</th><th>指標版 indicator</th><th>策略版 strategy</th></tr></thead>
  <tbody>
    <tr><td>主要用途</td><td>看盤、人工下單、設定通知</td><td>回測、參數掃描、績效統計</td></tr>
    <tr><td>持倉來源</td><td>腳本<strong>自行維護</strong>狀態機（var pos）</td><td>TradingView <strong>strategy.* 引擎</strong>撮合</td></tr>
    <tr><td>能否用 Strategy Tester</td><td><span class="tag t-bad">否</span></td><td><span class="tag t-ok">是</span>（淨利／MDD／勝率／PF）</td></tr>
    <tr><td>繪圖限制</td><td>無限制，可畫多層停損／停利線</td><td>可畫，但過度繪圖會拖慢回測</td></tr>
    <tr><td>成本模型</td><td>無（僅供看盤）</td><td>可設 commission / slippage</td></tr>
    <tr><td>部位大小</td><td>面板顯示建議單位數</td><td>可選權益比例或 ATR 風險比例</td></tr>
  </tbody>
</table></div>
<p class="callout info"><strong>為什麼不只用一份？</strong>指標版不能呼叫 <code>strategy.*</code>，
拿不到部位與績效；策略版若把全套繪圖與面板都塞進去，回測會明顯變慢，且
<code>plotshape</code> 過多會蓋掉 K 棒。實務上兩者分工最順：<strong>看盤用指標版、驗證用策略版</strong>。</p>

<h3>13.2 設計理由：為何針對加密貨幣調整（而非照搬股票策略）</h3>
<div class="tablewrap"><table>
  <thead><tr><th>加密貨幣特性</th><th>對應設計</th><th>理由</th></tr></thead>
  <tbody>
    <tr><td>24/7 交易、無收盤跳空保護</td><td><strong>ATR 自適應停損</strong>取代固定 %</td><td>固定 % 停損在高波動期會被瞬間掃出、低波動期又太寬</td></tr>
    <tr><td>波動大、趨勢強</td><td>趨勢線用 <strong>EMA</strong>（非 SMA）</td><td>EMA 對近期價格更敏感，趨勢反轉時反應更快</td></tr>
    <tr><td>假訊號多、來回洗盤</td><td><strong>動能雙重確認</strong>：RSI 中軸同側 ＋ MACD 柱同向</td><td>單靠均線交叉在盤整區會產生大量假突破</td></tr>
    <tr><td>盤整泥沼、低波動期</td><td><strong>ATR% 波動度門檻</strong></td><td>低波動時不進場，避免被來回磨損</td></tr>
    <tr><td>永續合約成熟、可雙向</td><td>做多／做空<strong>可分別開關</strong></td><td>現貨只能做多；合約可做空，參數需可切換</td></tr>
  </tbody>
</table></div>

<h3>13.3 訊號邏輯：三層過濾</h3>
<pre><code>【趨勢層 Trend】  EMA 快線 / 慢線 交叉   或   Supertrend 翻轉
        ↓ 必須先確立方向
【動能層 Momentum】 RSI 站中軸同側  ＋  MACD 柱方向一致
        ↓ 過濾掉逆勢與動能不足的假突破
【波動層 Volatility】 ATR% ≥ 門檻（可關閉）
        ↓ 過濾掉盤整泥沼
【觸發】 趨勢翻轉事件 ＋ 三層同時成立 ＋ 暖機期已過
        ↓
  做多進場 LONG                           做空進場 SHORT
  ├ 初始停損 = 進場價 − 3×ATR            ├ 初始停損 = 進場價 ＋ 3×ATR
  ├ 移動停損 = 收盤 − 5×ATR（只上移）    ├ 移動停損 = 收盤 ＋ 5×ATR（只下移）
  └ 停利（選用）= 進場價 ＋ 6×ATR        └ 停利（選用）= 進場價 − 6×ATR

【出場條件（任一成立）】
  1. 反向趨勢翻轉（死亡交叉 / Supertrend 翻空）→ 主動平倉
  2. 價格觸及停損價 → ATR 停損
  3. 價格觸及停利價（若啟用）→ ATR 停利</code></pre>

<h3>13.4 完整參數清單（31 個 input，全部可調）</h3>
<p><sup>指</sup> = 僅指標版有此參數；<sup>策</sup> = 僅策略版有此參數。其餘為兩版共用。</p>
<div class="tablewrap"><table>
  <thead><tr><th>群組</th><th>參數</th><th>預設</th><th>範圍</th><th>說明</th></tr></thead>
  <tbody>
{p_params}
  </tbody>
</table></div>
<p class="callout"><strong>部位建議怎麼算？</strong>指標版以「固定風險金額 ÷ 停損距離」換算單位數：
<code>單位數 = (帳戶名目資金 × 每筆風險%) ÷ |進場價 − 停損價|</code>。
例如資金 10,000、風險 1%（=100）、停損距離 3×ATR，即代表「這筆最多虧 100」。
策略版另有「ATR 風險比例」模式可直接把同一式子交給下單引擎。</p>

<h3>13.5 完整程式碼 — 指標版</h3>
<p>完整檔案：<a href="https://static.teamily.ai/sites/34468edc-e320-4e40-ae5e-3e9455ca31c2/documents/crypto-quant_v4/pine/ctma_indicator.pine">ctma_indicator.pine</a>（{IND_LINES} 行）</p>
<details class="codebox">
  <summary>📄 ctma_indicator.pine — 完整程式碼（點擊展開 / 收合）</summary>
<pre><code>{esc(IND)}</code></pre>
</details>

<h3>13.6 完整程式碼 — 策略版</h3>
<p>完整檔案：<a href="https://static.teamily.ai/sites/34468edc-e320-4e40-ae5e-3e9455ca31c2/documents/crypto-quant_v4/pine/ctma_strategy.pine">ctma_strategy.pine</a>（{STR_LINES} 行）</p>
<details class="codebox">
  <summary>📄 ctma_strategy.pine — 完整程式碼（點擊展開 / 收合）</summary>
<pre><code>{esc(STR)}</code></pre>
</details>

<h3>13.7 訊號判讀方式</h3>
<div class="tablewrap"><table>
  <thead><tr><th>圖上元素</th><th>意義</th><th>怎麼用</th></tr></thead>
  <tbody>
    <tr><td>▲ 綠色向上三角（K 棒下方）</td><td>做多進場訊號成立</td><td>訊號於<strong>當根收盤</strong>確立，下一根開盤進場最保守</td></tr>
    <tr><td>▼ 紅色向下三角（K 棒上方）</td><td>做空進場訊號成立</td><td>同上；若帳戶不能做空請關閉 <code>allowShort</code></td></tr>
    <tr><td>✕ 橘色叉號</td><td>ATR 停損觸發位置</td><td>檢視這筆是否為「被掃出後又續漲」的假停損</td></tr>
    <tr><td>紅色水平線</td><td>目前多／空單的停損價</td><td>線會隨移動停損逐根靠近價格，代表利潤被鎖住</td></tr>
    <tr><td>綠色水平線</td><td>停利價（僅 useTP = true 時）</td><td>若覺得常被停利提早出場，可關閉或加大倍數</td></tr>
    <tr><td>橘色／紫色折線</td><td>EMA 快線 (21) / 慢線 (55)</td><td>交叉即趨勢層的反轉事件來源</td></tr>
    <tr><td>青色折線</td><td>Supertrend</td><td>僅 <code>trendMode = Supertrend</code> 時顯示</td></tr>
    <tr><td>右上資訊面板</td><td>趨勢／RSI／MACD／ATR%／持倉／停損價／部位建議</td><td>一眼確認「現在該不該進、部位多大」</td></tr>
  </tbody>
</table></div>
<p class="callout ok"><strong>面板怎麼快速判讀：</strong>先看「趨勢來源」是否為多頭；
再看「ATR%」是否為綠色（通過門檻）；兩者都成立且持倉顯示「空手」時，
才是可準備進場的狀態。持倉顯示「多單」時，「停損價」即為隔日實際掛單價位。</p>

<h3>13.8 靜態檢查結果：46/46 全部通過</h3>
<p>以 <code>pine_static_check.py</code> 對兩份檔案逐項檢查，
依 Pine Script v6 官方語言規則驗證結構與模式，<strong>指標版 23 項、策略版 23 項，合計 46 項全部 PASS，0 WARN，0 FAIL</strong>：</p>
<div class="tablewrap"><table>
  <thead><tr><th>編號</th><th>檢查項目</th><th>檢查方式</th><th>結果</th></tr></thead>
  <tbody>
{p_check}
  </tbody>
</table></div>

<div class="callout warn">
  <strong>⚠️ 必須誠實說明：以上全部是「靜態檢查」，不是真正的編譯。</strong>
  <p>本沙盒環境<strong>無法執行 TradingView 的 Pine 編譯器</strong>，因此「可編譯」這個結論
  是基於：</p>
  <ul>
    <li>Pine Script v6 官方文件的語言規則（var 作用域、型別系統、函式簽章、繪圖限制）；</li>
    <li>對原始碼做的結構分析（括號／引號配對、縮排一致性、作用域歸屬）；</li>
    <li>與既有可編譯版本（v1 的 <code>//@version=5</code> 策略）的逐項對照。</li>
  </ul>
  <p><strong>這能抓出絕大多數常見的編譯錯誤</strong>——<code>var</code> 寫在 <code>if</code> 內、
  未使用的 input、括號失衡、<code>alertcondition</code> 不在全域、
  <code>location.absolute</code> 誤用布林值、序列被當純量條件、未來函數等。
  但<strong>不等同於編譯通過</strong>：編譯器還會檢查型別推導細節、函式多載解析、
  變數命名衝突等靜態分析無法完全覆蓋的部分。</p>
  <p><strong>建議：貼進 TradingView Pine Editor 編譯一次確認。</strong>
  若有任何編譯錯誤，錯誤訊息會直接指向行號，修正成本極低。</p>
</div>

<p>開發過程中被靜態檢查抓出並已修正的 <strong>3 個真實問題</strong>：</p>
<div class="tablewrap"><table>
  <thead><tr><th>問題</th><th>原本寫法</th><th>為何會壞</th><th>修正</th></tr></thead>
  <tbody>
    <tr><td><code>location.absolute</code> 型別錯誤</td><td><code>plotshape(boolCond, ..., location.absolute)</code></td>
      <td><code>location.absolute</code> 需要「價格序列」當絕對位置，傳布林值會型別不符</td>
      <td>改為 <code>cond ? close : na</code></td></tr>
    <tr><td>同一作用域重複宣告變數</td><td>兩個 <code>if</code> 區塊各用 <code>cand</code> / <code>cand2</code></td>
      <td>Pine 對同層級同名宣告會報錯，加尾數只是繞路</td>
      <td>兩個區塊各自使用 <code>cand</code>（作用域互斥，合法）</td></tr>
    <tr><td>布林運算直接使用裸 <code>na</code></td><td><code>useTP and not na(tpPx) and ...</code>（初步構想）</td>
      <td>Pine v5+ 的 <code>bool</code> 不可為 na，裸 na 參與 <code>and/or</code> 會編譯錯誤</td>
      <td>一律改用 <code>not na(x)</code>，並保留 <code>nz()</code> 保護歷史引用</td></tr>
  </tbody>
</table></div>

<h3>13.9 通知設定</h3>
<p>兩版皆提供 <code>alertcondition()</code>（可在 TradingView 的「警示」對話框直接選用）
與 <code>alert()</code>（訊號成立的當根立即觸發）。指標版提供 6 組、策略版提供 4 組：</p>
<div class="tablewrap"><table>
  <thead><tr><th>通知名稱</th><th>觸發時機</th><th>版本</th></tr></thead>
  <tbody>
    <tr><td><code>CTMA 做多進場</code></td><td>做多訊號成立</td><td>兩版</td></tr>
    <tr><td><code>CTMA 做空進場</code></td><td>做空訊號成立</td><td>兩版</td></tr>
    <tr><td><code>CTMA 停損觸發</code></td><td>價格觸及停損價</td><td>指標版</td></tr>
    <tr><td><code>CTMA 停利觸發</code></td><td>價格觸及停利價</td><td>指標版</td></tr>
    <tr><td><code>CTMA 多單趨勢出場</code></td><td>反向趨勢翻轉</td><td>兩版</td></tr>
    <tr><td><code>CTMA 空單趨勢出場</code></td><td>反向趨勢翻轉</td><td>兩版</td></tr>
  </tbody>
</table></div>
<p class="callout info">訊息內容已帶入 <code>{{ticker}}</code>、<code>{{close}}</code> 與停損價，
可直接接到 webhook 或手機推播。指標版的 <code>alert()</code> 使用
<code>alert.freq_once_per_bar_close</code>，避免盤中反覆觸發。</p>

<h3>13.10 優點與注意事項</h3>
<h4 style="margin-top:0;color:#86efac">✓ 優點</h4>
<ul>
  <li><strong>雙版本分工明確</strong>：看盤用指標版（含停損線與部位建議），驗證用策略版（可用 Strategy Tester 與成本模型）。</li>
  <li><strong>參數全開</strong>：31 個 input 全部可調且有群組分類，趨勢／動能／波動度三層可獨立開關。</li>
  <li><strong>零未來函數</strong>：完全未使用 <code>request.security</code>，不存在跨週期 lookahead 或 repaint 疑慮。</li>
  <li><strong>狀態機順序正確</strong>：指標版刻意設計為「先用舊停損判斷是否被打到，才更新移動停損」，
      避免同一根 K 棒內先移動停損、再宣稱被打到而產生的隱性未來函數。</li>
  <li><strong>風控內建</strong>：ATR 初始停損、ATR 移動停損（只朝有利方向）、可選 ATR 停利、可依 ATR 風險比例計算部位。</li>
  <li><strong>可自動化</strong>：<code>alertcondition</code> ＋ <code>alert()</code> 雙軌，方便接 webhook。</li>
  <li><strong>靜態檢查可重跑</strong>：<code>pine_static_check.py</code> 為獨立工具，改參數後可隨時重驗。</li>
</ul>

<h4 style="color:#fca5a5">✗ 缺點與注意事項</h4>
<ul>
  <li><strong>「可編譯」僅為靜態推論</strong>：沙盒無法執行 TradingView 編譯器，
      這是本交付<strong>最大的未驗證項</strong>，請務必自行貼進 Pine Editor 確認一次。</li>
  <li><strong>移動停損是雙面刃</strong>：依同一專案先前的實測，ATR×2 移動停損過緊時，
      BTC 有 <strong>63/72 筆</strong>是被停損掃出而非趨勢反轉出場，
      總報酬因此從 +215.76% 掉到 +8.72%。<strong>停損參數的影響遠大於均線週期本身</strong>。</li>
  <li><strong>指標版與策略版的持倉可能不完全一致</strong>：指標版用「當根收盤價」成交，
      策略版預設 <code>process_orders_on_close = false</code>（下一根開盤成交）。
      兩者對同一根 K 棒的判定可能差一根，屬正常現象。</li>
  <li><strong>單一時間框架</strong>：未做多時間框架共振（如日線趨勢 ＋ 4 小時進場），
      日內訊號可能較雜。</li>
  <li><strong>無交易成本內建於指標版</strong>：看盤用的損益僅為示意，實際成本見策略版與 Python 回測章節。</li>
  <li><strong>參數最佳化會過度擬合</strong>：本專案第 05 章與第 11、12 章已實證——
      同一組「最佳」參數樣本外 Sharpe 衰退 <strong>39%</strong>，
      walk-forward 更顯示訓練窗到測試窗衰退 <strong>62% ~ 81%</strong>。
      <strong>網格搜尋出的最佳參數不可直接採用。</strong></li>
</ul>

<div class="callout">
  <strong>建議的下一步驗證流程：</strong>
  <ol>
    <li>把 <code>ctma_indicator.pine</code> 貼進 Pine Editor → 編譯（確認無錯誤）。</li>
    <li>套用到 BTCUSDT 日線 → 目視檢查買賣點是否落在合理的趨勢翻轉處。</li>
    <li>把 <code>ctma_strategy.pine</code> 貼進 Pine Editor → 用 Strategy Tester 看淨利／MDD／勝率。</li>
    <li>把 <code>commission_value</code> 設為實際交易所費率、<code>slippage</code> 設為合理跳動，
       對照本報告第 03 章的 Python 回測數字，確認兩邊量級一致。</li>
    <li>最後才進入參數掃描，並以 walk-forward 驗證（第 12 章）確認穩健性。</li>
  </ol>
</div>
'''


def main() -> None:
    section = build()
    page = PAGE.read_text(encoding="utf-8")

    # 1) 插入章節（放在 disclaimer 之前，讓免責聲明維持在最後）
    marker = '\n<div class="disc">'
    assert page.count(marker) == 1, f"disclaimer marker count = {page.count(marker)}"
    page = page.replace(marker, section + marker, 1)

    # 2) TOC 加入第 13 項
    toc_old = '    <li><a href="#s12">WFO 雙模式對照（rolling vs anchored）</a></li>\n'
    assert page.count(toc_old) == 1
    page = page.replace(toc_old, toc_old + '    <li><a href="#s13">量化交易指標腳本（Pine 雙版本）</a></li>\n', 1)

    # 3) 徽章加入靜態檢查
    badge_old = '      <span class="badge b2">2,831 根日線</span>\n'
    assert page.count(badge_old) == 1
    page = page.replace(badge_old,
                        badge_old + '      <span class="badge b3">Pine 靜態檢查 46/46 通過</span>\n', 1)

    # 4) CSS：加入可折疊程式碼區塊樣式
    css_old = "  /* 表格在窄螢幕可橫向捲動，避免撐破版面 */"
    assert page.count(css_old) == 1
    css_new = ("  /* 可折疊程式碼區塊（兩份 .pine 完整程式碼） */\n"
               "  .codebox{background:var(--panel);border:1px solid var(--line);"
               "border-radius:14px;padding:14px 18px;margin:18px 0}\n"
               "  .codebox summary{cursor:pointer;font-weight:700;color:#cbd8ec;outline:none;"
               "list-style:revert}\n"
               "  .codebox summary:hover{color:#fff}\n"
               "  .codebox[open] summary{margin-bottom:10px;padding-bottom:10px;"
               "border-bottom:1px solid var(--line)}\n"
               "  .codebox pre{max-height:560px;overflow:auto;margin:0}\n"
               "  .codebox sup{color:var(--accent)}\n"
               + css_old)
    page = page.replace(css_old, css_new, 1)

    # 5) 頁尾加上靜態檢查結果
    foot_old = "WFO 雙模式對照 36/36 通過"
    assert page.count(foot_old) == 1
    page = page.replace(foot_old, foot_old + " ｜ Pine Script 靜態檢查 46/46 通過", 1)

    PAGE.write_text(page, encoding="utf-8")
    print(f"OK  已更新 {PAGE}  ({len(page)} bytes)")


if __name__ == "__main__":
    main()
