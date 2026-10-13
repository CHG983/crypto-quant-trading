/**
 * CTMA live engine — 瀏覽器端即時訊號引擎（純計算，無 DOM、無網路）
 * =================================================================
 * 把 Python 端 crypto_quant/ 的下列模組逐一移植到 JavaScript：
 *
 *   indicators.py      → ema / sma / rsi / macd / atr / supertrend
 *   strategy.py        → generate_signals（三層過濾：趨勢→動能→波動）
 *   signal_engine.py   → compute_strength / analyze_frame（趨勢、訊號、強度）
 *
 * 設計原則
 * --------
 * 1. **與 Python 逐值對齊**：EMA/RSI/ATR 的 pandas `ewm(adjust=False)` 語意、
 *    Supertrend 的棘輪迴圈、訊號的交集條件全部照抄，並由 `engine_test.mjs`
 *    對真實幣安資料做逐值比對（容差 1e-9）。
 * 2. **無未來函數**：所有指標與訊號只使用「當根與過去」的 K 棒。
 * 3. **可測試**：本檔不含任何網路或 DOM 存取，可在 Node 直接 require 後受測。
 *    儀表板中的版本是由 build_dashboard_v6.py 直接「內嵌」本檔原文，
 *    因此「受測的程式碼」與「上線的程式碼」逐字元相同（建置時會驗證雜湊）。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CTMA = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var VOL_WILD_CAP_PCT = 8.0;
  var WEIGHTS = { trend: 0.35, momentum: 0.30, volatility: 0.20, supertrend: 0.15 };

  /* ---- 布林／卡爾特專屬強度權重（本次新設計的啟發式評分，非既有程式碼） ----
   *
   * 原 computeStrength 對所有策略共用同一組動能分項（趨勢／RSI／MACD／ATR），
   * 導致布林反轉與卡爾特通道對同一標的算出「完全相同」的強度。
   * 兩者訊號特性不同（反轉 vs 突破），故各自給一組分項與權重：
   *
   *   BB（反轉明確程度）  depth(軌道外深度) 0.45 · rsi(RSI 極值) 0.30 · squeeze(通道壓縮) 0.25
   *   KC（突破明確程度）  breakout(突破幅度，ATR 標準化) 0.45 · slope(中軌斜率一致性) 0.30 · expansion(通道擴張) 0.25
   *
   * 權重和皆為 1.0，總分 = 100 × Σ(權重 × 分項)，仍落在 0–100 並沿用既有分級標籤。
   */
  var WEIGHTS_BB = { depth: 0.45, rsi: 0.30, squeeze: 0.25 };
  var WEIGHTS_KC = { breakout: 0.45, slope: 0.30, expansion: 0.25 };

  // 專屬評分用的標準化常數（皆為本次新設，非既有程式碼）
  var BB_SQUEEZE_TIGHT_PCT = 2.0;    // 通道寬度 ≤2% 視為「極度壓縮」→ squeeze 分項給滿分
  var BB_SQUEEZE_WIDE_PCT = 20.0;    // 通道寬度 ≥20% 視為「極寬」→ squeeze 分項給 0
  var KC_SLOPE_LOOKBACK = 2;         // 中軌斜率回看根數
  var KC_SLOPE_REF = 0.5;            // 斜率 / ATR 達 0.5 視為方向一致性滿分
  var KC_EXPANSION_LOOKBACK = 10;    // 通道擴張比較的回看根數
  var KC_EXPANSION_SPAN = 0.5;       // 現寬/前均寬 高出 1.0 達 0.5 者視為擴張滿分

  /* 強度評分方法標籤：讓前端能顯示「目前這個強度是用哪一套評分算的」 */
  function strengthMethodLabel(method) {
    if (method === 'bb') return '反轉評分 · 深度/RSI 極值/通道壓縮';
    if (method === 'keltner') return '突破評分 · 突破幅度/中軌斜率/通道擴張';
    return '動能評分 · 趨勢/動能/波動/Supertrend';
  }
  var TREND_LABELS = { 1: 'LONG', '-1': 'SHORT', 0: 'NEUTRAL' };
  var SIGNAL_LABELS = { 1: 'BULL_CROSS', '-1': 'BEAR_CROSS', 0: 'HOLD' };

  /* ------------------------------------------------------------------ *
   * 基礎工具
   * ------------------------------------------------------------------ */

  function isNum(v) { return typeof v === 'number' && Number.isFinite(v); }

  function clamp(x, lo, hi) {
    lo = lo === undefined ? 0 : lo;
    hi = hi === undefined ? 1 : hi;
    if (!isNum(x)) return 0;
    return Math.max(lo, Math.min(hi, x));
  }

  function round(x, nd) {
    if (!isNum(x)) return null;
    var f = Math.pow(10, nd);
    return Math.round(x * f) / f;
  }

  function strengthLabel(score) {
    if (score >= 75) return '極強';
    if (score >= 60) return '強';
    if (score >= 45) return '中';
    if (score >= 30) return '弱';
    return '極弱';
  }

  /* ------------------------------------------------------------------ *
   * 移動平均
   * ------------------------------------------------------------------ */

  /** 等同 pandas `s.ewm(span=length, adjust=False).mean()`（TradingView ta.ema）。 */
  function ema(values, length) {
    var n = values.length, out = new Array(n).fill(NaN);
    var a = 2 / (length + 1);
    var prev = NaN;
    for (var i = 0; i < n; i++) {
      var v = values[i];
      if (!isNum(v)) { out[i] = NaN; continue; }
      prev = isNum(prev) ? (1 - a) * prev + a * v : v;
      out[i] = prev;
    }
    return out;
  }

  /** 等同 pandas `s.ewm(alpha=alpha, adjust=False, min_periods=mp).mean()`。 */
  function ewmAlpha(values, alpha, minPeriods) {
    var n = values.length, out = new Array(n).fill(NaN);
    var prev = NaN, seen = 0;
    for (var i = 0; i < n; i++) {
      var v = values[i];
      if (!isNum(v)) { out[i] = NaN; continue; }
      prev = isNum(prev) ? (1 - alpha) * prev + alpha * v : v;
      seen += 1;
      out[i] = seen >= minPeriods ? prev : NaN;
    }
    return out;
  }

  function sma(values, length) {
    var n = values.length, out = new Array(n).fill(NaN), sum = 0;
    for (var i = 0; i < n; i++) {
      sum += isNum(values[i]) ? values[i] : NaN;
      if (i >= length) sum -= isNum(values[i - length]) ? values[i - length] : NaN;
      out[i] = i >= length - 1 ? sum / length : NaN;
    }
    return out;
  }

  /** 滾動母體標準差（布林通道用；與 TradingView ta.stdev 的母體定義一致）。 */
  function rollingStd(values, length) {
    var n = values.length, out = new Array(n).fill(NaN);
    for (var i = 0; i < n; i++) {
      if (i < length - 1) continue;
      var s = 0, ok = true;
      for (var j = i - length + 1; j <= i; j++) {
        if (!isNum(values[j])) { ok = false; break; }
        s += values[j];
      }
      if (!ok) continue;
      var mean = s / length, acc = 0;
      for (var k = i - length + 1; k <= i; k++) acc += (values[k] - mean) * (values[k] - mean);
      out[i] = Math.sqrt(acc / length);
    }
    return out;
  }

  /* ------------------------------------------------------------------ *
   * 動能指標
   * ------------------------------------------------------------------ */

  /** Wilder RSI（等同 TradingView ta.rsi）。 */
  function rsi(close, length) {
    var n = close.length;
    var gain = new Array(n).fill(NaN), loss = new Array(n).fill(NaN);
    for (var i = 1; i < n; i++) {
      var d = close[i] - close[i - 1];
      if (!isNum(d)) continue;
      gain[i] = d > 0 ? d : 0;
      loss[i] = d < 0 ? -d : 0;
    }
    var ag = ewmAlpha(gain, 1 / length, length);
    var al = ewmAlpha(loss, 1 / length, length);
    var out = new Array(n).fill(NaN);
    for (var j = 0; j < n; j++) {
      var g = ag[j], l = al[j];
      if (!isNum(g) || !isNum(l)) { out[j] = NaN; continue; }
      if (l === 0 && g > 0) out[j] = 100;
      else if (l === 0 && g === 0) out[j] = 50;
      else out[j] = 100 - 100 / (1 + g / l);
    }
    return out;
  }

  /** 回傳 { macd, signal, hist }。 */
  function macd(close, fast, slow, signalLen) {
    var ef = ema(close, fast), es = ema(close, slow);
    var n = close.length, line = new Array(n);
    for (var i = 0; i < n; i++) line[i] = ef[i] - es[i];
    var sig = ema(line, signalLen);
    var hist = new Array(n);
    for (var j = 0; j < n; j++) hist[j] = line[j] - sig[j];
    return { macd: line, signal: sig, hist: hist };
  }

  /* ------------------------------------------------------------------ *
   * 波動度與趨勢
   * ------------------------------------------------------------------ */

  function trueRange(high, low, close) {
    var n = close.length, tr = new Array(n).fill(NaN);
    for (var i = 0; i < n; i++) {
      var hl = high[i] - low[i];
      var cands = [hl];
      if (i > 0 && isNum(close[i - 1])) {
        cands.push(Math.abs(high[i] - close[i - 1]));
        cands.push(Math.abs(low[i] - close[i - 1]));
      }
      tr[i] = Math.max.apply(null, cands.filter(isNum));
    }
    return tr;
  }

  /** Wilder ATR（等同 TradingView ta.atr）。 */
  function atr(high, low, close, length) {
    return ewmAlpha(trueRange(high, low, close), 1 / length, length);
  }

  /** 回傳 { st, dir }；dir 為 +1（多頭）或 -1（空頭）。 */
  function supertrend(high, low, close, length, multiplier) {
    var n = close.length;
    var a = atr(high, low, close, length);
    var st = new Array(n).fill(NaN);
    var dir = new Array(n).fill(1);
    var fu = new Array(n).fill(NaN), fl = new Array(n).fill(NaN);

    for (var i = 0; i < n; i++) {
      if (!isNum(a[i])) continue;
      var hl2 = (high[i] + low[i]) / 2;
      var ub = hl2 + multiplier * a[i];
      var lb = hl2 - multiplier * a[i];

      if (i === 0 || !isNum(fu[i - 1])) {
        fu[i] = ub; fl[i] = lb; dir[i] = 1; st[i] = fl[i];
        continue;
      }
      fu[i] = (ub < fu[i - 1] || close[i - 1] > fu[i - 1]) ? ub : fu[i - 1];
      fl[i] = (lb > fl[i - 1] || close[i - 1] < fl[i - 1]) ? lb : fl[i - 1];

      if (dir[i - 1] === 1) dir[i] = close[i] < fl[i] ? -1 : 1;
      else dir[i] = close[i] > fu[i] ? 1 : -1;
      st[i] = dir[i] === 1 ? fl[i] : fu[i];
    }
    return { st: st, dir: dir };
  }

  /* ------------------------------------------------------------------ *
   * 參數預設值（與 StrategyParams / BacktestConfig 一致）
   * ------------------------------------------------------------------ */

  function defaultParams(over) {
    var p = {
      kind: 'ctma',
      ema_fast: 21, ema_slow: 55,
      rsi_len: 14, rsi_long_min: 50.0, rsi_short_max: 50.0,
      macd_fast: 12, macd_slow: 26, macd_signal: 9,
      atr_len: 14, atr_stop_mult: 3.0, atr_trail_mult: 5.0, atr_tp_mult: 6.0,
      min_atr_pct: 0.5,
      allow_short: true,
      use_supertrend: false, st_len: 10, st_mult: 3.0,
      near_trigger_pct: 0.5,
      // --- 布林通道反轉（BB Mean Reversion）---
      bb_len: 20, bb_mult: 2.0,
      bb_rsi_len: 14, bb_rsi_oversold: 30.0, bb_rsi_overbought: 70.0,
      // --- 卡爾特通道（Keltner Channels）---
      kc_ema_len: 20, kc_atr_len: 10, kc_mult: 2.0,
      // --- 建議槓桿比率（獨立區塊，不依賴策略選擇）---
      dr_target_vol: 2.0, dr_max_lev: 3.0, dr_min_lev: 0.5, dr_atr_len: 14
    };
    if (over) for (var k in over) if (Object.prototype.hasOwnProperty.call(over, k)) p[k] = over[k];
    return p;
  }

  /* ------------------------------------------------------------------ *
   * 策略預設（STRATEGY_PRESETS）
   * ------------------------------------------------------------------ *
   * 每一個預設都對應專案中「真實存在」的策略或參數組，來源標註於 source 欄位：
   *
   *   ctma_default     crypto_quant/strategy.py  StrategyParams 的預設值
   *   ctma_supertrend  同上，但 use_supertrend=True（strategy.py 的 Supertrend 過濾分支）
   *   ctma_long_only   同上，但 allow_short=False（只做多）
   *   wfo_9_34         crypto_quant/walkforward.py  DEFAULT_GRID 的網格組合
   *   wfo_13_55        同上
   *   wfo_21_89        同上
   *
   * 這些不是憑空發明的策略：前三個是 strategy.py 的參數開關，後三個是
   * walkforward.py / optimize.py 實際跑過的網格點。
   *
   * 註：原「動態風險敞口（動態槓桿比例）」已於本次自策略清單移除，改為儀表板上
   * 獨立的「建議槓桿比率」區塊（見 computeLeverageRow / dynamicLeverage），
   * 不依賴策略選擇，任何策略下都對每檔標的計算。策略數因此由 9 回到 8。
   * ------------------------------------------------------------------ */

  var STRATEGY_PRESETS = [
    {
      id: 'ctma_default',
      kind: 'ctma',
      name: 'CTMA 預設（EMA 21/55）',
      short: 'CTMA 預設',
      desc: 'EMA 21/55 交叉 + RSI(14) 站中軸同側 + MACD 柱方向一致 + ATR% ≥ 0.5% 過濾；3×ATR 停損、6×ATR 停利。',
      source: 'crypto_quant/strategy.py · StrategyParams 預設值',
      params: {}
    },
    {
      id: 'ctma_supertrend',
      kind: 'ctma',
      name: 'CTMA + Supertrend 過濾（EMA 21/55）',
      short: 'CTMA + Supertrend',
      desc: '在 CTMA 預設之上，額外要求 Supertrend(10, 3.0) 方向與交叉方向一致，過濾逆勢交叉。',
      source: 'crypto_quant/strategy.py · use_supertrend=True 分支',
      params: { use_supertrend: true, st_len: 10, st_mult: 3.0 }
    },
    {
      id: 'ctma_long_only',
      kind: 'ctma',
      name: 'CTMA 只做多（EMA 21/55）',
      short: 'CTMA 只做多',
      desc: '與 CTMA 預設相同，但關閉做空（allow_short=False），只保留多頭進場訊號。',
      source: 'crypto_quant/strategy.py · allow_short=False',
      params: { allow_short: false }
    },
    {
      id: 'wfo_9_34',
      kind: 'ctma',
      name: 'WFO 網格 9/34（ATR 停損 1.5×）',
      short: 'WFO 9/34',
      desc: 'walk-forward 網格中的一組：EMA 快 9 / 慢 34，ATR 停損 1.5×、移動停損 5×、停利 6×。',
      source: 'crypto_quant/walkforward.py · DEFAULT_GRID (ema_fast=9, ema_slow=34, atr_stop=1.5)',
      params: { ema_fast: 9, ema_slow: 34, atr_stop_mult: 1.5, atr_trail_mult: 5.0, atr_tp_mult: 6.0 }
    },
    {
      id: 'wfo_13_55',
      kind: 'ctma',
      name: 'WFO 網格 13/55（ATR 停損 2.0×）',
      short: 'WFO 13/55',
      desc: 'walk-forward 網格中的一組：EMA 快 13 / 慢 55，ATR 停損 2.0×、移動停損 5×、停利 6×。',
      source: 'crypto_quant/walkforward.py · DEFAULT_GRID (ema_fast=13, ema_slow=55, atr_stop=2.0)',
      params: { ema_fast: 13, ema_slow: 55, atr_stop_mult: 2.0, atr_trail_mult: 5.0, atr_tp_mult: 6.0 }
    },
    {
      id: 'wfo_21_89',
      kind: 'ctma',
      name: 'WFO 網格 21/89（ATR 停損 3.0×）',
      short: 'WFO 21/89',
      desc: 'walk-forward 網格中的一組：EMA 快 21 / 慢 89，ATR 停損 3.0×、移動停損 5×、停利 6×。',
      source: 'crypto_quant/walkforward.py · DEFAULT_GRID (ema_fast=21, ema_slow=89, atr_stop=3.0)',
      params: { ema_fast: 21, ema_slow: 89, atr_stop_mult: 3.0, atr_trail_mult: 5.0, atr_tp_mult: 6.0 }
    },
    {
      id: 'bb_reversion',
      kind: 'bb',
      name: '布林通道反轉（BB Mean Reversion）',
      short: '布林反轉',
      desc: '中軌 = SMA(close, 20)，上下軌 = 中軌 ± 2.0×母體標準差。價格觸及／跌破下軌視為超賣反轉做多，觸及／突破上軌視為超買反轉做空；再以 RSI(14) < 30（做多）／> 70（做空）過濾。停損停利沿用 ATR 倍數。',
      source: '標準布林通道定義（Bollinger 1980s）· SMA ± N×σ；本專案未內建此策略，為本次新增，公式與參數於頁面明列',
      params: { kind: 'bb', bb_len: 20, bb_mult: 2.0, bb_rsi_len: 14, bb_rsi_oversold: 30.0, bb_rsi_overbought: 70.0 }
    },
    {
      id: 'keltner',
      kind: 'keltner',
      name: '卡爾特通道（Keltner Channels）',
      short: '卡爾特通道',
      desc: '中軌 = EMA(close, 20)，上下軌 = 中軌 ± 2.0×ATR(10)。收盤突破上軌做多、跌破下軌做空；趨勢方向以收盤相對中軌位置判定。停損停利沿用 ATR 倍數。',
      source: '標準卡爾特通道定義（Keltner 1960 / Raschke 修正版）· EMA ± N×ATR；本專案未內建此策略，為本次新增，公式與參數於頁面明列',
      params: { kind: 'keltner', kc_ema_len: 20, kc_atr_len: 10, kc_mult: 2.0 }
    }
  ];

  var DEFAULT_PRESET_ID = 'ctma_default';

  function getPreset(id) {
    for (var i = 0; i < STRATEGY_PRESETS.length; i++) {
      if (STRATEGY_PRESETS[i].id === id) return STRATEGY_PRESETS[i];
    }
    return STRATEGY_PRESETS[0];
  }

  /** 依預設 id 取得「已與預設值合併」的完整參數物件；可再疊加使用者自訂覆寫。 */
  function paramsForPreset(id, overrides) {
    var base = defaultParams(getPreset(id).params);
    if (overrides) {
      for (var k in overrides) {
        if (Object.prototype.hasOwnProperty.call(overrides, k)) base[k] = overrides[k];
      }
    }
    return base;
  }

  /** 各策略種類對應的參數群組（決定參數編輯器顯示哪些欄位）。 */
  var KIND_GROUPS = {
    ctma: ['trend', 'momentum', 'risk', 'supertrend', 'direction'],
    bb: ['bollinger', 'risk', 'direction'],
    keltner: ['keltner', 'risk', 'direction']
  };

  /** 依預設 id 篩選出該策略相關的參數規格（供參數編輯器使用）。 */
  function specsForPreset(id) {
    var kind = getPreset(id).kind || 'ctma';
    var gs = KIND_GROUPS[kind] || KIND_GROUPS.ctma;
    return PARAM_SPECS.filter(function (s) { return gs.indexOf(s.group) >= 0; });
  }

  /* ------------------------------------------------------------------ *
   * 可自訂參數的規格（供前端產生輸入控制項與驗證）
   * ------------------------------------------------------------------ *
   * 每個 spec 的預設值一律取自 defaultParams()（即 StrategyParams），
   * 不另外硬編一組數字，避免與 Python 端預設值不一致。
   * ------------------------------------------------------------------ */
  var PARAM_SPECS = [
    { key: 'ema_fast',       label: 'EMA 快線週期',    type: 'int',   min: 2,   max: 500,  step: 1,   group: 'trend' },
    { key: 'ema_slow',       label: 'EMA 慢線週期',    type: 'int',   min: 3,   max: 1000, step: 1,   group: 'trend' },
    { key: 'rsi_len',        label: 'RSI 週期',        type: 'int',   min: 2,   max: 100,  step: 1,   group: 'momentum' },
    { key: 'atr_len',        label: 'ATR 週期',        type: 'int',   min: 2,   max: 100,  step: 1,   group: 'risk' },
    { key: 'atr_stop_mult',  label: 'ATR 停損倍數',    type: 'float', min: 0.1, max: 20,   step: 0.1, group: 'risk' },
    { key: 'atr_trail_mult', label: 'ATR 移動停損倍數', type: 'float', min: 0.1, max: 20,   step: 0.1, group: 'risk' },
    { key: 'atr_tp_mult',    label: 'ATR 停利倍數',    type: 'float', min: 0.1, max: 50,   step: 0.1, group: 'risk' },
    { key: 'min_atr_pct',    label: 'ATR% 門檻',       type: 'float', min: 0,   max: 20,   step: 0.1, group: 'risk' },
    { key: 'st_len',         label: 'Supertrend 週期', type: 'int',   min: 2,   max: 100,  step: 1,   group: 'supertrend' },
    { key: 'st_mult',        label: 'Supertrend 倍數', type: 'float', min: 0.1, max: 20,   step: 0.1, group: 'supertrend' },
    { key: 'allow_short',    label: '允許做空',        type: 'bool',  group: 'direction' },
    { key: 'use_supertrend', label: 'Supertrend 過濾', type: 'bool',  group: 'supertrend' },
    // --- 布林通道反轉 ---
    { key: 'bb_len',            label: '布林週期',        type: 'int',   min: 2,   max: 200,  step: 1,   group: 'bollinger' },
    { key: 'bb_mult',           label: '布林標準差倍數',  type: 'float', min: 0.1, max: 10,   step: 0.1, group: 'bollinger' },
    { key: 'bb_rsi_len',        label: '布林 RSI 週期',    type: 'int',   min: 2,   max: 100,  step: 1,   group: 'bollinger' },
    { key: 'bb_rsi_oversold',   label: 'RSI 超賣門檻',     type: 'float', min: 0,   max: 100,  step: 1,   group: 'bollinger' },
    { key: 'bb_rsi_overbought', label: 'RSI 超買門檻',     type: 'float', min: 0,   max: 100,  step: 1,   group: 'bollinger' },
    // --- 卡爾特通道 ---
    { key: 'kc_ema_len',        label: '卡爾特 EMA 週期',  type: 'int',   min: 2,   max: 200,  step: 1,   group: 'keltner' },
    { key: 'kc_atr_len',        label: '卡爾特 ATR 週期',  type: 'int',   min: 2,   max: 100,  step: 1,   group: 'keltner' },
    { key: 'kc_mult',           label: '卡爾特 ATR 倍數',  type: 'float', min: 0.1, max: 10,   step: 0.1, group: 'keltner' },
    // --- 建議槓桿比率（獨立區塊，不屬於任何策略；供該區塊的參數控制項使用） ---
    { key: 'dr_target_vol',     label: '目標波動度 (ATR%)', type: 'float', min: 0.1, max: 20,   step: 0.1, group: 'leverage' },
    { key: 'dr_max_lev',        label: '最大槓桿',        type: 'float', min: 0.1, max: 20,   step: 0.1, group: 'leverage' },
    { key: 'dr_min_lev',        label: '最小槓桿',        type: 'float', min: 0.1, max: 20,   step: 0.1, group: 'leverage' },
    { key: 'dr_atr_len',        label: '建議槓桿 ATR 週期', type: 'int',   min: 2,   max: 100,  step: 1,   group: 'leverage' }
  ];

  /* ------------------------------------------------------------------ *
   * 建議槓桿比率區塊的參數群組
   * ------------------------------------------------------------------
   * 該區塊獨立於策略選擇：不屬於任何策略的參數群組，因此不會出現在策略的
   * 參數編輯器中；但 dr_* 仍在 defaultParams() 內，故 validateParams 依然
   * 會驗證它們（沿用既有的範圍與合理性檢查）。
   * ------------------------------------------------------------------ */
  var LEVERAGE_GROUPS = ['leverage'];

  /** 依群組名稱集合篩選參數規格（供策略編輯器與建議槓桿編輯器共用）。 */
  function specsForGroups(groups) {
    return PARAM_SPECS.filter(function (s) { return groups.indexOf(s.group) >= 0; });
  }

  /**
   * 驗證使用者自訂參數。
   * 回傳 { ok, errors:[{key,msg}], params }；params 為「已合併」的完整參數物件，
   * 呼叫端應以 ok 決定是否採用（不合法時仍回傳合併結果，方便除錯）。
   */
  function validateParams(overrides, base) {
    var errors = [];
    var merged = defaultParams(base || {});
    if (overrides) {
      for (var k in overrides) {
        if (Object.prototype.hasOwnProperty.call(overrides, k)) merged[k] = overrides[k];
      }
    }

    for (var i = 0; i < PARAM_SPECS.length; i++) {
      var s = PARAM_SPECS[i];
      if (!Object.prototype.hasOwnProperty.call(merged, s.key)) continue;
      var v = merged[s.key];
      if (s.type === 'bool') {
        if (typeof v !== 'boolean') errors.push({ key: s.key, msg: s.label + ' 必須為布林值' });
        continue;
      }
      var num = Number(v);
      if (!Number.isFinite(num)) {
        errors.push({ key: s.key, msg: s.label + ' 必須為數字（目前為空或非數字）' });
        continue;
      }
      if (s.type === 'int' && Math.floor(num) !== num) {
        errors.push({ key: s.key, msg: s.label + ' 必須為正整數（目前 ' + num + '）' });
        continue;
      }
      if (num < s.min || num > s.max) {
        errors.push({
          key: s.key,
          msg: s.label + ' 必須介於 ' + s.min + ' 與 ' + s.max + ' 之間（目前 ' + num + '）'
        });
      }
    }

    // 跨欄位檢查：EMA 快線必須小於慢線
    var ef = Number(merged.ema_fast), es = Number(merged.ema_slow);
    if (Number.isFinite(ef) && Number.isFinite(es) && ef >= es) {
      errors.push({
        key: 'ema_fast',
        msg: 'EMA 快線週期（' + merged.ema_fast + '）必須小於慢線週期（' + merged.ema_slow + '）'
      });
    }

    // 跨欄位檢查：布林 RSI 超賣必須小於超買
    var os = Number(merged.bb_rsi_oversold), ob = Number(merged.bb_rsi_overbought);
    if (Number.isFinite(os) && Number.isFinite(ob) && os >= ob) {
      errors.push({
        key: 'bb_rsi_oversold',
        msg: 'RSI 超賣門檻（' + merged.bb_rsi_oversold + '）必須小於超買門檻（' + merged.bb_rsi_overbought + '）'
      });
    }

    // 跨欄位檢查：動態風險最小槓桿必須小於等於最大槓桿
    var mn = Number(merged.dr_min_lev), mx = Number(merged.dr_max_lev);
    if (Number.isFinite(mn) && Number.isFinite(mx) && mn > mx) {
      errors.push({
        key: 'dr_min_lev',
        msg: '最小槓桿（' + merged.dr_min_lev + '）必須小於等於最大槓桿（' + merged.dr_max_lev + '）'
      });
    }

    return { ok: errors.length === 0, errors: errors, params: merged };
  }

  /* ------------------------------------------------------------------ *
   * 指標與訊號
   * ------------------------------------------------------------------ */

  function addIndicators(bars, p) {
    var close = bars.map(function (b) { return b.close; });
    var high = bars.map(function (b) { return b.high; });
    var low = bars.map(function (b) { return b.low; });
    var out = {
      ema_fast: ema(close, p.ema_fast),
      ema_slow: ema(close, p.ema_slow),
      rsi: rsi(close, p.rsi_len),
      atr: atr(high, low, close, p.atr_len)
    };
    var m = macd(close, p.macd_fast, p.macd_slow, p.macd_signal);
    out.macd = m.macd; out.macd_signal = m.signal; out.macd_hist = m.hist;
    out.atr_pct = out.atr.map(function (v, i) { return v / close[i] * 100; });
    var stRes = supertrend(high, low, close, p.st_len, p.st_mult);
    out.supertrend = stRes.st; out.st_dir = stRes.dir;
    // --- 布林通道（SMA 中軌 ± N×母體標準差）---
    var bbMid = sma(close, p.bb_len);
    var bbStd = rollingStd(close, p.bb_len);
    out.bb_mid = bbMid; out.bb_std = bbStd;
    out.bb_upper = bbMid.map(function (v, i) { return isNum(v) && isNum(bbStd[i]) ? v + p.bb_mult * bbStd[i] : NaN; });
    out.bb_lower = bbMid.map(function (v, i) { return isNum(v) && isNum(bbStd[i]) ? v - p.bb_mult * bbStd[i] : NaN; });
    out.bb_rsi = rsi(close, p.bb_rsi_len);
    // --- 卡爾特通道（EMA 中軌 ± N×ATR）---
    var kcMid = ema(close, p.kc_ema_len);
    var kcAtr = atr(high, low, close, p.kc_atr_len);
    out.kc_mid = kcMid; out.kc_atr = kcAtr;
    out.kc_upper = kcMid.map(function (v, i) { return isNum(v) && isNum(kcAtr[i]) ? v + p.kc_mult * kcAtr[i] : NaN; });
    out.kc_lower = kcMid.map(function (v, i) { return isNum(v) && isNum(kcAtr[i]) ? v - p.kc_mult * kcAtr[i] : NaN; });
    // --- 建議槓桿比率用的 ATR（獨立區塊，不依賴策略）---
    out.dr_atr = atr(high, low, close, p.dr_atr_len);
    out.dr_atr_pct = out.dr_atr.map(function (v, i) { return v / close[i] * 100; });
    out.close = close; out.high = high; out.low = low;
    return out;
  }

  /**
   * 產生訊號（三層語意，與 strategy.py 完全一致）：
   *   bull_cross / bear_cross  狀態翻轉（無條件平倉）
   *   long_entry_ok / short_entry_ok  動能＋波動度過濾是否通過（決定是否開新倉）
   *   signal  1=看多翻轉, -1=看空翻轉, 0=無
   */
  function generateSignals(bars, p) {
    var ind = addIndicators(bars, p);
    var n = bars.length;
    var bull = new Array(n).fill(false), bear = new Array(n).fill(false);
    var longOk = new Array(n).fill(false), shortOk = new Array(n).fill(false);
    var signal = new Array(n).fill(0);

    for (var i = 0; i < n; i++) {
      var ef = ind.ema_fast[i], es = ind.ema_slow[i];
      if (i === 0) continue;
      var efP = ind.ema_fast[i - 1], esP = ind.ema_slow[i - 1];
      if (!isNum(ef) || !isNum(es) || !isNum(efP) || !isNum(esP)) continue;
      var cu = (ef > es) && (efP <= esP);
      var cd = (ef < es) && (efP >= esP);
      if (p.use_supertrend) {
        cu = cu && ind.st_dir[i] === 1;
        cd = cd && ind.st_dir[i] === -1;
      }
      bull[i] = cu; bear[i] = cd;

      var ap = ind.atr_pct[i], rv = ind.rsi[i], h = ind.macd_hist[i], a = ind.atr[i];
      var volOk = isNum(ap) && ap >= p.min_atr_pct;
      var warm = isNum(es) && isNum(a) && isNum(rv);
      longOk[i] = cu && isNum(rv) && rv > p.rsi_long_min && isNum(h) && h > 0 && volOk && warm;
      shortOk[i] = cd && isNum(rv) && rv < p.rsi_short_max && isNum(h) && h < 0 && volOk && warm && !!p.allow_short;

      signal[i] = cu ? 1 : (cd ? -1 : 0);
    }
    return {
      ind: ind, bull: bull, bear: bear,
      long_entry_ok: longOk, short_entry_ok: shortOk, signal: signal,
      bars: bars, params: p
    };
  }

  /* ------------------------------------------------------------------ *
   * 新增策略：布林通道反轉（BB Mean Reversion）
   * ------------------------------------------------------------------ *
   * 公式（標準定義，頁面明列）：
   *   中軌 = SMA(close, bb_len)
   *   上軌 = 中軌 + bb_mult × σ(close, bb_len)   （σ 為母體標準差）
   *   下軌 = 中軌 − bb_mult × σ(close, bb_len)
   *   做多（反轉）：close ≤ 下軌 且 RSI(bb_rsi_len) < bb_rsi_oversold
   *   做空（反轉）：close ≥ 上軌 且 RSI(bb_rsi_len) > bb_rsi_overbought
   * ------------------------------------------------------------------ */
  function generateSignalsBB(bars, p) {
    var ind = addIndicators(bars, p);
    var n = bars.length;
    var bull = new Array(n).fill(false), bear = new Array(n).fill(false);
    var longOk = new Array(n).fill(false), shortOk = new Array(n).fill(false);
    var signal = new Array(n).fill(0);

    for (var i = 0; i < n; i++) {
      var c = ind.close[i], lo = ind.bb_lower[i], up = ind.bb_upper[i], rv = ind.bb_rsi[i];
      if (!isNum(c) || !isNum(lo) || !isNum(up)) continue;
      var touchLow = c <= lo;
      var touchHigh = c >= up;
      bull[i] = touchLow; bear[i] = touchHigh;
      longOk[i] = touchLow && isNum(rv) && rv < p.bb_rsi_oversold;
      shortOk[i] = touchHigh && isNum(rv) && rv > p.bb_rsi_overbought && !!p.allow_short;
      signal[i] = touchLow ? 1 : (touchHigh ? -1 : 0);
    }
    return {
      ind: ind, bull: bull, bear: bear,
      long_entry_ok: longOk, short_entry_ok: shortOk, signal: signal,
      bars: bars, params: p
    };
  }

  /* ------------------------------------------------------------------ *
   * 新增策略：卡爾特通道（Keltner Channels）
   * ------------------------------------------------------------------ *
   * 公式（標準定義，頁面明列）：
   *   中軌 = EMA(close, kc_ema_len)
   *   上軌 = 中軌 + kc_mult × ATR(kc_atr_len)
   *   下軌 = 中軌 − kc_mult × ATR(kc_atr_len)
   *   做多：close > 上軌（突破）
   *   做空：close < 下軌（跌破）
   *   趨勢方向：close 相對中軌位置（通道方向判定）
   * ------------------------------------------------------------------ */
  function generateSignalsKeltner(bars, p) {
    var ind = addIndicators(bars, p);
    var n = bars.length;
    var bull = new Array(n).fill(false), bear = new Array(n).fill(false);
    var longOk = new Array(n).fill(false), shortOk = new Array(n).fill(false);
    var signal = new Array(n).fill(0);

    for (var i = 0; i < n; i++) {
      var c = ind.close[i], lo = ind.kc_lower[i], up = ind.kc_upper[i], mid = ind.kc_mid[i];
      if (!isNum(c) || !isNum(lo) || !isNum(up) || !isNum(mid)) continue;
      var brkUp = c > up, brkDn = c < lo;
      bull[i] = brkUp; bear[i] = brkDn;
      longOk[i] = brkUp && c > mid;
      shortOk[i] = brkDn && c < mid && !!p.allow_short;
      signal[i] = brkUp ? 1 : (brkDn ? -1 : 0);
    }
    return {
      ind: ind, bull: bull, bear: bear,
      long_entry_ok: longOk, short_entry_ok: shortOk, signal: signal,
      bars: bars, params: p
    };
  }

  /* ------------------------------------------------------------------ *
   * 依策略種類分派至對應的訊號產生器。 */
  function generateSignalsFor(bars, p) {
    var kind = p.kind || 'ctma';
    if (kind === 'bb') return generateSignalsBB(bars, p);
    if (kind === 'keltner') return generateSignalsKeltner(bars, p);
    return generateSignals(bars, p);
  }

  /** 依波動度計算建議槓桿（供「建議槓桿比率」獨立區塊使用）。 */
  function dynamicLeverage(atrPct, p) {
    if (!isNum(atrPct) || atrPct <= 0) return null;
    var raw = p.dr_target_vol / atrPct;
    var lev = Math.max(p.dr_min_lev, Math.min(p.dr_max_lev, raw));
    return {
      leverage: round(lev, 2),
      raw_leverage: round(raw, 2),
      position_pct: round(lev / p.dr_max_lev * 100, 1),
      realized_vol_pct: round(atrPct, 4),
      target_vol_pct: p.dr_target_vol,
      capped: raw > p.dr_max_lev ? 'max' : (raw < p.dr_min_lev ? 'min' : null)
    };
  }

  /**
   * 建議槓桿比率（獨立於策略選擇；任何策略下都對每檔標的計算）
   * ------------------------------------------------------------------
   * 公式（沿用原「動態風險敞口」策略的波動度目標法，本次移到獨立區塊）：
   *   實際波動度 = ATR(dr_atr_len) / close × 100   （即 ATR%）
   *   建議槓桿   = clamp(目標波動度 ÷ 實際波動度, dr_min_lev, dr_max_lev)
   *   部位比例   = 建議槓桿 ÷ dr_max_lev
   * 波動越高 → 槓桿越低（方向關係）。
   *
   * 參數預設值來源：
   *   目標波動度 2.0% / 最小槓桿 0.5× / ATR 週期 14 → 沿用原 dyn_risk 預設
   *   槓桿上限 3.0× → crypto_quant/backtest.py BacktestConfig.max_leverage
   *   方向模型（僅供對照，本區塊不使用方向）→ crypto_quant/strategy.py
   * ------------------------------------------------------------------
   */
  function computeLeverageRow(row, p) {
    var lv = dynamicLeverage(row.dr_atr_pct, p);
    if (!lv) return null;
    return {
      leverage: lv.leverage,
      raw_leverage: lv.raw_leverage,
      position_pct: lv.position_pct,
      realized_vol_pct: lv.realized_vol_pct,
      target_vol_pct: lv.target_vol_pct,
      capped: lv.capped,
      atr_abs: isNum(row.dr_atr) ? round(row.dr_atr, 8) : null,
      atr_pct: isNum(row.dr_atr_pct) ? round(row.dr_atr_pct, 4) : null,
      lever_atr_len: p.dr_atr_len
    };
  }

  /**
   * 策略專屬指標（供卡片與掃描表的「策略指標」欄使用）：
   *   bb       → 布林 %B = (close − 下軌) ÷ (上軌 − 下軌) × 100
   *   keltner  → 卡爾特 %B = 同上，改用卡爾特上下軌
   *   ctma     → ATR（絕對值）
   * 回傳 { key, label, value, text }
   */
  function metricFor(kind, row, p, trend) {
    if (kind === 'bb') {
      var lo = row.bb_lower, up = row.bb_upper, c = row.close;
      var b = (isNum(lo) && isNum(up) && isNum(c) && up > lo) ? (c - lo) / (up - lo) * 100 : null;
      return { key: 'bb_pctb', label: '布林 %B', value: isNum(b) ? round(b, 2) : null,
               text: isNum(b) ? round(b, 2).toFixed(2) + '%' : '—' };
    }
    if (kind === 'keltner') {
      var klo = row.kc_lower, kup = row.kc_upper, kc = row.close;
      var kb = (isNum(klo) && isNum(kup) && isNum(kc) && kup > klo) ? (kc - klo) / (kup - klo) * 100 : null;
      return { key: 'kc_pctb', label: '卡爾特 %B', value: isNum(kb) ? round(kb, 2) : null,
               text: isNum(kb) ? round(kb, 2).toFixed(2) + '%' : '—' };
    }
    return { key: 'atr', label: 'ATR（絕對）', value: isNum(row.atr) ? round(row.atr, 8) : null,
             text: isNum(row.atr) ? String(round(row.atr, 8)) : '—' };
  }

  /* ------------------------------------------------------------------ *
   * 訊號強度（0–100，複刻 signal_engine.compute_strength）
   * ------------------------------------------------------------------ */

  function computeStrength(row, trend, p) {
    var atrV = row.atr, close = row.close;
    if (!isNum(atrV) || atrV <= 0 || !isNum(close) || close <= 0) {
      return { trend: 0, momentum: 0, volatility: 0, supertrend: 0, total: 0, label: 'N/A' };
    }

    var spreadAtr = (row.ema_fast - row.ema_slow) / atrV;
    var t = trend > 0 ? clamp(spreadAtr / 1.5) : (trend < 0 ? clamp(-spreadAtr / 1.5) : 0);

    var rv = row.rsi, hist = row.macd_hist, r = 0, m = 0;
    if (isNum(rv) && isNum(hist)) {
      if (trend > 0) { r = clamp((rv - 50) / 20); m = clamp(hist / (0.5 * atrV)); }
      else if (trend < 0) { r = clamp((50 - rv) / 20); m = clamp(-hist / (0.5 * atrV)); }
    }
    var mom = (r + m) / 2;

    var ap = row.atr_pct, v;
    if (!isNum(ap)) v = 0;
    else {
      var lo = p.min_atr_pct;
      if (ap < lo) v = 0;
      else if (ap <= VOL_WILD_CAP_PCT) v = clamp(0.6 + 0.4 * ((ap - lo) / Math.max(VOL_WILD_CAP_PCT - lo, 1e-9)));
      else v = clamp(1 - ((ap - VOL_WILD_CAP_PCT) / VOL_WILD_CAP_PCT));
    }
    if (trend === 0) v *= 0.5;

    var st = 0;
    if (trend !== 0 && isNum(row.st_dir) && row.st_dir === (trend > 0 ? 1 : -1)) st = 1;

    var total = 100 * (WEIGHTS.trend * t + WEIGHTS.momentum * mom + WEIGHTS.volatility * v + WEIGHTS.supertrend * st);
    return {
      trend: round(t, 3), momentum: round(mom, 3), volatility: round(v, 3),
      supertrend: st, total: round(total, 1), label: strengthLabel(total),
      method: 'ctma', method_label: strengthMethodLabel('ctma')
    };
  }

  /* ------------------------------------------------------------------ *
   * 布林通道反轉：專屬強度評分（0–100）
   * ------------------------------------------------------------------ *
   * 本次新設計的啟發式評分（不是既有程式碼的原始邏輯），衡量「反轉訊號的明確程度」：
   *
   *   depth   軌道外深度：|%B − 50| / 50，夾在 [0,1]。價格越深入上／下軌之外（%B 越偏離中軌），
   *           反轉訊號越明確。這取代 CTMA 的「EMA 間距」趨勢分項。
   *   rsi     RSI 極值：訊號側的 RSI 越極端越強。做多用 (bb_rsi_oversold − RSI)/oversold，
   *           做空用 (RSI − bb_rsi_overbought)/(100 − bb_rsi_overbought)，皆夾在 [0,1]。
   *           這取代 CTMA 的「MACD 動能」分項。中性趨勢時折半。
   *   squeeze 通道壓縮：以 (上軌−下軌)/中軌 標準化，<2% 視為壓縮（突破後反轉更強）給高分，
   *           寬度越大分數越低。這個分項 CTMA 完全沒有。
   * ------------------------------------------------------------------ */
  function computeStrengthBB(row, trend, p) {
    var out = { depth: 0, rsi: 0, squeeze: 0, total: 0, label: 'N/A', method: 'bb', method_label: strengthMethodLabel('bb') };
    var up = row.bb_upper, lo = row.bb_lower, mid = row.bb_mid, c = row.close;
    if (!isNum(up) || !isNum(lo) || !isNum(mid) || !isNum(c) || up <= lo || mid <= 0) return out;

    // --- depth：%B 距中軌的相對深度 ---
    var pctB = (c - lo) / (up - lo) * 100;
    out.pct_b = round(pctB, 2);
    var depth = clamp(Math.abs(pctB - 50) / 50, 0, 1);

    // --- rsi：訊號側 RSI 極值程度 ---
    var rv = row.bb_rsi, r = 0;
    if (isNum(rv)) {
      var os = isNum(p.bb_rsi_oversold) ? p.bb_rsi_oversold : 30;
      var ob = isNum(p.bb_rsi_overbought) ? p.bb_rsi_overbought : 70;
      if (trend > 0) r = os > 0 ? clamp((os - rv) / os, 0, 1) : 0;
      else if (trend < 0) r = (100 - ob) > 0 ? clamp((rv - ob) / (100 - ob), 0, 1) : 0;
    }
    if (trend === 0) r *= 0.5;

    // --- squeeze：通道寬度（% of 中軌），越窄越強 ---
    var widthPct = (up - lo) / mid * 100;
    out.width_pct = round(widthPct, 3);
    var sq = 1 - clamp((widthPct - BB_SQUEEZE_TIGHT_PCT) / (BB_SQUEEZE_WIDE_PCT - BB_SQUEEZE_TIGHT_PCT), 0, 1);

    var total = 100 * (WEIGHTS_BB.depth * depth + WEIGHTS_BB.rsi * r + WEIGHTS_BB.squeeze * sq);
    out.depth = round(depth, 3); out.rsi = round(r, 3); out.squeeze = round(sq, 3);
    out.total = round(total, 1); out.label = strengthLabel(total);
    return out;
  }

  /* ------------------------------------------------------------------ *
   * 卡爾特通道：專屬強度評分（0–100）
   * ------------------------------------------------------------------ *
   * 本次新設計的啟發式評分（不是既有程式碼的原始邏輯），衡量「趨勢突破的明確程度」：
   *
   *   breakout  突破幅度：以 ATR 標準化。價在上軌外 → 距上軌距離 / (kc_mult × ATR)；
   *             價在下軌外 → 距下軌距離 / (kc_mult × ATR)；價在通道內 → 以距中軌的相對位置
   *             （|c − mid| / (kc_mult × ATR)）打折計分。夾在 [0,1]。
   *   slope     中軌斜率方向一致性：kc_mid 現在值 vs slLook 根之前，以 ATR 標準化 ±，夾在 [0,1]，
   *             需與趨勢方向一致。
   *   expansion 通道擴張程度：當前寬度 (2 × kc_mult × ATR) 相對前 exLook 根的平均寬度，
   *             比值 1 給 0，KELT_EXPANSION_SPAN 以上給 1。這與 BB 的 squeeze 方向相反
   *             （BB 要窄、KC 要寬），確保兩者對同一標的必然給出不同分數。
   * ------------------------------------------------------------------ */
  function computeStrengthKeltner(row, trend, p, ind, i) {
    var out = { breakout: 0, slope: 0, expansion: 0, total: 0, label: 'N/A', method: 'keltner', method_label: strengthMethodLabel('keltner') };
    var up = row.kc_upper, lo = row.kc_lower, mid = row.kc_mid, c = row.close;
    var atrV = row.atr;
    if (!isNum(up) || !isNum(lo) || !isNum(mid) || !isNum(c) || !isNum(atrV) || atrV <= 0) return out;

    var half = p.kc_mult * atrV;           // 通道半寬（= 中軌到軌道的距離）
    if (!isNum(half) || half <= 0) return out;

    // --- breakout：突破幅度（ATR 標準化）---
    var brk;
    if (c > up) brk = clamp((c - up) / half, 0, 1);
    else if (c < lo) brk = clamp((lo - c) / half, 0, 1);
    else brk = 0.5 * clamp(Math.abs(c - mid) / half, 0, 1);   // 通道內：以距中軌相對位置打折
    if (trend === 0) brk *= 0.5;

    // --- slope：中軌斜率方向一致性 ---
    var sl = 0;
    if (ind && ind.kc_mid && isNum(i) && i - KC_SLOPE_LOOKBACK >= 0) {
      var midAgo = ind.kc_mid[i - KC_SLOPE_LOOKBACK];
      if (isNum(midAgo)) {
        var slopeAtr = (mid - midAgo) / atrV;
        if (trend > 0) sl = clamp(slopeAtr / KC_SLOPE_REF, 0, 1);
        else if (trend < 0) sl = clamp(-slopeAtr / KC_SLOPE_REF, 0, 1);
      }
    }
    if (trend === 0) sl *= 0.5;

    // --- expansion：通道擴張程度（現寬 vs 前 N 根平均寬，以 ATR 為代理）---
    var ex = 0;
    if (ind && ind.kc_atr && isNum(i) && i - KC_EXPANSION_LOOKBACK >= 0) {
      var sum = 0, cnt = 0;
      for (var back = 1; back <= KC_EXPANSION_LOOKBACK; back++) {
        var a = ind.kc_atr[i - back];
        if (isNum(a) && a > 0) { sum += a; cnt++; }
      }
      if (cnt > 0) {
        var avgAgo = sum / cnt;
        var ratio = atrV / avgAgo;                        // >1 表示通道正在擴張
        ex = clamp((ratio - 1) / KC_EXPANSION_SPAN, 0, 1);
        out.width_ratio = round(ratio, 3);
      }
    }

    var total = 100 * (WEIGHTS_KC.breakout * brk + WEIGHTS_KC.slope * sl + WEIGHTS_KC.expansion * ex);
    out.breakout = round(brk, 3); out.slope = round(sl, 3); out.expansion = round(ex, 3);
    out.total = round(total, 1); out.label = strengthLabel(total);
    return out;
  }

  /* ------------------------------------------------------------------ *
   * 依策略種類分派強度評分
   *   ctma / wfo → 原 computeStrength（共用動能分項，未改動）
   *   bb                   → computeStrengthBB（反轉明確程度）
   *   keltner              → computeStrengthKeltner（突破明確程度）
   * ------------------------------------------------------------------ */
  function computeStrengthFor(kind, row, trend, p, ind, i) {
    if (kind === 'bb') return computeStrengthBB(row, trend, p);
    if (kind === 'keltner') return computeStrengthKeltner(row, trend, p, ind, i);
    return computeStrength(row, trend, p);
  }

  /* ------------------------------------------------------------------ *
   * 單一 K 棒序列的完整分析（複刻 signal_engine._analyze_frame）
   * ------------------------------------------------------------------ */

  function barsSince(flags, upto) {
    for (var i = upto; i >= 0; i--) if (flags[i]) return upto - i;
    return null;
  }

  function rowAt(sig, i) {
    var ind = sig.ind;
    return {
      close: ind.close[i], high: ind.high[i], low: ind.low[i],
      ema_fast: ind.ema_fast[i], ema_slow: ind.ema_slow[i],
      rsi: ind.rsi[i], macd: ind.macd[i], macd_signal: ind.macd_signal[i],
      macd_hist: ind.macd_hist[i], atr: ind.atr[i], atr_pct: ind.atr_pct[i],
      st_dir: ind.st_dir[i], supertrend: ind.supertrend[i],
      bb_mid: ind.bb_mid[i], bb_upper: ind.bb_upper[i], bb_lower: ind.bb_lower[i],
      bb_rsi: ind.bb_rsi[i],
      kc_mid: ind.kc_mid[i], kc_upper: ind.kc_upper[i], kc_lower: ind.kc_lower[i],
      dr_atr: ind.dr_atr[i], dr_atr_pct: ind.dr_atr_pct[i]
    };
  }

  function analyzeFrame(bars, p) {
    var sig = generateSignalsFor(bars, p);
    var i = bars.length - 1;
    var row = rowAt(sig, i);

    var ef = row.ema_fast, es = row.ema_slow, close = row.close;
    var kind = p.kind || 'ctma';
    // 趨勢方向依策略種類判定（頁面明列）：
    //   ctma：EMA 快線 vs 慢線
    //   bb / keltner：收盤相對中軌位置（通道方向）
    var trend;
    if (kind === 'bb') {
      trend = (isNum(close) && isNum(row.bb_mid)) ? (close > row.bb_mid ? 1 : (close < row.bb_mid ? -1 : 0)) : 0;
    } else if (kind === 'keltner') {
      trend = (isNum(close) && isNum(row.kc_mid)) ? (close > row.kc_mid ? 1 : (close < row.kc_mid ? -1 : 0)) : 0;
    } else {
      trend = (isNum(ef) && isNum(es)) ? (ef > es ? 1 : (ef < es ? -1 : 0)) : 0;
    }

    var strength = computeStrengthFor(kind, row, trend, p, sig.ind, i);
    var bullAgo = barsSince(sig.bull, i), bearAgo = barsSince(sig.bear, i);

    var lastCross = null;
    if (bullAgo !== null && (bearAgo === null || bullAgo <= bearAgo)) lastCross = { type: 'BULL_CROSS', bars_ago: bullAgo };
    else if (bearAgo !== null) lastCross = { type: 'BEAR_CROSS', bars_ago: bearAgo };

    // 該次交叉「當下」是否通過動能＋波動度過濾（回看交叉當根與前一根）
    function entryPassed(flags, ago) {
      if (ago === null) return false;
      var j = i - ago, lo = Math.max(0, j - 1);
      return !!(flags[j] || flags[lo]);
    }

    var emaGapPct = (isNum(ef) && isNum(es) && close > 0) ? (ef - es) / close * 100 : NaN;
    var spreadAtr = (isNum(ef) && isNum(es) && isNum(row.atr) && row.atr > 0) ? (ef - es) / row.atr : NaN;

    return {
      trend: trend,
      trend_label: TREND_LABELS[trend],
      price: round(close, 8),
      ema_fast: isNum(ef) ? round(ef, 8) : null,
      ema_slow: isNum(es) ? round(es, 8) : null,
      ema_gap_pct: isNum(emaGapPct) ? round(emaGapPct, 4) : null,
      ema_spread_atr: isNum(spreadAtr) ? round(spreadAtr, 4) : null,
      rsi: isNum(row.rsi) ? round(row.rsi, 2) : null,
      macd: isNum(row.macd) ? round(row.macd, 6) : null,
      macd_signal: isNum(row.macd_signal) ? round(row.macd_signal, 6) : null,
      macd_hist: isNum(row.macd_hist) ? round(row.macd_hist, 6) : null,
      atr: isNum(row.atr) ? round(row.atr, 8) : null,
      atr_pct: isNum(row.atr_pct) ? round(row.atr_pct, 4) : null,
      st_dir: isNum(row.st_dir) ? row.st_dir : null,
      supertrend: isNum(row.supertrend) ? round(row.supertrend, 8) : null,
      // --- 策略專屬欄位（依 kind 顯示）---
      bb_mid: isNum(row.bb_mid) ? round(row.bb_mid, 8) : null,
      bb_upper: isNum(row.bb_upper) ? round(row.bb_upper, 8) : null,
      bb_lower: isNum(row.bb_lower) ? round(row.bb_lower, 8) : null,
      bb_rsi: isNum(row.bb_rsi) ? round(row.bb_rsi, 2) : null,
      kc_mid: isNum(row.kc_mid) ? round(row.kc_mid, 8) : null,
      kc_upper: isNum(row.kc_upper) ? round(row.kc_upper, 8) : null,
      kc_lower: isNum(row.kc_lower) ? round(row.kc_lower, 8) : null,
      dr_atr: isNum(row.dr_atr) ? round(row.dr_atr, 8) : null,
      dr_atr_pct: isNum(row.dr_atr_pct) ? round(row.dr_atr_pct, 4) : null,
      strategy_kind: kind,
      strategy_metric: metricFor(kind, row, p, trend),
      signal: sig.signal[i],
      signal_label: SIGNAL_LABELS[sig.signal[i]],
      long_entry_ok: !!sig.long_entry_ok[i],
      short_entry_ok: !!sig.short_entry_ok[i],
      last_cross: lastCross,
      entry_passes: {
        long: entryPassed(sig.long_entry_ok, bullAgo),
        short: entryPassed(sig.short_entry_ok, bearAgo)
      },
      strength: strength
    };
  }

  /* ------------------------------------------------------------------ *
   * 對單一標的輸出完整即時狀態（複刻 signal_engine.analyze_symbol 的
   * 訊號／風控部分；帳戶持倉推導不在前端做，已在報告中明確標示）
   * ------------------------------------------------------------------ */

  function analyzeBars(bars, symbol, opts) {
    opts = opts || {};
    var p = opts.params || defaultParams();
    var interval = opts.interval || '1d';
    var nowMs = opts.nowMs || Date.now();
    var serverTimeMs = opts.serverTimeMs;
    var source = opts.source || 'browser-direct:data-api.binance.vision';

    if (!bars || bars.length < 60) {
      throw new Error('K 線不足（' + (bars ? bars.length : 0) + ' 根）');
    }

    var last = bars[bars.length - 1];
    var barOpenMs = last.openTime;
    var barCloseMs = last.closeTime;
    var barIsClosed = !!last.isClosed;

    var provisional = analyzeFrame(bars, p);

    var closed = bars.filter(function (b) { return b.isClosed; });
    var confirmed = closed.length >= 60 ? analyzeFrame(closed, p) : null;

    var prim = confirmed || provisional;

    // 風控關卡必須以「主口徑」那根 K 棒為基準（prim === confirmed 時即為已收盤的
    // 最後一根），否則卡片會顯示 confirmed 的價格，卻用 provisional 的 ATR 算停損，
    // 導致畫面上的停損與價格對不上（真實缺陷，已修正）。
    var atrV = prim.atr, price = prim.price, trend = prim.trend;
    var levels = null;
    if (isNum(atrV) && isNum(price) && trend !== 0) {
      var stop, tp;
      if (trend > 0) { stop = price - p.atr_stop_mult * atrV; tp = price + p.atr_tp_mult * atrV; }
      else { stop = price + p.atr_stop_mult * atrV; tp = price - p.atr_tp_mult * atrV; }
      var risk = Math.abs(price - stop), reward = Math.abs(tp - price);
      levels = {
        entry_ref: round(price, 8), stop: round(stop, 8), take_profit: round(tp, 8),
        risk_distance: round(risk, 8), risk_pct: price ? round(risk / price * 100, 4) : null,
        reward_distance: round(reward, 8), reward_pct: price ? round(reward / price * 100, 4) : null,
        rr_ratio: risk > 0 ? round(reward / risk, 3) : null,
        stop_mult: p.atr_stop_mult, tp_mult: p.atr_tp_mult
      };
    }

    var gap = prim.ema_gap_pct;
    var near = isNum(gap) && prim.signal === 0 && trend !== 0
      && Math.abs(gap) < p.near_trigger_pct;

    var delayS = (nowMs - barCloseMs) / 1000;

    return {
      symbol: symbol, interval: interval, source: source,
      params_kind: (p.kind || 'ctma'),
      fetched_at_utc: new Date(nowMs).toISOString(),
      binance_server_time_utc: serverTimeMs ? new Date(serverTimeMs).toISOString() : null,
      clock_offset_seconds: serverTimeMs ? round((nowMs - serverTimeMs) / 1000, 3) : null,
      bar_open_utc: new Date(barOpenMs).toISOString(),
      bar_close_utc: new Date(barCloseMs).toISOString(),
      bar_is_in_progress: !barIsClosed,
      bar_progress_pct: round(clamp((nowMs - barOpenMs) / (barCloseMs - barOpenMs) * 100, 0, 100), 1),
      data_delay_seconds: round(delayS, 1),
      data_delay_human: humanDelay(delayS),
      near_trigger: !!near,
      near_trigger_threshold_pct: p.near_trigger_pct,
      provisional: provisional,
      confirmed: confirmed,
      trend: prim.trend, trend_label: prim.trend_label,
      signal: prim.signal, signal_label: prim.signal_label,
      strength: prim.strength,
      levels: levels,
      repaint: !!(confirmed && confirmed.signal !== provisional.signal),
      bars_used: bars.length,
      bars_closed: closed.length
    };
  }

  function humanDelay(seconds) {
    var s = Math.abs(seconds);
    if (s < 1) return s.toFixed(2) + ' 秒';
    if (s < 90) return s.toFixed(1) + ' 秒';
    if (s < 5400) return (s / 60).toFixed(1) + ' 分鐘';
    if (s < 172800) return (s / 3600).toFixed(2) + ' 小時';
    return (s / 86400).toFixed(2) + ' 天';
  }

  /* ------------------------------------------------------------------ *
   * 市場掃描的排行邏輯（複刻 scanner.py）
   * ------------------------------------------------------------------ */

  var STABLE_BASES = {
    USDC: 1, USD1: 1, RLUSD: 1, TUSD: 1, FDUSD: 1, BUSD: 1, DAI: 1, USDP: 1,
    PYUSD: 1, USDE: 1, SUSDE: 1, USDD: 1, USTC: 1, USDX: 1, XUSD: 1, AEUR: 1,
    EURI: 1, EUR: 1, USDY: 1, USDM: 1, USDR: 1, GUSD: 1, LUSD: 1, FRAX: 1,
    USDS: 1, USDG: 1, USDQ: 1, USD0: 1, SUSD: 1, CRVUSD: 1, GHO: 1, DOLA: 1,
    UST: 1, MIM: 1, ALUSD: 1, USDF: 1, USDZ: 1, BUIDL: 1
  };
  var MIN_SCAN_ATR_PCT = 0.30;

  function isStablecoin(symbol) {
    if (!/USDT$/.test(symbol)) return false;
    return !!STABLE_BASES[symbol.slice(0, -4)];
  }

  function topSymbolsByVolume(tickers, n, minQuoteVolume, excludeStable) {
    n = n || 25;
    minQuoteVolume = minQuoteVolume === undefined ? 5e6 : minQuoteVolume;
    excludeStable = excludeStable === undefined ? true : excludeStable;
    var rows = [];
    for (var sym in tickers) {
      if (!/USDT$/.test(sym)) continue;
      if (excludeStable && isStablecoin(sym)) continue;
      var base = sym.slice(0, -4);
      if (/UP|DOWN|BULL|BEAR/.test(base)) continue;
      var t = tickers[sym];
      var qv = parseFloat(t.quoteVolume), lastPx = parseFloat(t.lastPrice);
      if (!Number.isFinite(qv) || !Number.isFinite(lastPx)) continue;
      if (qv < minQuoteVolume || lastPx <= 0) continue;
      rows.push({ symbol: sym, quoteVolume: qv, lastPrice: lastPx, changePct: parseFloat(t.priceChangePercent) || 0 });
    }
    rows.sort(function (a, b) { return b.quoteVolume - a.quoteVolume; });
    return rows.slice(0, n);
  }

  function rowOf(r, lev) {
    var c = r.confirmed || r.provisional;
    var lv = r.levels || {};
    return {
      symbol: r.symbol, trend: c.trend_label, signal: c.signal_label,
      strength: c.strength.total, strength_label: c.strength.label,
      price: c.price, ema_gap_pct: c.ema_gap_pct, ema_spread_atr: c.ema_spread_atr,
      rsi: c.rsi, macd_hist: c.macd_hist, atr_pct: c.atr_pct,
      near_trigger: r.near_trigger, repaint: r.repaint,
      stop: lv.stop === undefined ? null : lv.stop,
      take_profit: lv.take_profit === undefined ? null : lv.take_profit,
      rr_ratio: lv.rr_ratio === undefined ? null : lv.rr_ratio,
      leverage: lev || null,
      dr_atr: c.dr_atr === undefined ? null : c.dr_atr,
      dr_atr_pct: c.dr_atr_pct === undefined ? null : c.dr_atr_pct,
      metric_label: c.strategy_metric ? c.strategy_metric.label : null,
      metric_text: c.strategy_metric ? c.strategy_metric.text : null,
      strategy_kind: c.strategy_kind || null,
      trend_int: c.trend,
      data_delay_seconds: r.data_delay_seconds,
      bar_is_in_progress: r.bar_is_in_progress
    };
  }

  function buildRankings(results, topN, levParams) {
    topN = topN || 8;
    // levParams 為建議槓桿區塊的參數；提供時對每一列計算建議槓桿（與策略無關）
    var rows = results.map(function (r) {
      if (!levParams) return rowOf(r, null);
      var c = r.confirmed || r.provisional;
      return rowOf(r, computeLeverageRow(c, levParams));
    });
    var longs = rows.filter(function (x) { return x.trend_int === 1; })
      .sort(function (a, b) { return b.strength - a.strength; });
    var shorts = rows.filter(function (x) { return x.trend_int === -1; })
      .sort(function (a, b) { return b.strength - a.strength; });
    var near = rows.filter(function (x) { return x.near_trigger; })
      .sort(function (a, b) {
        return Math.abs(a.ema_gap_pct === null ? 9e9 : a.ema_gap_pct) - Math.abs(b.ema_gap_pct === null ? 9e9 : b.ema_gap_pct);
      });
    var strongest = rows.slice().sort(function (a, b) { return b.strength - a.strength; });
    var fresh = rows.filter(function (x) { return x.signal !== 'HOLD'; });
    return {
      total_scanned: rows.length,
      long_count: longs.length, short_count: shorts.length,
      neutral_count: rows.length - longs.length - shorts.length,
      near_trigger_count: near.length, fresh_signal_count: fresh.length,
      top_long: longs.slice(0, topN), top_short: shorts.slice(0, topN),
      top_near: near.slice(0, topN), top_strength: strongest.slice(0, topN),
      all_rows: rows
    };
  }

  return {
    // 指標
    ema: ema, sma: sma, rsi: rsi, macd: macd, atr: atr, supertrend: supertrend,
    trueRange: trueRange, ewmAlpha: ewmAlpha, addIndicators: addIndicators,
    rollingStd: rollingStd,
    // 訊號
    generateSignals: generateSignals, generateSignalsFor: generateSignalsFor,
    generateSignalsBB: generateSignalsBB, generateSignalsKeltner: generateSignalsKeltner,
    dynamicLeverage: dynamicLeverage, computeLeverageRow: computeLeverageRow,
    specsForGroups: specsForGroups, LEVERAGE_GROUPS: LEVERAGE_GROUPS,
    metricFor: metricFor,
    analyzeFrame: analyzeFrame,
    analyzeBars: analyzeBars, computeStrength: computeStrength,
    computeStrengthFor: computeStrengthFor,
    computeStrengthBB: computeStrengthBB, computeStrengthKeltner: computeStrengthKeltner,
    strengthMethodLabel: strengthMethodLabel,
    // 掃描
    rowOf: rowOf, buildRankings: buildRankings,
    topSymbolsByVolume: topSymbolsByVolume, isStablecoin: isStablecoin,
    // 策略預設
    STRATEGY_PRESETS: STRATEGY_PRESETS, DEFAULT_PRESET_ID: DEFAULT_PRESET_ID,
    getPreset: getPreset, paramsForPreset: paramsForPreset,
    specsForPreset: specsForPreset, KIND_GROUPS: KIND_GROUPS,
    // 可自訂參數
    PARAM_SPECS: PARAM_SPECS, validateParams: validateParams,
    // 常數 / 工具
    defaultParams: defaultParams, strengthLabel: strengthLabel, humanDelay: humanDelay,
    WEIGHTS: WEIGHTS, STABLE_BASES: STABLE_BASES, MIN_SCAN_ATR_PCT: MIN_SCAN_ATR_PCT,
    VOL_WILD_CAP_PCT: VOL_WILD_CAP_PCT,
    WEIGHTS_BB: WEIGHTS_BB, WEIGHTS_KC: WEIGHTS_KC,
    VERSION: '1.5.0'
  };
});
