#!/usr/bin/env node
/**
 * param_validate_test.mjs — 自訂參數驗證的單元測試
 * =================================================
 * 驗證 live_engine.js 新增的 PARAM_SPECS / validateParams / paramsForPreset(overrides)。
 * 這些是「參數自訂」功能的守門員：不合法必須被擋下，合法必須正確合併。
 *
 * 用法：node param_validate_test.mjs
 */
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const CTMA = require('./live_engine.js');

let pass = 0, fail = 0;
const failures = [];

function check(name, cond, detail) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; failures.push(name + (detail ? ' — ' + detail : '')); console.log('  FAIL ' + name + (detail ? ' — ' + detail : '')); }
}

console.log('=== 1. PARAM_SPECS 結構 ===');
check('specs 非空', Array.isArray(CTMA.PARAM_SPECS) && CTMA.PARAM_SPECS.length > 0);
const keys = CTMA.PARAM_SPECS.map(s => s.key);
['ema_fast','ema_slow','atr_stop_mult','atr_trail_mult','atr_tp_mult','rsi_len','atr_len',
 'min_atr_pct','st_len','st_mult','allow_short','use_supertrend'].forEach(k => {
  check('spec 含 ' + k, keys.includes(k));
});
check('每個 spec 有 label', CTMA.PARAM_SPECS.every(s => typeof s.label === 'string' && s.label.length > 0));
check('數值 spec 有 min/max/step', CTMA.PARAM_SPECS.filter(s => s.type !== 'bool')
  .every(s => Number.isFinite(s.min) && Number.isFinite(s.max) && Number.isFinite(s.step)));

console.log('\n=== 2. 預設值與 StrategyParams 一致 ===');
const d = CTMA.defaultParams();
check('ema_fast 預設 21', d.ema_fast === 21, 'got ' + d.ema_fast);
check('ema_slow 預設 55', d.ema_slow === 55, 'got ' + d.ema_slow);
check('atr_stop_mult 預設 3.0', d.atr_stop_mult === 3.0, 'got ' + d.atr_stop_mult);
check('atr_trail_mult 預設 5.0', d.atr_trail_mult === 5.0, 'got ' + d.atr_trail_mult);
check('atr_tp_mult 預設 6.0', d.atr_tp_mult === 6.0, 'got ' + d.atr_tp_mult);
check('rsi_len 預設 14', d.rsi_len === 14, 'got ' + d.rsi_len);
check('atr_len 預設 14', d.atr_len === 14, 'got ' + d.atr_len);
check('min_atr_pct 預設 0.5', d.min_atr_pct === 0.5, 'got ' + d.min_atr_pct);
check('st_len 預設 10', d.st_len === 10, 'got ' + d.st_len);
check('st_mult 預設 3.0', d.st_mult === 3.0, 'got ' + d.st_mult);
check('allow_short 預設 true', d.allow_short === true);
check('use_supertrend 預設 false', d.use_supertrend === false);

console.log('\n=== 3. 合法參數應通過 ===');
let r = CTMA.validateParams({ ema_fast: 9, ema_slow: 34, atr_stop_mult: 1.5 }, CTMA.defaultParams());
check('9/34 + ATR 1.5 合法', r.ok, JSON.stringify(r.errors));
check('合併後 ema_fast=9', r.params.ema_fast === 9);
check('合併後 ema_slow=34', r.params.ema_slow === 34);
check('合併後 atr_stop_mult=1.5', r.params.atr_stop_mult === 1.5);
check('未指定者保留預設 (rsi_len=14)', r.params.rsi_len === 14);

console.log('\n=== 4. 不合法：EMA 快線 >= 慢線 ===');
r = CTMA.validateParams({ ema_fast: 60, ema_slow: 55 }, CTMA.defaultParams());
check('快線 > 慢線 被擋下', !r.ok);
check('錯誤指向 ema_fast', r.errors.some(e => e.key === 'ema_fast'));
check('錯誤訊息含「必須小於」', r.errors.some(e => /必須小於/.test(e.msg)), JSON.stringify(r.errors));
r = CTMA.validateParams({ ema_fast: 55, ema_slow: 55 }, CTMA.defaultParams());
check('快線 == 慢線 被擋下', !r.ok);

