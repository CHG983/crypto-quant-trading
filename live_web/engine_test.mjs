#!/usr/bin/env node
/**
 * engine_test.mjs — live_engine.js 與 Python 實作的逐值等價性驗證
 * ================================================================
 * 讀 make_baseline.py 產出的基準（同一份幣安 K 線 CSV + Python 算出的
 * 全部指標與訊號），用 live_engine.js 重算，然後逐值比對。
 *
 * 為什麼這樣測才有意義
 * --------------------
 * 儀表板中的引擎是「內嵌在 HTML 裡的同一份 live_engine.js」。只要這裡
 * 證明 JS 與 Python 逐值一致（且 HTML 內嵌的是同一份原文、雜湊相符），
 * 就能合理推論瀏覽器端算出的訊號與 Python 端一致。
 *
 * 用法：
 *   node engine_test.mjs verify/BTCUSDT_1d_baseline.json verify/BTCUSDT_1d_klines.csv
 *   node engine_test.mjs --glob verify          # 跑完 verify/ 下所有配對
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const CTMA = require('./live_engine.js');

// 容差：pandas 與 JS 的浮點累加順序不同，允許極小誤差
const TOL = 1e-9;
const TOL_PCT = 1e-6;   // 百分比類指標（atr_pct / ema_gap_pct）

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  const head = lines[0].split(',');
  const idx = {};
  head.forEach((h, i) => { idx[h.trim()] = i; });
  const bars = [];
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(',');
    if (c.length < head.length) continue;
    bars.push({
      openTime: Date.parse(c[idx.date].replace(' ', 'T') + 'Z'),
      date: c[idx.date],
      open: parseFloat(c[idx.open]),
      high: parseFloat(c[idx.high]),
      low: parseFloat(c[idx.low]),
      close: parseFloat(c[idx.close]),
      volume: parseFloat(c[idx.volume]),
      closeTime: parseInt(c[idx.close_time_ms], 10),
      isClosed: c[idx.is_closed].trim() === 'True' || c[idx.is_closed].trim() === 'true'
    });
  }
  return bars;
}

/** 逐值比對兩條序列，回傳 {n, maxAbs, maxIdx, mismatches}。 */
function compareSeries(a, b, tol, name) {
  if (a.length !== b.length) {
    return { name, error: `長度不符 JS=${a.length} PY=${b.length}` };
  }
  let maxAbs = 0, maxIdx = -1, n = 0, bothNull = 0;
  const mismatches = [];
  for (let i = 0; i < a.length; i++) {
    const x = a[i], y = b[i];
    const xn = x === null || x === undefined || !Number.isFinite(x);
    const yn = y === null || y === undefined || !Number.isFinite(y);
    if (xn && yn) { bothNull++; continue; }
    if (xn !== yn) {
      mismatches.push({ i, js: x, py: y, kind: 'null-mismatch' });
      continue;
    }
    n++;
    const d = Math.abs(x - y);
    if (d > maxAbs) { maxAbs = d; maxIdx = i; }
    if (d > tol) mismatches.push({ i, js: x, py: y, diff: d });
  }
  return { name, n, bothNull, maxAbs, maxIdx, mismatches, passed: mismatches.length === 0 };
}

function compareFlags(a, b, name) {
  if (a.length !== b.length) return { name, error: `長度不符 JS=${a.length} PY=${b.length}` };
  const mismatches = [];
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) mismatches.push({ i, js: a[i], py: b[i] });
  return { name, n: a.length, mismatches, passed: mismatches.length === 0 };
}

