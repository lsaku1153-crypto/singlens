/**
 * 第 09 屏 · 微练习结果（2026-10-19）
 *
 * ★ 为什么单独一屏，而不是塞进 s3：
 *   作者反馈「微练习报告和完整报告混在一页，回完整报告也回不去」。
 *   微练习需要的是**一个结论 + 一个再练按钮**，不是总评/六维/教练/KTV
 *   一整套完整报告。所以把微练习结果独立成 s9，s3 只负责完整报告。
 *
 * 数据与交互在 bindings.js 的 `s9`；本文件只负责静态骨架。
 */
export const id = 's9';
export const no = '09';
export const title = '微练习结果';

const HTML = `
<div class="head">
  <div class="ghost">09</div>
  <div class="flex">
    <div>
      <div class="eyebrow"><svg class="ast"><use href="#d-dia"/></svg>Section 09 — Micro Practice</div>
      <h1>这一句，<br><span class="hollow">练得怎么样</span></h1>
    </div>
  </div>
  <div class="ruler" style="max-width:100%"></div>
  <div class="ruler-lbl" data-b="micro-meta">Micro practice</div>
</div>

<div class="wrap">
  <div class="card tick">
    <h3><span class="t" data-b="micro-title">微练习结果</span><span class="e">Micro Practice</span></h3>
    <div data-b="micro-context"></div>
    <div class="practice-ab" data-b="micro-ab"></div>
    <p class="plain" data-b="micro-pass"></p>
    <p class="plain" data-b="micro-reason"></p>
    <p class="plain plan-style" data-b="micro-style"></p>
    <div class="plan-demo" data-b="micro-demo"></div>
    <div class="plan-practice" data-b="micro-actions"></div>
  </div>
</div>
`;

export function render(root) {
  root.innerHTML = HTML;
}

export async function mount(root, ctx) {
  const { applyBindings } = await import('../bindings.js');
  return applyBindings('s9', root, ctx);
}

export async function unmount() {
  // 没有麦克风/WS，无需清理。
}
