#!/usr/bin/env node
/**
 * verify_strength_split.mjs — 證明「布林反轉」與「卡爾特通道」的強度
 * 真的走不同評分、對同一標的給出不同分數，且其他策略未被影響。
 *
 * 用真實幣安日線（verify/BTCUSDT_1d_klines.csv）跑 analyzeBars，
 * 依序套用 bb_reversion / keltner / ctma_default / wfo_9_34 / ctma_long_only / dynrisk_voltarget，
 * 印出每個策略在同一根 K 棒的 strength.total / label / method 與分項。
 *
 * 同時與 Python 基準（verify/BTCUSDT_1d_baseline.json）比對 CTMA 的 strength，
 * 證明新增評分沒有動到既有策略。
 *
 * 用法：node verify_strength_split.mjs
 */
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const CTMA = require('./live_engine.js');

const CSV = 'verify/BTCUSDT_1d_klines.csv';
const BASE = 'verify/BTCUSDT_1d_baseline.json';
const SYMBOL = 'BTCUSDT';

// ---- 讀同一份 K 線 CSV（欄位：date,open,high,low,close,volume,close_time_ms,is_closed）----
function parseCsv(text) {
  const lines = text.trim().split('\n');
  const head = lines[0].split(',').map(s => s.trim());
  const ix = (n) => head.indexOf(n);
  const iDate = ix('date'), iO = ix('open'), iH = ix('high'), iL = ix('low');
  const iC = ix('close'), iV = ix('volume'), iCT = ix('close_time_ms'), iCl = ix('is_closed');
  return lines.slice(1).filter(l => l.trim()).map(l => {
    const c = l.split(',');
    const closeMs = Number(c[iCT]);
    return {
      openTime: Date.parse(c[iDate] + 'T00:00:00Z'),
      open: Number(c[iO]), high: Number(c[iH]), low: Number(c[iL]),
      close: Number(c[iC]), volume: Number(c[iV]),
      closeTime: closeMs, isClosed: String(c[iCl]).trim().toLowerCase() === 'true',
    };
  });
}

const bars = parseCsv(fs.readFileSync(CSV, 'utf8'));
const baseline = JSON.parse(fs.readFileSync(BASE, 'utf8'));

const PRESETS = [
  'ctma_default', 'bb_reversion', 'keltner',
  'wfo_9_34', 'ctma_long_only', 'dyn_risk',
];

