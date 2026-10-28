/**
 * 第 07 屏 · 设置
 *
 * ⚠️ **本文件由 `tools/split_v7.py` 自动生成，内容逐字搬自
 *    `docs/mockups/ui_mockup_v7_ljfh.html`。不要在这里手写逻辑 ——
 *    重跑一次生成就会覆盖，而且不会报错，只是悄悄丢。**
 *
 * 数据绑定请改 `web/js/bindings.js`（那里是手写文件）。
 */
export const id = 's7';
export const no = '07';
export const title = '设置';

const HTML = `
<div class="head">
      <div class="ghost">07</div>
      <div class="eyebrow"><svg class="ast"><use href="#d-dia"/></svg>Section 07 — Settings</div>
      <h1>设置</h1>
      <p class="sub">大多数情况下你不需要动这里 —— 但如果系统"听不清"你的某些音，来这一页调一下就好。</p>
      <div class="ruler"></div>
      <div class="ruler-lbl">Vocal Range · Recording · Privacy</div>
    </div>

    <div class="mode-tabs settings-tabs" role="tablist" aria-label="设置分组">
      <button class="mtab on" type="button" role="tab" data-settings-tab="common" aria-selected="true">常用</button>
      <button class="mtab" type="button" role="tab" data-settings-tab="feedback" aria-selected="false">反馈</button>
      <button class="mtab" type="button" role="tab" data-settings-tab="privacy" aria-selected="false">数据与隐私</button>
      <button class="mtab" type="button" role="tab" data-settings-tab="about" aria-selected="false">关于与演示</button>
    </div>

      <!-- ★ AI 陪练（2026-10-07，独立第一张卡）。
           作者反馈「配置 key 的地方也不显眼，没有 AI 陪练的感觉」——
           原来它缩在「反馈方式」里当一行 setrow。
           ⇒ 提成**独立卡片 + 放在设置页最前**：它是"AI 陪练"的总开关，
              而设置页别的东西（音域、录音、隐私）都是常规配置。 -->
      <div class="card tick" data-settings-group="feedback" style="margin-bottom:14px">
        <h3><span class="t">AI 陪练</span><span class="e">AI Coach</span></h3>
        <div class="setrow" style="display:block">
          <div class="info" style="margin-bottom:12px">
            <b>配一个 API key，总评就由模型来写</b>
            <s>不配也能用 —— 会退回规则模板，只是措辞没模型那么灵活、
               也不会帮你判断"这一遍最该改哪一件事"</s>
          </div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <input data-b="llm-key-input" aria-label="大模型 API 密钥" type="password" autocomplete="off"
                   spellcheck="false" placeholder="sk-…（粘贴你的 key）"
                   style="flex:1;min-width:240px;font-family:var(--mono);
                          font-size:12.5px;padding:11px 14px;
                          border:1px solid var(--line2);border-radius:6px;
                          background:var(--panel);color:var(--ink)">
            <button class="btn solid" data-b="llm-key-save"
                    style="font-size:12.5px;padding:11px 22px">保存并测试</button>
            <button class="btn" data-b="llm-key-clear"
                    style="font-size:12.5px;padding:11px 16px">清除</button>
          </div>
          <!-- 状态行：**任何时刻都要说清"现在走的是哪条路"** ——
               "配了但没生效"与"没配"在界面上必须长得不一样 -->
          <div data-b="llm-key-status" class="plain"
               style="margin-top:12px;font-size:12.5px;color:var(--ink3)">正在读取…</div>
        </div>
      </div>

    <div class="card tick" data-settings-group="common" style="margin-bottom:14px">
      <h3><span class="t">演唱者</span><span class="e">Singer</span></h3>
      <div data-b="singer-body">正在读取演唱者…</div>
    </div>

    <div class="card tick" data-settings-group="common" style="margin-bottom:14px">
      <h3><span class="t">你的音域</span><span class="e">Vocal Range</span></h3>
      <div class="seg" id="rangeSeg">
        <button data-lo="40" data-hi="64">男低音</button>
        <button data-lo="43" data-hi="76">男中音</button>
        <button data-lo="48" data-hi="84" class="on">男高音</button>
        <button data-lo="53" data-hi="74">女低音</button>
        <button data-lo="57" data-hi="84">女高音</button>
        <button data-lo="40" data-hi="84">自动检测</button>
      </div>
      <div class="rangeviz">
        <div class="keys" id="rangeBar">
          <div class="band" id="rangeBand"></div>
        </div>
        <div class="lbl"><span>E2</span><span>C3</span><span>C4</span><span>C5</span><span>C6</span></div>
        <div class="note" id="rangeNote" data-b="range-note">
          当前是<b>男高音 C3–C6</b>。如果你唱比较低的歌时系统提示"听不清低音"，换成<b>男中音</b>或<b>自动检测</b>就好了。
        </div>
      </div>
      <div class="gate" style="margin-top:16px;margin-bottom:0">
        <span class="mk"><svg class="ic" style="width:18px;height:18px;stroke-width:1.8"><use href="#i-warn"/></svg></span>
        <span class="tx">
          <b>音域设窄了会怎样？</b>
          低于下限的音会被"削平"成一个假音高（我们叫<b>边界饱和</b>），分数就不准了。
          系统<b>不会替你改设置</b>；唱低音时如果听不清，把这里换成
          <b>男中音</b>或<b>自动检测</b>再试。
        </span>
        <span class="code">RANGE</span>
      </div>
    </div>

    <div class="card tick" data-settings-group="common" style="margin-bottom:14px">
      <h3><span class="t">录音</span><span class="e">Recording</span></h3>
      <div class="setrow">
        <div class="info"><b>麦克风</b><s data-b="mic-note">如果有多个输入设备，选一个你觉得声音最清楚的</s></div>
        <div class="ctl">
          <div class="seg" data-b="mic-seg"></div>
        </div>
      </div>
      <div class="setrow">
        <div class="info"><b>采样率</b><s>固定在 22050 Hz。音高检测不需要更高采样率，提高只会让分析变慢</s></div>
        <div class="ctl"><span class="mono" style="font-size:12px;color:var(--ink3)">22050 Hz</span></div>
      </div>
      <div class="setrow">
        <div class="info"><b>伴奏处理</b><s>跟着伴奏唱时，伴奏的贝斯和鼓会干扰音高检测。开启后会先做谐波分离再分析</s></div>
        <div class="ctl"><button class="sw2 on" data-b="cfg-accompaniment" aria-label="伴奏处理开关" onclick="this.classList.toggle('on')"><i></i></button></div>
      </div>
    </div>

    <div class="card tick" data-settings-group="feedback" style="margin-bottom:14px">
      <h3><span class="t">反馈方式</span><span class="e">Feedback</span></h3>
      <div class="setrow">
        <div class="info"><b>AI 文字点评</b><s>在报告里生成一段像老师说话的建议。关闭后只有图表和数字</s></div>
        <div class="ctl"><button class="sw2 on" data-b="cfg-ai-comment" aria-label="智能点评开关" onclick="this.classList.toggle('on')"><i></i></button></div>
      </div>

      <div class="setrow">
        <div class="info"><b>显示专业术语</b><s>在报告里显示 dB、Hz、rubato 这类专业说法（默认关闭）</s></div>
        <div class="ctl"><button class="sw2" id="proSw2" data-b="cfg-jargon" aria-label="专业术语模式开关" onclick="togglePro(this)"><i></i></button></div>
      </div>
      <div class="setrow" style="flex-wrap:wrap">
        <div class="info"><b>引导音轨音量</b><s>4 拍预备和旋律的音量；不影响麦克风，也不影响评分</s></div>
        <div class="ctl">
          <input type="range" data-b="cfg-guide-volume" min="0" max="1" step="0.05"
                 aria-label="引导音轨音量" style="width:120px">
          <span class="mono" data-b="cfg-guide-volume-val" style="font-size:11px;color:var(--ink3)">80%</span>
        </div>
        <div class="plain" data-b="cfg-guide-volume-note" style="flex:1 1 100%;font-size:11.5px;color:var(--ink3)"></div>
      </div>
    </div>

    <!-- ★ 2026-10-19：评分宽容度。只影响练习计划/微练习的**目标线**，
         不改六指标原始分和时间轴；让用户自己选“考试严”还是“保留个人处理”。 -->
    <div class="card tick" data-settings-group="feedback" style="margin-bottom:14px">
      <h3><span class="t">评分宽容度</span><span class="e">Tolerance</span></h3>
      <div class="setrow" style="flex-wrap:wrap">
        <div class="info"><b>风格容忍</b><s>严格 = 考试/比赛；标准 = 日常练习；宽松 = 保留更多个人处理。只改变“提示与过关线”，不改原始分</s></div>
        <div class="seg" data-b="cfg-style-tolerance" role="group" aria-label="评分宽容度">
          <button type="button" data-style="strict" aria-pressed="false">严格</button>
          <button type="button" data-style="standard" aria-pressed="true" class="on">标准</button>
          <button type="button" data-style="expressive" aria-pressed="false">宽松</button>
        </div>
      </div>
      <div class="plain" data-b="cfg-style-note" style="font-size:11.5px;color:var(--ink3);line-height:1.8"></div>
    </div>

    <!-- ★ 2026-10-19：AI 人声（女声绮萱 / 男声 Alemon / 关闭）。
         「听整句」旁会出现「AI 唱」；不影响评分，只影响试听音色。 -->
    <div class="card tick" data-settings-group="feedback" style="margin-bottom:14px">
      <h3><span class="t">AI 人声</span><span class="e">AI Voice</span></h3>
      <div class="setrow" style="flex-wrap:wrap">
        <div class="info"><b>试听音色</b><s>报告里「听整句」旁的「AI 唱」用哪个声库；本地 DiffSinger 合成，约 5 秒一句，不影响评分</s></div>
        <div class="seg" data-b="cfg-ai-voice" role="group" aria-label="AI 人声">
          <button type="button" data-ai-voice="female" aria-pressed="true" class="on">女声 · 绮萱</button>
          <button type="button" data-ai-voice="male" aria-pressed="false">男声 · Alemon</button>
          <button type="button" data-ai-voice="off" aria-pressed="false">关闭</button>
        </div>
      </div>
      <div class="plain" data-b="cfg-ai-note" style="font-size:11.5px;color:var(--ink3);line-height:1.8"></div>
    </div>

    <div class="card tick" data-settings-group="privacy" style="margin-bottom:14px">
      <h3><span class="t">数据与隐私</span><span class="e">Privacy</span></h3>
      <div class="setrow">
        <div class="info"><b>录音保存位置</b><s>录音文件只存在本机，不上传；AI 点评会发送报告文本，见下一行</s></div>
        <div class="ctl"><span class="mono" data-b="privacy-dir" style="font-size:11px;color:var(--ink3)">./data/recordings/sessions/</span></div>
      </div>
      <div class="setrow">
        <div class="info"><b>AI 点评数据</b><s>录音不上传；报告指标、问题片段、歌词和自述偏好会发给 LLM（DeepSeek 或部署时配置的服务）生成指导。无 key / 断网时降级为本地规则版</s></div>
        <div class="ctl"><span class="mono" style="font-size:11px;color:var(--ink3)">text only</span></div>
      </div>
      <div class="setrow">
        <div class="info"><b>已占用空间</b><s data-b="privacy-detail">共 24 条录音，最早一条 2026-08-29</s></div>
        <div class="ctl">
          <span class="mono" data-b="privacy-size" style="font-size:12px;color:var(--ink2)">18.4 MB</span>
          <button class="btn" data-b="export-btn" style="font-size:12px;padding:7px 16px">导出</button>
        </div>
      </div>
      <div class="setrow">
        <div class="info"><b>清空我的练习录音与记录</b><s data-b="purge-detail">这会删掉你的全部练习历史，无法恢复。成长档案里的进步曲线也会一起清空</s></div>
        <div class="ctl"><button class="btn danger" data-b="purge-btn" style="font-size:12px;padding:7px 16px">清空</button></div>
      </div>
    </div>

    <div class="card tick" data-settings-group="about">
      <h3><span class="t">关于</span><span class="e">About</span></h3>
      <div class="setrow">
        <div class="info"><b>SingLens v0.7.0</b><s>毕业设计作品 · 演唱 AI 陪练：音准节奏与表现力评分系统</s></div>
      </div>
      <div class="setrow">
        <div class="info"><b>用到的技术</b><s>Python 3.11 · librosa 0.11（YIN / PYIN 音高估计）· numpy · scipy · FastAPI · Web Audio API · SQLite</s></div>
      </div>
      <div class="setrow">
        <div class="info"><b>不需要 GPU</b><s>全部运算在普通笔记本 CPU 上完成。实时管线 0.004× 实时，精算管线 0.21× 实时</s></div>
      </div>
      <div class="setrow demo-row">
        <div class="info"><b>演示与内部页面</b><s>这两个不是用户功能：实验数据给答辩看，界面状态是设计稿。已从主菜单收起。</s></div>
        <div class="ctl">
          <button class="pbtn" type="button" data-goto="s6">实验数据</button>
          <button class="pbtn" type="button" data-goto="s8">界面状态</button>
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
  return applyBindings('s7', root, ctx);
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
  const sec = document.getElementById('s7');
  const fn = sec && sec._unmount;
  if (typeof fn === 'function') return fn();
  return undefined;
}
