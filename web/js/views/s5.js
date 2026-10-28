/**
 * 第 05 屏 · 成长档案
 *
 * ⚠️ **本文件由 `tools/split_v7.py` 自动生成，内容逐字搬自
 *    `docs/mockups/ui_mockup_v7_ljfh.html`。不要在这里手写逻辑 ——
 *    重跑一次生成就会覆盖，而且不会报错，只是悄悄丢。**
 *
 * 数据绑定请改 `web/js/bindings.js`（那里是手写文件）。
 */
export const id = 's5';
export const no = '05';
export const title = '成长档案';

const HTML = `
<div class="head">
      <div class="ghost">05</div>
      <div class="eyebrow"><svg class="ast"><use href="#d-dia"/></svg>Section 05 — Progress</div>
      <h1>这几遍<br><span class="hollow">有没有变好</span></h1>
      <p class="sub">每一次练完都会存下来。不只看今天的分数，更看这条线有没有往上走。</p>
      <div class="ruler"></div>
      <div class="ruler-lbl" data-b="prog-meta">4 Weeks · 12 Sessions</div>
    </div>

    <!-- P0 用户画像：用户自述 + 练习中稳定出现的倾向，不是声部/人格鉴定。 -->
    <section class="card tick portrait" data-b="portrait-card">
      <div class="portrait-plate">
        <div class="portrait-plate-top">05 / PORTRAIT</div>
        <div class="portrait-plate-count" data-b="portrait-plate-count">—</div>
        <div class="portrait-plate-unit">次练习</div>
        <div class="portrait-plate-meta" data-b="portrait-plate-meta">—</div>
        <div class="portrait-plate-conf" data-b="portrait-plate-conf">—</div>
        <div class="portrait-plate-foot">VOICE<br>PROFILE</div>
      </div>

      <div class="portrait-main">
      <div class="portrait-head">
        <div class="portrait-id">
          <h3><span class="t">我的练习画像</span></h3>
          <div class="portrait-meta" data-b="portrait-meta">还在了解你</div>
        </div>
        <div class="portrait-actions">
          <button class="btn" type="button" data-b="portrait-ask-v">让 V 说说</button>
          <button class="pbtn" type="button" data-b="portrait-edit">编辑</button>
        </div>
      </div>

      <div class="portrait-v" data-b="portrait-narrative"></div>
      <div class="portrait-v-actions"><span class="portrait-v-msg" data-b="portrait-ask-msg"></span></div>

      <div class="portrait-stated" data-b="portrait-stated"></div>
      <div class="portrait-signals" data-b="portrait-signals"></div>

      <div class="portrait-empty" data-b="portrait-empty" hidden></div>

      <details class="portrait-evidence">
        <summary><span>查看全部证据</span><s>7 项指标 · n · 置信度</s></summary>
        <div class="portrait-evidence-body">
          <div class="portrait-block">
            <div class="portrait-k">练习里稳定出现的倾向 <s>OBSERVED</s></div>
            <div data-b="portrait-observed" class="portrait-observed"></div>
            <div data-b="portrait-trends" class="portrait-trends"></div>
          </div>
          <div data-b="portrait-notes" class="portrait-notes"></div>
          <div class="portrait-foot" data-b="portrait-foot"></div>
        </div>
      </details>

      <form class="portrait-form" data-b="portrait-form" hidden>
        <div class="portrait-form-row">
          <label>目标
            <input data-b="portrait-goal" type="text" maxlength="120"
              placeholder="例如：想更稳、更贴原唱">
          </label>
          <label>经验
            <select data-b="portrait-exp">
              <option value="">不填</option>
              <option value="刚开始">刚开始</option>
              <option value="偶尔唱">偶尔唱</option>
              <option value="有基础">有基础</option>
            </select>
          </label>
        </div>
        <div class="portrait-form-row">
          <label>喜欢的唱法
            <input data-b="portrait-preferred" type="text"
              placeholder="例如：克制抒情, 气声（用逗号分隔）">
          </label>
          <label>不想要的建议
            <input data-b="portrait-dislikes" type="text"
              placeholder="例如：不想降调, 不喜欢假声">
          </label>
        </div>
        <label>常想练的歌
          <input data-b="portrait-songs" type="text"
            placeholder="例如：演员, 其实（用逗号分隔）">
        </label>
        <div class="portrait-form-acts">
          <button class="btn dark" type="submit" data-b="portrait-save">保存画像</button>
          <button class="btn" type="button" data-b="portrait-cancel">取消</button>
          <span class="portrait-form-msg" data-b="portrait-form-msg"></span>
        </div>
      </form>
      </div>
    </section>

    <section class="card tick portrait-next-card">
      <div class="portrait-next-head"><span class="portrait-k">下一步 <s>NEXT</s></span></div>
      <div class="portrait-comfort" data-b="portrait-comfort"></div>
      <div class="portrait-recs" data-b="portrait-recs"></div>

      <div class="comfort-panel" data-b="comfort-panel" hidden>
        <div class="comfort-head">
          <b>舒适音区自测</b>
          <s>SUBJECTIVE · 不是声部/绝对音域</s>
        </div>
        <div class="comfort-body">
          <div class="comfort-note" data-b="comfort-note">—</div>
          <div class="comfort-help" data-b="comfort-help">
            跟着提示音唱「啊」，每个音停两秒，再告诉我这个音舒不舒服。
          </div>
          <div class="comfort-acts">
            <button class="btn" type="button" data-b="comfort-play">▶ 听这个音</button>
            <button class="btn dark" type="button" data-b="comfort-ok">舒服</button>
            <button class="btn" type="button" data-b="comfort-hard">勉强</button>
            <button class="btn" type="button" data-b="comfort-no">不舒服</button>
          </div>
          <div class="comfort-progress" data-b="comfort-progress"></div>
          <div class="comfort-msg" data-b="comfort-msg"></div>
          <button class="pbtn comfort-close" type="button" data-b="comfort-close">先不测了</button>
        </div>
      </div>
    </section>

    <div class="growth">
      <div class="stack">
        <div class="card tick">
          <h3><span class="t">分数曲线</span><span class="e">Progress</span></h3>
          <!-- ★ 2026-10-20：同一个人可能练过很多首。把不同参考的分数连成
               一条"进步曲线"是假结论（小星星 60 分和自建模板 30 分不可比）。
               默认只画练得最多的那一首；想看全部时**只打点、不连线**。 -->
          <div class="prog-pickrow">
            <label for="progPick" data-b="prog-pick-label">看哪一首</label>
            <select id="progPick" data-b="prog-pick" aria-label="选择要看的参考曲目"></select>
          </div>
          <div class="chartwrap"><canvas id="prog"></canvas></div>
          <div class="legend">
            <span><em style="background:#0c0d10"></em>总分</span>
            <span><em style="background:#cf3f2a"></em>情感分（表现力）</span>
            <span><em style="background:#bfc4bf"></em>音准分</span>
          </div>
          <div class="plain" style="margin-top:14px;font-size:13px">
            <span data-b="prog-note">4 周 12 次练习，总分从 <b>61 涨到 78</b>。涨得最明显的是<b>情感分（+22）</b>，音准只涨了 6 分 —— 说明你本来音准就不差，<b>缺的一直是"放开唱"</b>。</span>
          </div>
        </div>

        <div class="card tick">
          <h3><span class="t">历史记录</span><span class="e">History</span></h3>
          <div class="histwrap" data-b="hist"></div>
        </div>
      </div>

      <div class="stack">
        <div class="stylecard">
          <div class="k">Recent Style</div>
          <h4 data-b="style-label">平稳抒情型</h4>
          <p data-b="style-summary">你唱歌时音量变化不大、每个字都是慢慢滑出来的、尾音有轻微自然的抖动。这种唱法温柔耐听，适合慢歌和民谣。</p>
          <div class="chips" data-b="style-chips">
            <span>音色温暖厚实</span>
            <span>起音偏柔</span>
            <span>有自然颤音</span>
            <span>节奏弹性好</span>
          </div>
          <p style="margin-top:16px;color:#ffffff66;font-size:11.5px">
            <span data-b="style-note">这是这几遍的中位数，样本少，先当参考。</span>
          </p>
        </div>

        <div class="card tick">
          <h3><span class="t">这几遍的分项中位数</span><span class="e">Breakdown</span></h3>
          <div class="duo">
            <div class="bd-group">评分项</div>
            <div class="row">
              <div class="lb"><span>音准</span><b class="mono" data-b="bd-pitch">86</b></div>
              <div class="track"><i data-b="bd-pitch-bar" style="width:86%"></i></div>
            </div>
            <div class="row">
              <div class="lb"><span>节奏</span><b class="mono" data-b="bd-rhythm">91</b></div>
              <div class="track"><i data-b="bd-rhythm-bar" style="width:91%"></i></div>
            </div>
            <div class="row">
              <div class="lb"><span>音量起伏</span><b class="mono" data-b="bd-dyn">54</b></div>
              <div class="track"><i class="a" data-b="bd-dyn-bar" style="width:54%"></i></div>
            </div>
            <div class="row">
              <div class="lb"><span>尾音控制</span><b class="mono" data-b="bd-vib">86</b></div>
              <div class="track"><i data-b="bd-vib-bar" style="width:86%"></i></div>
            </div>
            <!-- ★ 2026-10-20：句内推进 / 节奏弹性**不计分**（2026-10-17 起）。
                 原来它们和评分项混在一起叫"强项与短板"，于是"节奏弹性 14"
                 会被当成短板 —— 而那个数恰恰是艺术处理，不扣分。
                 单独分组、单独说明，避免把观察项读成扣分项。 -->
            <div class="bd-group bd-group-na">
              观察项 · 不计分
              <s>量到的东西是真的，但目前没有证据把它们当"唱得好坏"的刻度</s>
            </div>
            <div class="row">
              <div class="lb"><span>句内推进</span><b class="mono" data-b="bd-phrase">62</b></div>
              <div class="track"><i class="a" data-b="bd-phrase-bar" style="width:62%"></i></div>
            </div>
            <div class="row">
              <div class="lb"><span>节奏弹性</span><b class="mono" data-b="bd-rubato">78</b></div>
              <div class="track"><i data-b="bd-rubato-bar" style="width:78%"></i></div>
            </div>
          </div>
          <div class="action" style="margin-top:20px;font-size:13px">
            <span class="do">下一遍先练 →</span>
            <span data-b="bd-action">你唯一明显偏弱的是<b>音量起伏</b>。每天挑一句练"先小声后放开"，两周就能看到变化。</span>
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
  return applyBindings('s5', root, ctx);
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
  const sec = document.getElementById('s5');
  const fn = sec && sec._unmount;
  if (typeof fn === 'function') return fn();
  return undefined;
}