function runPair(basePath, csvPath) {
  const base = JSON.parse(fs.readFileSync(basePath, 'utf8'));
  const bars = parseCsv(fs.readFileSync(csvPath, 'utf8'));
  const p = CTMA.defaultParams();

  const label = `${base.symbol} ${base.interval}`;
  const results = [];

  if (bars.length !== base.rows) {
    results.push({ name: 'row-count', error: `JS=${bars.length} PY=${base.rows}`, passed: false });
  }

  // ---- 1) 指標序列逐值比對 ----
  const sig = CTMA.generateSignals(bars, p);
  const ind = sig.ind;
  const S = base.series;
  const seriesPairs = [
    ['ema_fast', ind.ema_fast, S.ema_fast, TOL],
    ['ema_slow', ind.ema_slow, S.ema_slow, TOL],
    ['rsi', ind.rsi, S.rsi, TOL],
    ['macd', ind.macd, S.macd, TOL],
    ['macd_signal', ind.macd_signal, S.macd_signal, TOL],
    ['macd_hist', ind.macd_hist, S.macd_hist, TOL],
    ['atr', ind.atr, S.atr, TOL],
    ['atr_pct', ind.atr_pct, S.atr_pct, TOL_PCT],
    ['supertrend', ind.supertrend, S.supertrend, TOL],
    ['st_dir', ind.st_dir, S.st_dir, 0]
  ];
  for (const [name, jsArr, pyArr, tol] of seriesPairs) {
    // Python 端 round(x, 10)；JS 端為原值 → 比較時容差要含四捨五入誤差
    const effectiveTol = Math.max(tol, name === 'atr_pct' ? TOL_PCT : TOL);
    results.push(compareSeries(jsArr, pyArr, effectiveTol, name));
  }

  // ---- 2) 訊號旗標逐值比對 ----
  const F = base.flags;
  results.push(compareFlags(sig.bull, F.bull_cross, 'bull_cross'));
  results.push(compareFlags(sig.bear, F.bear_cross, 'bear_cross'));
  results.push(compareFlags(sig.long_entry_ok, F.long_entry_ok, 'long_entry_ok'));
  results.push(compareFlags(sig.short_entry_ok, F.short_entry_ok, 'short_entry_ok'));
  results.push(compareFlags(sig.signal, F.signal, 'signal'));

  // ---- 3) analyzeFrame 的高階輸出比對 ----
  const frame = CTMA.analyzeFrame(bars, p);
  const pyFull = base.analyze_symbol || {};
  const pyProv = pyFull.provisional || {};
  const scalarPairs = [
    ['trend', frame.trend, pyProv.trend],
    ['trend_label', frame.trend_label, pyProv.trend_label],
    ['signal', frame.signal, pyProv.signal],
    ['signal_label', frame.signal_label, pyProv.signal_label],
    ['price', frame.price, pyProv.price],
    ['rsi', frame.rsi, pyProv.rsi],
    ['atr_pct', frame.atr_pct, pyProv.atr_pct],
    ['ema_gap_pct', frame.ema_gap_pct, pyProv.ema_gap_pct],
    ['ema_spread_atr', frame.ema_spread_atr, pyProv.ema_spread_atr],
    ['st_dir', frame.st_dir, pyProv.st_dir],
    ['strength.total', frame.strength.total, (pyProv.strength || {}).total],
    ['strength.label', frame.strength.label, (pyProv.strength || {}).label],
    ['strength.trend', frame.strength.trend, (pyProv.strength || {}).trend],
    ['strength.momentum', frame.strength.momentum, (pyProv.strength || {}).momentum],
    ['strength.volatility', frame.strength.volatility, (pyProv.strength || {}).volatility],
    ['strength.supertrend', frame.strength.supertrend, (pyProv.strength || {}).supertrend]
  ];
  const scalarMismatches = [];
  for (const [name, jsV, pyV] of scalarPairs) {
    const bothNull = (jsV === null || jsV === undefined) && (pyV === null || pyV === undefined);
    if (bothNull) continue;
    const eq = (typeof jsV === 'number' && typeof pyV === 'number')
      ? Math.abs(jsV - pyV) <= 1e-6
      : String(jsV) === String(pyV);
    if (!eq) scalarMismatches.push({ name, js: jsV, py: pyV });
  }
  results.push({ name: 'analyzeFrame scalars', n: scalarPairs.length, mismatches: scalarMismatches, passed: scalarMismatches.length === 0 });

  // ---- 4) 進場過濾的「回看當根」判斷 ----
  const entryMismatches = [];
  const pyEntry = pyProv.entry_passes || {};
  for (const side of ['long', 'short']) {
    if (frame.entry_passes[side] !== pyEntry[side]) {
      entryMismatches.push({ name: `entry_passes.${side}`, js: frame.entry_passes[side], py: pyEntry[side] });
    }
  }
  results.push({ name: 'entry_passes', n: 2, mismatches: entryMismatches, passed: entryMismatches.length === 0 });

  return { label, results };
}

