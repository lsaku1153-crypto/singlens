/**
 * 绑定 key 契约检查：`bindings.js` 引用的每个 data-b key，
 * 必须在对应视图的 HTML 里真的存在。
 *
 * 为什么必须查这个：
 *   `setText(root, 'pitch', ...)` 找不到元素时**什么都不做，也不报错** ——
 *   这是本项目最容易踩、也最难发现的一类缺陷：
 *   页面看着正常（稿子里的死数字还在），接线其实一处都没生效。
 *   s3 第一次截图就踩了这个（见 bindings.js 里 s3 的注释）。
 *
 * 反过来也查：视图里标了 data-b，但没有任何绑定会填它 →
 *   要么是忘了接，要么是键名拼错了。两者都要报出来。
 *
 * 用法：node tools/binding_keys_check.mjs
 */
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const WEB = path.resolve('web/js');
const V_BIND = path.join(WEB, 'bindings.js');

/** 视图文件 → 绑定函数名 */
const VIEWS = {
  s1: 's1', s2: 's2', s3: 's3', s4: 's4', s5: 's5', s6: 's6', s7: 's7',
  // ⚠️ s8 一度不在这个表里（它当时没有任何 data-b），
  //    于是 s8() 的代码会被算进**上一个函数**（s3）—— 新增 s8 的绑定后，
  //    报出来的是"s3 引用了 perm-ask/perm-retry"，指错了屏。
  //    少一屏的代价是"这一屏的 key 从来没人查"。
  s8: 's8',
  // ★ 2026-10-19：微练习结果独立 s9；不加进表里，s9() 的 key 会被算进上一个函数。
  s9: 's9',
};

/**
 * 动态 key 的展开表。
 * 静态解析看不到 `setText(root, \`bd-${k}\`)` 里的 k 是什么，
 * 所以把取值集合显式列出来 —— **这份清单同时也是给人看的文档**：
 * 看到它就知道"这一屏到底绑了哪几个 key"。
 */
const DYNAMIC = {
  // s5 六维横条
  'bd-${k}': ['pitch', 'rhythm', 'dyn', 'phrase', 'vib', 'rubato'].map((k) => `bd-${k}`),
  'bd-${k}-bar': ['pitch', 'rhythm', 'dyn', 'phrase', 'vib', 'rubato'].map((k) => `bd-${k}-bar`),
  // s6 权重表：2026-10-18 起只渲染**当前计分的四项**（另两项已移出，
  // 由 exp-dropped 说明），所以这里也从六项改成四项 —— 否则检查会要求
  // 视图里存在两个已经不存在的行。
  'w-${w.id}': ['dynamic_range', 'vibrato', 'onset_style', 'brightness']
    .map((k) => `w-${k}`),
  // s2 实时四个滚动分（`put('pitch', m.pitch)` → `lv-pitch` / `lv-pitch-bar`）
  'lv-${key}': ['pitch', 'rhythm', 'dynamic', 'vibrato'].map((k) => `lv-${k}`),
  'lv-${key}-bar': ['pitch', 'rhythm', 'dynamic', 'vibrato'].map((k) => `lv-${k}-bar`),
};

/**
 * **由 bindings 在运行时注入**的 key。
 *
 * s2 的「开始之前」面板整块是 `body.innerHTML = …` 现画的（原型稿里没有这一态），
 * 所以 `views/s2.js` 的静态 HTML 里当然找不到它。放进静态视图里也不行 ——
 * 那等于在视图里留一份只有运行时才被替换的假结构。
 * 判据：这个 key 由 bindings 自己产生、也由 bindings 自己消费 ⇒ 记在这里。
 */