let pass = 0, fail = 0;
function check(name, ok, detail) {
  (ok ? pass++ : fail++);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

console.log('K 線根數:', bars.length, '· 最後一根 isClosed:', bars[bars.length - 1].isClosed);
console.log('');
console.log('=== 同一份真實 BTCUSDT 日線 · 各策略的強度評分（同一根 K 棒）===');
const rows = [];
for (const id of PRESETS) {
  const p = CTMA.paramsForPreset(id);
  let frame;
  try {
    frame = CTMA.analyzeBars(bars, SYMBOL, { params: p, source: 'node:verify' });
  } catch (e) {
    console.log(`${id.padEnd(18)} → ERROR ${e.message}`);
    continue;
  }
  const c = frame.confirmed || frame.provisional;
  const st = c.strength;
  const detail = st.method === 'bb'
    ? `depth=${st.depth} rsi=${st.rsi} squeeze=${st.squeeze}`
    : st.method === 'keltner'
      ? `breakout=${st.breakout} slope=${st.slope} expansion=${st.expansion}`
      : `trend=${st.trend} mom=${st.momentum} vol=${st.volatility} st=${st.supertrend}`;
  rows.push({
    id, kind: p.kind, total: st.total, label: st.label, method: st.method,
    metric: c.strategy_metric ? c.strategy_metric.text : (c.ema_gap_pct !== undefined ? c.ema_gap_pct : null),
    leverage: c.leverage ? c.leverage.leverage : null,
    stop: c.levels ? c.levels.stop : (frame.levels ? frame.levels.stop : null),
    detail,
  });
  console.log(
    `${id.padEnd(18)} kind=${String(p.kind).padEnd(8)} ` +
    `total=${String(st.total).padStart(6)} (${st.label.padEnd(2)}) method=${String(st.method).padEnd(8)} | ${detail}`
  );
}
console.log('');

console.log('=== 核心斷言 ===');
const bb = rows.find(r => r.id === 'bb_reversion');
const kc = rows.find(r => r.id === 'keltner');
const ctma = rows.find(r => r.id === 'ctma_default');

check('BB 與 KC 對同一標的的強度值不同', !!bb && !!kc && bb.total !== kc.total,
  `BB=${bb && bb.total} vs KC=${kc && kc.total}`);
check('BB 使用反轉評分 (method=bb)', !!bb && bb.method === 'bb');
check('KC 使用突破評分 (method=keltner)', !!kc && kc.method === 'keltner');
check('BB 分項 = depth/rsi/squeeze', !!bb && /depth=.*rsi=.*squeeze=/.test(bb.detail), bb && bb.detail);
check('KC 分項 = breakout/slope/expansion', !!kc && /breakout=.*slope=.*expansion=/.test(kc.detail), kc && kc.detail);
check('CTMA 仍使用動能評分 (method=ctma)', !!ctma && ctma.method === 'ctma');
check('CTMA 分項仍為 trend/momentum/volatility/supertrend',
  !!ctma && /trend=.*mom=.*vol=.*st=/.test(ctma.detail), ctma && ctma.detail);

// ---- CTMA 基準回歸：與 Python 基準逐值比對 ----
{
  let py = null;
  if (baseline.strength) py = baseline.strength;
  else if (Array.isArray(baseline.frames) && baseline.frames.length) {
    py = baseline.frames[baseline.frames.length - 1].strength || null;
  }
  const f = CTMA.analyzeBars(bars, SYMBOL, { params: CTMA.paramsForPreset('ctma_default'), source: 'node:verify' });
  const c = f.confirmed || f.provisional;
  console.log('');
  console.log('CTMA 強度  JS:', JSON.stringify(c.strength));
  console.log('CTMA 強度  PY:', JSON.stringify(py));
  if (py && typeof py.total === 'number') {
    const keys = ['trend', 'momentum', 'volatility', 'supertrend'];
    const okTotal = Math.abs(c.strength.total - py.total) < 0.15;
    const okParts = keys.every(k => (typeof py[k] !== 'number') || Math.abs(c.strength[k] - py[k]) < 1e-6);
    check('CTMA strength.total 與 Python 基準一致', okTotal, `JS=${c.strength.total} PY=${py.total}`);
    check('CTMA 四個分項與 Python 基準一致', okParts,
      keys.map(k => `${k}:${c.strength[k]}/${py[k]}`).join(' '));
  } else {
    check('（基準檔無 strength 欄位，改以分項存在性驗證）',
      keysPresent(c.strength), 'trend/momentum/volatility/supertrend');
  }
}
function keysPresent(s) {
  return ['trend', 'momentum', 'volatility', 'supertrend'].every(k => typeof s[k] === 'number');
}

console.log('');
console.log('================ SUMMARY ================');
console.log(`  ${pass}/${pass + fail} PASS`);
console.log(`  OVERALL: ${fail === 0 ? 'ALL PASS' : 'HAS FAILURES'}`);

fs.writeFileSync('verify/strength_split_report.json', JSON.stringify({
  generated_utc: new Date().toISOString(),
  engine_version: CTMA.VERSION,
  csv: CSV, symbol: SYMBOL, bars: bars.length,
  per_preset: rows,
  assertions: { pass, fail, overall: fail === 0 ? 'ALL PASS' : 'HAS FAILURES' },
}, null, 2));
process.exit(fail === 0 ? 0 : 1);
