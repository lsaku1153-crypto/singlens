/**
 * 第 01 屏 · 选择模式
 *
 * ⚠️ **本文件由 `tools/split_v7.py` 自动生成，内容逐字搬自
 *    `docs/mockups/ui_mockup_v7_ljfh.html`。不要在这里手写逻辑 ——
 *    重跑一次生成就会覆盖，而且不会报错，只是悄悄丢。**
 *
 * 数据绑定请改 `web/js/bindings.js`（那里是手写文件）。
 *
 * ──────────────────────────────────────────────────────────────
 * ★★ 2026-10-10 现状更正（重要，与上面那段话有冲突）★★
 *
 * 上面那条"重跑生成就会覆盖"的告诫**今天真的验证过一次**：试跑
 * `tools/split_v7.py`，`git diff` 出来是
 *
 *     web/js/shell.js      −64 行   ← setPro / setProPersister / bindProButton 全在里面
 *     web/css/shell.css    −74 行
 *     web/js/views/s3.js  +346 行   ← plan-card、教练卡重做都在这里
 *     …另 8 个文件
 *
 * 也就是说：**稿子（2026-09-18 之后没再动过）已经落后于代码**，
 * 而这些"生成物"里早已有大量手写内容。生成器现在**不再是真相源**，
 * 重跑它是**破坏性**的；而且它不生成 `data-b`，跑完还得补
 * `mark_bindpoints.py` —— 中间漏任何一步都是静默的。
 *
 * ⇒ 所以第四条路（D · 导入 MIDI）是**直接加在这里**的，没有重跑生成器。
 *   下次要动这一屏，请先备份，不要直接跑 split_v7.py。
 *   （理想做法是把稿子补成与代码一致再生成，但那是另一件事，别混进来做。）
 * ──────────────────────────────────────────────────────────────
 */
export const id = 's1';
export const no = '01';
export const title = '选择模式';

