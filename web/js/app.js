/**
 * 应用入口（B1-1：只搭壳）。
 *
 * 做三件事：
 *   1. 打印数据源模式（`?src=mock` / `?src=api`）—— B1-3 才真正接线
 *   2. 起路由
 *   3. 把 8 屏的导航挂上
 *
 * 数据源在 `js/data/`（已完成，`source.js` 定义接口、`api.js` 9 个方法、
 * `mock.js` 假数据）。本步**先不 import**，避免把未接线的调用混进来。
 */
import { start, views, go } from './router.js';
import { $ } from './util/dom.js';
/* ★ 数据源模式**只从 data/index.js 取**，不在这里再判断一次（2026-09-24）。
   踩过的坑：这里原来自己写了一份 `params.get('src') === 'api' ? ...`，
   而真正决定数据来源的是 `data/index.js` 里的另一份默认值。
   改了一处、忘了另一处 ⇒ 徽标写着"真实后端"、页面渲染的却是 mock 假数据 ——
   **界面在说谎**，比没有徽标更坏。
   （`_verify_audit_findings.py` 的 P0-4 正是查这个：默认值必须唯一、必须是 api） */
import { srcMode } from './data/index.js';

function mode() {
  return srcMode;
}

function banner() {
  const m = mode();
  const b = $('#srcBadge');
  if (!b) return;
  b.textContent = m === 'api' ? '数据源：真实后端' : '数据源：mock（假数据）';
  b.dataset.mode = m;
  if (m === 'mock') {
    b.title = '当前用 mock 假数据渲染。加 ?src=api 可切到真实后端。';
  }
}

function main() {
  banner();
  console.info(`[SingLens] 数据源模式 = ${mode()}；视图 ${views.length} 屏`);
  start();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', main);
} else {
  main();
}

export { go };
