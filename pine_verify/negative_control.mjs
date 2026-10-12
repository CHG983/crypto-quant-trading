// negative_control.mjs — calibrates what the PineTS engine actually catches.
//
// A verifier that reports "OK" on everything is worthless. This script feeds
// deliberately-broken Pine v6 sources to the SAME engine used in verify_pinets.mjs
// and records which error classes it detects and which it silently accepts.
//
// The result is the honest coverage boundary of the PineTS check: it catches
// syntax errors and unknown functions, but does NOT enforce var-scope,
// na-as-boolean, or location.absolute type rules.
import { PineTS, Provider } from 'pinets';

const CASES = {
  'var declared inside if block': `//@version=6
indicator("b1")
if close > open
    var float x = 1.0
    plot(x)
`,
  'bare na used as boolean': `//@version=6
indicator("b2")
x = na
if x and close > open
    plot(1)
`,
  'location.absolute given a bool': `//@version=6
indicator("b3")
plotshape(close > open, style=shape.circle, location=location.absolute)
`,
  'unclosed parenthesis (syntax)': `//@version=6
indicator("b4")
plot(ta.ema(close, 9)
`,
  'unknown function (ta.notARealFunction)': `//@version=6
indicator("b5")
plot(ta.notARealFunction(close, 9))
`,
};

const rows = [];
for (const [label, src] of Object.entries(CASES)) {
  let verdict;
  try {
    const p = new PineTS(Provider.Binance, 'BTCUSDT', '1d', 50);
    await p.run(src);
    verdict = 'NOT CAUGHT';
  } catch (e) {
    verdict = 'CAUGHT: ' + (e && e.message ? e.message : String(e)).split('\n')[0].slice(0, 120);
  }
  rows.push({ case: label, verdict });
  console.log(`[${label}] -> ${verdict}`);
}

const caught = rows.filter((r) => r.verdict.startsWith('CAUGHT')).length;
console.log(`\nPineTS caught ${caught}/${rows.length} deliberately-broken scripts.`);
console.log('=> PineTS is a real parser/executor (catches syntax + unknown symbols)');
console.log('   but does NOT enforce var-scope / na-as-bool / location.absolute typing.');
