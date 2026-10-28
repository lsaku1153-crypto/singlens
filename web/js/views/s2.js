/**
 * 第 02 屏 · 开始练习
 *
 * ⚠️ **本文件由 `tools/split_v7.py` 自动生成，内容逐字搬自
 *    `docs/mockups/ui_mockup_v7_ljfh.html`。不要在这里手写逻辑 ——
 *    重跑一次生成就会覆盖，而且不会报错，只是悄悄丢。**
 *
 * 数据绑定请改 `web/js/bindings.js`（那里是手写文件）。
 */
export const id = 's2';
export const no = '02';
export const title = '开始练习';

const HTML = `
<div class="head">
      <div class="ghost">02</div>
      <div class="eyebrow"><svg class="ast"><use href="#d-dia"/></svg>Section 02 — Practice</div>
      <h1><span data-b="s2-state">准备练习</span><span class="sep">·</span><span class="hollow" data-b="s2-title">选一首曲目</span></h1>
      <div class="ruler"></div>
      <div class="ruler-lbl">Realtime Pipeline · Latency &lt; 200ms</div>
    </div>

    <div class="card tick" id="s2Setup" data-b="setup">
      <h3><span class="t">这次跟什么唱</span><span class="e">Pick A Reference</span></h3>
      <div data-b="setup-body">正在读取曲目与模板…</div>
    </div>

    <!-- ⬇︎⬇︎ 原型 live 画面从这里开始（默认隐藏，由 s2 接线切换）⬇︎⬇︎ -->
    <div class="practice" data-b="live-wrap" hidden>
      <!-- ★★ 2026-10-17 加：**伴奏 + 倒数引导**。
           作者的原话：「用户没办法听到伴奏，不知道从哪里开始唱」。
           而代码里早就写着「要求戴耳机跟伴奏唱」—— 但从来没给过一个音。
           这块就是那个"音"和那句"什么时候开口"。
           ⚠️ 由 bindings 的 beginCapture 驱动：它放 「…/guide.wav」
             （前 2 秒 4 拍预备 + 参考旋律），这里只负责显示。
           没有伴奏（比如模板/MIDI 参考）时整块保持 hidden。 -->
      <div class="cue" data-b="lv-cue" hidden>
        <div class="cue-n" data-b="lv-cue-n">准备</div>
        <div class="cue-t" data-b="lv-cue-t">先听 4 下节拍，第 5 下开口唱</div>
        <button class="pbtn" data-b="lv-guide-kind" type="button" hidden
                title="在伴奏和原唱之间切换">跟唱音轨：伴奏</button>
        <button class="pbtn" data-b="lv-guide-again" type="button">重新开始这一遍</button>
      </div>
      <!-- ★ 2026-10-18：走带放在 cue 之后、两个 .stack 之前，并横跨两列。
           原来它在左栏第 4 张卡之后 —— 1440×900 下 top=1216，
           用户唱的时候根本够不着「暂停 / 完成」。 -->
      <div class="transport">
        <button class="tbtn2 rec" data-b="lv-rec" title="重新开始这一遍" aria-label="重新开始这一遍"><svg class="ic" style="width:16px;height:16px"><use href="#i-rec"/></svg></button>
        <button class="tbtn2" data-b="lv-pause" title="暂停" aria-label="暂停"><svg class="ic" style="width:16px;height:16px"><use href="#i-pause"/></svg></button>
        <div class="tprog"><i data-b="lv-prog" style="width:0%"></i></div>
        <span class="mono" data-b="lv-time" style="font-size:11px;color:var(--ink3)">00:00.0</span>
        <span class="recstate" data-b="lv-state" role="status" aria-live="polite" aria-atomic="true">正在录</span>
        <button class="tbtn2 go" data-b="lv-go" title="完成演唱，开始精算" aria-label="完成演唱，开始精算"><svg class="ic" style="width:16px;height:16px"><use href="#i-check"/></svg></button>
      </div>
      <div class="lyrics-live" data-b="lv-lyrics" hidden>
        <div class="ly-head"><b>歌词</b><span>自动识别 · 可能有错</span></div>
        <div class="ly-scroll" data-b="lv-lyrics-list" role="log" aria-live="off"></div>
      </div>
      <div class="stack">
        <div class="card tick">
          <h3><span class="t">你正在唱</span><span class="e">Live Pitch</span></h3>
          <div class="chartwrap"><canvas id="live"></canvas></div>
          <div class="legend">
            <span><em style="background:#bfc4bf"></em>它希望你唱的音</span>
            <span><em style="background:#0c0d10"></em>你实际唱的音</span>
            <span><em style="background:#cf3f2a"></em>唱偏了（听得出走音）</span>
          </div>
        </div>

        <div class="card">
          <h3><span class="t">每个字唱得怎么样</span><span class="e">Per-Note</span></h3>
          <div class="chartwrap"><canvas id="roll"></canvas></div>
          <div class="tipbox" style="margin-top:14px;font-size:12.5px;color:var(--ink3)">
            一个方块 = 一个字。方块越高，说明你唱得越高。<br>
            想听具体某一段，练完在报告页里逐段回放。
          </div>
        </div>

      </div>

      <div class="stack">
        <div class="card tick">
          <h3><span class="t">现在怎么样</span><span class="e">Live Metrics</span></h3>
          <div style="display:flex;flex-direction:column;gap:19px">
            <div>
              <div class="mrow"><div class="lb">有没有唱走音<s>Pitch</s></div><b class="mono" data-b="lv-pitch">—</b></div>
              <div class="bar"><i class="acc" data-b="lv-pitch-bar" style="width:0%"></i></div>
            </div>
            <div>
              <div class="mrow"><div class="lb">有没有抢拍拖拍<s>Rhythm</s></div><b class="mono" data-b="lv-rhythm">—</b></div>
              <div class="bar"><i class="acc" data-b="lv-rhythm-bar" style="width:0%"></i></div>
            </div>
            <div>
              <div class="mrow"><div class="lb">唱得有起伏吗<s>Dynamics</s></div><b class="mono" data-b="lv-dynamic">—</b></div>
              <div class="bar"><i class="dim" data-b="lv-dynamic-bar" style="width:0%"></i></div>
            </div>
            <div>
              <div class="mrow"><div class="lb">尾音有没有抖<s>Vibrato</s></div><b class="mono" data-b="lv-vibrato">—</b></div>
              <div class="bar"><i class="warn" data-b="lv-vibrato-bar" style="width:0%"></i></div>
            </div>
          </div>
        </div>

        <div class="now">
          <div class="k">Now Singing</div>
          <div class="v mono" data-b="lv-note" data-empty="1">—</div>
          <div class="st" data-b="lv-verdict">还没开始</div>
        </div>

        <div class="card">
          <h3><span class="t">现在就试一下</span><span class="e">Try Now</span></h3>
          <div class="tipbox" data-b="lv-advice" aria-live="polite" aria-atomic="true">
            还没收到实时数据。开口唱，这一栏会给你即时的建议。
          </div>
        </div>
      </div>
    </div>
    <!-- ⬆︎⬆︎ 原型 live 画面到此结束 ⬆︎⬆︎ -->
`;

export function render(root) {
  root.innerHTML = HTML;
}

/** 渲染后接线（数据源在 bindings.js 里 import，本文件不碰数据）。 */
export async function mount(root, ctx) {
  const { applyBindings } = await import('../bindings.js');
  return applyBindings('s2', root, ctx);
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
  const sec = document.getElementById('s2');
  const fn = sec && sec._unmount;
  if (typeof fn === 'function') return fn();
  return undefined;
}