const INJECTED = {
  s2: [
    'new-tpl-name',
    // 2026-10-18：两段式的 tab / 引导 / pane 都是 `body.innerHTML` 现画的，
    // 和 new-tpl-name 同一个理由（静态视图里没有这个"切模式"的态）。
    'setup-guide',
    'tpl-list',
    'tpl-msg',
    'transpose-box',
    'transpose-val',
    'transpose-note',
    // ★ 模式 C（上传）那一组（2026-10-01）：文件选择框、参考选择器、说明文字
    //   全部由 s2 的 `body.innerHTML = …` 现画 —— 和 `new-tpl-name` 同一个理由
    //   （「开始之前」整块面板都是运行时画的，静态视图里没有这一态）。
    'upload-file', 'upload-ref', 'upload-note',
    // ★ 模式 D（导入 MIDI）那一组（2026-10-10）：同样是 `body.innerHTML` 现画。
    //   `midi-panel` 是空壳容器，轨清单/试听/确认按钮由 `renderMidi()` 填进去 ——
    //   因为**解析之前根本不知道有几个轨**，静态视图里没法有这一态。
    //   ⚠️ 后画的按钮由 renderMidi 自己绑 onclick（走同一个 `onAct`），
    //      不能靠 s2 开头那次 `body.querySelectorAll` —— 那时它们还不存在。
    'midi-file', 'midi-note', 'midi-panel', 'midi-audio', 'midi-title',
    // ★ 模式 E（上传歌曲，2026-10-20）：文件框/说明/结果面板同样是
    //   `body.innerHTML` 现画的；面板里的进度/质量/试听按钮由 render* 填。
    'song-file', 'song-note', 'song-panel', 'song-library',
  ],
  // s8 的 `perm-note`（申请麦克风的结果提示）由 bindings 的 s8() 用
  // `box.dataset.b = 'perm-note'` 现建 —— 静态视图里没有它，
  // 也不该有（原型里没这个元素，而它只在点过按钮之后才出现）。
  s8: ['perm-note'],
};

/**
 * 从 bindings.js 里切出每个视图函数的源码块。
 *
 * ⚠️ 这里原来是**按函数声明的位置**切：把 `s1`..`s8` 的位置排好序，
 *    第 i 块的结束点取"下一个视图函数的声明位置"。
 *    那个写法有一个**静默的漏检**，2026-10-01 加练习计划时撞上了：
 *
 *      · 视图函数在文件里**不是按编号顺序**声明的
 *        （实测顺序 `s1 s3 s8 s4 s5 s6 s7 s2` —— s2 在最后）
 *      · 于是 `s8` 那一块的结束点变成了它后面的**第一个视图函数** `s4`
 *        ⇒ 落在 `s8` 与 `s4` 之间的代码被算进了 s8，
 *          而 **`s8` 自己的函数体反而落在了 s2 的块里**
 *        ⇒ s8 的 key 检查一直在看别人的代码。
 *
 *    现在改成：**每块从自己的声明起，到下一个视图函数的声明为止；
 *    最后一个块到文件结束**。块之间夹着的辅助函数（`renderXxx`）仍然算在
 *    前一个视图头上 —— 那是对的，它们确实是为那个视图服务的
 *    （`renderAccBlocks` / `renderProblems` / `renderCoach` 就在 s3 与 s8 之间）。
 *    ⇒ 所以**新增的辅助函数要放在它所服务的视图后面**。
 */
function splitFns(src) {
  const out = {};
  const names = Object.values(VIEWS);
  const marks = [];
  for (const n of names) {
    const m = new RegExp(`^async function ${n}\\(`, 'm').exec(src);
    if (m) marks.push([n, m.index]);
  }
  // 按声明位置排序 → 每块的结束就是下一个声明的位置（不再假设编号顺序）
  marks.sort((a, b) => a[1] - b[1]);
  for (let i = 0; i < marks.length; i++) {
    const [name, start] = marks[i];
    const end = i + 1 < marks.length ? marks[i + 1][1] : src.length;
    out[name] = src.slice(start, end);
  }
  return out;
}

