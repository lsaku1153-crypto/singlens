/**
 * 第 06 屏 · 实验数据
 *
 * ⚠️ **本文件由 `tools/split_v7.py` 自动生成，内容逐字搬自
 *    `docs/mockups/ui_mockup_v7_ljfh.html`。不要在这里手写逻辑 ——
 *    重跑一次生成就会覆盖，而且不会报错，只是悄悄丢。**
 *
 * 数据绑定请改 `web/js/bindings.js`（那里是手写文件）。
 */
export const id = 's6';
export const no = '06';
export const title = '实验数据';

const HTML = `
<div class="head">
      <div class="ghost">06</div>
      <div class="eyebrow"><svg class="ast"><use href="#d-dia"/></svg>Section 06 — Experiment</div>
      <h1>实验数据<br><span class="hollow">与权重标定</span></h1>
      <p class="sub">这一页是实验记录：当前权重是<b>占位值</b>，不是回归结果；盲评相关 ρ 由实验库现场计算。</p>
      <div class="ruler"></div>
      <div class="ruler-lbl">Placeholder Weights · Live ρ</div>
      <div style="margin-top:12px"><button class="pbtn" type="button" data-goto="s7">← 回到设置</button></div>
    </div>

    <div class="stats">
      <div class="stat"><div class="k">参与演唱</div><div class="n" data-b="st-singers">10<small> 人</small></div></div>
      <div class="stat"><div class="k">录音样本</div><div class="n" data-b="st-samples">24<small> 条</small></div></div>
      <div class="stat"><div class="k">盲评评分者</div><div class="n" data-b="st-raters">3<small> 人</small></div></div>
      <div class="stat"><div class="k">系统 vs 人评分相关</div><div class="n" data-b="st-rho">—</div></div>
    </div>

    <div class="exp2">
      <div class="card tick">
        <h3><span class="t">系统评分 vs 人评分</span><span class="e">Scatter</span></h3>
        <div class="chartwrap"><canvas id="scatter"></canvas></div>
        <div class="plain" style="margin-top:14px;font-size:13px">
          每个点是一条录音。横轴是评分者均分（1–5），纵轴是系统<b>表现力分</b>（换算到 1–5）。<br>
          点越靠近对角线，说明系统越接近人的判断。
          <span data-b="exp-scatter-note">正在读取实验库…</span>
        </div>
      </div>

      <div class="card tick">
        <h3><span class="t">当前计分的权重（占位）</span><span class="e">Weights</span></h3>
        <table class="tbl">
          <thead><tr><th>指标</th><th>权重（占位）</th><th>状态</th></tr></thead>
          <tbody>
            <tr data-b="w-dynamic_range"><td>音量起伏</td><td class="w mono">—</td><td>待标定</td></tr>
            <tr data-b="w-vibrato"><td>尾音控制</td><td class="w mono">—</td><td>待标定</td></tr>
            <tr data-b="w-onset_style"><td>起音方式</td><td class="w mono">—</td><td>待标定</td></tr>
            <tr data-b="w-brightness"><td>音色明亮度</td><td class="w mono">—</td><td>待标定</td></tr>
          </tbody>
        </table>
        <div class="plain" data-b="exp-dropped" style="margin-top:14px;font-size:12.5px;color:var(--ink2)">
          已移出评分：句内推进、节奏弹性 —— 它们量到的东西是真的，但目前没有证据支持把它们当"表现力好坏"的刻度。
        </div>
        <div class="plain" style="margin-top:14px;font-size:12.5px">
          <span data-b="exp-weight-note">正在读取实验数据…</span>
        </div>
      </div>
    </div>

    <div class="gate ok" data-b="exp-gate-box" style="margin-top:14px;margin-bottom:0">
      <span class="mk"><svg class="ic" style="width:18px;height:18px;stroke-width:1.9"><use href="#i-check"/></svg></span>
      <span class="tx">
        <b data-b="exp-gate-head">验收指标：取不到</b>
        <span data-b="exp-gate">取不到验收指标（后端没返回 benchmarks）—— 这里故意留空，不显示任何既往结论。</span>
      </span>
      <span class="code" data-b="exp-gate-code">NO DATA</span>
    </div>
`;

export function render(root) {
  root.innerHTML = HTML;
}

/** 渲染后接线（数据源在 bindings.js 里 import，本文件不碰数据）。 */
export async function mount(root, ctx) {
  const { applyBindings } = await import('../bindings.js');
  return applyBindings('s6', root, ctx);
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
  const sec = document.getElementById('s6');
  const fn = sec && sec._unmount;
  if (typeof fn === 'function') return fn();
  return undefined;
}
