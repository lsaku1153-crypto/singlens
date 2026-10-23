/**
 * 真执行一遍 charts.js 的每个 draw*，**并断言"空数据不画假线"**。
 *
 * 为什么需要：
 *   ① `node --check` 只证明**能解析**，不证明**能跑**（按大括号配平抽出来的
 *      代码最怕截断/漏括号，那类问题语法可能仍合法，一执行就炸）。
 *   ② 更要紧的是**画的对不对**。原来这个脚本只断言"没抛异常"，
 *      于是下面这些 bug 它**一个都抓不到**：
 *        · 没有数据时画原型合成的正弦波（伪造用户的音量曲线）
 *        · 雷达图把 null（＝本次未评估）当 0 画成塌到圆心的顶点
 *        · `drawScatter()` 无参时画 24 个示意点，而旁边文字写着"还没有数据"
 *      这三条在截图上都"看着挺正常"—— 只有断言能抓住。
 *
 * 做法：造一个最小 DOM/Canvas 桩（**带调用计数**），跑每个 draw*，然后：
 *   · 空数据 → 画布必须 `hidden === true`，且**一次绘制调用都没有**
 *   · 有数据 → 画布必须可见，且有绘制调用
 *   · 雷达图 6 轴里 2 轴为 null → 顶点方块只能有 4 个（不能是 6 个）
 *
 * 用法：node tools/render_charts_check.mjs
 */

// ── 最小 canvas 2d 上下文桩（带调用计数）──────────────────
let calls = 0;
const ctxStub = new Proxy({}, {
  get(_, k) {
    if (k === 'canvas') return { width: 300, height: 150 };
    if (k === 'measureText') return () => ({ width: 10 });
    if (k === 'getImageData') return () => ({ data: new Uint8ClampedArray(4) });
    return () => { calls++; };
  },
  set() { calls++; return true; },
});

function mkCanvas(id, extra = {}) {
  const attrs = { id, ...extra };
  const el = {
    id,
    tagName: 'CANVAS',
    width: 300,
    height: 150,
    clientWidth: 300,
    clientHeight: 150,
    hidden: false,
    dataset: {},
    style: {},
    getContext: () => ctxStub,
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
  };
  return el;
}

const ELS = {
  radar: mkCanvas('radar'),
  cmpA: mkCanvas('cmpA'),
  cmpB: mkCanvas('cmpB'),
  prog: mkCanvas('prog'),
  scatter: mkCanvas('scatter'),
};
const EV = ['rms', 'vib', 'rubato'].map((k) => {
  const c = mkCanvas('ev_' + k);
  c.className = 'ev';
  c.dataset = { k };
  return c;
});

globalThis.document = {
  getElementById: (id) => ELS[id] || null,
  querySelector: () => null,
  querySelectorAll: (sel) => (sel.includes('.ev') ? EV : []),
  createElement: () => mkCanvas('tmp'),
};
globalThis.window = { devicePixelRatio: 1 };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};

const m = await import('../web/js/charts.js');

// ── 数据样本 ──────────────────────────────────────────────
const REAL_RADAR = { vals: [0.42, 0.55, 0.70, 0.31, 0.62, 0.48] };
const RADAR_2NA = { vals: [0.42, 0.55, null, 0.31, null, 0.48] };
const REAL_EV = {
  // ⚠️ dB 是**负数**（实测 −20 ~ −9）：第一版按 0~1 画，曲线整条贴底边
  rms_envelope: { t: [0, 100, 200], v: [-18.4, -12.1, -15.6], p5: -18.9, p95: -11.8, unit: 'dB' },
  vibrato_spectrum: { f: [3, 4, 5], v: [0.1, 0.9, 0.2], peak_hz: 4.7, unit: 'Hz' },
  onset_dev_trend: {
    t: [0, 100, 200, 300], dev: [5, -12, 20, -8],
    trend: [2, -4, 6, -2], residual: [3, -8, 14, -6], unit: 'ms',
  },
};
const REAL_PROG = {
  labels: ['W1', 'W2', 'W3'],
  series: { total: [61, 66, null], expr: [34, 44, 52], pitch: [79, 83, null] },
  reference_id: ['REF_TWINKLE_1', 'REF_TWINKLE_1', 'tpl_abc'],
};
const REAL_SCAT = { scatter: [[1.5, 1.8], [3.2, 3.4], [4.1, 4.0]] };

/** 跑一次 fn，返回它产生的绘制调用次数（画布状态要先复位）。 */
function run(cv, fn) {
  for (const c of Object.values(ELS)) c.hidden = false;
  for (const c of EV) c.hidden = false;
  calls = 0;
  fn();
  return { draws: calls, hidden: cv ? cv.hidden : null };
}

