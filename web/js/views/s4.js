/**
 * 第 04 屏 · 复练对比
 *
 * ⚠️ **本文件由 `tools/split_v7.py` 自动生成，内容逐字搬自
 *    `docs/mockups/ui_mockup_v7_ljfh.html`。不要在这里手写逻辑 ——
 *    重跑一次生成就会覆盖，而且不会报错，只是悄悄丢。**
 *
 * 数据绑定请改 `web/js/bindings.js`（那里是手写文件）。
 */
export const id = 's4';
export const no = '04';
export const title = '复练对比';

const HTML = `
<div class="head">
      <div class="ghost">04</div>
      <div class="eyebrow"><svg class="ast"><use href="#d-dia"/></svg>Section 04 — Compare</div>
      <h1>练过之后<br><span class="hollow">真的变好了吗</span></h1>
      <p class="sub">系统会把你紧接着重唱的那一遍，和上一遍逐字对齐比较 —— 不是感觉变好了，是能量出来的。</p>
      <div class="ruler"></div>
      <div class="ruler-lbl">Before / After · Aligned Per Note</div>
    </div>

    <div class="card tick" id="cmpSummary" style="margin-bottom:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap">
        <div>
          <div class="eyebrow" style="font-size:10px">Comparison</div>
          <div data-b="cmp-title" style="font-size:18px;font-weight:700;margin-top:9px">还没有可对比的两遍</div>
        </div>
        <span class="delta" data-b="delta">—</span>
      </div>
      <div class="bigrow">
        <div><div class="k">唱准了吗</div><div class="n" data-b="cmp-pitch">—</div></div>
        <div><div class="k">音量有变化了吗</div><div class="n" data-b="cmp-dyn">—</div></div>
        <div><div class="k">句内有推进了吗</div><div class="n" data-b="cmp-phrase">—</div></div>
      </div>
    </div>

    <!-- ★ 2026-10-18：空态必须**单独一块**，不能只改抬头。
         原来 0 次练习时下面两张卡还留着原型稿的
         「第 3 句 / 太平 / 明显改善 / 4.1 → 9.6」—— 界面在编造一次对比。 -->
    <div class="card tick" id="cmpEmpty" hidden>
      <h3><span class="t" data-b="cmp-empty-title">怎么开始对比</span><span class="e">No Comparison Yet</span></h3>
      <div class="plain" data-b="cmp-empty-note" style="font-size:13px;line-height:1.8">
        先按同一首曲目唱一遍，再唱第二遍；系统才能把两遍逐项摆在一起。
      </div>
      <div style="margin-top:16px">
        <button class="btn dark" data-goto="s2" type="button">去唱一遍 →</button>
      </div>
    </div>

    <div class="compare" id="cmpWrap" hidden>
      <div class="card">
        <div class="cmp-h"><span class="t" data-b="cmp-t-a">第一次</span><span class="lvl" data-b="cmp-lv-a">—</span></div>
        <div class="chartwrap"><canvas id="cmpA"></canvas></div>
        <div class="tipbox" data-b="cmp-tip-a" style="margin-top:14px;font-size:12.5px;color:var(--ink3)"></div>
      </div>
      <div class="card">
        <div class="cmp-h"><span class="t" data-b="cmp-t-b">第二次</span><span class="lvl" data-b="cmp-lv-b">—</span></div>
        <div class="chartwrap"><canvas id="cmpB"></canvas></div>
        <div class="tipbox" data-b="cmp-tip-b" style="margin-top:14px;font-size:12.5px;color:var(--ink3)"></div>
      </div>
    </div>

    <div class="gate ok" id="cmpGate" hidden style="margin-top:14px;margin-bottom:0">
      <span class="mk"><svg class="ic" style="width:18px;height:18px;stroke-width:1.9"><use href="#i-check"/></svg></span>
      <span class="tx" data-b="cmp-verdict">—</span>
      <span class="code">LOG·OK</span>
    </div>
`;

export function render(root) {
  root.innerHTML = HTML;
}

/** 渲染后接线（数据源在 bindings.js 里 import，本文件不碰数据）。 */
export async function mount(root, ctx) {
  const { applyBindings } = await import('../bindings.js');
  return applyBindings('s4', root, ctx);
}

/**
 * 离开这一屏时被 router 调用（切屏、点侧栏都会走到）。
 *
 * ★ 为什么需要它：**s2 会开麦克风与 WebSocket**。切屏不释放的后果
 *   不是"占点资源"—— 而是浏览器标签页上一直挂着录音红点，
 *   用户完全不知道还在录。这是隐私问题。
 *
 * 约定：需要收拾的视图在 `bindings.js` 里拿到 `ctx.onUnmount(fn)`
 * 注册自己的清理函数（见 `bindings.js` 的 s2）。这里只负责转发 ——
 * 生成物不承载逻辑，否则重跑一次生成就丢了（而**丢了不会报错**，
 * 只是麦克风不再被释放）。
 */
export async function unmount() {
  const sec = document.getElementById('s4');
  const fn = sec && sec._unmount;
  if (typeof fn === 'function') return fn();
  return undefined;
}