console.log('\n=== 5. 不合法：非正整數 / 非數字 / 超範圍 ===');
r = CTMA.validateParams({ ema_fast: 21.5 }, CTMA.defaultParams());
check('小數週期被擋下', !r.ok && r.errors.some(e => e.key === 'ema_fast'));
r = CTMA.validateParams({ ema_fast: NaN }, CTMA.defaultParams());
check('NaN 被擋下', !r.ok && r.errors.some(e => e.key === 'ema_fast'));
r = CTMA.validateParams({ ema_fast: 0 }, CTMA.defaultParams());
check('週期 0 被擋下', !r.ok);
r = CTMA.validateParams({ ema_fast: -5 }, CTMA.defaultParams());
check('負週期被擋下', !r.ok);
r = CTMA.validateParams({ atr_stop_mult: -1 }, CTMA.defaultParams());
check('負 ATR 倍數被擋下', !r.ok && r.errors.some(e => e.key === 'atr_stop_mult'));
r = CTMA.validateParams({ atr_stop_mult: 0 }, CTMA.defaultParams());
check('ATR 倍數 0 被擋下', !r.ok);
r = CTMA.validateParams({ atr_stop_mult: 999 }, CTMA.defaultParams());
check('ATR 倍數超上限被擋下', !r.ok);
r = CTMA.validateParams({ ema_slow: 99999 }, CTMA.defaultParams());
check('EMA 慢線超上限被擋下', !r.ok);

console.log('\n=== 6. 布林參數 ===');
r = CTMA.validateParams({ allow_short: false }, CTMA.defaultParams());
check('allow_short=false 合法', r.ok && r.params.allow_short === false);
r = CTMA.validateParams({ use_supertrend: true, st_len: 7, st_mult: 2.0 }, CTMA.defaultParams());
check('supertrend 開啟合法', r.ok && r.params.use_supertrend === true && r.params.st_len === 7);
r = CTMA.validateParams({ allow_short: 'yes' }, CTMA.defaultParams());
check('非布林 allow_short 被擋下', !r.ok);

console.log('\n=== 7. paramsForPreset 疊加覆寫 ===');
const base = CTMA.paramsForPreset('ctma_default');
check('無覆寫時 ema_fast=21', base.ema_fast === 21);
const over = CTMA.paramsForPreset('ctma_default', { ema_fast: 9, ema_slow: 34 });
check('覆寫後 ema_fast=9', over.ema_fast === 9);
check('覆寫後 ema_slow=34', over.ema_slow === 34);
check('覆寫不影響原物件', base.ema_fast === 21);
const wfo = CTMA.paramsForPreset('wfo_9_34');
check('wfo_9_34 預設 ema_fast=9', wfo.ema_fast === 9);
check('wfo_9_34 預設 atr_stop_mult=1.5', wfo.atr_stop_mult === 1.5);
const wfoOver = CTMA.paramsForPreset('wfo_9_34', { atr_stop_mult: 2.5 });
check('wfo 疊加覆寫 atr_stop_mult=2.5', wfoOver.atr_stop_mult === 2.5);
check('wfo 疊加後 ema_fast 仍為 9', wfoOver.ema_fast === 9);

console.log('\n=== 8. 版本 ===');
check('VERSION 為 1.2.0', CTMA.VERSION === '1.2.0', 'got ' + CTMA.VERSION);

console.log('\n================ SUMMARY ================');
console.log('  ' + pass + '/' + (pass + fail) + ' PASS');
if (fail) { console.log('  FAILURES:'); failures.forEach(f => console.log('    - ' + f)); }
console.log('  OVERALL: ' + (fail === 0 ? 'ALL PASS' : 'FAILED'));
process.exit(fail === 0 ? 0 : 1);