const CASES = [
  // ── 有数据：必须画、必须可见 ──
  ['有数据 · 雷达图', ELS.radar, () => m.drawRadar(REAL_RADAR), (r) => r.draws > 20 && !r.hidden],
  ['有数据 · 证据曲线', EV[0], () => m.drawEvidence(REAL_EV), (r) => r.draws > 20 && !r.hidden],
  ['有数据 · 复练对比', ELS.cmpA, () => m.drawCmp('cmpA', 'flat', REAL_EV), (r) => r.draws > 20 && !r.hidden],
  ['有数据 · 进步曲线', ELS.prog, () => m.drawProg(REAL_PROG), (r) => r.draws > 20 && !r.hidden],
  ['有数据 · 散点图', ELS.scatter, () => m.drawScatter(REAL_SCAT), (r) => r.draws > 20 && !r.hidden],

  /* ── 空数据：必须留白（这是本次新增的核心断言）──
     画布要隐藏，而且**一次绘制都不该发生** ——
     "画了但被藏起来"和"没画"在屏幕上一样，但前者说明代码里还留着假数据分支。 */
  ['空数据 · 雷达图不画假顶点', ELS.radar, () => m.drawRadar({ vals: [null, null, null, null, null, null] }),
    (r) => r.draws > 0, '空网格仍要画（不然是一片白）'],
  ['空数据 · 证据曲线藏画布', EV[0], () => m.drawEvidence({}), (r) => r.hidden === true && r.draws === 0],
  ['空数据 · 复练对比藏画布', ELS.cmpA, () => m.drawCmp('cmpA', 'flat', {}), (r) => r.hidden === true && r.draws === 0],
  ['空数据 · 复练对比(无参)藏画布', ELS.cmpB, () => m.drawCmp('cmpB', 'rich'), (r) => r.hidden === true && r.draws === 0],
  ['空数据 · 进步曲线藏画布', ELS.prog, () => m.drawProg({}), (r) => r.hidden === true && r.draws === 0],
  ['空数据 · 进步曲线(无参)藏画布', ELS.prog, () => m.drawProg(), (r) => r.hidden === true && r.draws === 0],
  ['空数据 · 散点图不留假点', ELS.scatter, () => m.drawScatter({ scatter: [] }),
    (r) => r.draws > 0, '空态要写字（"还没有配对数据"），所以会有绘制'],
  ['空数据 · 散点图(无参)不留假点', ELS.scatter, () => m.drawScatter(), (r) => r.draws > 0],
  ['fit 返回 {g,w,h}', null, () => { const r = m.fit(ELS.radar); if (!r || !r.g) throw new Error('fit 没返回 {g,w,h}'); return r; }, () => true],
  ['updateRangeBand 不炸', null, () => m.updateRangeBand(document), () => true],
];

let bad = 0;
console.log('='.repeat(78));
console.log('charts.js 真执行 + 空数据行为断言（最小 DOM 桩，不渲染像素）');
console.log('='.repeat(78));
for (const [name, cv, fn, ok, note] of CASES) {
  try {
    const r = run(cv, fn);
    if (ok(r)) {
      console.log(`  ✅ ${name}${note ? `（${note}）` : ''}`);
    } else {
      bad++;
      console.log(`  ❌ ${name} —— 绘制 ${r.draws} 次、hidden=${r.hidden}`);
    }
  } catch (e) {
    bad++;
    console.log(`  ❌ ${name}: ${e.message}`);
  }
}

/* ── 雷达图：null 轴**不许**出现顶点 ──
   判据用"顶点方块"的个数：`fillRect(x-3,y-3,6,6)` 每个评估轴一个。
   第一版把 null 当 0，于是 6 个轴都有方块（其中两个塌在圆心）。
   数 fillRect 太粗（网格/轴线不走 fillRect，但标签走 fillText），
   所以这里专门数 fillRect —— 雷达图里只有顶点用 fillRect。 */
{
  let rects = 0;
  const spy = new Proxy({}, {
    get(_, k) {
      if (k === 'canvas') return { width: 300, height: 150 };
      if (k === 'fillRect') return () => { rects++; };
      if (k === 'measureText') return () => ({ width: 10 });
      return () => {};
    },
    set() { return true; },
  });
  const cv = { width: 300, height: 150, clientWidth: 300, clientHeight: 150,
               getContext: () => spy, hidden: false };
  ELS.radar.getContext = () => spy;
  rects = 0;
  m.drawRadar(REAL_RADAR);
  const all6 = rects;
  rects = 0;
  m.drawRadar(RADAR_2NA);
  const only4 = rects;
  ELS.radar.getContext = () => ctxStub;
  if (all6 === 6 && only4 === 4) {
    console.log(`  ✅ 雷达图 null 轴不画顶点（6 轴 → ${all6} 个点；2 轴未评估 → ${only4} 个点）`);
  } else {
    bad++;
    console.log(`  ❌ 雷达图顶点数不对：全评估 ${all6}（应 6）、2 轴未评估 ${only4}（应 4）`);
  }
}

console.log('\n' + '='.repeat(78));
if (bad) {
  console.log(`  ${bad} 项不通过 —— charts.js 有问题`);
  process.exit(1);
}
console.log('  全部通过：有数据会画、**没数据不画假线**、null 轴不画顶点。');