const HTML = `
<div class="head">
      <div class="ghost">01</div>
      <div class="geo">
        <svg class="emblem"><use href="#d-dia"/></svg>
        <svg class="crosshair ch1"><use href="#d-ch"/></svg>
        <svg class="crosshair ch2"><use href="#d-cross"/></svg>
        <div class="coord">Mode Select<br>4 Routes</div>
        <div class="vtext">Pitch · Rhythm · Expression</div>
      </div>
      <div class="eyebrow"><svg class="ast"><use href="#d-dia"/></svg>Section 01 — Start</div>
      <h1>唱完，<br><span class="hollow">告诉你哪里不对</span></h1>
      <p class="sub">不用懂乐理，不用会看谱。你只管唱，剩下的交给它 —— 全部用大白话讲给你听，还告诉你下一遍该怎么唱。</p>
      <!-- 2026-10-21 皮肤层：首页右侧的「信号舱」。
           纯装饰，不带 data-b / 不参与接线；信息全部是**界面语言**（YIN / 22050 Hz / LOCAL），
           不是任何测量结果 —— 不在这里编造一个分数。
           小屏与 1366×768 由 CSS 隐藏，避免把四张模式卡挤到折叠线以下。 -->
      <div class="hero-hud" aria-hidden="true">
        <div class="hh-head"><span>PRTS · Signal</span><span class="hh-live"><i></i>Live</span></div>
        <svg class="hh-wave" viewBox="0 0 326 120" preserveAspectRatio="none">
          <path class="hh-grid" d="M0 24H326M0 48H326M0 72H326M0 96H326M40 0V120M80 0V120M120 0V120M160 0V120M200 0V120M240 0V120M280 0V120"/>
          <path class="hh-line ghost" d="M0 98 L18 96 L30 82 L42 88 L54 68 L66 74 L78 54 L90 62 L102 42 L114 52 L126 48 L138 72 L150 66 L162 90 L174 84 L186 62 L198 68 L210 46 L222 54 L234 38 L246 48 L258 64 L270 58 L282 78 L294 74 L306 88 L326 82"/>
          <path class="hh-line" d="M0 92 L18 90 L30 76 L42 82 L54 62 L66 68 L78 48 L90 56 L102 36 L114 46 L126 42 L138 66 L150 60 L162 84 L174 78 L186 56 L198 62 L210 40 L222 48 L234 32 L246 42 L258 58 L270 52 L282 72 L294 68 L306 82 L326 76"/>
          <circle class="hh-dot" cx="326" cy="76" r="3"/>
        </svg>
        <div class="hh-foot"><span>YIN / PYIN</span><span>22 050 HZ</span><span>LOCAL</span></div>
        <span class="hh-index">01</span>
      </div>
      <div class="ruler"></div>
      <div class="ruler-lbl">Input: Voice · Output: Guidance</div>
    </div>

    <!-- ★ 2026-10-10 删掉了原来这里那条「系统听不清你刚才的低音」横幅（ERR·RANGE）。
         为什么删：它是 v7 稿里的一条**静态文案**，任何用户**一打开首页**就看到
         「你唱到比较低的音时超出了识别范围，所以低音部分是猜的」—— 而他这时
         **一次都还没唱过**。属于本项目最忌讳的那一类：**界面在编造一件没发生的事**。

         ★ 为什么是"删"而不是"挪到别处"（这一条查过才敢下结论）：
           横幅描述的那个状态对应错误码 RANGE_SATURATED，而全仓检索下来，
           它**只出现在两处声明里** —— web/server.py 的 ERROR_CODES 元组、
           web/js/data/source.js 的 ErrorCode 枚举 ——
           **没有任何一行代码会真的抛出它**。
           ⇒ 也就是说：**系统永远不会产生这个事件**，横幅描述的是一个不存在的能力。
           所以删掉它**不丢任何信息**（不是"把真信息藏起来"）。

         ★ 那"低音超出识别范围"这件事的真实落点在哪：
           ① 设置页（s7）已经有**可操作**的说明：「唱比较低的歌时如果提示
              「听不清低音」，换成「男中音」或「自动检测」就好了」+ 当前音域实时显示
           ② 真要把它做成能力（判不准就明说），那是 **C17**（质量闸门）的范围 ——
              已经记在 docs/待办清单.md 里，不要在这里用一条假横幅代替
           ③ 第 08 屏的 7 个状态里**没有** RANGE_SATURATED，而那是**对的** ——
              没有的东西不该给它画一个状态卡
         （experiments/_audit_ui_cdp.py 会记录本屏 .gate 的文字，现在它会取到 null，
           那正是期望值；那个脚本只记录不断言，不受影响。）

         ⚠️ 本注释写在**模板字符串里面** ⇒ 里面**绝对不能出现反引号** ——
            一个反引号就会把模板截断，整个视图报 SyntaxError。
            （2026-10-10 写这段时就是这么挂的，靠 node --check 抓出来的。） -->

    <div class="song-strip" data-goto="s2" data-mode="E" role="button" tabindex="0"
         aria-label="上传一首歌，跟着原曲唱">
      <div class="k">NEW · 05</div>
      <div class="tx">
        <b>上传一首歌，跟着原曲唱</b>
        <s>系统自动分出原唱和伴奏，用原唱当参考；你跟着伴奏唱，它告诉你哪里不准。</s>
      </div>
      <span class="go">试试 →</span>
    </div>

    <div class="modes">
      <div class="mode hot" data-goto="s2" data-mode="A" role="button" tabindex="0">
        <div class="top">
          <div class="num">A<small>ROUTE</small></div>
          <span class="rec" style="color:#cf3f2a;border-color:#cf3f2a66">推荐新手</span>
        </div>
        <h4>跟着标准曲目唱</h4>
        <p>系统自带几首简单曲子的标准唱法，你跟着唱，它一个音一个音地比对。适合刚开始练。</p>
        <div class="foot"><span>Accuracy — Highest</span><span class="go">→</span></div>
      </div>
      <div class="mode" data-goto="s2" data-mode="B" role="button" tabindex="0">
        <div class="top">
          <div class="num">B<small>ROUTE</small></div>
          <span class="rec">最常用</span>
        </div>
        <h4>先唱一遍，再唱一遍</h4>
        <p>先随便清唱一遍当"样板"，第二遍跟它比。想练什么歌都行，不用找伴奏。</p>
        <p class="hp"><svg class="ast"><use href="#d-dia"/></svg><b>想跟伴奏唱？戴上耳机就行</b> —— 麦克风只会收到你的声音，评分和清唱一样准。<span class="why">伴奏由你自备，系统不提供也不存储任何伴奏音频。</span></p>
        <div class="foot"><span>Any Song · A Capella</span><span class="go">→</span></div>
      </div>
      <div class="mode" data-goto="s2" data-mode="C" role="button" tabindex="0">
        <div class="top">
          <div class="num">C<small>ROUTE</small></div>
          <span class="rec">离线分析</span>
        </div>
        <h4>上传已经录好的</h4>
        <p>手机里已有的<b>清唱</b>录音直接传上来，系统自动检查质量、出报告。</p>
        <p class="hp warn"><svg class="ast"><use href="#d-dia"/></svg>含伴奏也能传，但<b>评分仅供参考</b>。<span class="why">系统只做「谐波分离」，去不掉贝斯和和弦；想要准的结果，建议重录一版清唱。</span></p>
        <div class="foot"><span>File Input · WAV</span><span class="go">→</span></div>
      </div>
      <div class="mode" data-goto="s2" data-mode="D" role="button" tabindex="0">
        <div class="top">
          <div class="num">D<small>ROUTE</small></div>
          <span class="rec">想唱什么歌都行</span>
        </div>
        <h4>导入 MIDI 当参考</h4>
        <p>想唱哪首歌，就找它的 <b>MIDI</b> 传进来。系统从 MIDI 里<b>读出</b>正确旋律当参考，
           你跟着原曲唱，<b>走音判得出来</b>。</p>
        <p class="hp"><svg class="ast"><use href="#d-dia"/></svg><b>为什么不直接传歌？</b>
          从混音里"猜"旋律实测猜不准 —— 鼓和贝斯会把音高带跑。<span class="why">MIDI 是旋律的符号表示，不是录音本身；不涉及原曲版权。</span></p>
        <div class="foot"><span>MIDI · Symbolic Reference</span><span class="go">→</span></div>
      </div>
    </div>

    <div class="s1-foot" aria-hidden="true">
      <span class="mk"></span>
      <span>PRTS · LOCAL TERMINAL</span>
      <span>YIN + PYIN</span>
      <span>22050 HZ</span>
      <span class="end">S / 01</span>
    </div>
`;

export function render(root) {
  root.innerHTML = HTML;
}

/** 渲染后接线（数据源在 bindings.js 里 import，本文件不碰数据）。 */
export async function mount(root, ctx) {
  const { applyBindings } = await import('../bindings.js');
  return applyBindings('s1', root, ctx);
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
  const sec = document.getElementById('s1');
  const fn = sec && sec._unmount;
  if (typeof fn === 'function') return fn();
  return undefined;
}
