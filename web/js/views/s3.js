/**
 * 第 03 屏 · 评测报告
 *
 * ⚠️ **本文件由 `tools/split_v7.py` 自动生成，内容逐字搬自
 *    `docs/mockups/ui_mockup_v7_ljfh.html`。不要在这里手写逻辑 ——
 *    重跑一次生成就会覆盖，而且不会报错，只是悄悄丢。**
 *
 * 数据绑定请改 `web/js/bindings.js`（那里是手写文件）。
 */
export const id = 's3';
export const no = '03';
export const title = '评测报告';

const HTML = `
<div class="head">
      <div class="ghost">03</div>
      <div class="flex">
        <div>
          <div class="eyebrow"><svg class="ast"><use href="#d-dia"/></svg>Section 03 — Report</div>
          <h1>这次唱得<br><span class="hollow">怎么样</span></h1>
        </div>
        <button class="probtn" id="proBtn"><span class="sw"><i></i></span>显示专业术语</button>
      </div>
      <div class="ruler" style="max-width:100%"></div>
      <div class="ruler-lbl" data-b="report-meta">Song: 小星星 · Take 02 · 2026-09-14</div>
      <!-- 2026-10-20：歌曲参考必须说清"原唱人声自动分离、不是人工谱" -->
      <div class="ref-source" data-b="ref-source" hidden></div>
    </div>

    <!-- 2026-10-21 v2：报告改为**分页卡片**，不是长页 + 跳转锚点。
         一页只放一组卡片；切页时整组淡入，不再需要滚动很久。
         data-rep-page 由 bindings 的 initReportPager 控制，不进 data-b 契约。 -->
    <nav class="rep-nav" aria-label="报告分页与报告切换">
      <span class="rep-nav-k">Report Pages</span>
      <div class="rep-pager">
        <button class="rep-arrow" type="button" data-rep-step="-1" aria-label="上一页">←</button>
        <div class="rep-jumps" role="tablist" aria-label="报告分页">
          <button type="button" class="rep-jump on" role="tab" aria-selected="true" data-rep-page-btn="summary">01 总评</button>
          <button type="button" class="rep-jump" role="tab" aria-selected="false" data-rep-page-btn="coach">02 下一遍</button>
          <button type="button" class="rep-jump" role="tab" aria-selected="false" data-rep-page-btn="decomp">03 拆解</button>
          <button type="button" class="rep-jump" role="tab" aria-selected="false" data-rep-page-btn="problems">04 问题</button>
        </div>
        <button class="rep-arrow" type="button" data-rep-step="1" aria-label="下一页">→</button>
        <span class="rep-count" data-rep-count>01 / 04</span>
      </div>
      <label class="rep-pick" hidden>
        <span>切换报告</span>
        <select data-rep-pick aria-label="切换最近报告"></select>
      </label>
    </nav>

    <div class="report show-details" data-paged="1">
      <div class="stack">
      <div class="card tick summary rep-on" data-rep-page="summary">
        <h3><span class="t">总评</span><span class="e">Summary</span></h3>
        <!-- ★ 对齐说明放在分数之前：分数低时，先告诉用户“这份分数能不能信”。 -->
        <div class="deg" data-b="align-banner" hidden>
          <span class="dg-h" data-b="align-banner-head">ALIGNMENT DEGRADED</span>
          <div data-b="align-banner-body"></div>
        </div>
        <div class="total">
          <div class="v" data-b="total">—</div>
          <div class="d" data-b="total-delta" hidden></div>
        </div>
        <div class="total-basis" data-b="total-basis" hidden></div>
        <!-- ★ 删掉这一次：2026-10-15 加「删单条」，2026-10-20 从分数正下方
             挪到卡片最底部（见下方 danger-row）。危险动作不该占首屏视觉首位。 -->
        <div class="threeline">
          <div><div class="k">唱准了吗</div><div class="n mono" data-b="pitch">—</div></div>
          <div><div class="k">跟上拍子了吗</div><div class="n mono" data-b="rhythm">—</div></div>
          <div><div class="k">唱得有感情吗</div><div class="n mono" data-b="expr">—</div></div>
        </div>
        <div class="score-note" data-b="rhythm-basis" hidden></div>
        <!-- ★ 2026-10-20：分数怎么来的，用一句话说清。
             作者实测的最大困惑是「音准 14.5，但教练说整体偏差只有 -1.2 音分」——
             因为音准分是「整遍偏移 + 每个音稳不稳」两半，只报一个数必然像自相矛盾。
             这段文字只引用报告里已算出的 accuracy / stability，不新增测量。 -->
        <div class="score-note" data-b="score-note" hidden></div>
        <!-- ★ KTV 式并列参考分（2026-10-18）：八度折叠 + ±50 音分命中率 +
             时长加权。不替换六指标，只多给一个对离群点不敏感的口径。
             旧报告后端会按已存 segments 补算；真没有就隐藏。 -->
        <div class="ktvbox" data-b="ktv-box" hidden>
          <div class="ktv-h"><span class="t">KTV 口径</span><span class="e">Tolerance Band</span></div>
          <div class="ktv-v">
            <b class="mono" data-b="ktv-score">—</b>
            <span class="ktv-sub" data-b="ktv-detail"></span>
          </div>
          <!-- ★ 2026-10-20：与「唱准了吗」并列时，必须解释两者口径不同，
               否则用户看到「音准 14.5 / 命中 68.9%」只会认为评分坏了。 -->
          <div class="ktv-hint" data-b="ktv-hint"></div>
        </div>
        <!-- ★ 对齐说明已上移到 h3 之后：分数低时先看“能不能信”。 -->
        <canvas id="radar"></canvas>
        <div class="radar-cap">Six Dimensions · 归一化 0–1</div>
        <!-- ★ 删掉这一次（2026-10-15，审计发现缺「删单条」）。
             ★ 2026-10-20：从分数正下方挪到卡片**最底部**。
             原来紧贴 39.9 大分数，第一眼看到的是"删掉"而不是成绩；
             危险动作不应该占首屏视觉首位。仍然在用户正看着的这一屏里，
             点之前有确认，删完会说清「什么没删」（exp_* 与身份）。
             ⚠️ 本文件整段 HTML 在 JS 的 template literal 里 ⇒
                注释里**绝对不能出现反引号**。 -->
        <div class="danger-row" data-b="danger-row" hidden>
          <button class="pbtn" data-b="del-session" type="button" hidden
            title="删掉这一次练习：它的报告、分数和录音都会删掉，删了找不回来">
            删掉这一次
          </button>
        </div>
      </div>

      <div class="card tick decomp" data-rep-page="decomp">
        <h3><span class="t">音准和节奏，各拆成两层看</span><span class="e">Decomposition</span></h3>
        <div class="duo">
          <div class="row">
            <div class="lb"><span>音准 · 有没有整体偏高/偏低</span><b class="mono" data-b="pitch-acc">—</b></div>
            <div class="track"><i data-b="pitch-acc-bar" style="width:0%"></i></div>
          </div>
          <div class="row">
            <div class="lb"><span>音准 · 有没有忽高忽低</span><b class="mono" data-b="pitch-stab">—</b></div>
            <div class="track"><i data-b="pitch-stab-bar" style="width:0%"></i></div>
          </div>
          <div class="row">
            <div class="lb"><span>节奏 · 有没有整体抢拍/拖拍</span><b class="mono" data-b="rhythm-acc">—</b></div>
            <div class="track"><i data-b="rhythm-acc-bar" style="width:0%"></i></div>
          </div>
          <div class="row">
            <div class="lb"><span>节奏 · 有没有忽快忽慢</span><b class="mono" data-b="rhythm-stab">—</b></div>
            <div class="track"><i data-b="rhythm-stab-bar" style="width:0%"></i></div>
          </div>
          <div class="row">
            <div class="lb"><span>节奏 · 自由的弹性（加分项）</span><b class="mono" data-b="rubato" style="color:var(--acc-ink)">—</b></div>
            <div class="track"><i class="a" data-b="rubato-bar" style="width:0%"></i></div>
          </div>
        </div>
        <div class="plain" data-b="tempo-note" style="margin-top:12px;font-size:12.5px;font-weight:600"></div>
        <div class="plain" style="margin-top:16px;font-size:12.5px">
          把"准不准"拆成两层，是因为<b>整体偏低</b>和<b>忽高忽低</b>是两种完全不同的毛病：
          前者可能只是调性不合，后者才是控制力问题。节奏最后一项更是<b>加分项而非扣分项</b>。
        </div>
      </div>
      </div>

      <div class="stack">
        <!-- ★ 2026-10-18：练习计划卡放到**右栏最前**，在教练卡之前。
             它是整页唯一要用户"照做"的块（下一遍只改哪一件事）；
             原来在左栏、雷达之后（桌面 y≈1300，手机 y≈1100）。
             右栏更宽，步骤不用挤在 320px 的窄列里；手机由 CSS order 排第二。 -->
        <div class="card tick" data-b="plan-card" data-rep-page="coach" hidden>
          <h3><span class="t">下一遍只改这一件事</span><span class="e">Next Take</span></h3>
          <div data-b="plan-body"></div>
        </div>

        <!-- ★ 2026-10-19：把「存成跟唱模板」从**左栏底部**挪到右栏最前。
             作者实测：唱完 B 的第一遍，模板没出现在列表里 —— 其实卡在
             左栏 y≈1212（一屏 900 看不到），用户以为没存。
             ★ 2026-10-20：计划卡再往前一步 —— 两卡都在首屏时，先给"下一遍
             怎么唱"（会改变这一次的分数），再给"存模板"（保存动作）。
             模板卡仍是右栏第 2 张，一屏内可见。 -->
        <div class="card tick" data-b="tpl-card" data-rep-page="coach" hidden>
          <h3><span class="t">把这一遍存成跟唱模板</span><span class="e">Save As Template</span></h3>
          <div class="plain" style="font-size:12.5px;margin-bottom:10px">
            存下来之后，下次就能<b>跟着这一遍唱</b> —— 不限于内置那两首，你想练什么歌都行。
          </div>
          <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
            <input data-b="tpl-name" aria-label="给这个模板起个名字" type="text" placeholder="给这个模板起个名字，比如「我的原创·副歌」"
                   style="flex:1;min-width:220px;padding:9px 12px;border:1px solid var(--line2);border-radius:6px;font:inherit;font-size:13px">
            <button class="btn" data-b="tpl-save" style="font-size:12.5px;padding:9px 18px">存为模板</button>
          </div>
          <div class="plain" data-b="tpl-note" style="margin-top:10px;font-size:12px;color:var(--ink3)"></div>
        </div>

        <!-- ★ 教练卡（2026-10-07 重做）。
             为什么放在**右栏最前**（而不是"重点练"之前、也不是左栏）：
             这一块是"陪练"的核心价值 —— 一句判断 + 一个具体练法。
             作者反馈「总评太短、不显眼、没有 AI 陪练的感觉」。

             ⚠️ 第一版我把它放在了**左栏**（"总评"卡下面）—— 那是错的：
                左栏是窄列，教练卡跟着排到了页面很下面，一屏根本看不到。
                ⇒ **显眼 = 在右栏最前 + 深色大卡**，两样缺一不可。

             结构上也改了：**结论 / 展开 / 练法三段分开**，
             练法单独用一个色块突出（那是用户唯一真正要照做的事）。 -->
        <div class="ai coach-hero" data-rep-page="coach">
          <div class="coach-top">
            <span class="av">V</span>
            <div class="coach-head">
              <b>教练说</b>
              <s data-b="coach-src">AI Coach</s>
            </div>
            <span class="coach-live" data-b="coach-live" hidden>AI 生成</span>
          </div>
          <div class="coach-main" data-b="coach-body"></div>
          <p class="note" data-b="coach-note"></p>
          <div class="acts">
            <button class="btn solid" data-goto="s2">就按这个再唱一遍 →</button>
            <button class="btn" data-goto="s6">看看成长档案</button>
          </div>
        </div>

<!-- ⬇︎ 六块分项结论：由 bindings.js 的 s3() 按 comments_by_metric 整块重画
     （原来这里是 v7 稿的冻结文案，与后端结论矛盾 —— 见项目记忆 §59.4、§61）⬇︎ -->
        <div class="acc-wrap" data-b="acc-wrap" data-rep-page="decomp"></div>
        <!-- ⬆︎ 到此为止 ⬆︎ -->

        <div class="card tick problems" data-rep-page="problems">
          <h3><span class="t" data-b="problem-title">哪些地方需要再练</span><span class="e">Practice These</span></h3>
          <div data-b="problem-list"></div>
          <div class="plain" data-b="problem-empty" hidden
               style="font-size:12.5px;color:var(--ink3)">
            这一次没有检出成段的问题 —— 音准和节奏都在容差内。
          </div>
        </div>
      </div>

      </div>
`;

export function render(root) {
  root.innerHTML = HTML;
}

/** 渲染后接线（数据源在 bindings.js 里 import，本文件不碰数据）。 */
export async function mount(root, ctx) {
  const { applyBindings } = await import('../bindings.js');
  return applyBindings('s3', root, ctx);
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
  const sec = document.getElementById('s3');
  const fn = sec && sec._unmount;
  if (typeof fn === 'function') return fn();
  return undefined;
}
