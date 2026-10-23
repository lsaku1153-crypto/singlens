/**
 * 哈希路由。8 屏一一对应，不引框架。
 *
 * 与原 v7 稿的 `go(id)` 的差别：
 *   · 稿子是**一次性把所有屏写死在 HTML 里**，切换靠 class="on"
 *   · 这里改成**按需 import 视图模块**，切到哪屏才加载哪屏
 *     （s3 报告页有 316 行 + 4 个 canvas，没必要一进站就加载）
 *
 * URL 形如 `/#s3`；进站默认 s1。
 */

const VIEWS = [
  { id: 's1', no: '01', title: '选择模式', load: () => import('./views/s1.js') },
  { id: 's2', no: '02', title: '开始练习', load: () => import('./views/s2.js') },
  { id: 's3', no: '03', title: '评测报告', load: () => import('./views/s3.js') },
  { id: 's4', no: '04', title: '复练对比', load: () => import('./views/s4.js') },
  { id: 's5', no: '05', title: '成长档案', load: () => import('./views/s5.js') },
  { id: 's6', no: '06', title: '实验数据', load: () => import('./views/s6.js') },
  { id: 's7', no: '07', title: '设置',     load: () => import('./views/s7.js') },
  { id: 's8', no: '08', title: '界面状态', load: () => import('./views/s8.js') },
  // ★ 2026-10-19：微练习结果独立一屏，s3 只放完整报告。
  { id: 's9', no: '09', title: '微练习结果', load: () => import('./views/s9.js') },
];

export const views = VIEWS;

const listeners = new Set();
let current = null;
/** 上一个视图模块 —— 切屏时要调它的 `unmount`（见 go() 里的说明）。 */
let currentMod = null;

export const currentId = () => current;
export const onChange = (fn) => listeners.add(fn);

function wanted() {
  const h = (location.hash || '').replace(/^#/, '');
  return VIEWS.some((v) => v.id === h) ? h : 's1';
}

export async function go(id, { silent = false } = {}) {
  const v = VIEWS.find((x) => x.id === id) || VIEWS[0];
  if (!silent && location.hash !== '#' + v.id) {
    location.hash = '#' + v.id;      // 交给 hashchange 再走一遍
    return;
  }
  const host = document.getElementById('view');
  if (!host) return;
  let mod;
  try {
    mod = await v.load();
  } catch (e) {
    host.innerHTML = `<div style="padding:32px;color:#c33">视图 ${v.id} 加载失败：${e.message}</div>`;
    console.error(e);
    return;
  }

  /* ★ 新视图**加载成功之后**，才让上一屏收拾干净。
     为什么必须有：s2 会开麦克风与 WebSocket。切屏时不释放的后果
     **不是"占点资源"** —— 而是浏览器标签页上一直挂着录音红点，
     用户完全不知道还在录。这是隐私问题，不是性能问题。

     ⚠️ 顺序讲究：放在 `v.load()` **之后**。若放在最前面而新视图加载失败，
       就变成"旧画面还在、但麦克风已经被释放"—— 用户看着一个正在录的界面，
       其实什么都没录，而且按什么都没反应。宁可不释放，也别造成这种假象。

     ⚠️⚠️ 这段代码**必须留在生成器里**（`tools/split_v7.py` 的 ROUTER_JS）。
       踩过的坑（2026-09-23）：`web/js/router.js` 是**生成文件**，
       我直接改了它、也跑了测试，然后重跑一次全量生成 —— 改动**被静默冲掉**，
       而测试因为断言写松了照样报绿。审计（另一条会话）抓住了它。
       凡是"生成器会重写的文件"，改动都要落在生成器里。 */
  if (currentMod && typeof currentMod.unmount === 'function') {
    try { await currentMod.unmount(); }
    catch (e) { console.warn('[router] ' + current + ' 的 unmount 失败：', e); }
  }

  current = v.id;
  currentMod = mod;
  // 侧栏高亮
  document.querySelectorAll('aside nav button').forEach((b) => {
    b.classList.toggle('on', b.dataset.s === v.id);
  });
  host.dataset.view = v.id;
  host.replaceChildren();
  const sec = document.createElement('section');
  sec.id = v.id;
  sec.className = 'screen on';
  host.append(sec);
  /* ★ ctx 里带一个"收拾这一屏"的入口，视图把它挂到 `unmount()` 上。
     这样"谁能开资源、谁负责关"都落在同一个模块里，
     而不是让 router 去猜哪个视图需要什么清理。
     ⚠️ ctx **两个调用都要传**：views/sN.js 的 `render(root)` 不收第二个参数
        没关系，但 `mount(root, ctx)` 必须收到 —— 漏传就等于整个机制不生效
        （2026-09-23 实测：漏传后 `sec._unmount` 永远是 undefined）。 */
  const ctx = { router: { go, currentId }, view: v,
                onUnmount: (fn) => { sec._unmount = fn; } };
  if (typeof mod.render === 'function') {
    try {
      await mod.render(sec, ctx);
    } catch (e) {
      sec.innerHTML = `<div style="padding:24px;color:#c33">渲染 ${v.id} 失败：${e.message}</div>`;
      console.error(e);
    }
  }
  if (typeof mod.mount === 'function') await mod.mount(sec, ctx);
  listeners.forEach((fn) => fn(v.id));
}

export function start() {
  window.addEventListener('hashchange', () => go(wanted(), { silent: true }));
  // 侧栏点击
  document.querySelectorAll('aside nav button').forEach((b) => {
    b.addEventListener('click', () => go(b.dataset.s));
  });
  /* ★ 全站统一的 `[data-goto="sN"]` 落点（2026-09-24）。
     为什么放在路由里而不是各视图里：
       · 视图里写一个 `data-goto` 只是**一个属性**，没有处理器时它什么都不做，
         而按钮看着完全正常 —— s3 的「再唱一遍」和 s8 的 7 个按钮
         就是这么一直死着的（审计只发现了 s3 的那两个）。
       · 放在这里 = 一处实现，全站可用，新写的按钮不会再漏接。
     `data-mode` 是"带着选择跳过去"：写进 sessionStorage 给 s2 读。
     ⚠️ 排除 s1 的三张模式卡：它们自带处理器（还要顺手选默认参考曲目），
        两边都响应的话会 go() 两次。 */
  document.addEventListener('click', (e) => {
    const el = e.target && e.target.closest && e.target.closest('[data-goto]');
    if (!el || el.closest('.mode[data-mode]')) return;
    const to = el.dataset.goto;
    if (!VIEWS.some((v) => v.id === to)) return;
    if (el.dataset.mode) {
      try { sessionStorage.setItem('sl.mode', el.dataset.mode); } catch { /* 隐私模式 */ }
    }
    go(to);
  });
  go(wanted(), { silent: true });
}
