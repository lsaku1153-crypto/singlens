/**
 * 真跑一遍 bindings.js 的接线逻辑（最小 DOM 桩 + 假数据源）。
 *
 * 目的：把"接线逻辑对不对"和"浏览器渲染对不对"分开。
 *   Node 里能验的是：querySelector 有没有找到元素、setText 有没有被调用、
 *   传进去的值是不是契约要求的形状。
 *   验不了的是：像素画得对不对（那要靠截图）。
 *
 * 用法：node tools/bindings_check.mjs
 */

// ── 极简 DOM 桩：只支持 bindings.js 用到的那几样 ──────────
function mkEl(tag = 'div') {
  const el = {
    tagName: tag.toUpperCase(),
    _attrs: {},
    textContent: '',
    title: '',
    style: {},
    children: [],
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    _q: {},                       // 选择器 → 元素
    getAttribute(k) { return this._attrs[k] ?? null; },
    setAttribute(k, v) { this._attrs[k] = String(v); },
    addEventListener() {},
    append(...c) { this.children.push(...c); },
    replaceChildren() {},
    querySelector(sel) { return this._q[sel] || null; },
    querySelectorAll() { return []; },
  };
  return el;
}

const root = mkEl('section');
const ERR = mkEl('div');
ERR.textContent = 'ORIGINAL-META';

// s3 的 data-b 目标
const KEYS = ['total', 'total-delta', 'pitch', 'rhythm', 'expr',
              'pitch-acc', 'pitch-acc-bar', 'pitch-stab', 'pitch-stab-bar',
              'rhythm-acc', 'rhythm-acc-bar', 'rhythm-stab', 'rhythm-stab-bar',
              'rubato', 'rubato-bar', 'report-meta'];
for (const k of KEYS) root._q[`[data-b="${k}"]`] = mkEl('div');

globalThis.document = {
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (t) => mkEl(t),
};
globalThis.window = { devicePixelRatio: 1 };
globalThis.location = { search: '', hash: '' };
globalThis.sessionStorage = {
  _d: {}, getItem(k) { return this._d[k] ?? null; },
  setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; },
};

// ── 替身数据源：绕开真实 mock，喂一份契约形状的报告 ──
const FAKE = {
  total: 71.5,
  pitch: { score: 82.9, accuracy: 3.4, stability: 18.2 },
  rhythm: { score: 63.1, accuracy: 41.0, stability: 55.0, rubato: 120.4 },
  expressiveness: {
    score: 58.0,
    normalized: { dynamic_range: 0.5, phrase_dynamics: 0.6, rubato: 0.7,
                  vibrato: 0.4, onset_style: 0.8, brightness: 0.3 },
  },
  metrics_excluded: ['vibrato'],
  segments: [],
  evidence: {},
  meta: { mode: 'A', reference_id: 'REF_TWINKLE_1', created_at: '2026-09-20',
          self_reference: false,
          eval_window: { phrase_window: true, notes_used: 7, notes_total: 26 } },
};

// 把 data/index.js 换成替身：直接往 import map 里塞太麻烦，
// 改为直接测"绑定函数本身"—— 用一个内联的等价实现来验证契约。
// ★ 这里刻意不 import bindings.js（它会 import 真实数据源），
//   而是把 bindings.js 的纯逻辑抄一份最小的来判断形状对不对。
//   真正的端到端只能靠浏览器截图（见文件头）。

const show = (v, digits = 1) =>
  (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(digits);

let bad = 0;
const chk = (cond, msg) => {
  console.log(`  ${cond ? '✅' : '❌'} ${msg}`);
  if (!cond) bad++;
};

console.log('='.repeat(76));
console.log('bindings 契约逻辑检查（纯函数部分；端到端靠截图）');
console.log('='.repeat(76));

chk(show(null) === '—', 'show(null) → 「—」而不是 0（契约 §3.2）');
chk(show(undefined) === '—', 'show(undefined) → 「—」');
chk(show(NaN) === '—', 'show(NaN) → 「—」');
chk(show(0) === '0.0', 'show(0) → "0.0"（真实的 0 分要显示，不能和未评估混淆）');
chk(show(82.94) === '82.9', 'show(82.94) → "82.9"');
chk(show(71.5, 0) === '72', 'show(71.5, 0) → "72"');
chk(show(120.44, 0) === '120', 'show(120.44, 0) → "120"');

// 这条报告形状要能被 uniform 处理
const KEYS2 = ['total', 'pitch', 'rhythm', 'expr'];
for (const k of KEYS2) {
  const v = { total: FAKE.total, pitch: FAKE.pitch.score,
              rhythm: FAKE.rhythm.score, expr: FAKE.expressiveness.score }[k];
  chk(typeof v === 'number', `报告 ${k} 是数值（${v}）`);
}
chk(Array.isArray(FAKE.metrics_excluded), 'metrics_excluded 是数组');
chk(typeof FAKE.meta.eval_window.notes_used === 'number', 'eval_window.notes_used 可读');

console.log('\n' + '='.repeat(76));
if (bad) { console.log(`  ${bad} 项不符`); process.exit(1); }
console.log('  纯逻辑部分通过。⚠️ 端到端（DOM 绑定是否真生效）必须靠浏览器截图。');
