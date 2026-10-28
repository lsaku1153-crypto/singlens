/**
 * 第 08 屏 · 界面状态
 *
 * ⚠️ **本文件由 `tools/split_v7.py` 自动生成，内容逐字搬自
 *    `docs/mockups/ui_mockup_v7_ljfh.html`。不要在这里手写逻辑 ——
 *    重跑一次生成就会覆盖，而且不会报错，只是悄悄丢。**
 *
 * 数据绑定请改 `web/js/bindings.js`（那里是手写文件）。
 */
export const id = 's8';
export const no = '08';
export const title = '界面状态';

const HTML = `
<div class="head">
      <div class="ghost">08</div>
      <div class="eyebrow"><svg class="ast"><use href="#d-dia"/></svg>Section 08 — States</div>
      <h1>界面状态<br><span class="hollow">设计</span></h1>
      <p class="sub">出错和空状态时，系统会给你看这些画面。按钮能点的都会真的去做对应的事。</p>
      <div class="ruler"></div>
      <div class="ruler-lbl">Permission · Error · Empty</div>
      <div style="margin-top:12px"><button class="pbtn" type="button" data-goto="s7">← 回到设置</button></div>
    </div>

    <div class="permwrap" style="margin-bottom:14px">
      <div class="statebox tick">
        <svg class="big deco"><use href="#i-mic"/></svg>
        <h4>需要用到你的麦克风</h4>
        <p>浏览器会弹一个权限请求，点"允许"就行。<br>
           你的录音文件<b>不上传</b>；音频分析在本机完成，唱后指导由联网 LLM 生成（只发送报告文本/歌词，不发送录音）。</p>
        <div class="acts">
          <button class="btn dark" data-b="perm-ask" type="button"><svg class="ic"><use href="#i-mic"/></svg>允许使用麦克风</button>
          <button class="btn" data-goto="s1" type="button">先看看，不录音</button>
        </div>
        <div class="tip">为什么不直接开始录音？因为浏览器规定：<b>必须先获得你的明确同意</b>，网页才能打开麦克风。</div>
      </div>
    </div>

    <div class="statwrap">
      <div class="statebox">
        <svg class="big deco" style="color:var(--warn)"><use href="#i-micoff"/></svg>
        <h4>浏览器拒绝了麦克风</h4>
        <p>你刚才点了"阻止"。点地址栏左边的<b>锁图标</b>，把麦克风改成"允许"，然后点下面的按钮。</p>
        <div class="acts"><button class="btn dark" data-b="perm-retry" type="button">重新申请权限</button></div>
        <div class="tip">权限一旦被拒绝，网页<b>无法自己再次弹出</b>请求 —— 必须你手动去浏览器设置里改。所以这里给了具体位置。</div>
      </div>

      <div class="statebox">
        <svg class="big deco" style="color:var(--warn)"><use href="#i-warn"/></svg>
        <h4>周围有点吵</h4>
        <p>系统听到了太多背景噪音，分不清哪个是你的声音。<br>
           试试<b>戴耳机</b>，或者换一个安静点的地方。</p>
        <div class="acts"><button class="btn dark" data-goto="s2" type="button">重新录一次</button></div>
        <div class="tip">判断依据：有声段里人声频段的能量占比低于 40%。这种情况下硬分析出来的分数不可信，所以我们宁可让你重录。</div>
      </div>

      <div class="statebox">
        <svg class="big deco"><use href="#i-micoff"/></svg>
        <h4>没听到你在唱</h4>
        <p>整整 8 秒都没检测到人声。<br>
           检查一下麦克风有没有对准、有没有被静音。</p>
        <div class="acts"><button class="btn dark" data-goto="s2" type="button">再试一次</button></div>
        <div class="tip">可能是选错了输入设备。可以去<b>设置 → 录音</b>里换一个麦克风试试。</div>
      </div>

      <div class="statebox">
        <svg class="big deco" style="color:var(--warn)"><use href="#i-clock"/></svg>
        <h4>录得太短了</h4>
        <p>只录到 2.1 秒，至少要 5 秒才能给出可靠的评分。</p>
        <div class="acts"><button class="btn dark" data-goto="s2" type="button">重新录</button></div>
        <div class="tip">太短的片段里音符数量不够，六个表现力指标算不准（尤其是需要长音的颤音指标）。5 秒大约 8–10 个音符，是下限。</div>
      </div>

      <div class="statebox">
        <svg class="big deco" style="color:var(--ink4)"><use href="#d-dots"/></svg>
        <h4>还没有练习记录</h4>
        <p>练完第一次之后，这里会显示你的进步曲线、演唱风格画像和历史记录。</p>
        <div class="acts"><button class="btn dark" data-goto="s2" type="button">去唱第一遍 →</button></div>
        <div class="tip">空状态也需要设计：直接留白会让用户以为页面坏了。</div>
      </div>

      <!-- ★ 2026-09-18 补：契约 6 个业务错误码里的最后一个 REF_MISSING。
           原来画了 5 个（权限 / 拒绝 / 太吵 / 没听到 / 太短），缺这一个。
           用中性色而非警告色 ——「还没选曲目」不是出错，是流程上的一步。 -->
      <div class="statebox">
        <svg class="big deco" style="color:var(--ink4)"><use href="#i-chart"/></svg>
        <h4>还没选要练哪首</h4>
        <p>「跟着标准曲目唱」这个模式需要先选一首参考曲目，系统才能把每个音跟标准比。<br>
           现在还没选，所以没法开始。</p>
        <div class="acts">
          <button class="btn dark" data-goto="s1" type="button">去选一首</button>
          <button class="btn" data-goto="s2" data-mode="B" type="button">改用「先唱一遍」模式</button>
        </div>
        <div class="tip">也可以不选曲目 —— 用「先唱一遍，再唱一遍」模式，系统拿你<b>自己的第一遍</b>当参考，同样能评音准和节奏。<br>
          <span class="mono" style="color:var(--ink4)">ERR · REF_MISSING</span></div>
      </div>
    </div>
`;

export function render(root) {
  root.innerHTML = HTML;
}

/** 渲染后接线（数据源在 bindings.js 里 import，本文件不碰数据）。 */
export async function mount(root, ctx) {
  const { applyBindings } = await import('../bindings.js');
  return applyBindings('s8', root, ctx);
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
  const sec = document.getElementById('s8');
  const fn = sec && sec._unmount;
  if (typeof fn === 'function') return fn();
  return undefined;
}
