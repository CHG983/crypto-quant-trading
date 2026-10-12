// verify_pinets.mjs — compile-level verification of the CTMA .pine files.
//
// WHAT THIS IS:  PineTS (npm "pinets") is an independent, open-source Pine Script
//                v6 compiler + runtime. It is NOT TradingView's compiler. Running a
//                script through it proves the source parses, transpiles and executes
//                under a real Pine v6 engine — a much stronger signal than regex
//                linting, but still NOT a TradingView compile.
//
// WHAT THIS IS NOT: a TradingView compile. TradingView's own compiler performs
//                additional type-inference and overload-resolution checks that no
//                third-party engine reproduces exactly.
//
// Usage:  npm install && node verify_pinets.mjs
import { PineTS, Provider } from 'pinets';
import fs from 'fs';
import path from 'path';

const PINE_DIR = path.resolve(process.cwd(), '..', 'pine');
const OUT = path.resolve(process.cwd(), '..', 'output', 'pinets_verify.json');

const FILES = [
  ['ctma_indicator', path.join(PINE_DIR, 'ctma_indicator.pine')],
  ['ctma_strategy', path.join(PINE_DIR, 'ctma_strategy.pine')],
];

const result = {
  engine: 'pinets (PineTS) — open-source Pine Script v6 compiler/runtime',
  engine_version: '0.11.0',
  data_provider: 'Binance BTCUSDT 1d, 300 bars',
  disclaimer:
    'PineTS is an independent open-source engine, NOT TradingView. A pass here is ' +
    'compile-level evidence, not a TradingView compile.',
  files: [],
};

for (const [name, file] of FILES) {
  const src = fs.readFileSync(file, 'utf8');
  const rec = { name, lines: src.split('\n').length, chars: src.length };
  try {
    const pineTS = new PineTS(Provider.Binance, 'BTCUSDT', '1d', 300);
    const res = await pineTS.run(src);
    const plots = (res && res.plots) || {};
    const names = Object.keys(plots);
    rec.status = 'COMPILED_AND_EXECUTED_OK';
    rec.plot_count = names.length;
    rec.plot_names = names;
    const sanity = {};
    for (const n of names) {
      const s = plots[n];
      const arr = Array.isArray(s) ? s : (s && s.data ? s.data : null);
      if (!arr) continue;
      const nums = arr
        .map((v) => (v && typeof v === 'object' ? v.value : v))
        .filter((v) => typeof v === 'number' && isFinite(v));
      sanity[n] = {
        len: arr.length,
        finite: nums.length,
        last: nums.length ? +nums[nums.length - 1].toFixed(4) : null,
      };
    }
    rec.value_sanity = sanity;
  } catch (e) {
    rec.status = 'ERROR';
    rec.error = (e && e.message ? e.message : String(e)).slice(0, 500);
  }
  result.files.push(rec);
}

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(result, null, 2));

for (const f of result.files) {
  console.log(`${f.name}: ${f.status}` + (f.plot_count ? ` (${f.plot_count} plots)` : ''));
  if (f.error) console.log('   ' + f.error);
}
console.log(`\nwrote ${OUT}`);