/** 抽出一段代码里引用的所有 key。 */
function keysIn(code) {
  const keys = new Set();
  const dynamic = new Set();
  const pats = [
    /setText\(\s*root\s*,\s*'([^']+)'/g,
    /setBar\(\s*root\s*,\s*'([^']+)'/g,
    /\[data-b="([^"]+)"\]/g,
    /`\[data-b="([^"]+)"\]`/g,
    /setText\(\s*root\s*,\s*`([^`]+)`/g,
    /setBar\(\s*root\s*,\s*`([^`]+)`/g,
    /data-b="([^"]+)"/g,
    // ★ 通过**参数**传进去的 key：`setLv(key, ...)` / `bindSw(key, ...)`。
    //   静态解析看不到函数体里的 `${key}`，只能回到调用点去取实参。
    //   漏掉这一类的后果和"键名拼错"一样：元素永远填不上，且不报错。
    /(?:setLv|bindSw)\(\s*'([^']+)'/g,
    // s9 的 `set('micro-...', ...)` 小助手（2026-10-19）
    /set\(\s*'([^']+)'/g,
  ];
  for (const re of pats) {
    let m;
    while ((m = re.exec(code))) {
      const k = m[1];
      if (k.includes('${')) {
        dynamic.add(k);
        const ex = DYNAMIC[k];
        if (ex) ex.forEach((x) => keys.add(x));
      } else {
        keys.add(k);
      }
    }
  }
  return { keys, dynamic };
}

const src = await readFile(V_BIND, 'utf8');
const fns = splitFns(src);

let bad = 0;
let warn = 0;
console.log('='.repeat(78));
console.log('绑定 key 契约检查（bindings.js ↔ views/*.js）');
console.log('='.repeat(78));

for (const [view, fnName] of Object.entries(VIEWS)) {
  const code = fns[fnName];
  if (!code) { console.log(`  ⚠️  ${view}: bindings.js 里没有函数 ${fnName}()`); warn++; continue; }

  const { keys, dynamic } = keysIn(code);
  const viewSrc = await readFile(path.resolve(`web/js/views/${view}.js`), 'utf8');
  const declared = new Set([...viewSrc.matchAll(/data-b="([^"]+)"/g)].map((m) => m[1]));
  // 运行时注入的 key 也算"存在"（见文件头 INJECTED 的说明）
  const injected = new Set(INJECTED[view] || []);
  injected.forEach((k) => declared.add(k));

  const missing = [...keys].filter((k) => !declared.has(k) && !k.includes('$'));
  const unbound = [...declared].filter((k) => !keys.has(k) && !k.includes('$'));

  const problems = [];
  if (missing.length) problems.push(`引用但视图里没有：${missing.join(', ')}`);
  if (unbound.length) problems.push(`视图里标了但没人填：${unbound.join(', ')}`);

  if (problems.length) {
    bad++;
    console.log(`  ❌ ${view.padEnd(4)} ${problems.join('；')}`);
  } else {
    console.log(`  ✅ ${view.padEnd(4)} 绑定 ${String(keys.size).padStart(2)} 个 key，` +
                `视图声明 ${String(declared.size).padStart(2)} 个，完全对应` +
                (dynamic.size ? `（含 ${[...dynamic].join(' ')} 展开）` : '') +
                (injected.size ? `（含 ${[...injected].join(' ')} 运行时注入）` : ''));
  }
}

console.log('\n' + '='.repeat(78));
if (bad) {
  console.log(`  ${bad} 屏有 key 对不上。⚠️ 对不上的后果是【静默失效】：`);
  console.log('     界面上留着稿子里的死数字，看着一切正常。');
  process.exit(1);
}
console.log(`  全部对应。${warn ? `（${warn} 项提醒）` : ''}`);
console.log('  ⚠️ 本检查只保证"元素找得到"，不保证"数值取得对" —— 那要靠真跑一遍 + 截图。');