function printReport(out) {
  const { label, results } = out;
  const failed = results.filter(r => !r.passed);
  console.log(`\n=== ${label} ===`);
  for (const r of results) {
    if (r.error) { console.log(`  FAIL ${r.name.padEnd(24)} ${r.error}`); continue; }
    const status = r.passed ? 'PASS' : 'FAIL';
    let extra = `n=${r.n}`;
    if (r.maxAbs !== undefined) extra += `  max|Δ|=${r.maxAbs.toExponential(3)}`;
    if (r.bothNull) extra += `  both-null=${r.bothNull}`;
    if (!r.passed) extra += `  mismatches=${r.mismatches.length}`;
    console.log(`  ${status} ${r.name.padEnd(24)} ${extra}`);
    if (!r.passed) {
      r.mismatches.slice(0, 4).forEach(mm => console.log(`       ${JSON.stringify(mm)}`));
      if (r.mismatches.length > 4) console.log(`       ... 共 ${r.mismatches.length} 處`);
    }
  }
  console.log(`  → ${results.length - failed.length}/${results.length} PASS`);
  return failed.length === 0;
}

// ---------- main ----------
const args = process.argv.slice(2);
let pairs = [];

if (args[0] === '--glob') {
  const dir = args[1] || 'verify';
  const files = fs.readdirSync(dir);
  const bases = files.filter(f => f.endsWith('_baseline.json'));
  for (const b of bases) {
    const sym = b.replace('_baseline.json', '');
    const csv = path.join(dir, `${sym}_klines.csv`);
    if (fs.existsSync(csv)) pairs.push([path.join(dir, b), csv]);
  }
} else if (args.length >= 2) {
  pairs = [[args[0], args[1]]];
} else {
  console.error('用法: node engine_test.mjs <baseline.json> <klines.csv>  |  node engine_test.mjs --glob verify');
  process.exit(2);
}

if (pairs.length === 0) { console.error('找不到任何 baseline/CSV 配對'); process.exit(2); }

let allPass = true;
const summary = [];
const detail = [];
for (const [b, c] of pairs) {
  const out = runPair(b, c);
  const ok = printReport(out);
  allPass = allPass && ok;
  summary.push({ label: out.label, checks: out.results.length, failed: out.results.filter(r => !r.passed).length });
  detail.push({
    label: out.label,
    checks: out.results.map(r => ({
      name: r.name,
      passed: r.passed,
      n: r.n === undefined ? null : r.n,
      max_abs_delta: r.maxAbs === undefined ? null : r.maxAbs,
      mismatches: r.passed ? 0 : (r.mismatches || []).length,
      error: r.error || null
    }))
  });
}

console.log('\n================ SUMMARY ================');
for (const s of summary) console.log(`  ${s.label}: ${s.checks - s.failed}/${s.checks} PASS`);
console.log(`  OVERALL: ${allPass ? 'ALL PASS' : 'HAS FAILURES'}`);

// --json 時額外輸出結果檔，供報告引用（並抑制上面的人類可讀輸出）
const jsonIdx = args.indexOf('--json');
if (jsonIdx >= 0) {
  const outPath = args[jsonIdx + 1] || 'engine_parity.json';
  // 取所有序列比對中的最大殘差（僅數值型）
  let globalMax = 0, globalAt = null;
  for (const d of detail) {
    for (const c of d.checks) {
      if (c.max_abs_delta !== null && c.max_abs_delta > globalMax) {
        globalMax = c.max_abs_delta; globalAt = `${d.label} / ${c.name}`;
      }
    }
  }
  const totalChecks = detail.reduce((a, d) => a + d.checks.length, 0);
  const totalFailed = detail.reduce((a, d) => a + d.checks.filter(c => !c.passed).length, 0);
  const payload = {
    generated_at_utc: new Date().toISOString(),
    tool: 'engine_test.mjs',
    engine_version: CTMA.VERSION,
    tolerance: { numeric: TOL, percent: TOL_PCT },
    total_checks: totalChecks,
    failed_checks: totalFailed,
    status: allPass ? 'ALL PASS' : 'HAS FAILURES',
    max_abs_delta: globalMax,
    max_abs_delta_at: globalAt,
    pairs: detail
  };
  fs.writeFileSync(outPath, JSON.stringify(payload, null, 2));
  console.log(`  [json] ${outPath}`);
}

process.exit(allPass ? 0 : 1);
