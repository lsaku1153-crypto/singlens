/**
 * 各屏的数据绑定 —— **B1-3 的接线都集中在这里**。
 *
 * ★ 为什么不写在 `views/sN.js` 里 ★
 *   `views/sN.js` 是 `tools/split_v7.py` **自动生成**的（从 v7 单文件稿切出来）。
 *   在生成物里手写逻辑，下次重跑 split 就被覆盖 —— 而且不会报错，只是悄悄丢。
 *   所以生成物只保留一行 `applyBindings(root, ctx)` 调用，
 *   真正的接线放在这里（手写文件，重跑 split 不受影响）。
 *
 * 数据源：`js/data/index.js`（`?src=mock` 假数据 / `?src=api` 真后端）。
 * 契约：docs/接口契约.md —— 尤其 **null = 本次未评估，显示「—」而不是 0**（§3.2）。
 */
/* ★ G3（2026-10-01）：当前演唱者身份 + 名册。
   身份**只从 identity.js 读写**，名册**只从 roster.js 读** ——
   `sl.singer` 原来在这里和 s1() 里各写一份，那正是"两个真相源"的形状。
   静态 import（而不是函数内 await import）：身份在模块初始化期就要用，
   而这些模块本身不 import bindings.js，不存在循环依赖。 */
import { getSingerId, setSingerId, DEFAULT_SINGER as DEFAULT_SINGER_ID }
  from './data/identity.js';
import { loadRoster, switchSinger, createSinger, renameSinger as renameSingerApi,
         removeSinger } from './data/roster.js';
/* ★ 「显示专业术语」的**唯一开关**（2026-10-01）。
   以前这里没有 import，两个入口各自为政：报告页那个按钮**压根没绑**
   （全仓只有 `shell.js` 读过 `#proBtn` 的 class），设置页那个被 `bindSw`
   覆盖掉内联 `onclick="togglePro(this)"` 之后只剩"切自己的 class + 存后端" ——
   而 `setPro()`（唯一会设置 `body.pro` 的地方）谁都没调。
   结果：`.jargon` 是 `display:none`（要 `body.pro`），**四块专业术语永远看不到**，
   而 `shell.js` 的注释还写着"报告页与设置页同步"。 */
import { setPro, togglePro, setProPersister, veilOn } from './shell.js';

const setText = (root, key, text) => {
  const el = root.querySelector(`[data-b="${key}"]`);
  if (el) el.textContent = text;
};
const setBar = (root, key, pct) => {
  const el = root.querySelector(`[data-b="${key}"]`);
  if (el) el.style.width = Math.max(0, Math.min(100, Number(pct) || 0)) + '%';
};
/** null/undefined → 「—」；数值 → 保留 digits 位 */
const show = (v, digits = 1) =>
  (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(digits);

/* ══════════════════════════════════════════════════════════
 * 01 选择模式
 * ══════════════════════════════════════════════════════════ */
async function s1(root, ctx) {
  const { source } = await import('./data/index.js');
  let boot = null;
  try {
    boot = await source.bootstrap();
  } catch (e) {
    console.warn('[s1] bootstrap 失败，保留原型文案：', e.message);
  }
  const refs = (boot && boot.references) || [];

  // ★ 把【数据源自己声明的演唱者 id】记下来。
  //   为什么必须由 bootstrap 给：api 源用真实名册、mock 源用样例名册，
  //   而 s5 的成长档案要拿这个 id 去查历史/进步曲线。
  //   曾经写死兜底值 'singer_local'，于是成长档案**安静地查了一个不存在的人**：
  //   页面不报错，只是永远显示"还没有练习记录"。
  //
  //  ⚠️⚠️ G3（2026-10-01）这里原来是这样一句：
  //        `sessionStorage.setItem('sl.singer', singers[0].singer_id)`
  //      也就是说**每次进第 01 屏都会把当前演唱者重置成名册里的第一个人**。
  //      只有一个人时看不出问题；有了多用户之后，后果是：
  //      用户切到「同桌」→ 去 s1 选模式 → 回到 s5，看到的是**自己的数据**
  //      （而且他不知道为什么）。这正是 G3 要消灭的那类故障。
  //      现在只在"当前身份不在名册里"时才回退（首次使用 / 那个人已被删除），
  //      回退本身由 roster.js 负责并记录 `healedFrom`，界面会如实说出来。
  const singers = (boot && boot.singers) || [];
  if (!singers.some((s) => s.singer_id === singerId())) {
    // 空名册也要有个落点：保持现状（而不是编一个 id 出来）
    if (singers.length) setSingerId(singers[0].singer_id);
  }

  const hotFoot = root.querySelector('.mode.hot .foot span');
  if (hotFoot && refs.length) {
    hotFoot.textContent = `内置 ${refs.length} 首 · ` +
      refs.map((r) => r.title).join(' / ');
  }

  // 三张模式卡：记住选了哪条路，然后去 s2
  root.querySelectorAll('.mode[data-mode]').forEach((card) => {
    card.style.cursor = 'pointer';
    card.addEventListener('click', () => {
      const mode = card.dataset.mode;
      try {
        sessionStorage.setItem('sl.mode', mode);
        if (mode === 'A' && refs.length) {
          sessionStorage.setItem('sl.ref', refs[0].reference_id);
        }
      } catch { /* 隐私模式可能禁用 storage */ }
      if (ctx && ctx.router && ctx.router.go) ctx.router.go('s2');
      else if (card.dataset.goto) location.hash = '#' + card.dataset.goto;
    });
    /* ★ 2026-10-18：键盘可达。原来这里是 `div`，没有 tabindex / keydown ——
       键盘用户从首页就无法进入练习流程。role/tabindex 在视图里加，
       这里补 Enter/Space 的等价点击。 */
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        card.click();
      }
    });
  });
  // 上传歌曲（模式 E）：点击由 router 的 data-goto 统一处理；
  // 这里只补键盘等价（它不在 .mode 里，模式卡那段选择器覆盖不到）。
  const strip = root.querySelector('.song-strip');
  if (strip) {
    strip.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        strip.click();
      }
    });
  }

  return { booted: !!boot, refs: refs.length };
}

/* ══════════════════════════════════════════════════════════
 * 03 评测报告
 * ══════════════════════════════════════════════════════════ */
/**
 * 取报告；如果后端说「还在算」（409 / CONFLICT）就等，而不是当成"记录没了"。
 *
 * ★ 2026-10-18 审查的头号 bug：
 *   `finish` 后立即跳到 s3，而精算在后台跑；第一次 GET /report 必然撞上
 *   `409 NOT_FOUND`（服务端当时把"未就绪"写成了 NOT_FOUND）。s3 看到
 *   NOT_FOUND 就清掉 `sl.sess` 并写"这条记录已经不在了" —— 用户刚唱完
 *   的那一遍实际已 SCORED，却再也打不开。
 *
 * 这里只认 **CONFLICT** 是"等"，NOT_FOUND 仍然交给上层当"真不存在"。
 * 轮询上限 180 秒与上传路径一致；期间用 veil 告诉用户"正在分析"。
 */
async function fetchReportWithWait(source, uid, metaEl) {
  const t0 = Date.now();
  for (;;) {
    try {
      return await source.getReport(uid);
    } catch (e) {
      const code = e && e.code;
      if (code !== 'CONFLICT') throw e;
      if (metaEl) metaEl.textContent = '正在分析这一遍…（通常几秒，长录音会久一点）';
      if (Date.now() - t0 > 180000) {
        throw new Error('分析超时（超过 3 分钟）—— 这一遍的录音还在，可以稍后重试');
      }
      await new Promise((r) => setTimeout(r, 700));
    }
  }
}

async function s3(root, ctx) {

  /* 2026-10-21 v2：报告改为**分页卡片**。
     一页只显示一组卡片；切页时不做锚点滚动，也不再把 2~3 屏内容堆在一起。
     页面切换器在报告加载前就能用。 */
  initReportPager(root, ctx);

  const { source, isMock } = await import('./data/index.js');
  const charts = await import('./charts.js');
  void isMock;
  const q = new URLSearchParams(location.search);
  let uid = q.get('sess');
  if (!uid) { try { uid = sessionStorage.getItem('sl.sess'); } catch { uid = null; } }
  // ★ mock 模式下没有真实会话，就取 mock 的样例报告。
  //   否则本屏只显示稿子里写死的 78/86/91/62 —— 既不真也不假，
  //   **根本没法验证接线有没有生效**。
  //   （第一次截图就踩了这个：页面看着挺正常，其实一个 data-b 都没填。）
  const isMockNow = isMock;
  const mockFallback = isMockNow && !uid;
  if (mockFallback) uid = 'sess_mock_main';
  // 真实模式下也要有回退：直接打开 #s3 时 URL 和 storage 都是空的，
  // 不回退就会看到原型稿里的死数字（比空态更坏 —— 会被当成自己的成绩）。
  if (!uid && !mockFallback) uid = await currentSession(source);

  const meta = root.querySelector('[data-b="report-meta"]');
  if (!uid) {
    /* ★ 早退路径**必须把稿子里的数字清掉**（2026-09-23，审计 P0-3）。
       原来这里只改了一行说明文字就 return —— 于是屏幕上留着原型稿的
       `78 / 100`、`86`、`91`、`62`，与"还没有评测记录"那句话同时出现。
       混合态比全空危险得多：用户会以为 78 是自己刚唱的那一遍。 */
    blankReport(root, '还没有评测记录 —— 去「开始练习」唱一遍。');
    charts.drawRadar();
    return { bound: false };
  }

  let rep;
  if (meta) meta.textContent = '正在分析这一遍…（通常几秒，长录音会久一点）';
  veilOn(true);
  try {
    rep = await fetchReportWithWait(source, uid, meta);
  } catch (e) {
    /* ★ 这个会话指针不能用了 —— 清掉它，别让"上一条"永远挡住"下一条"
       （2026-10-01）。两种码对应两件不同的事，`NOT_FOUND` 还会把**裸 uid**
       带进 `e.message`（"会话 sess_xxx 不存在"），摆在用户面前没有意义。
       ⚠️ **CONFLICT 不在这里**（2026-10-18）：它表示"还在算"，
          已经被 fetchReportWithWait 消化；只有真正的 NOT_FOUND 才清指针。 */
    if (e && (e.code === 'FORBIDDEN' || e.code === 'NOT_FOUND')) {
      try { sessionStorage.removeItem('sl.sess'); } catch { /* 隐私模式 */ }
      blankReport(root, e.code === 'FORBIDDEN'
        ? '这条记录属于别人 —— 你可能切换过身份。已经清掉过期的会话指针，重唱一遍即可。'
        : '这条记录已经不在了（可能被「清空」删掉了）。已经清掉过期的会话指针，重唱一遍即可。');
      charts.drawRadar();
      return { bound: false, reason: e.code };
    }
    // 精算失败（NO_VOICE / TOO_SHORT / FAILED…）：把**服务端那句人话**原样给用户，
    // 不加"取报告失败"这种会让人以为界面坏了的帽子。
    blankReport(root, e.code === 'NO_VOICE'
      ? e.message
      : `取报告失败：${e.message}`);
    return { bound: false, error: e.message };
  } finally {
    veilOn(false);
  }

  // ★ 报告的 meta 要提到**外层**来取。
  //   踩过的坑：`const m = rep.meta` 原先声明在 `if (meta) {` 块里（那是 DOM 元素
  //   变量，不是元数据），块外再用 `m.reference_title` 直接 `ReferenceError`。
  //   而 `applyBindings` 的 catch 会把它兜住 ⇒ 表现是"卡片显示了、但输入框空着"，
  //   页面上只有一条红色横幅说明原因（那个机制是上一轮加的，这次正靠它定位）。
  const m = rep.meta || {};

  /* ★ 2026-10-23：旧报告也按同一门槛显示。
     旧数据已经落库了 18.6；不重跑分析时，UI 仍应遵守“对齐 <70% 不把逐音节奏
     当结论”的规则。只影响显示，不改数据库；新报告由服务端直接输出 null。 */
  try {
    const al0 = (rep.meta && rep.meta.align) || {};
    const ew0 = (rep.meta && rep.meta.eval_window) || {};
    const ratio0 = typeof al0.align_ratio === 'number' ? al0.align_ratio : null;
    const low0 = ratio0 !== null && ratio0 < 0.70
      && !(rep.meta && rep.meta.self_reference)
      && !(rep.rhythm && rep.rhythm.basis === 'phrase');
    if (low0) {
      const why0 = `对齐率仅 ${(ratio0 * 100).toFixed(1)}%`
        + (al0.aligned && al0.total ? `（${al0.aligned}/${al0.total}）` : '')
        + '，本次节奏与总分不计算';
      if (rep.rhythm) {
        rep.rhythm.score = null;
        rep.rhythm.accuracy = null;
        rep.rhythm.stability = null;
        rep.rhythm.rubato = null;
        rep.rhythm.reason = why0;
      }
      if (rep.ktv) {
        rep.ktv.score = null;
        rep.ktv.rhythm_score = null;
        rep.ktv.rhythm_hit_rate = null;
      }
      rep.total = null;
      rep.meta.eval_window = Object.assign({}, ew0, { rhythm_unassessed: why0 });
    }
  } catch { /* 显示层兜底，不阻断报告 */ }

  // ★ 对齐降级横幅（2026-10-02，B6b）。**放在这里**，而不是混在下面
  //   `if (meta)` 那一大块里 —— 那一块改的是"标题行文字"，
  //   而这条横幅是"整个报告可信度"的说明，两者触发条件也不同。
  //   ⚠️ 必须在 `if (meta)` **之前**调用：即使标题行元素缺失，
  //      降级也一定要说出来（那正是这个洞的教训）。
  renderAlignBanner(root, rep);

  /* ★ 删掉这一次（2026-10-15，审计发现没有"删单条"）。
     放在这里而不是列表页：**用户正看着这一次**，这是他会想删的地方。
     ⚠️ 只有**真实源 + 有会话号 + 库里有记录**时才显示 ——
        mock 源的"练习"是假的，删它没有意义，反而会让人以为真删了。
     ⚠️ 危险动作 ⇒ 确认框，且删完**刷新整屏**（报告已经不存在了）。 */
  const delBtn = root.querySelector('[data-b="del-session"]');
  if (delBtn) {
    const canDel = !isMockNow && !!uid;
    delBtn.hidden = !canDel;
    // 外层那条虚线只在按钮真的可见时出现（否则空态卡片底部留一道虚线）。
    const delRow = root.querySelector('[data-b="danger-row"]');
    if (delRow) delRow.hidden = !canDel;
    if (canDel && !delBtn.dataset.wired) {
      delBtn.dataset.wired = '1';
      delBtn.onclick = async () => {
        if (!window.confirm('删掉这一次练习？\n\n'
            + '报告、分数和这一遍的录音都会删掉，**删了找不回来**。\n'
            + '（实验数据和你的演唱者身份不受影响。）')) return;
        delBtn.disabled = true;
        const old = delBtn.textContent;
        delBtn.textContent = '正在删…';
        try {
          await source.deleteSession(uid, singerId());
          try { sessionStorage.removeItem('sl.sess'); } catch { /* 隐私模式 */ }
          // 报告已经没了 ⇒ 转回"还没有记录"的空态，而不是留一屏数字
          blankReport(root,
            '这一次已经删掉了（报告、分数和录音一起删的；'
            + '实验数据与你的身份没动）。去「开始练习」唱一遍，'
            + '或去成长档案看别的记录。');
          charts.drawRadar();
        } catch (e) {
          delBtn.disabled = false;
          delBtn.textContent = old;
          // 失败必须说清 —— 不然用户以为删掉了，其实还在
          blankReport(root, `删掉这一次失败：${e.message}。它**还在**，可以再试一次。`);
        }
      };
    }
  }

  if (meta) {
    const MODE = { A: '教材曲目', B: '跟唱模板', C: '上传录音' };
    // ★ 这一行必须**短**。踩过的坑：原来拼成
    //   `模式 B · tpl_c341f5b99908 · 2026-09-23T10:26:44`，
    //   撑爆容器**被 CSS 裁掉**（截图里只显示到 "…· sess_158b3"）——
    //   屏幕上不报错，只是信息看不全。
    //   主行只留"模式 + 曲名"，时间与 id 挪到 title 里备查。
    const title = m.reference_title || m.reference_id || '';
    // ★ 2026-10-20：歌曲参考不要写成"跟唱模板" —— 它是上传歌曲分离出来的，
    //   用户需要知道参考从哪来（后面的 ref-source 横幅会说明质量）。
    const kindLabel = (m.reference_source === 'song_vocal')
      ? '歌曲参考' : (MODE[m.mode] || ('模式 ' + (m.mode || '?')));
    meta.textContent = `${kindLabel}` +
      (title ? ` · ${title}` : '');
    const tr = Number(m.transpose) || 0;
    if (tr) meta.textContent += ` · 移调 ${tr > 0 ? '+' : ''}${tr} 半音`;
    meta.title = [m.reference_id && `参考 id：${m.reference_id}`,
                  m.created_at && `时间：${m.created_at}`,
                  rep.session_uid && `会话：${rep.session_uid}`,
                  m.reference_source && `参考来源：${m.reference_source}`,
                  tr && `参考移调：${tr > 0 ? '+' : ''}${tr} 半音`]
      .filter(Boolean).join('\n');
    if (mockFallback) meta.textContent = '[mock 样例] ' + meta.textContent;
    // 模式 B 自比降级：音准/节奏本次不评。屏幕上的字要短，
    // **原因**放进 title —— 用户悬停能看到"为什么没评"，而不是以为唱得差。
    if (m.self_reference) {
      meta.textContent += '　⚠️ 本次不评音准与节奏';
      if (m.self_reference_reason) meta.title += `\n原因：${m.self_reference_reason}`;
    }
    // 只评了一段（参考只覆盖前 N 个音）
    const ew = m.eval_window || {};
    if (ew.phrase_window) {
      meta.textContent += `（只评了 ${ew.notes_used}/${ew.notes_total} 个音）`;
    }
    // N11：带伴奏的录音先做了谐波分离，分数不能当清唱比。
    if (m.accompaniment_mode) {
      meta.textContent += '　⚠️ 本次做过伴奏分离，分数仅供参考';
      meta.title += '\n这次录音含伴奏：系统先做谐波分离再分析，'
        + '分离残留会影响音准/力度，分数不能和清唱直接比。';
    }
    // ★ 2026-10-20：歌曲参考的来源/质量横幅。
    const rsEl = root.querySelector('[data-b="ref-source"]');
    if (rsEl) {
      const q = m.reference_quality || null;
      if (m.reference_source === 'song_vocal' && q) {
        const lv = q.level || 'ok';
        const lvText = { good: '好', ok: '一般', bad: '不足' }[lv] || '一般';
        const song = m.reference_song || {};
        const seg = (typeof song.start_ms === 'number' && typeof song.end_ms === 'number')
          ? ` · 第 ${Math.floor(song.start_ms / 60000)}:${String(Math.floor(song.start_ms / 1000) % 60).padStart(2, '0')}`
            + `–${Math.floor(song.end_ms / 60000)}:${String(Math.floor(song.end_ms / 1000) % 60).padStart(2, '0')} 段`
          : '';
        const leak = (typeof q.leak_db === 'number') ? ` · 原唱残留 ${q.leak_db.toFixed(1)} dB` : '';
        rsEl.hidden = false;
        rsEl.className = `ref-source r-${lv}`;
        rsEl.innerHTML = `<b>参考来自原唱人声（自动分离）</b>`
          + `<span>质量：${lvText}${leak}${seg} · 提取到 ${q.notes || '—'} 个音`
          + `${m.reference_lyrics_count ? ` · ${m.reference_lyrics_count} 行歌词` : ' · 未识别歌词'}</span>`
          + `<s>这不是人工校对的谱：换气、装饰音、和声可能被算成音；`
          + `不满意就换一段，或改用「导入 MIDI」。`
          + `表现力分靠麦克风的动态范围，手机/笔记本的自动增益可能把它压平，`
          + `不一定是唱得平；先看音准、节奏和 KTV。`
          + `${lv === 'bad' ? ' 本次分数仅供参考。' : ''}</s>`
          + `<div class="ref-acts">`
          + `<button class="pbtn" type="button" data-ref-act="vocal">▶ 听原唱</button>`
          + `<button class="pbtn" type="button" data-ref-act="backing">▶ 听伴奏</button>`
          + `</div>`;
        const refId = m.reference_id;
        const playRef = (kind, btn) => {
          if (!refId) return;
          const url = `/api/template/${encodeURIComponent(refId)}/${kind}.wav`
            + `?singer_id=${encodeURIComponent(singerId())}`;
          const old = btn.textContent;
          btn.disabled = true; btn.textContent = '正在加载…';
          const a = new Audio(url);
          a.oncanplay = () => {
            btn.textContent = '▶ 正在播放…';
            a.play().catch((e) => {
              btn.textContent = `⚠️ ${e.message}`;
              setTimeout(() => { btn.textContent = old; btn.disabled = false; }, 2200);
            });
          };
          a.onended = () => { btn.textContent = old; btn.disabled = false; };
          a.onerror = () => {
            btn.textContent = '⚠️ 这条参考没有这段音频';
            setTimeout(() => { btn.textContent = old; btn.disabled = false; }, 2200);
          };
        };
        rsEl.querySelectorAll('[data-ref-act]').forEach((b) => {
          b.onclick = () => playRef(b.dataset.refAct === 'vocal' ? 'vocal' : 'backing', b);
        });
      } else {
        rsEl.hidden = true;
        rsEl.innerHTML = '';
      }
    }
  }

  /* 总分：`<div class="v" data-b="total">78<small> / 100</small></div>`
     ⚠️ 必须用 innerHTML 才能留住那个 `<small> / 100</small>`（2026-09-24）。
        第一版用 `setText` 直接把 textContent 设成 "33.1" ——
        「/ 100」被冲掉了，屏幕上只剩一个孤零零的数字，
        用户不知道满分是多少（原型里是有的）。
        这是"接线把原型的信息改少了"的一类问题：数值对了，语义丢了。 */
  const totEl = root.querySelector('[data-b="total"]');
  if (totEl) {
    /* 2026-10-18：未评估时不再把 88px 的「—」画成一条黑杠；
       直接写「未评估」（小一号），下面那行说明原因。 */
    totEl.innerHTML = rep.total === null
      ? '<span class="na">未评估</span>'
      : `${esc(show(rep.total))}<small> / 100</small>`;
  }
  const dEl = root.querySelector('[data-b="total-delta"]');
  if (dEl) {
    dEl.textContent = rep.total === null
      ? '本次未评估（缺音准或节奏）' : '';
    // 空态时那块 1px 红框会变成一个空的小红方块 —— 收起来。
    dEl.hidden = !dEl.textContent.trim();
  }
  const tbEl = root.querySelector('[data-b="total-basis"]');
  if (tbEl) {
    const basis = (rep.meta || {}).total_basis;
    const isKtv = basis === 'ktv';
    const isPhrase = basis === 'ktv_phrase';
    const strict = (rep.meta || {}).total_strict;
    tbEl.hidden = !(isKtv || isPhrase);
    tbEl.textContent = isPhrase
      ? `整首跟唱 · 音准按 KTV，节奏按句级${typeof strict === 'number'
        ? `（严格加权 ${show(strict)}）` : ''}`
      : isKtv
        ? `整首跟唱 · 按 KTV 容差带计分${typeof strict === 'number'
          ? `（严格加权 ${show(strict)}）` : ''}`
        : '';
  }
  /* KTV 式并列参考分（2026-10-18）：有就显示，没有就收起。
     ★ 2026-10-21：总分/卡片都改走 KTV 容差带；命中率只用来解释，
     不再把"51 音分=0 分"那种断崖直接当分数。 */
  const ktv = rep.ktv;
  const ktvBox = root.querySelector('[data-b="ktv-box"]');
  if (ktvBox) {
    const ok = ktv && typeof ktv.score === 'number' && Number.isFinite(ktv.score);
    ktvBox.hidden = !ok;
    if (ok) {
      setText(root, 'ktv-score', show(ktv.score));
      const bits = [];
      if (typeof ktv.pitch_score === 'number') {
        bits.push(`音准带内分 ${show(ktv.pitch_score)}`
          + (typeof ktv.pitch_hit_rate === 'number'
            ? `（命中 ${show(ktv.pitch_hit_rate)}%）` : ''));
      }
      if (typeof ktv.rhythm_score === 'number') {
        bits.push(`节奏带内分 ${show(ktv.rhythm_score)}`
          + (typeof ktv.rhythm_hit_rate === 'number'
            ? `（命中 ${show(ktv.rhythm_hit_rate)}%）` : ''));
      }
      if (typeof ktv.octave_folded === 'number' && ktv.octave_folded > 0) {
        bits.push(`八度折叠 ${ktv.octave_folded} 音`);
      }
      const pt = ktv.pitch_tolerance_cents || 50;
      const rt = ktv.rhythm_tolerance_ms || 100;
      bits.push(`±${pt} 音分 / ±${rt} ms 内满分，`
        + `到 ${ktv.pitch_band_cents || pt * 2} 音分 / `
        + `${ktv.rhythm_band_ms || rt * 2} ms 线性到 0`);
      setText(root, 'ktv-detail', bits.join(' · '));
      const kh = root.querySelector('[data-b="ktv-hint"]');
      if (kh) {
        const isSongTotal = (rep.meta || {}).total_basis === 'ktv';
        let h = isSongTotal
          ? '整首跟唱的总评按 KTV 容差带算：每个音在容差内满分，'
            + '超出后还有一个缓冲带，不是"差 1 音分就 0 分"。'
            + '上面「唱准了吗 / 跟上拍子」现在也是同一口径，'
            + '不会再出现卡片 0 分、总分另一个数的情况。'
          : 'KTV 分是参考分：带内满分，带外还有缓冲带；'
            + '上面「唱准了吗」还看整遍稳不稳。';
        if (typeof ktv.rhythm_offset_ms === 'number'
            && Math.abs(ktv.rhythm_offset_ms) >= 100) {
          h += `节奏已扣掉整遍起唱延迟 `
            + `${Math.round(ktv.rhythm_offset_ms)} ms。`;
        }
        kh.textContent = h;
      }
    }
  }
  setText(root, 'pitch', show(rep.pitch.score));
  setText(root, 'rhythm', show(rep.rhythm.score));
  setText(root, 'expr', show(rep.expressiveness.score));

  /* ★ 2026-10-24：节奏现在是**句级**时，明确标出来。
     逐音对齐率低不代表句级不可用；反过来，用户也不该把 18.6 的逐音分
     当成句级结论。这里只解释口径，不改分。 */
  const rb = root.querySelector('[data-b="rhythm-basis"]');
  if (rb) {
    const ph = rep.rhythm.phrase
      || ((rep.meta || {}).phrase_rhythm) || null;
    const beat = rep.beat_diagnostic
      || ((rep.meta || {}).beat_diagnostic) || null;
    const bp = rep.beat_practice || null;
    const isPhrase = rep.rhythm.basis === 'phrase' && ph
      && typeof ph.score === 'number';
    const hasBeat = beat && typeof beat.hit_rate === 'number';
    rb.hidden = !(isPhrase || hasBeat);
    let html = '';
    if (isPhrase) {
      const n = ph.phrase_count || 0;
      const ratio = ((rep.meta || {}).align || {}).align_ratio;
      const pct = typeof ratio === 'number' ? Math.round(ratio * 100) : null;
      html = `节奏按 <b>${n} 句</b>估算：句首偏移 + 句长比例 + 句内残差。`
        + (pct !== null && pct < 70
          ? `逐音对齐只有 ${pct}%，只用于定位，不再当节奏结论。`
          : '');
      const rows = Array.isArray(ph.phrases) ? ph.phrases.slice() : [];
      rows.sort((a, b) => (a.score || 0) - (b.score || 0));
      const w = rows[0];
      if (w) {
        const no = (typeof w.line_index === 'number') ? w.line_index + 1 : '?';
        const txt = String(w.text || '').slice(0, 12);
        const bits = [];
        if (typeof w.start_dev_ms === 'number' && Math.abs(w.start_dev_ms) >= 150) {
          bits.push(`句首${w.start_dev_ms > 0 ? '晚' : '早'} `
            + `${(Math.abs(w.start_dev_ms) / 1000).toFixed(2)}s`);
        }
        if (typeof w.duration_ratio === 'number'
            && Math.abs(w.duration_ratio - 1) >= 0.06) {
          bits.push(`句长${w.duration_ratio > 1 ? '拖' : '赶'} `
            + `${Math.round(Math.abs(w.duration_ratio - 1) * 100)}%`);
        }
        if (bits.length) {
          html += `<br>最弱：第 <b>${no}</b> 句`
            + (txt ? `「${esc(txt)}」` : '') + ` ${bits.join('、')}。`;
        }
      }
    }
    if (hasBeat) {
      const phase = Number(beat.phase_ms) || 0;
      const phaseTxt = Math.abs(phase) >= 1000
        ? `${(Math.abs(phase) / 1000).toFixed(1)}s`
        : `${Math.round(Math.abs(phase))}ms`;
      const tol = Math.round(beat.tolerance_ms || 120);
      let line = `节拍诊断（不计分）：扣掉整体偏移后踩中 `
        + `<b>${Number(beat.hit_rate).toFixed(0)}%</b>（±${tol}ms），`
        + `平均${phase >= 0 ? '晚' : '早'} ${phaseTxt}`;
      const bars = Array.isArray(beat.worst_bars) ? beat.worst_bars : [];
      if (bars.length && typeof bars[0].start_ms === 'number') {
        const s = Math.max(0, Math.round(bars[0].start_ms / 1000));
        const mm = String(Math.floor(s / 60)).padStart(2, '0');
        const ss = String(s % 60).padStart(2, '0');
        line += `；最不稳 ${mm}:${ss}`
          + `（${bars[0].hit_beats || 0}/${bars[0].active_beats || 0} 拍）`;
      }
      if (bp && bp.label) line += `；先练 ${bp.label}`;
      html += (html ? '<br>' : '') + line + '。';
    }
    rb.innerHTML = html;
    // G58：最不稳小节有可拆片段时，给一个“只练这几小节”入口。
    // 复用现有微练习管线，只多传 kind=beat；没有片段就不造死按钮。
    if (hasBeat && bp && bp.segment
        && Array.isArray(bp.segment.notes) && bp.segment.notes.length >= 2) {
      const row = document.createElement('div');
      row.className = 'plan-practice';
      row.style.marginTop = '10px';
      const b = document.createElement('button');
      b.className = 'btn dark';
      b.type = 'button';
      b.textContent = '只练这几小节';
      b.title = '只练 ' + (bp.label || '这几拍');
      b.onclick = async () => {
        b.disabled = true;
        const old = b.textContent;
        b.textContent = '正在准备这几小节…';
        try {
          await startPracticeAtIndex(source, uid, [bp], 0, 'beat');
        } catch (e) {
          b.textContent = `⚠️ 打不开：${e.message}`;
          setTimeout(() => { b.textContent = old; b.disabled = false; }, 3000);
        }
      };
      row.appendChild(b);
      rb.appendChild(row);
    }
  }

  /* ★ 2026-10-20：把「分数低扣在哪一半」写在分数下面。
     作者实测：「音准 14.5，但教练说整体偏差只有 -1.2」——
     这两句都对，可用户读起来就是自相矛盾。
     音准分 = 整遍偏移（accuracy）+ 每个音稳不稳（stability），
     只报一个数必然像前后打架。这里只引用已算出的两个量。 */
  const snEl = root.querySelector('[data-b="score-note"]');
  if (snEl) {
    const parts = [];
    const ps = rep.pitch.score;
    const pa = rep.pitch.accuracy;
    const pst = rep.pitch.stability;
    if (typeof ps === 'number') {
      if (typeof pst === 'number' && pst > 60
          && !(typeof pa === 'number' && Math.abs(pa) >= 30)) {
        parts.push(`音准分主要扣在「每个音稳不稳」：上下浮动约 `
          + `<b>${pst.toFixed(0)} 音分</b>；整遍并没有跑调`
          + (typeof pa === 'number' ? `（整体偏移 ${pa.toFixed(1)} 音分）` : '') + '。');
      } else if (typeof pa === 'number' && Math.abs(pa) >= 30) {
        parts.push(`整遍音准偏${pa < 0 ? '低' : '高'}约 <b>${Math.abs(pa).toFixed(0)} 音分</b>`
          + (typeof pst === 'number' ? `（每个音还要再抖 ${pst.toFixed(0)} 音分）` : '') + '。');
      }
    }
    const rs = rep.rhythm.score;
    const rst = rep.rhythm.stability;
    if (rep.rhythm.basis !== 'phrase'
        && typeof rs === 'number' && typeof rst === 'number' && rst > 120) {
      parts.push(`节奏分主要扣在「每个音前后不稳」：残差抖动约 `
        + `<b>${rst.toFixed(0)} 毫秒</b>，不是整遍快慢。`);
    }
    /* ★ 2026-10-20：把"一个离群音把平均分拉垮"说出来。
       实测（假麦克风喂参考音）：6 个音里 5 个偏差 0，最后 1 个 139ms 的
       碎片偏了 420 音分 → 音准 0.0，而 KTV 分 87.3。用户只会认为评分坏了。
       音准分是「平均超出容差的量」，单个坏音确实能一票否决 —— 这是
       现行口径的真实行为，不该藏起来。这里只做**解释**，不改分。 */
    const segs = Array.isArray(rep.segments) ? rep.segments : [];
    const devs = segs.filter((x) => typeof x.pitch_cents_dev === 'number');
    if (devs.length >= 3 && typeof rep.pitch.score === 'number') {
      const pen = (d) => Math.max(0, Math.abs(d) - 25);
      const totalPen = devs.reduce((a, x) => a + pen(x.pitch_cents_dev), 0);
      const worst = devs.reduce((a, x) =>
        Math.abs(x.pitch_cents_dev) > Math.abs(a.pitch_cents_dev) ? x : a, devs[0]);
      const share = totalPen > 0 ? pen(worst.pitch_cents_dev) / totalPen : 0;
      if (share >= 0.5 && Math.abs(worst.pitch_cents_dev) >= 100) {
        const dur = Math.max(0, (worst.end_ms || 0) - (worst.start_ms || 0));
        parts.push(`其中第 ${worst.note_index + 1} 个音偏了 `
          + `<b>${Math.abs(worst.pitch_cents_dev).toFixed(0)} 音分</b>`
          + (dur > 0 && dur < 300 ? `（只唱了 ${Math.round(dur)} 毫秒）` : '')
          + `，一个音就占了音准扣分的约 ${Math.round(share * 100)}%；`
          + `KTV 分按时长加权，不会被这一个音主导。`);
      }
    }
    /* ★ 2026-10-21：KTV 口径下卡片分已经不是"平均超出容差"，
       上面那段"音准主要扣在稳定性"会和新卡片打架。这里直接用
       KTV 的两个带内分和命中率解释，别再拿严格分说事。 */
    if ((rep.meta || {}).total_basis === 'ktv' && ktv) {
      const kp = typeof ktv.pitch_score === 'number' ? ktv.pitch_score : null;
      const kr = typeof ktv.rhythm_score === 'number' ? ktv.rhythm_score : null;
      const ph = typeof ktv.pitch_hit_rate === 'number' ? ktv.pitch_hit_rate : null;
      const rh = typeof ktv.rhythm_hit_rate === 'number' ? ktv.rhythm_hit_rate : null;
      const pt = ktv.pitch_tolerance_cents || 50;
      const rt = ktv.rhythm_tolerance_ms || 100;
      const p2 = ktv.pitch_band_cents || pt * 2;
      const r2 = ktv.rhythm_band_ms || rt * 2;
      const bits = [];
      if (kp !== null) {
        bits.push(`音准带内分 <b>${show(kp)}</b>`
          + (ph !== null ? `（命中 ${show(ph)}%）` : ''));
      }
      if (kr !== null) {
        bits.push(`节奏带内分 <b>${show(kr)}</b>`
          + (rh !== null ? `（命中 ${show(rh)}%）` : ''));
      }
      let s = `这张卡和总分同一口径：${bits.join('、')}。`
        + `音准 ±${pt} 音分内满分，到 ${p2} 音分线性到 0；`
        + `节奏先扣掉整遍起唱延迟，±${rt} ms 内满分，到 ${r2} ms 线性到 0；`
        + `按音长加权、八度折叠。不是要求每个音严丝合缝。`;
      if (typeof rep.rhythm.stability === 'number'
          && rep.rhythm.stability > 120) {
        s += `你这次每个音前后抖动约 ${rep.rhythm.stability.toFixed(0)} ms，`
          + `节奏是主要扣分项；想更接近 KTV 的宽容度，可在设置里切「宽松」`
          + `（±75 音分 / ±180 ms）。`;
      }
      parts.length = 0;
      parts.push(s);
    }
    snEl.hidden = !parts.length;
    if (parts.length) snEl.innerHTML = parts.join(' ');
  }

  /* 拆解条：数值是可读的偏差量，条宽用对应分数。
     ⚠️ **必须带单位**（2026-09-24 加）：
       这四个数是"偏差量"而不是分数 —— pitch.accuracy 是**音分**，
       rhythm.accuracy/stability/rubato 是**毫秒**。原型里只写光秃秃的数字，
       而实测有一遍的 `rhythm.accuracy` 是 **3776**（比参考慢了 42% 的那一遍）。
       屏幕上写着「有没有整体抢拍/拖拍　3776」而条宽是 0% ——
       读者只能把 3776 当成"分数"，于是"这项满分"和"这项零分"同时出现。
       契约 §2.2 里这些字段本来就带单位，界面上不该丢掉。 */
  const num = (v, digits, unit) =>
    (v === null || v === undefined || Number.isNaN(v)) ? '—'
      : `${Number(v).toFixed(digits)}${unit ? ' ' + unit : ''}`;
  setText(root, 'pitch-acc', num(rep.pitch.accuracy, 1, '音分'));
  setBar(root, 'pitch-acc-bar', rep.pitch.score);
  setText(root, 'pitch-stab', num(rep.pitch.stability, 1, '音分'));
  setBar(root, 'pitch-stab-bar', rep.pitch.score);
  setText(root, 'rhythm-acc', num(rep.rhythm.accuracy, 0, 'ms'));
  setBar(root, 'rhythm-acc-bar', rep.rhythm.score);
  setText(root, 'rhythm-stab', num(rep.rhythm.stability, 0, 'ms'));
  setBar(root, 'rhythm-stab-bar', rep.rhythm.score);
  setText(root, 'rubato', num(rep.rhythm.rubato, 0, 'ms'));
  setBar(root, 'rubato-bar', rep.rhythm.rubato);

  /* ★ 「有没有跟住拍子」必须说出来（2026-09-23，§53）。
     背景：节奏分里一半是「准确度」= |onset 偏差均值|，上限 120ms ——
     它衡量的是**绝对时刻对得上参考**。清唱没有外部拍子时，
     用户按自己的速度走，这个量就变成"你选的速度"，不是"你的节奏感"：
     实测三段清唱素材分别比参考慢 22% / 10% / 28%，于是准确度分直接 0。

     产品决定（用户在 2026-09-23 确认）：**要求戴耳机跟伴奏/节拍器唱**，
     即承认"要有一个外部拍子才谈得上节奏分"。
     所以这里不再沉默地扣分，而是把速度差**明确报出来** ——
     用户看到"你比参考慢了 22%"，就知道自己没跟住，而不是以为节奏感差。

     ⚠️ 这只是**反馈**，不改评分（rubric 一个字没动）。 */
  const tr = rep.rhythm.tempo_ratio;
  const tEl = root.querySelector('[data-b="tempo-note"]');
  if (tEl) {
    if (typeof tr !== 'number' || !isFinite(tr)) {
      tEl.textContent = '';
      tEl.style.display = 'none';
    } else {
      /* ⚠️ 方向别搞反（第一版就反了，截图/查 DOM 才发现）。
         `tempo_ratio` 是 **时长比**（演唱用时 / 参考用时）：
           ratio 1.27 → 唱一遍花了参考的 1.27 倍时间 → **慢了**
         而人话里的"快慢百分之几"说的是**速度**，速度 = 1 / 时长比。
         第一版直接 `(tr-1)*100` 得到 +27% 再按正负判"快"，于是
         一个慢了 27% 的录音被写成"你比参考快了 27%" —— **方向完全相反**。
         正确：speed = 1/tr，速度差 = (1/tr − 1)×100，负号 = 慢。 */
      const speed = 1 / tr;
      const pct = (speed - 1) * 100;             // 负 = 比参考慢
      tEl.style.display = '';
      if (Math.abs(pct) <= 10) {
        // 「差 -6%」这种写法虽然没错，但读起来要脑子转一下。
        // 直接说"比参考慢 6%（在容差内）"更省事，方向也明确。
        tEl.textContent = Math.abs(pct) < 1
          ? '速度与参考一致。'
          : `速度比参考${pct < 0 ? '慢' : '快'} ${Math.abs(pct).toFixed(0)}%（在容差内）。`;
        tEl.style.color = 'var(--good)';
      } else {
        // 用 innerHTML 才能加粗；也顺带说明"这正是节奏分低的原因"
        tEl.innerHTML =
          `⚠️ 你比参考<b>${pct < 0 ? '慢' : '快'}了 ${Math.abs(pct).toFixed(0)}%</b>` +
          `（速度比为参考的 ${speed.toFixed(2)} 倍）。` +
          `节奏分里有一半是「有没有踩在拍子上」，所以这样会被扣分 —— ` +
          `下一遍请<b>戴耳机放原曲（或开节拍器）跟着唱</b>，麦克风只收你的人声。`;
        /* ★ C14（2026-10-15）：**换气也要算进去**。
           唱歌是要换气的：每句末尾会插进 ~0.5–1 秒静音，6 句就累积 ~3.5 秒。
           而 `tempo_ratio` 量的是**总时长** ⇒ 如果这个参考**没有换气位**
           （`meta.reference_max_gap_ms === 0`），那"你慢了 17%"里
           有一部分根本不是没跟拍子，而是**换气**。

           ⚠️ **只在参考确实没有换气位时才说**：
              有换气位的参考（如 `REF_TWINKLE_6B`，句间 700ms）已经把换气
              算进参考时长了 ⇒ 那时再提换气就是**替用户找借口**。
           措辞留余地（"有可能会偏高"），因为这是**推断**不是结论。 */
        const gapMs = Number((rep.meta || {}).reference_max_gap_ms) || 0;
        if (pct < 0 && gapMs === 0) {
          tEl.innerHTML +=
            `<br>顺带说一句：如果这个参考<b>每句之间没有留换气的时间</b>，` +
            `那上面这个百分比**有可能会偏高** —— 换气也是要花时间的，` +
            `而那不算你没跟住拍子。`;
        }
        tEl.style.color = 'var(--acc-ink)';
      }
    }
  }

  // 雷达图：六维归一化值（契约 §2.2 expressiveness.normalized）
  // ★ null 要**原样传下去**，不能填 0（2026-09-23，审计 P0-1 的连带问题）。
  //   第一版写的是 `typeof nz[k] === 'number' ? nz[k] : 0` —— 于是"颤音这次没算出来"
  //   在图上变成一个**顶点塌到圆心**的尖角，标签还被染成红色（`vals[i]<0.65`）。
  //   用户的解读必然是"我颤音 0 分"。契约 §3.2：**null 就是"本次未评估"**。
  const nz = rep.expressiveness.normalized || {};
  const order = ['dynamic_range', 'phrase_dynamics', 'rubato',
                 'vibrato', 'onset_style', 'brightness'];
  /* U2：历史样本中位参照环。只有至少 3 次练习才画，且六轴都必须有值；
     数据来自 /profile 的 normalized 中位数（不是前端另算一套）。 */
  let medVals = null;
  try {
    const prof = await source.getProfile(singerId());
    if (prof && (prof.session_count || 0) >= 3) {
      const pn = prof.normalized || {};
      const arr = order.map((k) => (
        typeof pn[k] === 'number' && isFinite(pn[k]) ? pn[k] : null));
      if (arr.every((v) => v !== null)) medVals = arr;
    }
  } catch { /* 画像读不到就不画参照环，不耽误报告 */ }
  charts.drawRadar({
    vals: order.map((k) => (typeof nz[k] === 'number' ? nz[k] : null)),
    median: medVals,
  });
  const rcap = root.querySelector('.radar-cap');
  if (rcap) {
    rcap.textContent = medVals
      ? 'Six Dimensions · 归一化 0–1 · 虚线 = 历史中位'
      : 'Six Dimensions · 归一化 0–1';
  }

  /* ── 六块「分项结论」整块重画（2026-09-23，审计 P0-1）───────────
     背景：这六块以前是 v7 稿的**冻结文案**（一个 data-b 都没有），
     而且与后端算出来的 `comments_by_metric` 互相矛盾：
     UI 说颤音「很好」，后端说「本次未评估」；UI 说音色「温暖厚实」，后端说「清亮」。
     同一个页面上两套结论，用户信哪一套都是错的。

     契约 §5.1：**分项用模板（`comments_by_metric`），总评才用 LLM（`comments`）**。

     ⚠️ 为什么"整块重画"而不是"给 30 个文案加 data-b"：
        六块 × 五处文案 = 30 个 key，顺序错一处就会串
        （"音量"的结论跑到"颤音"下面），**串了不报错、看着还很正常**。
        整块重画只有一个出错点（数据源的键名）。

     ⚠️ 顺序：**先重画（生出 canvas）、再画证据曲线**。
        反过来的话 `drawEvidence` 找不到任何 `canvas.ev` ——
        页面不报错，只是三张证据图全空着。 */
  renderAccBlocks(root, rep);
  renderProblems(root, rep);
  renderCoach(root, rep);
  // ★ 练习计划（B1）：放在最后，因为它是**收敛**层 ——
  //   上面三块是"这一遍发生了什么"，这一块是"下一遍只改哪一件事"。
  renderPracticePlan(root, rep, source, uid);

  /* 证据曲线：三块里的 canvas 各自只认自己那段数据（`data-k`）。
     ⚠️ 三个 `<details>` 里只有第一块是 `open` 的 —— 关着的 `<details>`
        内容 **clientWidth 为 0**，canvas 画不出来（v7 原型就有这个毛病：
        展开颤音那一块看到的是空白）。所以展开时补画一次。 */
  const drawEv = () => {
    try {
      charts.drawEvidence(rep.evidence || {});
    } catch (e) {
      console.warn('[s3] drawEvidence 失败：', e.message);
    }
  };
  drawEv();
  root.querySelectorAll('details.acc').forEach((d) => {
    d.addEventListener('toggle', () => { if (d.open) drawEv(); });
  });
  /* 分页后，证据图第一次绘制时所在页是 display:none，canvas 宽度为 0。
     切到「拆解」页后补画一次；rAF 保证布局已经可见。 */
  const onReportPage = (e) => {
    if (!e.detail || e.detail.page !== 'decomp') return;
    requestAnimationFrame(() => drawEv());
  };
  window.addEventListener('sl:report-page', onReportPage);
  if (ctx && typeof ctx.onUnmount === 'function') {
    ctx.onUnmount(() => window.removeEventListener('sl:report-page', onReportPage));
  }

  /* ── 「存为跟唱模板」（G4-a）─────────────────────────────
     为什么放在报告页：模式 B 的第一遍 = "先随便唱一遍当样板"。
     用户唱完看到的第一个东西就是这份报告，让他在这儿决定"这一遍当模板"，
     是唯一不用额外导航的时机。

     ⚠️ 只在这一遍**有音符**时才该出现 —— 没有音符就提炼不出模板。
        判据是报告里的 segments 非空，不是猜。 */
  const tplCard = root.querySelector('[data-b="tpl-card"]');
  if (tplCard) {
    const nSeg = (rep.segments || []).length;
    tplCard.hidden = !nSeg;
    if (nSeg) {
      const nameEl = root.querySelector('[data-b="tpl-name"]');
      const btn = root.querySelector('[data-b="tpl-save"]');
      if (nameEl) {
        /* ⚠️ 两个坑，都踩过：
           ① `sessionStorage.getItem(k)` 在键**不存在**时返回 `null`，
              而 `null || ''` 是 `''` —— 看着没问题，但 `String(null)` 是
              **字符串 'null'**（truthy）。第一版把 `'null'` 填进了输入框，
              用户看到框里写着 "null"，还能直接存成一个叫 null 的模板。
           ② 设 `el.value = x` 只改 DOM 属性，不改 HTML 属性 ——
              而 `tools/probe_frontend.py` / `_verify_tpl_name_relay.py`
              用 `--dump-dom` 读的是 HTML 属性，于是探针看到的是空值。
              两处都设一遍，让"界面上是什么"和"DOM 里能读到什么"一致。 */
        const clear = () => { nameEl.value = ''; nameEl.setAttribute('value', ''); };
        clear();
        let pending = null;
        try { pending = sessionStorage.getItem('sl.pending_tpl_name'); } catch { /* 隐私模式 */ }
        const def = (pending && pending.trim())
          ? pending.trim()
          : (m.reference_title ? `跟唱·${m.reference_title}` : '');
        nameEl.value = def;
        nameEl.setAttribute('value', def);
      }
      if (btn) {
        btn.onclick = async () => {
          const title = ((nameEl && nameEl.value) || '').trim();
          // 名字必填。默认的「我的模板 MM-DD HH:MM」在一堆模板里分不清哪首是哪首。
          if (!title) {
            setText(root, 'tpl-note',
              '先给模板起个名字 —— 以后你会有好几个模板，靠时间戳分不清哪首是哪首。');
            if (nameEl) nameEl.focus();
            return;
          }
          btn.disabled = true;
          const old = btn.textContent;
          btn.textContent = '正在保存…';
          setText(root, 'tpl-note', '');
          try {
            const t = await source.createTemplate({ sessionUid: uid, title });
            setText(root, 'tpl-note',
              `✅ 已存成模板「${t.title || title}」（${t.note_count || '?'} 个音）。` +
              `回到「开始练习」就能选它 —— 以后想练什么歌都行。`);
            try { sessionStorage.removeItem('sl.pending_tpl_name'); } catch { /* ignore */ }
          } catch (e) {
            // 失败要**说清原因**。模板质量门控会拒绝"唱得太不稳"的那一遍，
            // 而"重录一遍"这条建议对用户是有用的 —— 不能吞掉。
            setText(root, 'tpl-note', `⚠️ 没能存成模板：${e.message}`);
          } finally {
            btn.disabled = false;
            btn.textContent = old;
          }
        };
      }
    }
  }

  await fillReportPicker(root, source, uid, ctx, rep);
  return { bound: true, uid, excluded: rep.metrics_excluded || [] };
}

/* ══════════════════════════════════════════════════════════
 * 03 报告页的三个"整块重画"
 *
 * 为什么是函数而不是写在 s3() 里：这三块各自要拼几十行 HTML，
 * 混在 s3() 里会把"取数/判空/画图"的逻辑淹掉。
 * ══════════════════════════════════════════════════════════ */

/** HTML 转义。凡是把**后端来的字符串**塞进 innerHTML 的地方都要过一遍。 */
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

/**
 * 把 s3 报告页**所有会显示数字/结论的位置清空**，并写明为什么。
 *
 * ★ 为什么需要一个专门的函数（2026-09-23，审计 P0-3）：
 *   s3 有两条早退路径（没有会话 / 取报告失败）。它们原来只改了抬头那一行字
 *   就 `return` 了 —— 屏幕上留着原型稿的 `78 / 100`、`86`、`91`、`62`
 *   和拆解条的五组数字，与"还没有评测记录"同时出现。
 *
 *   这类 bug 的特征是**探针抓不到**：`data-b` 的 key 全都在、文本全是真文本，
 *   只是内容是原型的。所以这里**逐项列出来清**，而不是靠"感觉都清了"。
 *   新增一个显示数字的 key 时，也必须加到这个列表里。
 */
function blankReport(root, why) {
  // 用模块级那两个（`setText(root,key,text)` / `setBar(root,key,pct)`），
  // 不要在这里再定义一个同名的局部函数 —— 同一天已经因为"两个地方各自
  // 渲染同一件事"出过一次自相矛盾（散点图 vs 旁边那行文字）。
  const T = (k, v) => setText(root, k, v);
  T('report-meta', why || '');
  const _tot = root.querySelector('[data-b="total"]');
  if (_tot) _tot.innerHTML = '<span class="na">未评估</span>';
  else T('total', '—');
  T('total-delta', '');
  const _td = root.querySelector('[data-b="total-delta"]');
  if (_td) _td.hidden = true;
  T('total-basis', '');
  const _tb = root.querySelector('[data-b="total-basis"]');
  if (_tb) _tb.hidden = true;
  T('pitch', '—');
  T('rhythm', '—');
  T('expr', '—');
  // 空态的「—」不要用 21px 粗体画成三根黑杠（截图里像进度条）：
  // 缩小、转灰，读成"没有值"而不是"一个数"。
  for (const k of ['pitch', 'rhythm', 'expr']) {
    const n = root.querySelector(`[data-b="${k}"]`);
    if (n) n.classList.add('dim');
  }
  for (const k of ['pitch-acc', 'pitch-stab', 'rhythm-acc', 'rhythm-stab', 'rubato']) {
    T(k, '—');
    setBar(root, k + '-bar', 0);
  }
  T('tempo-note', '');
  const tn = root.querySelector('[data-b="tempo-note"]');
  if (tn) tn.style.display = 'none';
  const tpl = root.querySelector('[data-b="tpl-card"]');
  if (tpl) tpl.hidden = true;
  // ★ 空报告时必须把**对齐降级横幅**也收起来（2026-10-02，B6b）。
  //   否则切换报告/清空之后，上一份的横幅会留在屏幕上 ——
  //   那比不显示更糟：用户会以为**这一份**也降级了。
  renderAlignBanner(root, null);
  const wrap = root.querySelector('[data-b="acc-wrap"]');
  if (wrap) wrap.innerHTML = '';
  T('problem-list', '');
  // 标题留一句**有意义的话**，不要留空字符串 ——
  // `<h3>` 里只剩英文小标 "Practice These"，看着像渲染坏了。
  T('problem-title', '先唱一遍才知道该练哪里');
  const pe = root.querySelector('[data-b="problem-empty"]');
  if (pe) {
    // ★ 2026-10-20：空态不能只留一条横线。给一句"这里将来会有什么"，
    //   再加一个**真的能点**的下一步（否则用户得自己去侧栏找「开始练习」）。
    pe.hidden = false;
    pe.innerHTML = '唱完一遍后，这里会列出具体是哪一句、哪几个音需要再练。'
      + '<div style="margin-top:12px">'
      + '<button class="btn dark" type="button" data-goto="s2">去唱一遍 →</button>'
      + '</div>';
  }
  T('coach-body', '');
  T('coach-note', '');
  T('coach-src', 'AI Coach');
  // ★ 2026-10-20：空态把整张教练卡收起来。原来只清空文字，留下一整块
  //   深色大卡 + 两个按钮（"就按这个再唱一遍"），看起来像渲染失败。
  const _coach = root.querySelector('.coach-hero');
  if (_coach) _coach.hidden = true;
  // ★ 空报告时"AI 生成"徽标必须收起来 ——
  //   否则上一份报告的徽标会留在屏幕上，等于**在界面上撒谎**。
  const _cl = root.querySelector('[data-b="coach-live"]');
  if (_cl) _cl.hidden = true;
  // ★ 练习计划卡（B1，2026-10-01）也必须藏掉 ——
  //   否则空态下会留一张只剩标题「下一遍只改这一件事」的空卡，
  //   而"空着"和"没接线"在界面上长得一样（这条已经写进本文件好几处了）。
  const plan = root.querySelector('[data-b="plan-card"]');
  if (plan) plan.hidden = true;
  const pb = root.querySelector('[data-b="plan-body"]');
  if (pb) pb.replaceChildren();
  /* ★ 2026-10-20：空态不该展示"空雷达 + 空拆解"。
     它们在有报告时是证据，在没报告时只是六个"未评估"标签和四根空条 ——
     截图实测像一张没画完的图。留一张"未评估"总评 + 一句"先唱一遍"就够。 */
  const _radar = root.querySelector('#radar');
  if (_radar) _radar.hidden = true;
  const _rcap = root.querySelector('.radar-cap');
  if (_rcap) _rcap.hidden = true;
  const _dec = root.querySelector('.decomp');
  if (_dec) _dec.hidden = true;
  const _sn = root.querySelector('[data-b="score-note"]');
  if (_sn) _sn.hidden = true;
  const _dr = root.querySelector('[data-b="danger-row"]');
  if (_dr) _dr.hidden = true;
}

/**
 * 对齐降级横幅（待办 B6b，2026-10-02）。
 *
 * ━━━ 为什么必须有这块 ━━━
 *
 * `meta.align_degraded` 与 `meta.align_reason` **后端从 2026-09 就在算了**，
 * `.deg` 的样式也在 `shell.css` 里备好了 —— 但**前端从没引用过**。
 * 于是：唱的和参考对不上时，音准/节奏静默变成「本次未评估」，
 * **屏幕上只有"没分数"，没有"为什么"**。用户会读成"我唱得太差了"。
 *
 * ⚠️ 这个洞与 2026-10-02 那次事故**是同一个形状**：
 *    我拿 7 音参考去对作者 42 音的录音，对齐降级、命中 0，
 *    而系统**不报错**，给出一堆看起来正常的数 ——
 *    我据此查了一整天的"切分稳定性"，其实根因是参考不对。
 *    ⇒ 降级**必须说出来**，否则错误会被下游当成数据。
 *
 * @param {Element} root 视图根
 * @param {object|null} rep 报告；`null` 表示空态（收起横幅）
 */
function renderAlignBanner(root, rep) {
  const box = root.querySelector('[data-b="align-banner"]');
  const body = root.querySelector('[data-b="align-banner-body"]');
  const head = root.querySelector('[data-b="align-banner-head"]');
  if (!box || !body) return;
  /* ★ 2026-10-10：标题**按情形给**。
     写死一个 "ALIGNMENT DEGRADED" 的后果不是难看，而是**说错话** ——
     三种情形里只有一种真的是"降级"，另外两种标题会误导用户往错的方向想。 */
  const setHead = (t) => { if (head) head.textContent = t; };

  const meta = (rep && rep.meta) || null;
  const degraded = !!(meta && meta.align_degraded);
  // ★★ 2026-10-02 新增的第二种情形：**对齐结构没坏，但内容对不上**。
  //
  //   为什么必须补这一条：`align_degraded` 只在 `method == "EQUAL_DIVISION"`
  //   时为真。但实测存在一种更隐蔽的错配 —— 对齐照样走出 DTW、
  //   `align_ratio` 还挺高，**却给了一个看起来正常的分数**：
  //
  //       情形                     音准   节奏   method  命中    match   音准acc
  //       整段对 7 音参考（必然错）    0.0  55.3   DTW    7/7    1.000   219.5
  //       14 音素材对 42 音参考（错） 51.0  50.0   DTW   35/36   0.972    −7.0
  //       正常（对）                81.7  62.0   DTW   35/42   0.833    10.7
  //
  //   ⚠️ 第 2 行是陷阱：错配却拿到音准 **51 分**、`match` **0.972**。
  //      **单看任何一项都抓不出来。**
  //   ⚠️ 这正是 2026-10-02 那次事故的形状：参考写短了，系统不报错，
  //      给出一堆看着正常的数，我据此查了一整天的"切分稳定性"。
  //
  //   判据（三项**同时**成立才提示，宁可漏报不要误报）：
  //     · 对齐没降级（走的是正常 DTW）
  //     · 对齐率 ≥ 0.7（结构上是"对上了"的）
  //     · **音准分 ≤ 5**（几乎是 0 —— 逐音音高整体差得离谱）
  //   实测这个组合：对的（81.7）不触发；错的（0.0）触发。
  const pitch = (rep && rep.pitch) || {};
  const al = (meta && meta.align) || {};
  const ew = (meta && meta.eval_window) || {};
  const ratio = typeof al.align_ratio === 'number' ? al.align_ratio : null;
  const pScore = typeof pitch.score === 'number' ? pitch.score : null;
  const contentMismatch = !degraded
    && ratio !== null && ratio >= 0.7
    && pScore !== null && pScore <= 5
    && !(meta && meta.self_reference);

  /* ★★ 2026-10-10 新增第三种情形：**参考只覆盖了你唱的一小部分**。
   *
   * 为什么必须补：`_align_window()` 在"演唱音数 > 参考音数"时会**挑一段最佳窗口**
   * 去对齐（`phrase_window`），分数是**对那个窗口**算的 —— 这本身是对的。
   * 但它**不说**这件事，于是：
   *
   *     实测（`experiments/_probe_ref_trust.py`，2026-10-10）
   *       素材                        参考            覆盖     音准
   *       合成 14 音（唱的就是参考）  REF_TWINKLE_2   0.78–1.00  96–100  ← 正确
   *       合成 14 音 / 真人六句       REF_TWINKLE_1   0.09–0.50  52.7–100 ← ⚠️ 只评了一小段，却给高分
   *
   *     `REF_TWINKLE_1` 是 7 个音，用户唱了 14/26/75 个音 ⇒
   *     系统只评了其中 7 个，**却报出 100 分**。而 B6b 那两条判据都不触发
   *     （`align_ratio` 不低、`pitch` 不低）—— 分数看着完全正常。
   *
   * ⚠️ 判据用「覆盖」而不是"猜参考错没错"：
   *    `notes_used` 恒等于 `min(演唱音数, 参考音数)`（见 `_align_window`），
   *    所以 `覆盖 = notes_used / notes_total < 1` **等价于**"你唱的比参考覆盖的多"。
   *    这是一条**事实**，不是统计推断 —— 而 §85 已经否证过两条统计判据
   *    （`contour_corr` / `len_ratio`），所以这里不去猜"参考是不是错了"。
   *
   * ⚠️ 阈值 0.6 的来处（不是拍的）：
   *    实测正确情形最低 **0.78**（合成素材在参考本身上唱，段数还会多出 ~21%，
   *    见 §86），而"参考短于素材"最高 **0.50**。
   *    取 0.6 落在 [0.50, 0.78] 这条空档里，两边各留约 20% 余量。
   *    ⇒ 覆盖 0.6 ⟺ 你唱的音数超过参考的 1.67 倍。
   *    ⚠️ 样本只有 10 组合成 + 5 段真人 ⇒ **阈值是暂定的**，
   *       录到更多真人素材后要重定（与 §85 的教训同一条）。 */
  const notesTotal = typeof ew.notes_total === 'number' ? ew.notes_total : null;
  const notesUsed = typeof ew.notes_used === 'number' ? ew.notes_used : null;
  const coverage = (notesTotal && notesUsed) ? notesUsed / notesTotal : null;
  const lowCoverage = !degraded && !contentMismatch
    && !(meta && meta.self_reference)
    && coverage !== null && coverage < 0.6;
  /* ★ 2026-10-23：**对齐率低**也要说出来。
     实测整首歌 506 个用户音符只对上 312 个（61.7%）时，系统仍会算出一个
     18.6 的节奏分 —— 但那个分数只代表“能对上的 312 个音”，
     不是“整遍唱得差”。项目自己的注释也写着：正确情形实测最低 0.78，
     0.6 只是“结构上还能跑”的下限，不是“可以拿来做结论”的置信度。
     ⇒ 低于 0.70 时显示 LOW ALIGNMENT，不偷偷把低分当成演唱水平。 */
  const lowAlign = !degraded && !contentMismatch && !lowCoverage
    && !(meta && meta.self_reference)
    && !(rep && rep.rhythm && rep.rhythm.basis === 'phrase')
    && ratio !== null && ratio < 0.70;

  if (!degraded && !contentMismatch && !lowCoverage && !lowAlign) {
    box.hidden = true;
    body.replaceChildren();
    setHead('ALIGNMENT DEGRADED');   // 复位，免得下次显示时留着上一次的标题
    return;
  }

  const wrap = document.createElement('div');
  const line1 = document.createElement('div');
  const reason = (meta && meta.align_reason) || '';
  const nRef = ew.notes_total || ew.notes_used || null;
  // ★ 2026-10-19：nRef 是**对齐窗口**的音数，不是整首参考的音数。
  //   原来写「参考共 8 个音」会被读成"整首参考只有 8 个音"（实际 42 个）。
  const fullRef = meta && typeof meta.reference_notes === 'number'
    ? meta.reference_notes : null;
  const selfRef = !!(meta && meta.self_reference);
  const srcKind = meta && meta.reference_source;

  if (lowCoverage) {
    /* 措辞原则（照抄这个项目对"未评估"的一贯做法）：
       ① 先给**事实**（评了几个音 / 一共唱了几个音），不先给结论
       ② 再说**通常的解释**，并且明确它只是"通常"
       ③ 最后给**能做什么**，而不是让用户自己猜。
       ⚠️ 不能写成"你的参考错了" —— 也可能只是他练了三遍。
          两种情况下**该说的话其实是同一句**："这次只评了其中一段"。 */
    const pct = Math.round(coverage * 100);
    line1.textContent = `这次只评了你唱的 ${notesTotal} 个音里的 `
      + `${notesUsed} 个（${pct}%）。`;
    const line2 = document.createElement('div');
    line2.style.marginTop = '8px';
    line2.innerHTML = '分数是<b>对那 ' + notesUsed + ' 个音</b>算的，'
      + '不是对整遍算的。<b>通常</b>是因为参考比这一遍短 —— '
      + '参考只有 ' + notesUsed + ' 个音，而你这一遍唱出了 ' + notesTotal + ' 个'
      + '（唱了多遍、或者唱了更长的段落）。'
      + '换句话说：<b>你后面唱的没被算进去</b>。';
    const line3 = document.createElement('div');
    line3.style.marginTop = '8px';
    line3.innerHTML = '想让整遍都被评到：<b>换一个覆盖同样长度的参考</b>'
      + '（内置曲目里选长一点的那首），或者<b>只唱参考覆盖的那一段</b>。';
    wrap.appendChild(line1);
    wrap.appendChild(line2);
    wrap.appendChild(line3);
    const raw = document.createElement('div');
    raw.style.marginTop = '8px';
    raw.style.fontSize = '11px';
    raw.style.opacity = '.75';
    raw.textContent = `参考 ${notesUsed} 个音 · 这一遍切出 ${notesTotal} 个音 · `
      + `覆盖 ${pct}%（低于 60% 才提示）`;
    wrap.appendChild(raw);
    setHead('PARTIAL EVALUATION');
    body.replaceChildren(wrap);
    box.hidden = false;
    return;
  }

  if (lowAlign) {
    const pct = Math.round(ratio * 100);
    const matched = (typeof al.aligned === 'number') ? al.aligned : null;
    const total = notesTotal || al.total || null;
    const off = (al.onset_dev && typeof al.onset_dev.median === 'number')
      ? Math.round(al.onset_dev.median) : null;
    const tempo = (rep.rhythm && typeof rep.rhythm.tempo_ratio === 'number')
      ? rep.rhythm.tempo_ratio : null;
    line1.innerHTML = `这次只有 <b>${pct}%</b> 的音符能对上参考`
      + (matched && total ? `（${matched}/${total}）` : '')
      + `，<b>本次节奏不计分</b>。`;
    const line2 = document.createElement('div');
    line2.style.marginTop = '8px';
    line2.textContent = '能对上的音太少，逐音节奏就不成立；系统没有把这类低分'
      + '当成你的演唱水平。通常说明你的切分和参考版本不完全一致'
      + '（唱法、装饰音、参考本身有差异），也可能只是这一遍的收音/起唱没有对齐。';
    const line3 = document.createElement('div');
    line3.style.marginTop = '8px';
    line3.innerHTML = '建议先<b>戴耳机、只练一小段</b>，或者换一首内置短参考再测；'
      + '整首歌的逐音节奏，系统目前还给不出高置信度的结论。';
    const raw = document.createElement('div');
    raw.style.marginTop = '8px';
    raw.style.fontSize = '11px';
    raw.style.opacity = '.75';
    raw.textContent = (off !== null ? `起唱偏移 ${off}ms · ` : '')
      + (tempo !== null ? `速度 ${tempo.toFixed(3)}× · ` : '')
      + `对齐 ${matched || '—'}/${total || '—'}（${pct}%）`;
    wrap.appendChild(line1);
    wrap.appendChild(line2);
    wrap.appendChild(line3);
    wrap.appendChild(raw);
    setHead('LOW ALIGNMENT CONFIDENCE');
    body.replaceChildren(wrap);
    box.hidden = false;
    return;
  }

  if (contentMismatch) {
    // ⚠️ 措辞要**留有余地**：这一条是推断，不是确定的事。
    //    也可能只是这个人真的整首都唱低了 —— 不能把推断说成结论。
    // ⚠️ 这里拼的是 innerHTML，**不能用 Markdown 的 `**粗体**`** ——
    //    那会原样显示成星号（第一版就是这么写的）。要用 <b>。
    line1.innerHTML = '<b>音准这一项几乎是 0，但对齐本身是好的。</b>'
      + '这<b>通常</b>说明你唱的和我手里的参考不是同一段（曲目选错了、'
      + '唱的是另一段、或者唱了几遍而参考只有一遍）—— '
      + '但也可能只是这一遍音准确实差得比较多。'
      + '请先确认参考对不对；如果参考没错，那这个音准分就是它该有的样子。';
    setHead('REFERENCE MISMATCH');
    box.hidden = false;
    body.replaceChildren(wrap);
    wrap.appendChild(line1);
    if (nRef) {
      const raw = document.createElement('div');
      raw.style.marginTop = '8px';
      raw.style.fontSize = '11px';
      raw.style.opacity = '.75';
      raw.textContent =
        `对齐窗口 ${nRef} 个参考音`
        + (fullRef ? `（全曲 ${fullRef} 个）` : '')
        + `，对齐率 ${(ratio * 100).toFixed(0)}%，`
        + `音准 ${pScore.toFixed(1)} 分。`;
      wrap.appendChild(raw);
    }
    return;
  }

  if (selfRef) {
    // 模式 B 自比：这是**设计如此**，不是故障
    setHead('SELF BASELINE');
    line1.innerHTML = '这一遍是<b>拿它自己当参考</b>的（跟唱模板的第一遍），'
      + '所以音准和节奏这次不评 —— <b>第二遍才有得比</b>。';
  } else {
    setHead('ALIGNMENT DEGRADED');
    line1.innerHTML = '这次<b>没能把你唱的和我手里的参考对上</b>，'
      + '所以音准和节奏<b>本次未评估</b>（不是 0 分，是没测）。'
      + '下面的表现力只反映<b>音量/音色</b>，参考不匹配时'
      + '<b>不能当成“唱得好”</b>。';
    const line2 = document.createElement('div');
    line2.style.marginTop = '8px';
    let hint = '';
    if (srcKind === 'builtin_score') {
      hint = '最可能的原因：<b>你唱的和这首参考不是同一段</b>'
        + '（曲目选错了、唱的是另一段、或者唱了几遍而参考只有一遍）。'
        + '换一首对得上的参考再试一次。';
    } else if (srcKind === 'user_template') {
      hint = '最可能的原因：<b>这一遍和当初录模板那一遍差太多</b>'
        + '（速度、段落、或者唱的遍数不同）。可以重新录一个模板。';
    } else {
      hint = '最可能的原因：<b>参考与你实际唱的不是同一段</b>。'
        + '换一个对得上的参考再试一次。';
    }
    line2.innerHTML = hint;
    wrap.appendChild(line2);
  }
  wrap.insertBefore(line1, wrap.firstChild);

  // 原始原因如实附上（可折叠不了就小字直出）——**不静默**：
  // 排查时不用猜，用户也能把这句话直接反馈给开发者。
  if (reason) {
    const raw = document.createElement('div');
    raw.style.marginTop = '8px';
    raw.style.fontSize = '11px';
    raw.style.opacity = '.75';
    let extra = '';
    if (nRef) extra = `（参考共 ${nRef} 个音）`;
    raw.textContent = `系统给出的原因：${reason}${extra}`;
    wrap.appendChild(raw);
  }

  body.replaceChildren(wrap);
  box.hidden = false;
}

/** 六块分项结论的顺序、问句、英文小标、对应的证据曲线。顺序取自 v7 稿，没改。 */
const ACC_BLOCKS = [
  { k: 'dynamic_range', q: '你整首歌的音量有变化吗', en: 'Dynamics · 起伏', ev: 'rms' },
  { k: 'vibrato', q: '你唱长音的时候，声音会轻轻抖吗', en: 'Vibrato · 颤音', ev: 'vib' },
  { k: 'rubato', q: '你的节奏是死板还是在跟着感觉走', en: 'Rubato · 弹性', ev: 'rubato' },
  { k: 'onset_style', q: '你每个字是怎么唱出来的', en: 'Onset · 起音', ev: null },
  { k: 'brightness', q: '你的声音听上去是亮的还是厚的', en: 'Timbre · 音色', ev: null },
  { k: 'phrase_dynamics', q: '每一句内部有没有强弱推进', en: 'Phrase · 乐句', ev: null },
];

/**
 * 专业术语行（`body.pro` 才显示）。
 *
 * ★ 只写**这一遍测出来的数**，不写"同龄练习者中位数"这类比较 ——
 *   我们没有任何同龄人常模数据，v7 稿里那句"中位数约 12 dB，你处于后 40%"
 *   是编的。**给一个自己编出来的百分位，比不给更坏**：
 *   用户会拿它当事实，而它无从验证。
 */
function jargonOf(key, ex) {
  const num = (v, d = 1, unit = '') =>
    (typeof v === 'number' && isFinite(v)) ? `<code>${v.toFixed(d)}${unit}</code>` : null;
  const M = {
    dynamic_range: () => {
      const a = num(ex.dynamic_range, 1, ' dB');
      return a && `力度动态范围 ${a}（RMS 能量的 P95 − P5 分位差）。`;
    },
    phrase_dynamics: () => {
      const a = num(ex.phrase_dynamics, 1, ' dB');
      return a && `逐乐句内部动态落差均值 ${a}。`;
    },
    rubato: () => {
      const a = num(ex.rubato, 1, ' ms');
      return a && `onset 偏差序列的趋势分量标准差 ${a}（弹性/艺术处理，不计入扣分）。`;
    },
    vibrato: () => {
      const r = num(ex.vibrato_rate, 1, ' Hz');
      const d = num(ex.vibrato_depth, 2, ' 半音');
      if (!r && !d) return null;
      return `颤音速率 ${r || '—'}、深度 ${d || '—'}（专业歌手典型区间 5–7 Hz、0.25–0.5 半音）。`;
    },
    onset_style: () => {
      const a = num(ex.onset_style, 0, ' ms');
      return a && `音符起音时间的中位数 ${a}（越小越干脆）。`;
    },
    brightness: () => {
      const a = num(ex.brightness, 0, ' Hz');
      return a && `谱质心均值 ${a}（越高听感越亮）。`;
    },
  };
  const fn = M[key];
  const line = fn ? fn() : null;
  return line ? `专业说法：${line}` : null;
}

/** 六块分项结论：按 `comments_by_metric` 重画整块。 */
function renderAccBlocks(root, rep) {
  const wrap = root.querySelector('[data-b="acc-wrap"]');
  if (!wrap) return;
  const cbm = rep.comments_by_metric || {};
  const ex = rep.expressiveness || {};
  const nz = ex.normalized || {};
  const excluded = new Set(rep.metrics_excluded || []);
  /* ★ 2026-10-18：有练习计划卡时，分项里不再重复"下一遍这么唱"。
     否则同屏又出现三张处方（教练 / 分项 / 计划），重点还不一致。
     计划卡是**唯一**处方；分项只回答"这一项怎么样"。 */
  const hasPlan = !!(rep.practice_plan &&
    (rep.practice_plan.focus || (rep.practice_plan.steps || []).length));

  wrap.innerHTML = ACC_BLOCKS.map(({ k, q, en, ev }, i) => {
    const c = cbm[k] || {};
    const v = nz[k];
    const assessed = !excluded.has(k) && typeof v === 'number' && c.plain;
    /* ★ 2026-10-21：徽标颜色与文字**同源**。
       原来颜色跟 normalized 走、文字跟 c.level 走，于是同一个「很好」可能
       红底（normalized 0.58）也可能绿底（0.66）—— 用户无法建立颜色直觉。
       现在先去掉 level 里的排版空格（后端写的是「很 好」），再按措辞分档；
       「温暖厚实」这类**描述性**标签保持中性，不硬塞绿/红。 */
    const lvlRaw = c.level || (assessed ? '已评估' : '本次未评估');
    const lvlText = String(lvlRaw).replace(/\s+/g, '');
    let cls = 'L-desc';
    if (!assessed || /未评估|未测/.test(lvlText)) cls = 'L-na';
    else if (/(偏弱|偏低|偏高|偏平|偏紧|偏抖|偏飘|偏散|偏硬|单调|不足|不稳|太平|太紧|较差|很差|不好|生硬)/.test(lvlText)) cls = 'L-mid';
    else if (/很好|优秀|自然|准确|丰富|清晰|干脆/.test(lvlText)) cls = 'L-good';
    else if (/还行|一般|中等/.test(lvlText)) cls = 'L-desc';
    const lvl = lvlRaw;
    const body = [];
    if (ev && assessed) body.push(`<canvas class="ev" data-k="${ev}"></canvas>`);
    // 未评估时 `plain` 里往往带着**原因**（后端写的，比如"没有足够长的长音"），
    // 要说出来 —— 否则用户只看到"未评估"三个字，会以为系统坏了。
    body.push(`<div class="plain">${esc(c.plain ||
      '这一项这次没有算出来 —— 通常是因为录音里没有足够长的长音或音符太少。')}</div>`);
    if (assessed && c.action && !hasPlan) {
      body.push(`<div class="action"><span class="do">下一遍这么唱 →</span>${esc(c.action)}</div>`);
    }
    if (assessed) {
      const j = jargonOf(k, ex);
      if (j) body.push(`<div class="jargon">${j}</div>`);
    }
    return `<details class="acc"${i === 0 ? ' open' : ''}>
  <summary>
    <span class="q">${esc(q)}<s>${esc(en)}</s></span>
    <span class="lvl ${cls}">${esc(lvl)}</span>
  </summary>
  <div class="body">${body.join('\n    ')}</div>
</details>`;
  }).join('\n');
}

/**
 * 正在放的那一段。**同一时刻只放一个** ——
 * 三个问题片段各放各的会叠成噪音，而用户点第二个时的意图显然是"换成这个"。
 */
let segAudio = null;
let segKey = '';

/** 停掉正在放的片段，并把按钮状态复位。 */
function stopSeg() {
  if (segAudio) {
    try { segAudio.pause(); } catch (e) { /* 停不掉也不影响下面 */ }
    segAudio = null;
  }
  segKey = '';
  document.querySelectorAll('[data-segbtn]').forEach((b) => {
    b.classList.remove('on');
    if (b.dataset.segbtn === 'loop') b.textContent = '只练这一段';
  });
}

/**
 * 放这一遍录音里的某一小段（G13）。
 *
 * @param rep   报告（要 `session_uid` 与 `meta.audio_available`）
 * @param p     一个 problem（要 `start_ms` / `end_ms`）
 * @param loop  true = 循环（"只练这一段"）
 * @param btn   触发它的按钮（用来切播放态）
 *
 * ⚠️ **前后各留 400ms**：报告给的区间是"这几个音"，
 *    而直接从某个音的起音处开始放，听感是**半截**的 ——
 *    用户要判断的是"这一段听起来怎么样"，需要一点上下文。
 *    留白写在按钮的 `title` 里，不藏。
 */
function playSeg(rep, p, loop, btn) {
  const uid = rep.session_uid || '';
  const sid = singerId();
  const a = Math.max(0, Number(p.start_ms || 0) - 400);
  const b = Number(p.end_ms || 0) + 400;
  const key = `${uid}:${a}:${b}:${loop ? 'L' : '1'}`;
  if (segKey === key) { stopSeg(); return; }        // 再点一次 = 停
  stopSeg();
  if (!uid || !sid || !(b > a)) return;
  const url = `/api/session/${encodeURIComponent(uid)}/audio`
            + `?singer_id=${encodeURIComponent(sid)}`
            + `&from_ms=${Math.round(a)}&to_ms=${Math.round(b)}`;
  const au = new Audio(url);
  au.loop = !!loop;
  au.onended = () => { if (!au.loop) stopSeg(); };
  /* ⚠️ 播不出来**要说出来**。静默失败在这里最坏：用户点了没反应，
     只会以为自己浏览器坏了（本项目写过两次的那条规矩）。
     两种失败都要管：HTTP 错误，以及浏览器拒绝播放。 */
  au.onerror = () => {
    stopSeg();
    if (btn) {
      btn.textContent = '这一段取不到了';
      btn.disabled = true;
      setTimeout(() => { btn.disabled = false; btn.textContent = '听这一段'; }, 2200);
    }
  };
  segAudio = au;
  segKey = key;
  if (btn) {
    btn.classList.add('on');
    if (loop) btn.textContent = '停止';
  }
  au.play().catch(() => au.onerror());
}

/**
 * 「重点练这几段」：按 `problems` 重画。
 *
 * ⚠️ 原稿这里是两条**编造的**问题片段（"第 3 句偏低"、"副歌太平"）。
 *    对用户来说"系统说我第 3 句偏低"是关于他声音的**断言** ——
 *    编这种句子比编一个分数更糟。后端 `problems` 返回什么就说什么。
 *
 * ★ G13（2026-10-15）：**这里现在能逐段复听了。**
 *    原来这段注释写的是"没有'循环练习这一段'按钮：会话录音不入库
 *    （`Session.rt.audio` 只在内存里活到精算结束），没有任何端点能放出一段音频"
 *    —— 那条理由**已经不成立了**：后端现在把这一遍落盘
 *    （`data/recordings/sessions/{uid}.wav`），并有
 *    `GET /api/session/{uid}/audio?from_ms=&to_ms=` 能切出一段。
 *    ⇒ 于是按钮加回来，但**规矩不变**：拿不到音频时
 *      （`meta.audio_available` 为假，例如录音没存下来、或清空过数据）
 *      **一个按钮都不放** —— 按下去没反应的按钮比没有按钮更坏。
 */
function renderProblems(root, rep) {
  const list = root.querySelector('[data-b="problem-list"]');
  if (!list) return;
  stopSeg();                 // 重画 = 上一次的播放作废（否则声音还响着，按钮却没了）
  const title = root.querySelector('[data-b="problem-title"]');
  const empty = root.querySelector('[data-b="problem-empty"]');
  const ps = Array.isArray(rep.problems) ? rep.problems : [];
  const meta = rep.meta || {};
  const canAudio = !!meta.audio_available && !!rep.session_uid;

  // kind 只说明"哪一类"，**不推断方向**：契约 §2.4 的 kind 是 PITCH/RHYTHM/DYNAMIC，
  // 没有"偏高还是偏低"。写成"偏低"（原稿那样）就是替后端下了一个它没下的结论。
  const KIND = { PITCH: '音准', RHYTHM: '节奏', DYNAMIC: '起伏' };
  const mmss = (ms) => {
    const t = Math.max(0, Number(ms) || 0) / 1000;
    return `${String(Math.floor(t / 60)).padStart(2, '0')}:` +
           `${(t % 60).toFixed(1).padStart(4, '0')}`;
  };

  list.innerHTML = ps.map((p, i) => {
    // 时长也写出来：用户能据此判断"值不值得单独练"（1 秒的就别练了）
    const secs = Math.max(0, (Number(p.end_ms || 0) - Number(p.start_ms || 0)) / 1000);
    const btns = canAudio ? `
  <span class="pact">
    <button class="pbtn" data-segbtn="play" data-i="${i}"
      title="放你唱的这几秒（前后各多放 0.4 秒，好让这一句完整）">听这一段</button>
    <button class="pbtn" data-segbtn="loop" data-i="${i}"
      title="循环放这几秒，跟着它反复练 —— 再点一次停">只练这一段</button>
  </span>` : '';
    return `<div class="problem">
  <span class="lvl ${p.severity === 'HIGH' ? 'L-bad' : 'L-mid'}">${esc(KIND[p.kind] || p.kind || '问题')}</span>
  <span class="tt">${esc(p.headline || '')}<s>${esc(p.detail || '')}</s></span>
  ${btns}
  <span class="tm">${mmss(p.start_ms)}<i>${secs >= 0.1 ? secs.toFixed(1) + 's' : ''}</i></span>
</div>`;
  }).join('\n');

  // 事件在**画完之后**挂（`innerHTML` 会把之前挂的监听一起清掉）
  if (canAudio) {
    list.querySelectorAll('[data-segbtn]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const p = ps[Number(btn.dataset.i)];
        if (p) playSeg(rep, p, btn.dataset.segbtn === 'loop', btn);
      });
    });
  }

  if (title) {
    /* ★ 标题要跟着**计划卡**走（2026-10-01，B1）。
       原来写「重点练这 N 段就够了」—— 那是一句**并列的处方**，
       而报告页现在有一张专门的处方卡（「下一遍只改这一件事」）。
       两处并列的处方 = 用户又得自己选一次，等于没收敛。

       现在这块的定位改成「**依据**」：它是上面那条建议落在哪几个音上。
       ⚠️ 有 `focus` 时才这么写 —— 没有计划卡时（老报告/取不到）不能
          引用一张不存在的卡，那就退回原来的说法。 */
    const hasPlan = !!(rep.practice_plan && rep.practice_plan.focus);
    const n = ps.length;
    if (!n) {
      title.textContent = '这一次没有成段的问题';
    } else if (hasPlan) {
      title.textContent = `上面那条建议，落在这${n === 1 ? '一' : n + ''}处`;
    } else {
      title.textContent = `重点练这${n === 1 ? '一' : n + ''}段就够了`;
    }
  }
  if (empty) empty.hidden = ps.length > 0;
}

/**
 * 「教练说」：总评。
 *
 * 契约 §5.1：**总评由 LLM 产出**（`comments` + `comment_source`），
 * 分项才用模板。而这一段从没被显示过 —— 页面上一直是 v7 稿的四段冻结文案，
 * 里面还带着具体数字（"22 秒到 31 秒那段副歌"），对不上任何一次真实评测。
 */
function renderCoach(root, rep) {
  const body = root.querySelector('[data-b="coach-body"]');
  if (!body) return;
  // ★ 2026-10-20：空态时整张教练卡被 `blankReport` 藏起来了
  //   （否则新用户看到一整块只有按钮的黑卡）。有报告时再放出来。
  const card = root.querySelector('.coach-hero');
  if (card) card.hidden = false;
  const st = rep.coach_struct || null;
  const txt = (rep.comments || '').trim();

  /* ★ 2026-10-07 重做：优先渲染**结构化**三段（结论 / 展开 / 练法）。
     为什么：作者反馈「总评太短、不显眼、没有 AI 陪练的感觉」。
     原来不管什么来源都拍成一串 <p>，读起来像一行附注；
     而"练法"才是用户唯一真正要照做的东西，必须单独突出。

     ⚠️ 拿不到 `coach_struct` 就**退回散文**（老报告、模型没按 JSON 回、
        规则版兜底都属于这种情况）—— **界面不会因此空掉**。 */
  if (st && (st.headline || st.body || st.drill)) {
    /* ★ 2026-10-18：有练习计划卡时，教练卡不再重复"下一遍这么练"。
       同屏两张处方（教练 / 计划）重点还不一致，用户不知道先照哪个做。
       计划卡是唯一处方；教练只回答"这一遍发生了什么"。 */
    const hasPlan = !!(rep.practice_plan &&
      (rep.practice_plan.focus || (rep.practice_plan.steps || []).length));
    const parts = [];
    if (st.headline) {
      parts.push(`<p class="co-line">${esc(st.headline)}</p>`);
    }
    if (st.body) {
      parts.push(`<p class="co-body">${esc(st.body)}</p>`);
    }
    if (st.drill && !hasPlan) {
      // 练法单独一块：左边一道竖线 + 小标题，让它一眼可辨
      parts.push('<div class="co-drill">'
        + '<span class="co-drill-h">下一遍这么练</span>'
        + `<p>${esc(st.drill)}</p></div>`);
    }
    body.innerHTML = parts.join('');
  } else {
    const paras = txt ? txt.split(/\n{1,}/).map((s) => s.trim()).filter(Boolean) : [];
    body.innerHTML = paras.length
      ? paras.map((p) => `<p class="co-body">${esc(p)}</p>`).join('')
      : '<p class="note">这一次没有生成总评（后端没返回 comments）。</p>';
  }

  // 来源要说清楚：LLM 是模型名，规则兜底是 `rule_fallback`。
  // 用户有权知道这段评价是模型写的还是模板拼的。
  const isLLM = !!rep.comment_source && rep.comment_source !== 'rule_fallback';
  const tag = root.querySelector('[data-b="coach-src"]');
  if (tag) {
    tag.textContent = isLLM
      ? `由 ${rep.comment_source} 生成`
      : '规则版教练 · 本地生成';
  }
  // 「AI 生成」徽标只在这段**真的**由模型写时才亮 ——
  // 否则就是在界面上撒谎（本项目吃过好几次"界面正常但事情没做"的亏）。
  const live = root.querySelector('[data-b="coach-live"]');
  if (live) live.hidden = !isLLM;

  const note = root.querySelector('[data-b="coach-note"]');
  if (note) {
    const bits = [];
    if (!isLLM && txt) {
      bits.push('想让总评更像老师说话？去「设置 → 反馈方式」配一个 API key。');
    }
    // 只分析了后一段的时候必须说（`meta.analyzed_from_ms`，见 server.finish 的注释）。
    // ⚠️ 它在 **meta 里面**，不是报告顶层 —— 第一版读 `rep.analyzed_from_ms`
    //    永远拿到 undefined，于是"只分析了后 30 秒"这句**永远不会显示**，
    //    而它正是要防止用户以为"整首都评了"的那句话。
    const from = (rep.meta || {}).analyzed_from_ms;
    if (typeof from === 'number' && from > 0) {
      bits.push(`⚠️ 本次只分析了第 ${(from / 1000).toFixed(1)} 秒之后的部分。`);
    }
    note.textContent = bits.join(' ');
    note.hidden = !bits.length;
  }
}

/* ══════════════════════════════════════════════════════════
 * 练习计划（B1，2026-10-01）
 *
 * 把"这一遍哪里不对"收敛成"下一遍只改哪一件事"。
 * 放在文件末尾而不是 s3 旁边：这段是**独立的一层**（诊断 → 处方），
 * 与上面那些"把报告字段填进 DOM"的活不是一类。函数声明会提升，位置无所谓。
 *
 * 三条渲染纪律（都对应本项目踩过的坑）：
 *  ① **整块由后端数据渲染**。不给 `practice_plan` 就把整张卡藏起来 ——
 *     绝不退回原型稿文案（"看着有结论、其实没依据"已经出过两次）。
 *  ② **文案全部 `textContent`**。`why` 里会掺进用户自己的数据；
 *     后端文案虽然是我们写的，但别养成把字符串直接塞进 innerHTML 的习惯。
 *  ③ **示范音是"可选增强"，不是前提**。没有示范照样要能读懂怎么练 ——
 *     所以拿不到音频时按钮**自己消失**，而不是留一个点了没反应的死按钮
 *     （死按钮这个坑刚修完，不能再造一个）。
 * ══════════════════════════════════════════════════════════ */
function renderPracticePlan(root, rep, source, uid) {
  const card = root.querySelector('[data-b="plan-card"]');
  const body = root.querySelector('[data-b="plan-body"]');
  if (!card || !body) return;
  const plan = rep.practice_plan;
  if (!plan || !plan.focus) {
    card.hidden = true;
    return;
  }
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  body.replaceChildren();

  // ① 重点（这一遍只改这一件事）
  body.append(el('div', 'plan-focus', plan.focus));
  // ①b 这一句在歌里的位置（2026-10-19）：第几句、歌词、时间、问题字。
  const ctx = plan.context || {};
  if (ctx.line) {
    body.append(el('div', 'plan-context',
      `第 ${ctx.line_no || '?'} 句「${ctx.line}」`
      + (ctx.time ? ` · ${ctx.time}` : '')
      + (ctx.syllables ? ` · 问题在「${ctx.syllables}」` : '')));
  }
  // ② 怎么算练好 —— 这条是"陪练"与"报数"的分界：给了**可验证的目标**。
  //    ★ 2026-10-20：目标从卡底挪到"重点"下面。原来它在一整段
  //    why + 3 条步骤 + 风格说明之后（手机要滚两屏），而它才是
  //    用户真正要记住的一个数。
  if (plan.done) {
    const box = el('div', 'plan-done');
    box.append(el('b', null, '怎么算练好：'));
    box.append(el('span', null, plan.done));
    body.append(box);
  }
  // ③ AI 陪练：只练这一段（2026-10-19）
  //    有 practice_card 才给按钮 —— 没有可定位的参考片段时不造死按钮。
  //    ★ 2026-10-20：主按钮前置到目标下面。原来它在整张卡的最底部、
  //    和"听整句/朗读/AI 唱"挤在同一行之后，用户要读完一屏才看见
  //    "所以现在该干什么"。**处方的主按钮必须在处方旁边。**
  const pc = rep.practice_card;
  if (pc && pc.segment && Array.isArray(pc.segment.notes)
      && pc.segment.notes.length >= 2) {
    const row = el('div', 'plan-practice');
    const btn = el('button', 'btn dark', '练这一句');
    btn.type = 'button';
    btn.title = '只练这一段：' + (pc.label || '');
    btn.onclick = async () => {
      btn.disabled = true;
      const old = btn.textContent;
      btn.textContent = '正在准备这一段…';
      try {
        await startPracticeAtIndex(source, uid, pc.queue || [pc], 0);
      } catch (e) {
        btn.textContent = `⚠️ 打不开微练习：${e.message}`;
        setTimeout(() => { btn.textContent = old; btn.disabled = false; }, 3000);
      }
    };
    row.append(btn);
    row.append(el('span', 'plain',
      '只练这一段；过了再回完整报告。'));
    body.append(row);
  }
  // ④ 示范音（可播放）—— 拿不到就不放按钮，别造死按钮
  const demo = plan.demo || {};
  const hasNotes = Array.isArray(demo.notes) && demo.notes.length > 0;
  const hasClick = demo.kind === 'metronome' && demo.beat_ms;
  if (hasNotes || hasClick) {
    const row = el('div', 'plan-demo');
    const isLine = demo.kind === 'reference_line';
    const rmeta = rep.meta || {};
    const lctx = plan.context || {};
    const refId = rmeta.reference_id;
    // ★ 2026-10-20：歌曲参考的「听整句」不再放合成音，直接放**原唱+伴奏**
    //   的这一段真实音频。合成音的音高/音色会让人怀疑"是不是唱错了"；
    //   原唱+伴奏是这首歌本来的样子。截 2–6 秒，点了立刻响。
    const canSeg = rmeta.reference_source === 'song_vocal' && refId
      && Number.isFinite(Number(lctx.line_start_ms))
      && Number.isFinite(Number(lctx.line_end_ms));
    const btn = el('button', 'btn',
      isLine ? (canSeg ? '▶ 听整句（原唱+伴奏）' : '▶ 听整句') : '▶ 听一遍示范');
    btn.type = 'button';
    btn.onclick = async () => {
      btn.disabled = true;
      const label = btn.textContent;
      btn.textContent = '正在播放…';
      try {
        if (canSeg) {
          const t0 = Math.max(0, Math.round(Number(lctx.line_start_ms)));
          const t1 = Math.max(t0 + 500, Math.round(Number(lctx.line_end_ms)));
          const url = `/api/template/${encodeURIComponent(refId)}/segment.wav`
            + `?kind=mix&start_ms=${t0}&end_ms=${t1}`
            + `&singer_id=${encodeURIComponent(singerId())}`;
          const a = new Audio(url);
          await new Promise((resolve, reject) => {
            a.onended = resolve;
            a.onerror = () => reject(new Error('原唱这一段取不到'));
            a.play().catch(reject);
          });
        } else {
          await playPlanDemo(demo);
        }
      } catch (e) {
        btn.textContent = `⚠️ 播放失败（${e.message}）`;
        setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 2500);
        return;
      }
      btn.textContent = label;
      btn.disabled = false;
    };
    row.append(btn);
    if (ctx.line) {
      // ★ 2026-10-19：真正唱出来（本地 DiffSinger，女声/男声按设置）。
      //   「朗读歌词」已删（2026-10-20 作者：不需要；TTS 只读词，对唱没帮助）。
      //   歌曲参考暂不显示 AI 唱：整首的句子上下文还没接进 AI 接口，
      //   点了会 400；作者也说这轮先不管。
      if (typeof source.aiSingUrl === 'function'
          && (rep.meta || {}).reference_source !== 'song_vocal') {
        const ai = el('button', 'btn', '🎤 AI 唱');
        ai.type = 'button';
        ai.title = '本地 DiffSinger 合成，约 5 秒一句；不影响评分';
        ai.onclick = async () => {
          if (ai.disabled) return;
          ai.disabled = true;
          const old = ai.textContent;
          ai.textContent = 'AI 正在唱…（约 5 秒）';
          try {
            const cfg = await source.getConfig();
            const voice = ((cfg && cfg.feedback) || {}).ai_voice || 'female';
            if (voice === 'off') {
              ai.textContent = 'AI 人声已关闭（去设置打开）';
              setTimeout(() => { ai.textContent = old; ai.disabled = false; }, 2200);
              return;
            }
            const url = source.aiSingUrl({ sessionUid: uid, voice });
            if (!url) throw new Error('mock 模式没有本地 DiffSinger');
            const audio = new Audio(url);
            audio.onended = () => {
              ai.textContent = old;
              ai.disabled = false;
            };
            await audio.play();
            ai.textContent = voice === 'male' ? '🎤 AI 唱（男声）' : '🎤 AI 唱（女声）';
          } catch (e) {
            ai.textContent = `⚠️ AI 唱失败：${e.message}`;
            setTimeout(() => { ai.textContent = old; ai.disabled = false; }, 3500);
          }
        };
        row.append(ai);
      }
    }
    if (demo.hint) {
      row.append(el('span', 'plain',
        canSeg ? '原唱+伴奏的真实这一句' : demo.hint));
    }
    body.append(row);
  }
  // ⑤ 为什么这么说 / 具体怎么练 / 依据 —— 收进可展开区。
  //    ★ 2026-10-20：原来这三块全是展开的，手机上一张计划卡要滚两屏；
  //    而"下一遍只改这一件事"应该是一眼能读完的。结论/目标/按钮在前，
  //    论证与步骤按需展开（默认收起，内容一字不少）。
  const steps = plan.steps || [];
  const facts = plan.facts || [];
  if (plan.why || steps.length || plan.style_note || facts.length) {
    const det = el('details', 'plan-more');
    const sum = el('summary');
    sum.textContent = '为什么这么说'
      + (steps.length ? ` · 怎么练（${steps.length} 步）` : '');
    det.append(sum);
    if (plan.why) det.append(el('p', 'plain plan-why', plan.why));
    if (steps.length) {
      const ol = el('ol', 'plan-steps');
      steps.forEach((s) => ol.append(el('li', null, s)));
      det.append(ol);
    }
    // 风格容忍（2026-10-19）：哪些偏差是合法艺术处理，先说清楚，
    // 避免把"唱成机器"当成练好。
    if (plan.style_note) {
      det.append(el('p', 'plain plan-style', '风格容忍：' + plan.style_note));
    }
    if (facts.length) {
      const wrap = el('div', 'plan-facts');
      facts.forEach((f) => {
        const chip = el('span', 'chip');
        chip.append(el('i', null, f.k));
        chip.append(el('b', null, String(f.v) + (f.unit || '')));
        wrap.append(chip);
      });
      det.append(wrap);
    }
    body.append(det);
  }
  card.hidden = false;
}

/**
 * AI 陪练：进入「只练这一句」。
 *
 * 这里只负责两件事：让后端把参考片段存成短模板；把状态写进
 * sessionStorage 后跳 s2。真正的开麦/录制仍走 s2 现有 B 模式 ——
 * 不另造一条录音管线，避免"微练习的分数和正式报告不是一个引擎"。
 */
async function startPracticeAtIndex(source, originalUid, queue, index, kind = 'note') {
  if (!source || !originalUid) throw new Error('没有会话，无法开始微练习');
  const r = await source.practiceSegment(originalUid, singerId(), index, kind);
  const q = Array.isArray(queue) && queue.length ? queue : [r.card];
  const idx = Number.isInteger(r.segment_index) ? r.segment_index : index;
  const state = {
    originalUid,
    templateId: r.template_id,
    title: r.title || '微练习',
    card: r.card || q[idx],
    queue: q,
    index: idx,
    started: false,
    madeUid: null,
    at: Date.now(),
  };
  try { sessionStorage.setItem('sl.practice', JSON.stringify(state)); }
  catch { /* 隐私模式：本次仍能跳，但结果卡拿不到状态 */ }
  location.hash = '#s2';
}

/**
 * 微练习这一遍的 before/after。
 *
 * 优先用这次报告的 practice_card.before —— 但只有它的片段和练习片段
 * **完全一致**时才能用：下一段练习的模板很短，报告可能给出"这一遍没明显
 * 要改"（practice_card 为 null）或另一个片段，直接拿会张冠李戴。
 * 不一致时从报告的逐音 segments 现算同一段（音准/起音），
 * 整遍量（tempo/dynamic）从 rhythm/expressiveness 取。
 */
function practiceAfter(rep, st) {
  const seg = st && st.card && st.card.segment;
  if (!seg) return null;
  // G58：节拍练习的 before/after 都用节拍命中率，不拿音准冒充。
  if (st.card.kind === 'beat_segment') {
    const bd = (rep && (rep.beat_diagnostic
      || ((rep.meta || {}).beat_diagnostic))) || null;
    return bd && typeof bd.hit_rate === 'number'
      ? { beat_hit_rate: bd.hit_rate } : null;
  }
  const pc = rep.practice_card;
  if (pc && pc.segment && pc.segment.from === seg.from
      && pc.segment.to === seg.to && pc.before) {
    return pc.before;
  }
  const rows = (rep.segments || []).filter(
    (s) => typeof s.ref_note_index === 'number'
      && s.ref_note_index >= seg.from && s.ref_note_index <= seg.to);
  const nums = (key) => rows.map((s) => Math.abs(Number(s[key])))
    .filter((v) => Number.isFinite(v));
  const med = (arr) => {
    if (!arr.length) return null;
    const s = arr.slice().sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };
  const devs = nums('pitch_cents_dev');
  const onsets = nums('onset_dev_ms');
  const out = {
    notes: rows.length,
    pitch_median_abs: med(devs) == null ? null : Math.round(med(devs) * 10) / 10,
    pitch_hit_25: devs.length
      ? Math.round(1000 * devs.filter((d) => d <= 25).length / devs.length) / 10 : null,
    pitch_hit_50: devs.length
      ? Math.round(1000 * devs.filter((d) => d <= 50).length / devs.length) / 10 : null,
    onset_median: med(onsets) == null ? null : Math.round(med(onsets) * 10) / 10,
  };
  if (rep.rhythm && rep.rhythm.tempo_ratio != null) {
    out.tempo_ratio = rep.rhythm.tempo_ratio;
  }
  if (rep.expressiveness && rep.expressiveness.dynamic_range != null) {
    out.dynamic_range = rep.expressiveness.dynamic_range;
  }
  return out;
}

/**
 * 微练习结果卡：before/after + 过没过 + 再练一次/回完整报告。
 *
 * 只在"当前报告就是微练习那一遍"时出现。before 来自点「练这一句」时
 * 保存的原报告 practice_card；after 来自这次微练习报告（同一片段）。
 * 两个都是同一套逐音偏差算出来的，不另造指标。
 */
/**
 * 播放练习计划里的示范音。两种：
 *   · `notes`     —— 参考旋律的一小段（给音准问题：告诉你"应该是这个音"）
 *   · `metronome` —— 参考速度的拍子（给节奏问题：告诉你"应该是这个速度"）
 *
 * ⚠️ 用 Web Audio **现场合成**，不需要任何音频文件：
 *    示范就是"音高 + 时长"，服务端已经把该给的数给全了
 *    （`analysis/practice_plan.py` 的 `_demo_for_span` / `_metronome_demo`），
 *    前端只负责把它变成声音。不占带宽、不用新端点、也不涉及版权
 *    （合成音不是任何人的录音）。
 */
async function playPlanDemo(demo) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) throw new Error('这个浏览器不支持 Web Audio');
  const ctx = new Ctx();
  try {
    const t0 = ctx.currentTime + 0.06;      // 留点起步时间，免得第一个音被吃掉
    let end = t0;

    if (demo.kind === 'metronome' && demo.beat_ms) {
      const beat = demo.beat_ms / 1000;
      const n = Math.max(4, Math.min(16, demo.beats || 8));
      for (let i = 0; i < n; i++) {
        const t = t0 + i * beat;
        const osc = ctx.createOscillator();
        const g = ctx.createGain();
        osc.type = 'square';
        /* 每 4 拍重一下 —— 跟真节拍器一样。
           一串等响的"嗒嗒嗒"听不出小节在哪，用户没法跟着数拍。 */
        const accent = i % 4 === 0;
        osc.frequency.value = accent ? 1600 : 1000;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(accent ? 0.22 : 0.12, t + 0.005);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.06);
        osc.connect(g); g.connect(ctx.destination);
        osc.start(t); osc.stop(t + 0.08);
      }
      end = t0 + n * beat;
      await new Promise((r) => setTimeout(r, (end - ctx.currentTime) * 1000 + 120));
      return;
    }

    for (const n of (demo.notes || [])) {
      const dur = Math.max(120, n.dur_ms || 400) / 1000;
      const t = end;                       // 顺序排下去
      const hz = 440 * Math.pow(2, (n.midi - 69) / 12);
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      // 三角波：比正弦更像"有人声的音"，又不像方波那么刺耳
      osc.type = 'triangle';
      osc.frequency.value = hz;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.20, t + 0.02);         // 起音 20ms
      g.gain.setValueAtTime(0.20, t + dur * 0.7);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur * 0.95); // 收音
      osc.connect(g); g.connect(ctx.destination);
      osc.start(t); osc.stop(t + dur);
      end = t + dur;
    }
    await new Promise((r) => setTimeout(r, Math.max(0, (end - ctx.currentTime) * 1000) + 120));
  } finally {
    /* ★ 必须关掉 AudioContext：浏览器对同时存在的实例有上限（Chrome 是 6 个）。
       不关的话连点几次示范就再也播不出声，而界面上**看不出任何异常**
       —— 正是"点了没反应"那一类。 */
    try { await ctx.close(); } catch { /* 已关 */ }
  }
}

/* ══════════════════════════════════════════════════════════
 * 08 界面状态
 *
 * 这一屏是"出错/空状态长什么样"的对照页。v7 稿里 7 个按钮**一个都没接**，
 * 现在至少做到：**能点的都真的去做对应的事**（见 tools/mark_bindpoints.py
 * 里 s8 的规则），做不到的按钮（「就先这样分析」）直接删掉。
 * ══════════════════════════════════════════════════════════ */
/* ══════════════════════════════════════════════════════════
 * 03 报告：分页卡片 + 最近报告切换（2026-10-21 v2）
 * ----------------------------------------------------------
 * 为什么用自定义 data-rep-* 而不是 data-b：
 *   `tools/binding_keys_check.mjs` 把 data-b / data-act 当契约 key 检查；
 *   这些是纯 UI 锚点，不参与数据契约，放进去只会让检查表变长。
 * ══════════════════════════════════════════════════════════ */
const REP_PAGES = ['summary', 'coach', 'decomp', 'problems'];

function initReportPager(root, ctx) {
  const report = root.querySelector('.report');
  const nav = root.querySelector('.rep-nav');
  if (!report || !nav) return null;
  const cards = [...report.querySelectorAll('[data-rep-page]')];
  const buttons = [...nav.querySelectorAll('[data-rep-page-btn]')];
  const arrows = [...nav.querySelectorAll('[data-rep-step]')];
  const count = nav.querySelector('[data-rep-count]');
  let index = 0;

  const setPage = (key, { scroll = false } = {}) => {
    let i = REP_PAGES.indexOf(key);
    if (i < 0) i = 0;
    index = i;
    const page = REP_PAGES[index];
    report.dataset.page = page;
    cards.forEach((el) => el.classList.toggle('rep-on', el.dataset.repPage === page));
    buttons.forEach((b) => {
      const on = b.dataset.repPageBtn === page;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
    });
    arrows.forEach((a) => {
      const dir = Number(a.dataset.repStep) || 0;
      a.disabled = (dir < 0 && index === 0) || (dir > 0 && index === REP_PAGES.length - 1);
    });
    if (count) count.textContent = `0${index + 1} / 0${REP_PAGES.length}`;
    // 页面从 display:none 变可见后，canvas 的 clientWidth 才恢复；让 s3 重画证据图
    window.dispatchEvent(new CustomEvent('sl:report-page', { detail: { page } }));
    if (scroll) {
      // 把分页条滚到吸顶位置，新页内容从它下面开始。
      // 页面本身不够高、滚不到那个位置时（如「拆解」页只有 996px），
      // 回到页首，让页头完整出现，而不是卡在半截标题上。
      const navH = nav.getBoundingClientRect().height || 52;
      const target = report.getBoundingClientRect().top + window.scrollY - navH - 8;
      const maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      const top = target > maxY - 8 ? 0 : target;
      try { window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' }); }
      catch { window.scrollTo(0, Math.max(0, top)); }
    }
  };

  buttons.forEach((b) => b.addEventListener('click', () => setPage(b.dataset.repPageBtn, { scroll: true })));
  arrows.forEach((a) => a.addEventListener('click', () => {
    const n = index + (Number(a.dataset.repStep) || 0);
    if (n >= 0 && n < REP_PAGES.length) setPage(REP_PAGES[n], { scroll: true });
  }));
  nav.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const n = index + (e.key === 'ArrowRight' ? 1 : -1);
    if (n >= 0 && n < REP_PAGES.length) setPage(REP_PAGES[n], { scroll: true });
  });
  setPage('summary');
  return { setPage, current: () => REP_PAGES[index] };
}

/** 时间：优先 MM-DD HH:mm，坏值原样截断。 */
function repWhen(v) {
  const d = new Date(v);
  if (Number.isFinite(d.getTime())) {
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  return String(v || '').slice(5, 16).replace('T', ' ');
}

const repShort = (v, n = 18) => {
  const s = String(v || '未命名');
  return s.length > n ? s.slice(0, n) + '…' : s;
};

/** 报告下拉里的歌名：去掉括号里的长解释，只留“曲名 · 版本”。 */
const repSongShort = (v, n = 16) => {
  const s = String(v || '未命名');
  const parts = s.split('·').map((x) => x.trim()).filter(Boolean);
  const base = parts.length > 1
    ? `${parts[0]} · ${parts[1].replace(/[（(].*$/, '').trim()}`
    : s;
  return repShort(base, n);
};

/**
 * 报告切换器。
 * 数据来源是 `/singer/{sid}/history`（已经是“同一演唱者”的范围）；
 * 第一项固定是**最新一次**，其余是最近 12 次，方便回头看不只一首歌。
 * 切换时把 URL 的 `?sess=` 去掉、改写 sessionStorage，再让路由器重载 s3。
 * 为什么必须去掉 URL 里的 sess：那个参数优先级最高，否则用户下一次唱完，
 * 报告页仍会打开旧链接里的那一次（这是一个会静默发生的错误）。
 */
async function fillReportPicker(root, source, uid, ctx, rep) {
  const pick = root.querySelector('[data-rep-pick]');
  const box = pick && pick.closest('.rep-pick');
  if (!pick || !box) return;
  let hist = [];
  // 50 条足够覆盖“每首歌的最新一次”；真正展示时只取最近的 10 条 + 每首歌的 latest
  try { hist = await source.getHistory(getSingerId(), 50); } catch { hist = []; }
  hist = Array.isArray(hist) ? hist.filter((h) => h && h.session_uid) : [];
  if (!hist.length) { box.hidden = true; return; }

  const latest = hist[0];
  const meta = (rep && rep.meta) || {};
  const add = (parent, value, label) => {
    const o = document.createElement('option');
    o.value = value;
    o.textContent = label;
    parent.append(o);
    return o;
  };
  const group = (label) => {
    const g = document.createElement('optgroup');
    g.label = label;
    pick.append(g);
    return g;
  };

  add(pick, '__latest__',
    `最新一次 · ${repSongShort(latest.reference_title)} · ${repWhen(latest.created_at)}`);

  /* 用户真正的需求是“唱过的歌的最新报告”，而不只是最近 12 次 ——
     如果最近一直在练同一首，别的歌会被挤出 12 条。这里按参考去重，
     每首歌只保留最新一次，单独成组。
     ⚠️ 不在这里显示分数：history 里的 total 是**入库时的旧口径**，
        而报告页会按当前 KTV 口径重算（例如 35.5 → 56.6）。
        同一个报告两个数并排会让人以为系统坏了；分数以打开后的页面为准。 */
  const gSong = group('每首歌最新');
  const seen = new Set();
  hist.forEach((h) => {
    const key = h.reference_id || h.reference_title || h.session_uid;
    if (seen.has(key)) return;
    seen.add(key);
    add(gSong, h.session_uid,
      `${repSongShort(h.reference_title)} · ${repWhen(h.created_at)}`);
  });

  const gRecent = group('最近报告');
  hist.slice(0, 10).forEach((h) => {
    add(gRecent, h.session_uid,
      `${repSongShort(h.reference_title)} · ${repWhen(h.created_at)}`);
  });

  // 当前报告不在最近 50 条里（很旧的分享链接）——补一条，避免下拉显示成“最新”
  if (!hist.some((h) => h.session_uid === uid)) {
    add(pick, uid,
      `当前 · ${repSongShort(meta.reference_title)} · ${repWhen(meta.created_at)}`);
  }

  box.hidden = false;
  pick.title = '切换最近报告；「每首歌最新」按参考去重，最近报告是全部 take';
  // 选中当前：最新一次选中“最新一次”，其余选中自己的 uid（可能同时出现在两个分组里）
  pick.value = (uid === latest.session_uid) ? '__latest__' : uid;

  pick.onchange = async () => {
    const val = pick.value;
    const next = val === '__latest__' ? latest.session_uid : val;
    if (!next || next === uid) return;
    pick.disabled = true;
    try {
      const q = new URLSearchParams(location.search);
      q.delete('sess');
      const qs = q.toString();
      history.replaceState(null, '', location.pathname + (qs ? `?${qs}` : '') + location.hash);
    } catch { /* 老浏览器 */ }
    try { sessionStorage.setItem('sl.sess', next); } catch { /* 隐私模式 */ }
    if (ctx && ctx.router && ctx.router.go) {
      await ctx.router.go('s3', { silent: true });
    } else {
      location.reload();
    }
  };
}



async function s8(root, ctx) {
  void ctx;
  const note = (el, text, ok) => {
    if (!el) return;
    let box = root.querySelector('[data-b="perm-note"]');
    if (!box) {
      box = document.createElement('p');
      box.dataset.b = 'perm-note';
      box.className = 'tip';
      box.style.marginTop = '10px';
      el.closest('.acts').insertAdjacentElement('afterend', box);
    }
    box.textContent = text;
    box.style.color = ok ? 'var(--good)' : 'var(--acc-ink)';
  };

  /* 「允许使用麦克风 / 重新申请权限」= **真的去要权限**。
     为什么这一屏要真做：契约 §3.2 的 `MIC_DENIED` 是**真实用户最容易撞上的**
     一个状态，而"被拒绝之后怎么恢复"只有浏览器知道 —— 光画一张图，
     用户照着点还是不知道去哪儿改。这里至少把"申请 → 结果"这条路打通，
     并把浏览器给的原话（NotAllowedError 等）显示出来。
     ⚠️ 必须由用户手势触发，不能在 mount 时自动调 —— 否则浏览器直接拒绝，
        而且用户看不到权限弹窗，只会看到一句"被拒绝了"。 */
  const ask = async (btn) => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      note(btn, '这个浏览器不给网页用麦克风（需要 https 或 localhost）。');
      return;
    }
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = '等待你的选择…';
    let stream = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      note(btn, '✅ 麦克风已允许。回到「开始练习」就能唱了 —— ' +
        '浏览器会记住这个选择。', true);
    } catch (e) {
      // 照实说：NotAllowedError（点了阻止）/ NotFoundError（没有麦克风）/
      // NotReadableError（被别的程序占用）对用户来说是完全不同的三件事。
      const M = {
        NotAllowedError: '你点了阻止，或者浏览器记住了上次的"阻止"。点地址栏左边的锁图标，把麦克风改成"允许"。',
        PermissionDeniedError: '浏览器拒绝了。点地址栏左边的锁图标改成"允许"。',
        NotFoundError: '没有找到麦克风设备 —— 检查一下有没有插好。',
        NotReadableError: '麦克风被别的程序占用了（比如正在开的会议软件），关掉它再试。',
      };
      note(btn, `⚠️ 没能拿到麦克风：${M[e.name] || e.message || e.name}`);
    } finally {
      // ★ 一定要把轨道关掉：这一屏只是"试一下权限"，
      //   拿着一条活着的音轨不放，标签页上会一直亮着录音红点。
      if (stream) stream.getTracks().forEach((t) => t.stop());
      btn.disabled = false;
      btn.textContent = old;
    }
  };
  root.querySelectorAll('[data-b="perm-ask"], [data-b="perm-retry"]').forEach((b) => {
    b.addEventListener('click', () => ask(b));
  });
  return { bound: true };
}

/* ══════════════════════════════════════════════════════════
 * 公共小工具
 * ══════════════════════════════════════════════════════════ */

/**
 * 当前演唱者 id。
 *
 * ★ 权威来源是 `bootstrap().singers[0].singer_id`（api 源返回**真实名册**）——
 *   s1 会把**它返回的那个 id** 记下来（见 s1）。
 *   这里不许自己编一个默认值：编出来的 id 与后端记录用的 id 不一致时，
 *   成长档案/进步曲线会**安静地查错人** —— 页面正常、就是不显示数据，
 *   而"没数据"和"查错人"在界面上长得一模一样。
 *
 * ★ G3（2026-10-01）：读写**全部**收敛到 `data/identity.js`。
 *   这里只做转发，不再自己碰 storage ——
 *   `sl.singer` 从 sessionStorage 搬到 localStorage、换人时清 `sl.sess`，
 *   这些规则都写在那一个文件里。两份实现必然有一天不同步，
 *   而不同步的表现是"某几屏显示 A、某几屏显示 B"（**静默串数据**）。
 */
const DEFAULT_SINGER = DEFAULT_SINGER_ID;
function singerId() {
  return getSingerId();
}

/** 两个分数之间的差：任一为 null 就返回 null（不能拿 0 当"没评估"）。 */
const diff = (a, b) =>
  (a === null || a === undefined || b === null || b === undefined) ? null : a - b;

/**
 * 当前该看哪一次会话。
 *
 * URL 的 `?sess=` 优先（可以分享/收藏某一次），其次是 sessionStorage
 * （刚练完自动带过来），最后**回退到历史里最近的一次**。
 *
 * ★ 为什么必须有最后这层回退：
 *   直接打开 `#s3` / `#s4`（刷新、从侧栏点进来、关掉标签页再回来）时，
 *   前两个来源都是空的 —— 于是页面显示**原型稿里写死的数字（78/86/91）**。
 *   那比"空"更坏：空态至少诚实，假数字会被当成自己的成绩。
 *   `wantRetry` 为真时优先挑"有 retry_of_uid 的那一次"——
 *   复练对比页要的正是"练过之后"的那一遍。
 */
async function currentSession(source, { wantRetry = false } = {}) {
  const q = new URLSearchParams(location.search);
  const fromUrl = q.get('sess');
  if (fromUrl) return fromUrl;
  try {
    const s = sessionStorage.getItem('sl.sess');
    if (s) return s;
  } catch { /* 隐私模式 */ }
  try {
    const hist = await source.getHistory(singerId(), 20);
    if (!Array.isArray(hist) || !hist.length) return null;
    if (wantRetry) {
      const r = hist.find((h) => h.retry_of_uid);
      if (r) return r.session_uid;
    }
    return hist[0].session_uid;
  } catch {
    return null;
  }
}

/** 涨跌标签。null → 「本次未评估」，绝不当成 0 分。 */
function deltaLabel(d, digits = 1) {
  if (d === null) return '本次未评估';
  const v = Number(d);
  if (Math.abs(v) < 0.05) return '持平';
  return (v > 0 ? '↑' : '↓') + Math.abs(v).toFixed(digits);
}

/* ══════════════════════════════════════════════════════════
 * 04 复练对比
 * ══════════════════════════════════════════════════════════ */
async function s4(root, ctx) {
  void ctx;
  const { source, isMock } = await import('./data/index.js');
  const charts = await import('./charts.js');
  const q = new URLSearchParams(location.search);

  // 这一屏要的是"复练的那一遍"。优先从 URL / sessionStorage 拿，
  // 没有再退回当前这位演唱者最近一次练习。
  let uid = q.get('sess');
  if (!uid) { try { uid = sessionStorage.getItem('sl.sess'); } catch { uid = null; } }
  const sid = singerId();

  const setHead = (a, b, note) => {
    setText(root, 'cmp-t-a', a);
    setText(root, 'cmp-t-b', b);
    if (note) {
      const el = root.querySelector('[data-b="cmp-verdict"]');
      if (el) el.textContent = note;
    }
  };

  /* 空态/失败态：**整块**换成一张空态卡。
     ⚠️ 只改抬头是不够的：下面两张对比卡里还有原型稿的
        「第 3 句 / 太平 / 明显改善 / 4.1 → 9.6」。用户会把它当成真的结论。 */
  const emptyCard = root.querySelector('#cmpEmpty');
  const cmpWrap = root.querySelector('#cmpWrap');
  const cmpGate = root.querySelector('#cmpGate');
  const cmpSummary = root.querySelector('#cmpSummary');
  const showEmpty = (title, note) => {
    // ★ 2026-10-20：空态连**顶部的总结卡**一起收起来。
    //   原来只藏了下面的两张图，顶部还留着"还没有可对比的两遍"+
    //   三个黑色破折号 + 一个空徽标，截图里像一张没填完的表。
    if (cmpSummary) cmpSummary.hidden = true;
    if (cmpWrap) cmpWrap.hidden = true;
    if (cmpGate) cmpGate.hidden = true;
    if (emptyCard) emptyCard.hidden = false;
    setText(root, 'cmp-empty-title', title || '怎么开始对比');
    setText(root, 'cmp-empty-note', note || '先按同一首曲目唱一遍，再唱第二遍。');
    setText(root, 'cmp-title', '还没有可对比的两遍');
    setText(root, 'delta', '—');
    setText(root, 'cmp-pitch', '—');
    setText(root, 'cmp-dyn', '—');
    setText(root, 'cmp-phrase', '—');
  };
  const showCompare = () => {
    if (emptyCard) emptyCard.hidden = true;
    if (cmpSummary) cmpSummary.hidden = false;
    if (cmpWrap) cmpWrap.hidden = false;
    if (cmpGate) cmpGate.hidden = false;
  };

  if (isMock && !uid) {
    // mock 模式下取样例的"复练"报告，否则本屏全是稿子里的死数字，
    // **根本没法验证接线有没有生效**（s3 第一次就踩过这个）。
    uid = 'sess_mock_retry';
  }
  // 真实模式右回退：优先挑"有 retry_of_uid 的那一次"——
  // 本屏要的正是"练过之后"的那一遍，拿最近一次普通练习比不出任何东西。
  if (!uid && !isMock) uid = await currentSession(source, { wantRetry: true });
  if (!uid) {
    showEmpty('怎么开始对比',
      '还没有可对比的两遍。先按同一首曲目唱一遍，再唱第二遍；'
      + '系统才能把两遍逐项摆在一起。');
    return { bound: false, reason: 'no session' };
  }

  let cmp;
  try {
    cmp = await source.getComparison(uid);
  } catch (e) {
    /* ★ 两种"这个会话指针不能用了"，都要**清掉指针**并说人话（2026-10-01）：
         · FORBIDDEN  —— 它属于别人（你可能切换过身份）
         · NOT_FOUND  —— 它已经不存在（被"清空"删了 / 是很久以前留下的旧值）
       之前会把裸 uid 摆到用户面前，并断言"这一遍已经存进成长档案了" ——
       而这一遍根本不在库里。
        ⚠️ 2026-10-18：CONFLICT 是"还在算"，NOT_COMPARABLE 是"两首不同"，
           两者都**不是**"记录没了"，不能清指针、也不能当空态里说"去唱第一遍"。 */
    const stale = e && (e.code === 'FORBIDDEN' || e.code === 'NOT_FOUND');
    if (stale) {
      try { sessionStorage.removeItem('sl.sess'); } catch { /* 隐私模式 */ }
      showEmpty('这条练习已经不在了',
        (e.code === 'FORBIDDEN'
          ? '上一条记录属于别人 —— 你可能切换过身份。'
          : '上一条记录已经不在了（可能被「清空」删掉了）。') +
        '已经清掉过期的会话指针，重唱一遍即可。');
      return { bound: false, reason: e.code };
    }
    if (e && e.code === 'CONFLICT') {
      showEmpty('两遍里还有一遍在分析',
        '等精算跑完再来看对比，通常几秒。');
      return { bound: false, reason: e.code };
    }
    if (e && e.code === 'NOT_COMPARABLE') {
      showEmpty('两次练的不是同一首', e.message || '换一首歌练的不能直接比较。');
      return { bound: false, reason: e.code };
    }
    // 其余情况：复练对比需要"上一遍 + 这一遍"。只有一遍时后端会拒绝 ——
    // 这是正常状态，应该解释清楚，而不是报一个看不懂的错。
    showEmpty('还不能对比', `还不能对比：${e.message}。这一遍已经存进成长档案了。`);
    return { bound: false, reason: e.message };
  }

  const { base, retry, delta } = cmp || {};
  if (!base || !retry) {
    // ★ 不假设"调用成功 = 数据齐全"。
    //   实测（2026-09-23）：这个接口**成功返回了**，但对象里没有 base/retry ——
    //   于是下一行读 `.score` 直接抛 TypeError，而抛出的位置在 setHead 之后、
    //   pair() 之前 ⇒ 页面上出现"抬头是新的、三行数字还是原型稿的 74 → 88"
    //   这种**半真半假的混合态**。混合态比全空危险得多。
    showEmpty('还不能对比',
      `返回的数据里没有可比的两遍（拿到的是 ` +
      `${JSON.stringify(Object.keys(cmp || {}))}）。`);
    return { bound: false, reason: 'comparison shape mismatch',
             got: Object.keys(cmp || {}) };
  }
  showCompare();
  setHead('第一次', '第二次', null);

  setText(root, 'delta', `情感分 ${show(base.expressiveness.score)} → ` +
                         `${show(retry.expressiveness.score)}　` +
                         `${deltaLabel(delta.expr)}`);
  const deltaEl = root.querySelector('[data-b="delta"]');
  if (deltaEl) {
    const dv = Number(delta.expr);
    deltaEl.classList.toggle('bad', Number.isFinite(dv) && dv <= -3);
    deltaEl.classList.toggle('mid', Number.isFinite(dv) && dv > -3 && dv < 3);
  }

  // 三行对比：左=第一次，右=复练
  const pair = (k, a, b, digits = 1) =>
    setText(root, k, `${show(a, digits)} → ${show(b, digits)}`);

  pair('cmp-pitch', base.pitch.score, retry.pitch.score);
  // ★ 「音量有变化了吗」必须与**图上画的**是同一个量。
  //   原型这里写死的是 4.1 → 9.6，而图上画的是响度包络；两者对不上的话
  //   就出现"图上一条平线、旁边写着起伏 9.6 dB"的自相矛盾。
  //   现在两边都取 rms_envelope 的 p95−p5（dB），图与数同源。
  const dynA = charts.rmsRange(base.evidence || {});
  const dynB = charts.rmsRange(retry.evidence || {});
  pair('cmp-dyn', dynA, dynB);
  pair('cmp-phrase', base.expressiveness.phrase_dynamics,
       retry.expressiveness.phrase_dynamics);

  // 曲目标题：原型写死「第 3 句 · 复练前后」，这里按真实参考说清楚是"跟什么比"。
  //   meta.reference_id 就是为此存的（模板有自己的身份，见 web/templates.py）
  const mBase = base.meta || {};
  const mRetry = retry.meta || {};
  setText(root, 'cmp-title',
    `${mRetry.reference_title || mBase.reference_title
       || mRetry.reference_id || mBase.reference_id || '本次练习'} · 复练前后`);

  // 两张卡的说明：按**实测的起伏幅度**给话，不再写死数字
  const describe = (rng, better) => {
    if (rng === null) return '这次没有音量包络数据，看不出起伏。';
    if (rng < 5) return `音量基本是一条平线（起伏只有 ${rng} dB）—— 从头到尾一个大小声。`;
    if (rng < 10) return `音量有一定的起伏（${rng} dB），但还没有明显地放开。`;
    return better
      ? `出现了"先渐强、后渐弱"的形状，音量起伏达到 ${rng} dB。`
      : `这一遍的音量起伏已经比较大（${rng} dB）。`;
  };
  setText(root, 'cmp-tip-a', describe(dynA, false));
  setText(root, 'cmp-tip-b', describe(dynB, true));

  // 等级标签：由**实际变化量**决定，不再写死"太平/明显改善"
  const dExpr = diff(retry.expressiveness.score, base.expressiveness.score);
  const lv = (v) => (v === null ? '—'
    : v >= 8 ? '明显改善' : v >= 3 ? '略有改善' : v <= -3 ? '反而退步' : '基本持平');
  const setLv = (key, txt, kind) => {
    const el = root.querySelector(`[data-b="${key}"]`);
    if (!el) return;
    el.textContent = txt;
    el.classList.toggle('L-good', kind === 'good');
    el.classList.toggle('L-bad', kind === 'bad');
  };
  setLv('cmp-lv-a', '基准', null);
  setLv('cmp-lv-b', lv(dExpr),
       dExpr !== null && dExpr >= 3 ? 'good'
       : dExpr !== null && dExpr <= -3 ? 'bad' : null);

  // 曲线：画真实响度包络（charts.drawCmp 会优先用 rms_envelope）
  charts.drawCmp('cmpA', 'flat', base.evidence || {});
  charts.drawCmp('cmpB', 'good', retry.evidence || {});

  // 结论条：如实说，包括"没变好"
  const verdict = root.querySelector('[data-b="cmp-verdict"]');
  const gate = root.querySelector('#cmpGate');
  if (gate) {
    const good = dExpr !== null && dExpr >= 3;
    const bad = dExpr !== null && dExpr <= -3;
    gate.classList.toggle('ok', good);
    gate.classList.toggle('bad', bad);
    const code = gate.querySelector('.code');
    if (code) code.textContent = good ? 'LOG·OK' : bad ? 'LOG·退' : 'LOG·平';
  }
  if (verdict) {
    if (dExpr === null) {
      verdict.textContent = '两次里有未评估的项，暂时算不出变化。';
    } else if (dExpr >= 3) {
      verdict.innerHTML = '<b>这一遍确实比上一次有变化。</b>' +
        `情感分 +${dExpr.toFixed(1)}。两次的分数差是真实的；` +
        '至于这个变化是不是因为那条建议，系统分不清 —— 但变化本身能量出来。';
    } else if (dExpr > -3) {
      verdict.innerHTML = '<b>这一次变化不大。</b>' +
        '不一定是没听懂 —— 也可能这一遍状态不同。建议专门只改一件事再唱一次。';
    } else {
      verdict.innerHTML = '<b>这一次反而退步了。</b>' +
        `情感分 ${dExpr.toFixed(1)}。唱歌状态起伏是正常的，别灰心 —— ` +
        '先回到上一遍唱得好的那条路上，再改别的地方。';
    }
  }
  void sid;
  return { bound: true, uid, delta: dExpr };
}

/* ══════════════════════════════════════════════════════════
 * 05 成长档案
 * ══════════════════════════════════════════════════════════ */
async function s5(root, ctx) {
  void ctx;
  const { source } = await import('./data/index.js');
  const charts = await import('./charts.js');
  const sid = singerId();

  let prog = null; let hist = []; let prof = null;
  /**
   * 把成长档案整屏清成**诚实的空态**（2026-10-01）。
   *
   * ★ 为什么必须有：早退路径原来只改了一行说明文字就 return ——
   *   屏幕上于是留着**原型稿的叙事**：
   *     「取成长数据失败：没有演唱者 S01」  ← 真话
   *     「4 周 12 次练习，总分从 61 涨到 78」 ← 稿子里的假话，就在它下面
   *   用户看到的是"报错的同时还在说我进步了"。**混合态比全空危险得多** ——
   *   这和 s3 那次（`78/86/91/62` 配"还没有评测记录"）是同一个形状，
   *   当时修了 s3/s4，**漏了 s5**。
   *
   * 为什么不是"什么都不显示"：空着会被读成 0 分。所以显式写「—」/「还没有记录」。
   */
  const blankGrowth = (reason) => {
    setText(root, 'prog-meta', reason);
    const pr = root.querySelector('[data-b="prog-pick"]');
    if (pr) { pr.replaceChildren(); pr.hidden = true; }
    const prl = root.querySelector('[data-b="prog-pick-label"]');
    if (prl) prl.hidden = true;
    // 没有数据时画布必须整块收起来：canvas 隐藏了，但 .chartwrap 的
    // 边框还在，会留一个 18px 高、660px 宽的空框（截图实测像渲染坏了）。
    const cw0 = root.querySelector('#prog');
    const w0 = cw0 ? cw0.closest('.chartwrap') : null;
    if (w0) w0.hidden = true;
    const lg0 = root.querySelector('.legend');
    if (lg0) lg0.hidden = true;
    // 三个分量 + 六根横条：全部写成「—」，别留稿子里的数字
    for (const k of ['pitch', 'rhythm', 'dyn', 'phrase', 'vib', 'rubato']) {
      setText(root, `bd-${k}`, '—');
      setBar(root, `bd-${k}-bar`, 0);
    }
    setText(root, 'bd-action', '');
    const hist = root.querySelector('[data-b="hist"]');
    if (hist) hist.replaceChildren();
    const note = root.querySelector('[data-b="prog-note"]');
    if (note) {
      note.innerHTML = '<b>还没有练习记录。</b>去「开始练习」唱一遍，'
        + '这一页才会有内容 —— 现在这些位置留空，'
        + '不是"你得 0 分"。';
    }
    /* 风格卡：原型里没有 `data-b`（只有一个 class），所以按 class 取。
       ⚠️ 只把**里面的字**换成诚实空态，不整块 hidden ——
          空态在原型里本来就有专门文案（"还没有数据 / 练过至少一次之后…"），
          藏掉反而少一句解释。 */
    const card = root.querySelector('.stylecard');
    if (card) {
      const k = card.querySelector('.k');
      const name = card.querySelector('.name, h4, b');
      if (k) k.textContent = 'Your Style · 还没有数据';
      if (name) name.textContent = '练过至少一次之后，这里会显示你的演唱特点。';
    }
    charts.drawProg();
  };

  try {
    // ★ 三个请求各自兜底，不要用 Promise.all 的整体 reject 来决定成败。
    //   原来 getProgress 抛 404（演唱者不存在）就整体进 catch，
    //   而另外两个是分开 catch 的 —— 这个不对称让早退路径成了"半清空"。
    prog = await source.getProgress(sid);
  } catch (e) {
    blankGrowth(`取成长数据失败：${e.message}`);
    return { bound: false, error: e.message };
  }
  try { hist = await source.getHistory(sid, 8); } catch { hist = []; }
  try { prof = await source.getProfile(sid); } catch { prof = null; }

  const sp = (prog && prog.series) || {};
  const labels = (prog && prog.labels) || [];
  const times = (prog && prog.times) || [];
  const n = labels.length || (sp.total || []).length;
  const refs = sp.reference_id || [];
  const refTitles = sp.reference_title || [];
  const modes = sp.mode || [];

  /* ★ 2026-10-20：内部 id → 人话。
     历史记录里原来直接显示 `REF_SELF`，模板显示 `tpl_xxxx` ——
     用户看不出自己练的是哪首，而这一屏正是用来看"我练了什么、有没有进步"的。 */
  const REF_LABEL = {
    REF_SELF: '自比 · 第一遍',
    REF_TWINKLE_1: '小星星 · 第一句',
    REF_TWINKLE_2: '小星星 · 前两句',
    REF_TWINKLE_FULL: '小星星 · 完整四句',
    REF_TWINKLE_6: '小星星 · 六句',
    REF_TWINKLE_6B: '小星星 · 六句（含换气）',
    REF_ODE_1: '欢乐颂 · 第一句',
    REF_TEMPLATE: '自建模板',
  };
  const refLabel = (id, title, mode) => title || REF_LABEL[id]
    || (String(id || '').startsWith('tpl_') ? '自建模板' : '')
    || ({ A: '教材曲目', B: '跟唱模板', C: '上传录音', D: 'MIDI 参考' }[mode] || '练习');

  // ── 按参考曲目分组：只有"同一首"的分数才能连成趋势 ──
  //   ★ 2026-10-20 重做。原来把 23 次全画在一条线上，其中 20 多次分别属于
  //     不同模板（微练习每段都会新建一个 tpl_*），图上于是有 20 多条
  //     "换曲目"竖线，文字互相压成一团；而不同参考的分数本来不可比。
  const groups = (() => {
    const m = new Map();
    for (let i = 0; i < n; i++) {
      const id = refs[i] || '__none__';
      if (!m.has(id)) {
        m.set(id, { id, label: refLabel(refs[i], refTitles[i], modes[i]), idx: [] });
      }
      m.get(id).idx.push(i);
    }
    const out = [...m.values()];
    out.sort((a, b) => b.idx.length - a.idx.length);
    return out;
  })();
  const pickEl = root.querySelector('[data-b="prog-pick"]');
  const pickRow = root.querySelector('[data-b="prog-pick-label"]');
  if (pickEl) {
    pickEl.replaceChildren();
    if (groups.length > 1) {
      const all = document.createElement('option');
      all.value = '__all__';
      all.textContent = `全部 ${n} 次（只打点，不连线）`;
      pickEl.append(all);
      groups.forEach((g) => {
        const o = document.createElement('option');
        o.value = g.id;
        o.textContent = `${g.label} · ${g.idx.length} 次`;
        pickEl.append(o);
      });
      pickEl.value = groups[0].id;
      pickEl.hidden = false;
      if (pickRow) pickRow.hidden = false;
    } else {
      // 只有一首：不摆一个只有一项的下拉框，直接在图注里说
      const o = document.createElement('option');
      o.value = groups.length ? groups[0].id : '__all__';
      o.textContent = groups.length ? `${groups[0].label} · ${n} 次` : '还没有';
      pickEl.append(o);
      pickEl.hidden = true;
      if (pickRow) pickRow.hidden = true;
    }
  }

  setText(root, 'prog-meta',
    n ? `${n} 次练习 · ${labels[0] || ''} 起` : '还没有练习记录');

  const note = root.querySelector('[data-b="prog-note"]');
  const chartWrap = (() => {
    const cv = root.querySelector('#prog');
    return cv ? cv.closest('.chartwrap') : null;
  })();
  const legend = root.querySelector('.legend');
  const drawFor = (sel) => {
    const all = sel === '__all__';
    const g = groups.find((x) => x.id === sel) || groups[0] || null;
    const idx = all ? Array.from({ length: n }, (_, i) => i) : (g ? g.idx : []);
    if (!idx.length) { charts.drawProg(); return; }
    if (chartWrap) chartWrap.hidden = false;
    if (legend) legend.hidden = false;
    const arr = (key) => idx.map((i) => (sp[key] || [])[i]);
    const total = arr('total');
    const expr = arr('expr');
    const pitch = arr('pitch');
    // x 轴刻度：同一天练很多次时日期全一样，用 HH:MM 才有区分度
    const sameDay = new Set(idx.map((i) => labels[i])).size <= 1;
    const xlab = idx.map((i) => (sameDay && times[i])
      ? `${labels[i]} ${times[i]}` : (labels[i] || ''));
    charts.drawProg({
      series: { total, expr, pitch },
      labels: xlab,
      dotsOnly: all,
    });

    if (note) {
      if (all) {
        note.innerHTML = `全部 <b>${n}</b> 次练习分别用了 <b>${groups.length}</b> 个参考，`
          + `不同曲子的分数<b>不可比</b>，所以这里只打点、不连线。`
          + `<br><span style="color:var(--ink3)">上面选一首具体的曲子，`
          + `才能看出这一首有没有越唱越好。</span>`;
        return;
      }
      const t = total.map((v, k) => [idx[k], v]).filter(([, v]) => typeof v === 'number');
      const ex = expr.map((v, k) => [idx[k], v]).filter(([, v]) => typeof v === 'number');
      const pi = pitch.map((v, k) => [idx[k], v]).filter(([, v]) => typeof v === 'number');
      if (t.length >= 2) {
        const dt = t[t.length - 1][1] - t[0][1];
        const dex = ex.length >= 2 ? ex[ex.length - 1][1] - ex[0][1] : null;
        const dpi = pi.length >= 2 ? pi[pi.length - 1][1] - pi[0][1] : null;
        const word = (v, unit = '分') => Math.abs(v) < 0.5 ? '基本持平'
          : (v > 0 ? `涨了 ${v.toFixed(0)} ${unit}` : `降了 ${Math.abs(v).toFixed(0)} ${unit}`);
        const skipped = total.filter((v) => v === null || v === undefined).length;
        note.innerHTML =
          `这一首（${g.label}）练了 <b>${g.idx.length}</b> 次，`
          + (Math.abs(dt) < 0.5
            ? `总分基本没变（<b>${t[t.length - 1][1].toFixed(0)}</b>）`
            : `总分 <b>${t[0][1].toFixed(0)} → ${t[t.length - 1][1].toFixed(0)}</b>`)
          + `。`
          + (dex !== null ? `情感分${word(dex)}；` : '')
          + (dpi !== null ? `音准${word(dpi)}。` : '')
          + (skipped
            ? `<br><span style="color:var(--ink3)">有 ${skipped} 次没测出分数，`
              + `曲线在那里是断开的 —— <b>断线表示"没测"，不是"得了 0 分"</b>。</span>`
            : '');
      } else if (t.length === 1) {
        note.innerHTML = `这一首（${g.label}）只练了 <b>1</b> 次，还看不出趋势。`
          + `再多练几次这条线才有意义。`
          + (groups.length > 1
            ? `<br><span style="color:var(--ink3)">你一共练过 ${groups.length} 个参考，`
              + `上面可以换一首看。</span>` : '');
      } else {
        note.textContent = '这一首还没有可比较的分数 —— '
          + '每次的三大分量缺一个就不会给总分。';
      }
    }
  };
  if (pickEl) {
    pickEl.addEventListener('change', () => drawFor(pickEl.value));
  }
  if (!n) {
    if (pickRow) pickRow.hidden = true;
    if (pickEl) pickEl.hidden = true;
    if (chartWrap) chartWrap.hidden = true;
    if (legend) legend.hidden = true;
    if (note) {
      note.innerHTML = '<b>还没有练习记录。</b>去「开始练习」唱一遍，'
        + '这一页才会有内容 —— 现在这些位置留空，不是"你得 0 分"。';
    }
    charts.drawProg();
  } else {
    drawFor(pickEl && !pickEl.hidden && pickEl.value
      ? pickEl.value : (groups[0] ? groups[0].id : '__all__'));
  }

  // ── 历史记录：整块重画 ──
  const wrap = root.querySelector('[data-b="hist"]');
  if (wrap) {
    wrap.replaceChildren();
    if (!hist.length) {
      const p = document.createElement('div');
      p.className = 'hist';
      p.style.color = 'var(--ink3)';
      p.textContent = '还没有练习记录。';
      wrap.append(p);
    } else {
      hist.forEach((h, i) => {
        const row = document.createElement('div');
        row.className = 'hist';
        const d = document.createElement('span');
        d.className = 'd';
        /* ★ 日期要**从字符串里认出 YYYY-MM-DD**，不能按固定下标切。
           踩过的坑：第一版写 `String(created_at).slice(5, 10)` ——
           mock 源给的是 '2026-09-14'，切出来是 '09-14'，看着挺好；
           而 api 源给的是 ISO 时间戳 '2026-09-23T10:15:02'，同一下标切出来是
           **'9T10:'**，再经 `.slice(5)` 兜底就成了 '29795' 这种鬼东西。
           两种源的格式本来就不同，那就老老实实用正则去匹配。 */
        const ds = String(h.created_at || '');
        const dm = ds.match(/(\d{4})-(\d{2})-(\d{2})/);
        const tm = ds.match(/[T ](\d{2}:\d{2})/);
        /* ★ 2026-10-20：同一天可能练很多遍，只显示 10-19 无法区分。
           日期 + 时间一起给；列宽随之放宽（.hist .d）。 */
        d.textContent = dm
          ? `${dm[2]}-${dm[3]}${tm ? ' ' + tm[1] : ''}`
          : (ds.slice(0, 10) || '—');
        const s = document.createElement('span');
        s.className = 's';
        s.textContent = show(h.total_score, 0);
        const t2 = document.createElement('span');
        t2.className = 't';
        // 优先显示**真实的参考名**（模式 B 的模板标题/自建曲名），
        // 没有再退回人话化的参考/模式名 —— 只说"跟唱模板"等于没说跟什么唱。
        // ★ 2026-10-20：不能再把 `REF_SELF` / `tpl_xxxx` 这种内部 id 直接丢给用户。
        t2.textContent = refLabel(h.reference_id, h.reference_title, h.mode);
        const up = document.createElement('span');
        /* ★ 2026-10-20：只和**同一首参考**上一次比。
           原来直接拿列表下一条（上一次练习）比 —— 昨天的小星星 60 分和
           今天自建模板 30 分摆在一起写"↓30"，那个差没有任何意义。
           与 s4「不同曲目不能比」是同一条规矩，只是这里在列表里。 */
        const sameRef = (x) => (x.reference_id || null) === (h.reference_id || null);
        const prev = hist.slice(i + 1).find(sameRef);
        const dd = prev ? diff(h.total_score, prev.total_score) : null;
        if (dd === null) {
          up.className = '';
          up.textContent = h.total_score === null ? '未评估' : '';
          up.style.color = 'var(--ink4)';
        } else {
          up.className = dd >= 0 ? 'up' : 'dn';
          up.textContent = (dd >= 0 ? '↑' : '↓') + Math.abs(dd).toFixed(0);
          up.title = `与同一首参考的上一次（${String(prev.created_at || '').slice(0, 10)}）比`;
        }
        row.append(d, s, t2, up);
        /* ★ 2026-10-18：历史行可点开报告。
           原来只是 4 个 span —— 用户看得到分数，却没有任何入口回到那一次。
           s3 早就有 `?sess=` / `sl.sess` 的读取逻辑，缺的只是这里的入口。 */
        row.tabIndex = 0;
        row.setAttribute('role', 'button');
        row.title = '打开这一遍的报告';
        row.setAttribute('aria-label',
          `打开 ${d.textContent} 这一遍的报告，`
          + (h.total_score === null ? '未评估' : `${s.textContent} 分`));
        const openReport = () => {
          try { sessionStorage.setItem('sl.sess', h.session_uid); } catch { /* 隐私模式 */ }
          location.hash = '#s3';
        };
        row.addEventListener('click', openReport);
        row.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            openReport();
          }
        });
        wrap.append(row);
      });
    }
  }

  // ── 强项与短板：六维横条 ──
  // ★ 用 profile.normalized（评分器当时用的归一化值），不是前端自己重算的。
  const nz = (prof && prof.normalized) || {};
  const bars = [
    ['pitch', nz.pitch],
    ['rhythm', nz.rhythm],
    ['dyn', nz.dynamic_range === null || nz.dynamic_range === undefined
      ? null : nz.dynamic_range * 100],
    ['phrase', nz.phrase_dynamics === null || nz.phrase_dynamics === undefined
      ? null : nz.phrase_dynamics * 100],
    ['vib', nz.vibrato === null || nz.vibrato === undefined ? null : nz.vibrato * 100],
    ['rubato', nz.rubato === null || nz.rubato === undefined ? null : nz.rubato * 100],
  ];
  bars.forEach(([k, v]) => {
    setText(root, `bd-${k}`, show(v, 0));
    setBar(root, `bd-${k}-bar`, v === null || v === undefined ? 0 : v);
    // 未评估的条不能画成"0 分" —— 加斜纹标记（shell.css 的 .na）
    const bar = root.querySelector(`[data-b="bd-${k}-bar"]`);
    if (bar) bar.classList.toggle('na', v === null || v === undefined);
  });

  // ── 风格卡 ──
  if (prof) {
    setText(root, 'style-label', prof.cluster_label || '—');
    setText(root, 'style-summary', prof.summary || '');
    setText(root, 'style-note',
      `这是这 ${prof.session_count || 0} 遍的中位数，样本少，先当参考 —— 不是长期画像。`);
    const chips = root.querySelector('[data-b="style-chips"]');
    if (chips) {
      chips.replaceChildren();
      (prof.chips || []).forEach((c) => {
        const sp2 = document.createElement('span');
        sp2.textContent = c;
        chips.append(sp2);
      });
      if (!(prof.chips || []).length) {
        const sp2 = document.createElement('span');
        sp2.textContent = '样本还不够';
        chips.append(sp2);
      }
    }
  }

  // ── 「下一遍先练」：只从**参与评分**的项里挑 ──
  //   ★ 2026-10-20：原来把句内推进/节奏弹性也算进"最低项"，
  //     于是可能出现"下一遍先练：节奏弹性（14）"—— 而这两项
  //     2026-10-17 起就**不计分**，且 rubato 低正是"唱得自由"，
  //     把它当短板会教用户改掉一个不是缺点的地方。
  const action = root.querySelector('[data-b="bd-action"]');
  if (action) {
    const LABEL = { pitch: '音准', rhythm: '节奏', dyn: '音量起伏',
                    phrase: '句内推进', vib: '尾音控制', rubato: '节奏弹性' };
    const SCORED = new Set(['pitch', 'rhythm', 'dyn', 'vib']);
    const ranked = bars.filter(([k, v]) => SCORED.has(k) && typeof v === 'number')
                       .sort((a, b) => a[1] - b[1]);
    if (!ranked.length) {
      action.textContent = '练过一次之后，这里会指出最该改的那一项。';
    } else {
      const [wk, wv] = ranked[0];
      const TIP = {
        pitch: '每天挑一句慢唱，把每个字都唱"站住"再走下一个音。',
        rhythm: '跟着原曲打拍子唱，先把拍点唱准，再管好不好听。',
        dyn: '每天挑一句练"先小声后放开"，把起伏做出来。',
        phrase: '把一句分成"起—推—收"三段，最后一段故意放轻。',
        vib: '把句尾最后一个音拖长两拍，让它自然地抖起来。',
        rubato: '别怕自由一点 —— 句尾稍微拖一点，反而更有味道。',
      };
      /* ★ 2026-10-18：措辞从"你目前最弱的是"改成"这几遍里相对最低的一项"。
         项目记忆 §66.7 已经用四步实验证伪「长期弱点」：人内变异 ≥ 人间变异，
         同一文件跑两次逐位相同。所以这里只能描述**这几遍**，不能当成人格标签。 */
      action.innerHTML = `这几遍里相对最低的是<b>${LABEL[wk]}</b>（${wv.toFixed(0)}）。` +
        (TIP[wk] || '') +
        '<br><span style="font-size:11.5px;color:var(--ink3)">' +
        '样本少，先别把它当长期结论；多练几遍再看这条线。</span>';
    }
  }

  return { bound: true, sessions: n };
}

/* ══════════════════════════════════════════════════════════
 * 06 实验数据
 * ══════════════════════════════════════════════════════════ */
async function s6(root, ctx) {
  void ctx;
  const { source } = await import('./data/index.js');
  const charts = await import('./charts.js');

  let ex = null;
  try {
    ex = await source.getExperimentSummary();
  } catch (e) {
    setText(root, 'exp-scatter-note', `取实验数据失败：${e.message}`);
    charts.drawScatter();
    return { bound: false, error: e.message };
  }

  const st = ex.stats || {};
  // ⚠️ 这些数**现在就是 0 / null**（真人评分还没采集）。必须如实显示：
  //    显示 0 而不是编一个数，是因为这一页是给答辩看的 ——
  //    编出来的数会在答辩现场被问穿。
  setText(root, 'st-singers', `${show(st.singers, 0)}\u00a0人`);
  if (st.singers === null) setText(root, 'st-singers', '—');
  setText(root, 'st-samples', st.samples === null ? '—' : `${st.samples}\u00a0条`);
  setText(root, 'st-raters', st.raters === null ? '—' : `${st.raters}\u00a0人`);
  setText(root, 'st-rho', st.rho === null || st.rho === undefined ? '—' : show(st.rho, 2));

  const pts = (ex.scatter || []).filter((p) => Array.isArray(p) &&
    typeof p[0] === 'number' && typeof p[1] === 'number');
  charts.drawScatter({ scatter: pts.map(([x, y]) => [x, y]) });

  const sn = root.querySelector('[data-b="exp-scatter-note"]');
  if (sn) {
    if (!pts.length) {
      sn.innerHTML = '还没有配对数据 —— 这一页要等<b>真人盲评</b>采集完才有内容。' +
        '现在横轴纵轴都是空的，不是"系统与人完全不一致"，而是<b>还没开始比</b>。';
    } else {
      sn.innerHTML = `每个点是一条录音。横轴是评分者的<b>平均分</b>，纵轴是<b>系统表现力分</b>（换算到 1–5）。<br>` +
        `当前 <b>Spearman ρ = ${show(st.rho, 2)}</b>（目标 &gt; 0.6；n=${pts.length} 条录音）。`;
    }
  }

  // 权重表：直接用后端给的数，标出哪些还是占位值
  (ex.weights || []).forEach((w) => {
    const tr = root.querySelector(`[data-b="w-${w.id}"]`);
    if (!tr) return;
    const tds = tr.querySelectorAll('td');
    if (tds.length < 3) return;
    tds[1].textContent = w.beta === null ? '—' : show(w.beta, 2);
    tds[2].textContent = w.p === null ? '待标定'
      : (w.significant ? `p ${w.p}` : `p ${w.p}`);
    tds[2].style.color = w.p === null ? 'var(--ink4)'
      : (w.significant ? 'var(--good)' : 'var(--ink3)');
    if (w.placeholder) tds[1].style.color = 'var(--ink3)';
  });

  const wn = root.querySelector('[data-b="exp-weight-note"]');
  if (wn) {
    /* ★ 2026-10-17 改写：原来只有两档 ——
         `weights_are_placeholder` 真 →「上表是占位权重」
         假 →「Ridge 回归，已做 VIF 共线性检验」
       而第二档是**假的**：从来没跑过回归。一旦标定过版本号、标志变假，
       界面就会声称做过一件没做过的事。
       ⇒ 现在分开说三件事，各自如实。 */
    const ver = ex.weight_version || '?';
    const dropped = (ex.dropped_metrics || []).join(' / ');
    const DL = { dynamic_range: '音量起伏', phrase_dynamics: '句内推进',
                 vibrato: '尾音控制', rubato: '节奏弹性',
                 onset_style: '起音方式', brightness: '音色明亮度' };
    const dropEl = root.querySelector('[data-b="exp-dropped"]');
    if (dropEl) {
      dropEl.textContent = ex.dropped_metrics && ex.dropped_metrics.length
        ? `已移出评分：${ex.dropped_metrics.map((k) => DL[k] || k).join('、')}`
          + ' —— 它们量到的东西是真的，但目前没有证据支持把它们当"表现力好坏"的刻度。'
          + '报告里仍然显示，只是不计分。'
        : '当前没有指标被移出评分。';
    }
    if (ex.weights_are_placeholder) {
      wn.innerHTML =
        `模型 <b>R² = ${ex.r2 === null || ex.r2 === undefined ? '未跑回归' : show(ex.r2, 2)}</b>` +
        `（权重版本 <b>${ver}</b>）。<br>` +
        `<span style="color:var(--acc-ink)">⚠️ <b>权重仍是占位值，不是回归结果</b></span>` +
        `（没有跑过 Ridge 回归；盲评数量目前只够算相关性，不够标定六项权重）。<br>` +
        (ex.normalization_calibrated
          ? `✅ <b>归一化区间</b>已按真实素材（10 条）重标 —— ` +
            `原区间会让两项指标对真人恒满分。<br>`
          : '') +
        (dropped
          ? `两项没进权重：<b>${dropped}</b>（原因见下）。`
          : '');
    } else {
      wn.innerHTML =
        `模型 <b>R² = ${show(ex.r2, 2)}</b>（Ridge 回归，已做 VIF 共线性检验）。`;
    }
  }

  /* ★ 验收指标条：**整条**由后端数据渲染（2026-09-24 重写）。
     改之前的样子，以及为什么必须改：
       · `views/s6.js:65` 静态写着「三条可量化的验收指标全部达标：」
       · `views/s6.js:68` 静态盖着徽章「PASS·3/3」
       · 只有中间那个 `<span data-b="exp-gate">` 是动态的
     于是同一张卡里同时出现"全部达标"和"第三条还不能宣称"，徽章还说 3/3 ——
     而 rho 恒为 null 时，`rhoOk` 永远为假，**"全部达标"那句话根本不可达**：
     它只可能来自静态 HTML。一个句子永远不会由数据产生、却一直显示在屏幕上，
     那它就不是文案，是**伪造的结论**。
     现在：开头那句、中间那句、右边徽章全部由 `benchmarks` 渲染；
     数字只从后端来（含出处），前端不再写任何基准值。 */
  const gate = root.querySelector('[data-b="exp-gate"]');
  const gateBox = root.querySelector('[data-b="exp-gate-box"]');
  const gateHead = root.querySelector('[data-b="exp-gate-head"]');
  const gateCode = root.querySelector('[data-b="exp-gate-code"]');

  const bms = Array.isArray(ex.benchmarks) ? ex.benchmarks : [];
  // 后端没给 benchmarks（老版本 / 读失败）时，**不要把写死的结论留在屏幕上**。
  // 这正是本项目反复踩的"空着和没接线长得一样"——所以这里显式降级成"取不到"。
  const bmHtml = bms.map((b) => {
    const val = b.pending
      ? '<b>待验证</b>（需真人盲评）'
      : `${show(b.measured, b.decimals ?? 1)}${b.unit || ''}`;
    const mark = b.pending ? '' : (b.ok ? ' ✅' : ' ❌');
    const title = b.source ? ` title="出处：${b.source}"` : '';
    return `<span${title}>${b.label} ${val}（目标 ${b.cmp_text}）${mark}</span>`;
  });

  if (gate) {
    gate.innerHTML = bms.length
      ? bmHtml.join(' · ')
      : '取不到验收指标（后端没返回 benchmarks）—— 这里**故意留空**，' +
        '不显示任何既往结论。';
  }
  if (gateHead) {
    const s = ex.benchmarks_summary || {};
    const total = s.total ?? bms.length;
    const ok = s.ok ?? bms.filter((b) => b.ok).length;
    const pending = s.pending ?? bms.filter((b) => b.pending).length;
    gateHead.innerHTML = !bms.length
      ? '验收指标：取不到'
      : (pending
        ? `${total} 条可量化验收指标已达标 <b>${ok}</b> 条，` +
          `<b style="color:var(--acc-ink)">${pending} 条待验证</b>：`
        : (ok === total
          ? `三条可量化的验收指标全部达标：`
          : `验收指标 <b>${ok}/${total}</b> 达标，其余未达标：`));
  }
  if (gateCode) {
    // 徽章也要跟着数据变 —— 否则它就是一个永远说"通过"的图章
    const s = ex.benchmarks_summary || {};
    const ok = s.ok ?? bms.filter((b) => b.ok).length;
    const total = s.total ?? bms.length;
    const pending = s.pending ?? bms.filter((b) => b.pending).length;
    gateCode.textContent = !bms.length ? 'NO DATA'
      : (pending ? `PASS·${ok}/${total} · 待验证 ${pending}`
                 : (ok === total ? `PASS·${ok}/${total}`
                                 : `FAIL·${ok}/${total}`));
  }
  if (gateBox && bms.length) {
    // 未全达标就不该挂着绿色的"全部达标"样式
    const allOk = (ex.benchmarks_summary || {}).ok === bms.length;
    gateBox.classList.toggle('ok', !!allOk);
    gateBox.classList.toggle('warn', !allOk);
  }
  return { bound: true, points: pts.length, rho: st.rho ?? null };
}

/* ══════════════════════════════════════════════════════════
 * 07 设置
 * ══════════════════════════════════════════════════════════ */
async function s7(root, ctx) {
  void ctx;

  /* ★ 2026-10-18：设置页分组（常用 / 反馈 / 数据与隐私 / 关于与演示）。
     原来 7 张卡纵向堆 2936px，AI key、音域、隐私混在一起。
     现在一次只显示一组；06/08 两个内部页面也收在「关于与演示」里。
     Tab 是原生 button，Enter/Space 自动触发 click。 */
  const sTabs = [...root.querySelectorAll('[data-settings-tab]')];
  const sGroups = [...root.querySelectorAll('[data-settings-group]')];
  const showSettings = (g) => {
    const group = sGroups.some((x) => x.dataset.settingsGroup === g) ? g : 'common';
    sGroups.forEach((x) => { x.hidden = x.dataset.settingsGroup !== group; });
    sTabs.forEach((t) => {
      const on = t.dataset.settingsTab === group;
      t.classList.toggle('on', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    try { sessionStorage.setItem('sl.settingsTab', group); } catch { /* 隐私模式 */ }
  };
  sTabs.forEach((t) => {
    t.addEventListener('click', () => showSettings(t.dataset.settingsTab));
  });
  let startTab = 'common';
  try { startTab = sessionStorage.getItem('sl.settingsTab') || 'common'; } catch { /* 隐私模式 */ }
  showSettings(startTab);

  const { source } = await import('./data/index.js');
  const charts = await import('./charts.js');

  let cfg;
  try {
    cfg = await source.getConfig();
  } catch (e) {
    setText(root, 'range-note', `读设置失败：${e.message}`);
    return { bound: false, error: e.message };
  }

  /* ══════════════════════════════════════════════════════════
   * 演唱者（G3 多用户，2026-10-01）
   * ══════════════════════════════════════════════════════════
   * 这一段是整屏里**唯一会改变"你在看谁的数据"**的地方，所以它必须：
   *   ① 说清当前是谁（不是"某个人"，是名字 + id + 练了几次）；
   *   ② 换人之后**立刻**把这一屏和侧栏都刷新（否则屏幕上还是旧人的名字）；
   *   ③ 删除时**如实报告删了什么、没删什么**（录音文件故意不删，见 §58.3）。
   *
   * ⚠️ 整块是现画的（`innerHTML`），原型稿里没有"多用户"这一态。
   *    用 `textContent` 拼节点而不是模板字符串拼 HTML：演唱者名字是
   *    **用户输入**，直接插进 HTML 就等于给自己开了一个 XSS 口子 ——
   *    而这个服务的隔离性全靠"只服务本机/局域网"（契约 §8.1），
   *    不能指望外网进不来。
   */
  const body = root.querySelector('[data-b="singer-body"]');

  /** 建元素的小工具（**只用 textContent**，见上面那条 XSS 的说明）。 */
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  };
  const btn = (label, act, cls = 'go') => {
    const b = el('button', cls, label);
    b.type = 'button';
    b.dataset.act = act;
    return b;
  };

  /* "上一次操作的结果"存在这里，而不是直接写进 DOM（2026-10-01 实测踩到）：
     第一版先 `note('已切换到…')` 再 `redraw()`，而 redraw 会
     `replaceChildren()` 整块重画 —— **刚写进去的那句话立刻被冲掉**。
     表现是"点了切换，名单变了，但没有任何文字说明发生了什么"，
     而"没有说明"与"切换没生效"在截图里几乎一样。
     所以消息先存下来，由 redraw 负责渲染（它是唯一的重画入口）。 */
  let pendingNote = null;
  const note = (msg, warn = false) => {
    pendingNote = { msg, warn };
    const box = body && body.querySelector('[data-role="singer-note"]');
    if (box) paintNote(box);
  };
  const paintNote = (box) => {
    box.replaceChildren();
    if (!pendingNote) {
      // 没有待报告的事 → 说清"当前是谁、换人会怎样"，而不是留一片空白
      box.className = 'who-note';
      box.append(el('b', null, '换人之后，第 03 / 04 / 05 屏的数据都会跟着换。'));
      box.append(document.createTextNode(
        '侧栏底部那一行永远显示当前是谁 —— 因为"这是谁的数据"看不出来时，'
        + '"数据对不对"就无从判断。'));
      return;
    }
    box.className = 'who-note' + (pendingNote.warn ? ' warn' : '');
    const { msg } = pendingNote;
    if (msg instanceof Node) box.append(msg);
    else box.textContent = String(msg || '');
  };

  const redraw = async () => {
    if (!body) return;
    let list;
    try {
      list = await loadRoster();
    } catch (e) {
      body.replaceChildren(el('div', 'who-note warn',
        `读取演唱者名册失败：${e.message}。` +
        `（当前身份仍是 ${getSingerId()} —— 界面不会替你换人）`));
      return;
    }
    const { singers, current, healedFrom, created } = list;
    body.replaceChildren();

    /* 空名册（2026-10-01）：`loadRoster` 现在会**自动补一位**，
       所以正常情况下走不到这里。能走到只有两种：
         · 后端不支持新建（老数据源/桩）
         · 新建请求失败
       两种都该说清"为什么空"，而不是留一句"一个新库不该这样"让用户干瞪眼。 */
    if (!singers.length) {
      body.append(el('div', 'who-note warn',
        created
          ? `已自动为你登记了第一位演唱者（${created}）。`
          : '名册是空的，而且**自动登记没成功** —— 点下面的「新建演唱者」建一位再练。' +
            '（练习记录必须挂在某位演唱者名下，否则成长档案不知道算谁的）'));
    } else if (created) {
      body.append(el('div', 'who-note',
        `这是个新库，已自动登记了第一位演唱者（${created}）。` +
        `想改名字就在下面重命名。`));
    }
    if (healedFrom) {
      pendingNote = { warn: true, msg:
        `原来的演唱者 ${healedFrom.from} 在名册里已经不存在了`
        + `（可能在别处被删掉了），已自动切到 ${healedFrom.to}。` };
    }

    const listWrap = el('div', 'picklist');
    singers.forEach((s) => {
      const row = el('div', 'pickrow');
      row.dataset.sid = s.singer_id;
      const info = el('div');
      const t = el('div', 't');
      t.append(document.createTextNode(s.name || s.singer_id));
      if (s.singer_id === current) {
        const cur = el('span', 'm', '　← 当前');
        t.append(cur);
      }
      info.append(t);
      info.append(el('div', 'm',
        `${s.singer_id} · ${s.sessions ?? '?'} 次练习` +
        ` · ${s.templates ?? 0} 个跟唱模板`));
      row.append(info);

      const acts = el('div', 'who-acts');
      if (s.singer_id !== current) {
        const b = btn('切到这个人', 'switch');
        b.addEventListener('click', async () => {
          await switchSinger(s.singer_id);
          const nb = el('span');
          nb.append(document.createTextNode(`已切换到「${s.name}」（${s.singer_id}）。`));
          nb.append(document.createTextNode(
            '第 05 屏的成长档案、第 03/04 屏的报告现在都指着他 —— '));
          const go = el('button', 'go', '去看他的成长档案');
          go.type = 'button';
          go.dataset.goto = 's5';
          nb.append(go);
          note(nb);
          await redraw();            // 侧栏由 identity 的变化事件自己刷新
        });
        acts.append(b);
      } else {
        const tag = btn('当前', 'current');
        tag.disabled = true;
        tag.setAttribute('aria-pressed', 'true');
        acts.append(tag);
      }
      const ren = btn('改名', 'rename');
      ren.addEventListener('click', () => {
        // 改名用行内输入框，不用 `window.prompt`：
        // prompt 在某些浏览器里会被拦、也无法在自动截图里复现
        // （而"点不动"与"改不了"在界面上长得一样）。
        const form = el('div', 'who-form');
        const input = el('input');
        input.type = 'text';
        input.maxLength = 24;
        input.value = s.name || '';
        input.placeholder = '新的名字';
        input.dataset.role = 'rename-input';
        const ok = btn('保存', 'rename-save');
        const cancel = btn('取消', 'rename-cancel');
        cancel.addEventListener('click', () => { redraw(); });
        ok.addEventListener('click', async () => {
          const nm = input.value.trim();
          if (!nm) { note('名字不能是空的。', true); input.focus(); return; }
          try {
            const r = await renameSingerApi(s.singer_id, nm);
            note(`已改名：${r.renamed_from || '（原名）'} → ${nm}。` +
                 '练习记录不受影响（记录挂在 singer_id 上，不挂在名字上）。');
            await redraw();
          } catch (e) {
            note(`改名失败：${e.message}。名字没有改动。`, true);
          }
        });
        input.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter') ok.click();
          if (ev.key === 'Escape') redraw();
        });
        form.append(input, ok, cancel);
        row.replaceChildren(info, form);
        input.focus();
        input.select();
      });
      acts.append(ren);
      const del = btn('删除', 'delete');
      del.addEventListener('click', async () => {
        /* ⚠️ 二次确认必须**逐条说清删什么、留什么**（§54.4 的规矩）。
           "录音文件不会删"这一条尤其要说 —— 不说的话，用户以为删干净了，
           而磁盘上还留着；说了他才有的选（要连文件一起清走设置页的"清空"）。 */
        const okGo = window.confirm(
          `删除演唱者「${s.name}」？\n\n` +
          `会删掉：\n` +
          `　· 他的 ${s.sessions ?? '?'} 条练习记录与进步曲线\n` +
          `　· 他的 ${s.templates ?? 0} 个跟唱模板\n\n` +
          `不会删：\n` +
          `　· 录音文件（文件名里没有可靠的演唱者信息，猜错会删掉别人的录音）\n` +
          `　· 实验数据（研究样本）\n\n` +
          `删掉之后**无法恢复**。`);
        if (!okGo) return;
        try {
          const r = await removeSinger(s.singer_id);
          const nb = el('span');
          nb.append(document.createTextNode(
            `已删除「${r.name || s.singer_id}」：${r.sessions_deleted} 条练习记录、` +
            `${r.templates_deleted} 个模板。`));
          nb.append(document.createTextNode(
            `录音文件删了 ${r.files_deleted ?? 0} 个 —— ${r.skipped_reason || ''} `));
          if (r.switchedTo) {
            nb.append(document.createTextNode(`已自动切到 ${r.switchedTo}。`));
          }
          note(nb, true);
          await redraw();
        } catch (e) {
          note(`删除失败：${e.message}。他的记录没有被删。`, true);
        }
      });
      acts.append(del);
      row.append(acts);
      listWrap.append(row);
    });
    body.append(listWrap);

    // ── 新建 ──
    const form = el('div', 'who-form');
    const input = el('input');
    input.type = 'text';
    input.maxLength = 24;
    input.placeholder = '新演唱者的名字（比如「同桌」）';
    input.dataset.role = 'new-singer-name';
      input.setAttribute('aria-label', '新演唱者的名字');
    const add = btn('新建演唱者', 'create');
    const doCreate = async () => {
      const nm = input.value.trim();
      if (!nm) { note('先给他起个名字。', true); input.focus(); return; }
      try {
        const r = await createSinger(nm);
        // 新建之后**切到他**：用户建一个人就是为了用他。
        await switchSinger(r.singer.singer_id);
        note(`已新建「${nm}」（${r.singer.singer_id}）并切换到他。` +
             '他现在还没有练习记录 —— 去「开始练习」唱一遍。');
        await redraw();
      } catch (e) {
        note(`新建失败：${e.message}`, true);
      }
    };
    add.addEventListener('click', doCreate);
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') doCreate(); });
    form.append(input, add);
    body.append(form);

    // 提示区永远存在（`note()` 要往里写）。它是**唯一**报告"刚才那一动作
    // 到底做了什么"的地方 —— 删了谁、删了几条、文件为什么还在。
    const noteBox = el('div', 'who-note');
    noteBox.dataset.role = 'singer-note';
    paintNote(noteBox);
    body.append(noteBox);
  };

  await redraw();

  const save = async (patch) => {
    try { await source.putConfig(patch); } catch (e) {
      console.warn('[s7] 保存设置失败：', e.message);
    }
  };

  /**
   * 给开关用的保存：**等它写完**，并且**把失败说出来**（2026-10-01）。
   *
   * ★ 为什么不能用上面那个 fire-and-forget 的 `save`：
   *   ① 开关是"点完就走"的交互 —— 用户点了立刻切屏或刷新，而请求还在飞。
   *      实测：点完立刻刷新，后端还是旧值（**设置看起来没保存**），
   *      而界面上按钮是亮的、什么都没提示。
   *   ② `save` 把失败吞成一条 `console.warn` —— 用户永远看不到。
   *      这跟本项目一直在修的"静默失效"是同一类：**设置了但没生效**。
   *   所以这里：先写完再返回；失败就把话说在设置页上。
   */
  const saveAndReport = async (patch, noteKey, okText) => {
    try {
      await source.putConfig(patch);
      if (noteKey) setText(root, noteKey, okText || '已保存。');
      return true;
    } catch (e) {
      const msg = `⚠️ 没保存上：${e.message} —— 这个开关下次打开会恢复原样。`;
      if (noteKey) setText(root, noteKey, msg);
      // 有专门的说明位就写在说明位，没有就挂到反馈卡的那行小字上
      if (!noteKey) setText(root, 'mic-note', msg);
      return false;
    }
  };

  // ── 音域 ──
  const rg = cfg.range || {};
  const seg = root.querySelector('#rangeSeg');
  /** 页面上当前选中的音域预设名（给文案用；取不到就退回后端 preset） */
  let rangeName = rg.preset || '—';
  if (seg) {
    const btns = [...seg.querySelectorAll('button')];
    // 把后端的 lo/hi 标到对应的预设上。后端 preset 是「自动收窄」（按录音自适应），
    // 页面上对应「自动检测」那颗 —— 名字不同，要**显式映射**，不能靠字符串相等。
    const AUTO = '自动检测';
    let on = btns.find((b) => +b.dataset.lo === +rg.lo_midi
                            && +b.dataset.hi === +rg.hi_midi);
    if (!on && rg.preset) on = btns.find((b) => b.textContent.trim() === rg.preset);
    if (!on && /自动|自适应|收窄/.test(String(rg.preset || ''))) {
      on = btns.find((b) => b.textContent.trim() === AUTO);
    }
    if (!on && btns.length) on = btns[btns.length - 1];
    btns.forEach((b) => b.classList.toggle('on', b === on));
    if (on) rangeName = on.textContent.trim();
    btns.forEach((b) => {
      b.onclick = () => {
        btns.forEach((x) => x.classList.toggle('on', x === b));
        charts.updateRangeBand(root);
        const lo = +b.dataset.lo, hi = +b.dataset.hi;
        setText(root, 'range-note', `当前是「${b.textContent.trim()}」（MIDI ${lo}–${hi}）。` +
          (b.textContent.trim() === AUTO
            ? '系统会在每次录音时按你的人声自动收窄音域 —— 拿不准就选这个。'
            : '换一首更低的歌时如果提示「听不清低音」，换成更低的音域或「自动检测」。'));
        save({ range: { lo_midi: lo, hi_midi: hi, preset: b.textContent.trim() } });
      };
    });
    charts.updateRangeBand(root);
  }
  // ⚠️ 只在这里设一次。第一版在 if(seg) 内外各设了一次，
  //    结果外面那次把里面按按钮名拼的文案**覆盖掉**了 ——
  //    表现是「当前音域 MIDI 36–84（自动收窄）」，而后端 preset 是个给人看的词，
  //    不该直接念出来。文案要说"页面上选中的是哪个预设"。
  setText(root, 'range-note',
    `当前是「${rangeName}」（MIDI ${rg.lo_midi ?? '—'}–${rg.hi_midi ?? '—'}）。` +
    '唱比较低的歌时如果提示「听不清低音」，换成「男中音」或「自动检测」就好了。');

  // ── 开关：把写死的 onclick 换掉，改成真的写回后端 ──
  /**
   * 开关绑定。
   *
   * ★ `onChange` 这个回调是 2026-10-01 加的，为了修一个真 bug：
   *   「显示专业术语」这一项**不只是存一个偏好** —— 它要切换
   *   `<body class="pro">`，而 `.jargon { display:none }` 正是挂在那上面。
   *   原来的写法（切自己的 class + 存后端）看起来一切正常：按钮亮了、
   *   刷新后还是亮的（后端存住了）—— 但**页面上什么都没变**。
   *   这正是本项目最贵的那类 bug：**看得见、动不了，而且看起来是好的**。
   */
  const bindSw = (key, get, group, field, onChange) => {
    const el = root.querySelector(`[data-b="${key}"]`);
    if (!el) return null;
    el.classList.toggle('on', !!get());
    el.onclick = async () => {
      const prev = el.classList.contains('on');
      el.classList.toggle('on');
      const now = el.classList.contains('on');
      if (typeof onChange === 'function') onChange(now);
      // ★ 等保存结果：失败就**把开关拨回去**并说明原因（2026-10-01）。
      //   拨回去是必须的 —— 否则界面上显示"已开启"，而后端还是关着，
      //   下次打开就变回去，用户只会觉得"这软件记不住设置"。
      const okSaved = await saveAndReport({ [group]: { [field]: now } });
      if (!okSaved) {
        el.classList.toggle('on', prev);
        if (typeof onChange === 'function') onChange(prev);
      }
      return okSaved;
    };
    return el;
  };
  const fb = cfg.feedback || {};
  bindSw('cfg-ai-comment', () => fb.ai_comment, 'feedback', 'ai_comment');
  /* ↓ 术语开关：必须真的切 `body.pro`，否则四块专业术语永远看不到。
     它和报告页那个按钮是**同一个开关的两张脸**，所以两处都走
     `setProPersister` 这一条保存路径 —— 各存各的必然有一天不同步。 */
  bindSw('cfg-jargon', () => fb.show_jargon, 'feedback', 'show_jargon', setPro);
  /* 引导音轨音量：只影响播放的 4 拍 + 旋律，不碰麦克风、不影响评分。
     `oninput` 只更新显示，`onchange` 才写库 —— 拖动时不该每 5% 打一次请求。 */
  const volEl = root.querySelector('[data-b="cfg-guide-volume"]');
  const volVal = root.querySelector('[data-b="cfg-guide-volume-val"]');
  const volNote = root.querySelector('[data-b="cfg-guide-volume-note"]');
  if (volEl) {
    let saved = Math.max(0, Math.min(1,
      typeof fb.guide_volume === 'number' ? fb.guide_volume : 0.8));
    const paint = (v) => {
      if (volVal) volVal.textContent = `${Math.round(v * 100)}%`;
    };
    volEl.value = String(saved);
    paint(saved);
    volEl.oninput = () => {
      paint(Math.max(0, Math.min(1, Number(volEl.value) || 0)));
      if (volNote) volNote.textContent = '';
    };
    volEl.onchange = async () => {
      const v = Math.max(0, Math.min(1, Number(volEl.value) || 0));
      const ok = await saveAndReport({ feedback: { guide_volume: v } },
        'cfg-guide-volume-note',
        `已保存：引导音轨音量 ${Math.round(v * 100)}%。`);
      if (ok) {
        saved = v;
        paint(v);
      } else {
        volEl.value = String(saved);
        paint(saved);
      }
    };
  }
  /* ★ 2026-10-19 评分宽容度：只改练习计划/微练习的目标线，不改六指标原始分。 */
  const styleEl = root.querySelector('[data-b="cfg-style-tolerance"]');
  const styleNote = root.querySelector('[data-b="cfg-style-note"]');
  if (styleEl) {
    const labels = { strict: '严格', standard: '标准', expressive: '宽松' };
    const desc = {
      strict: '音准 20 音分 / 速度 ±10% / 起音 60ms / 动态 10dB',
      standard: '音准 30 音分 / 速度 ±15% / 起音 80ms / 动态 8dB',
      expressive: '音准 40 音分 / 速度 ±20% / 起音 100ms / 动态 6dB',
    };
    let styleSaved = ['strict', 'standard', 'expressive'].includes(fb.style_tolerance)
      ? fb.style_tolerance : 'standard';
    const paintStyle = (v) => {
      styleEl.querySelectorAll('button[data-style]').forEach((b) => {
        const on = b.dataset.style === v;
        b.classList.toggle('on', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      if (styleNote) {
        styleNote.textContent = `当前：${labels[v]}（${desc[v]}）。`
          + '严格适合考试/比赛，宽松保留更多个人处理。';
      }
    };
    paintStyle(styleSaved);
    styleEl.querySelectorAll('button[data-style]').forEach((b) => {
      b.onclick = async () => {
        const v = b.dataset.style;
        if (!v || v === styleSaved) return;
        const ok = await saveAndReport({ feedback: { style_tolerance: v } },
          'cfg-style-note',
          `已保存：评分宽容度「${labels[v]}」。下一次报告按这个标准出计划。`);
        if (ok) styleSaved = v;
        paintStyle(styleSaved);
      };
    });
  }
  /* ★ 2026-10-19 AI 人声：女声绮萱 / 男声 Alemon / 关闭。 */
  const aiEl = root.querySelector('[data-b="cfg-ai-voice"]');
  const aiNote = root.querySelector('[data-b="cfg-ai-note"]');
  if (aiEl) {
    const labels = { female: '女声 · 绮萱', male: '男声 · Alemon', off: '关闭' };
    let aiSaved = ['female', 'male', 'off'].includes(fb.ai_voice)
      ? fb.ai_voice : 'female';
    const paintAi = (v) => {
      aiEl.querySelectorAll('button[data-ai-voice]').forEach((b) => {
        const on = b.dataset.aiVoice === v;
        b.classList.toggle('on', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      if (aiNote) {
        aiNote.textContent = v === 'off'
          ? '已关闭：报告里点「AI 唱」会提示去设置打开。'
          : `当前：${labels[v]}。报告「听整句」旁会出现「AI 唱」，`
            + '本地合成、约 5 秒一句；不影响评分。';
      }
    };
    paintAi(aiSaved);
    aiEl.querySelectorAll('button[data-ai-voice]').forEach((b) => {
      b.onclick = async () => {
        const v = b.dataset.aiVoice;
        if (!v || v === aiSaved) return;
        const ok = await saveAndReport({ feedback: { ai_voice: v } },
          'cfg-ai-note', `已保存：AI 人声「${labels[v]}」。`);
        if (ok) aiSaved = v;
        paintAi(aiSaved);
      };
    });
  }
  setProPersister(async (on) => {
    const okSaved = await saveAndReport({ feedback: { show_jargon: on } });
    // 存不上就把界面拨回去：留着"已开启"而后端是关的，下次打开又变回去，
    // 用户只会觉得"这软件记不住设置"（而实际是没存上）。
    if (!okSaved) setPro(!on);
  });
  /* 2026-10-18：`cfg-colorize` 那一行设置**没有消费者**（`colorize_deviation`
     从来没有被图表读过），已从 s7 删掉。这里不再绑一个不存在的开关 ——
     "看得见、动不了"的开关比没有更坏。 */
  const rc = cfg.recording || {};
  bindSw('cfg-accompaniment', () => rc.accompaniment_mode,
         'recording', 'accompaniment_mode');

  // ══════════════════════════════════════════════════════════
  // ★ AI 点评的 API key（2026-10-07）
  //
  //   三条设计决定，都是为了让"配了没生效"和"没配"**长得不一样**：
  //     ① 状态行**永远**说清现在走的是哪条路（模型 / 规则模板）
  //     ② 保存会**先真调一次**后端才落盘 —— 所以"已保存"= 真能用
  //     ③ 清了 key 之后**提醒开关也关了** —— 否则用户以为还在用模型
  //   ⚠️ key **永不回显**：只显示 `sk-…a1b2` 那种提示。
  // ══════════════════════════════════════════════════════════
  const llmInput = root.querySelector('[data-b="llm-key-input"]');
  const llmBtn = root.querySelector('[data-b="llm-key-save"]');
  const llmClear = root.querySelector('[data-b="llm-key-clear"]');

  function llmSay(text, kind) {
    // kind: 'ok' | 'bad' | 'idle'
    const el = root.querySelector('[data-b="llm-key-status"]');
    if (!el) return;
    el.textContent = text;
    el.style.color = kind === 'ok' ? 'var(--ok, #2e7d52)'
      : kind === 'bad' ? 'var(--acc-ink)' : 'var(--ink3)';
  }

  // ⚠️ 这里只留一个刷新函数。第一版写了两个（一个还用了 `**` 加粗）——
  //    而状态行是 `textContent`（**不是 innerHTML**）⇒ 星号会原样显示。
  //    同一个坑本项目已经踩过（`innerHTML` 里写 Markdown），这次是反过来。
  async function refreshLlm() {
    try {
      const s = await source.getLlmKey();
      if (s && s.mock) {
        llmSay('mock 模式：不连后端，配不了 key（换成 ?src=api 再试）', 'idle');
        return;
      }
      if (!s || !s.configured) {
        llmSay('当前没配 key —— 报告页的「总评」由规则模板生成。', 'idle');
        return;
      }
      const where = s.source === 'env' ? '环境变量' : '本机文件';
      llmSay(`当前已配置（${where}，${s.hint || '—'}）· 模型 ${s.model}`
        + (s.enabled ? ' · AI 点评已开启' : ' · 但 AI 点评开关是关着的'),
        s.enabled ? 'ok' : 'bad');
    } catch (e) {
      llmSay(`读不到 key 状态：${e.message}`, 'bad');
    }
  }
  refreshLlm();

  if (llmBtn) {
    llmBtn.addEventListener('click', async () => {
      const k = (llmInput && llmInput.value || '').trim();
      if (!k) { llmSay('先粘贴 key 再点保存。', 'bad'); return; }
      llmBtn.disabled = true;
      llmSay('正在验证这把 key（会真调一次模型）…', 'idle');
      try {
        const r = await source.saveLlmKey(k, true);
        if (llmInput) llmInput.value = '';   // 存完就清空输入框，别留在屏幕上
        llmSay(`已保存并验证通过（${r.hint || '—'}）· AI 点评已开启`, 'ok');
        // 开关按钮也要跟着亮 —— 否则用户会以为没生效
        const sw = root.querySelector('[data-b="cfg-ai-comment"]');
        if (sw) sw.classList.add('on');
      } catch (e) {
        // ⚠️ 后端把"401 不对/402 欠费/429 额度用完"翻成了人话，直接透传
        llmSay(`没能保存：${e.message}`, 'bad');
      } finally {
        llmBtn.disabled = false;
      }
    });
  }

  if (llmClear) {
    llmClear.addEventListener('click', async () => {
      if (!window.confirm('清掉本机保存的 API key？\n'
        + '（环境变量里那份程序改不了，需要你自己 unset）')) return;
      try {
        const r = await source.clearLlmKey();
        llmSay(r.cleared
          ? '已清除。报告页的总评回到规则模板。'
          : '没有可清除的 key（可能来自环境变量，程序改不了）。', 'idle');
        const sw = root.querySelector('[data-b="cfg-ai-comment"]');
        if (sw) sw.classList.remove('on');
      } catch (e) {
        llmSay(`清除失败：${e.message}`, 'bad');
      }
    });
  }

  // ── 隐私 ──
  const pv = cfg.privacy || {};
  setText(root, 'privacy-dir', pv.record_dir || './data/recordings/');
  const mb = pv.used_bytes === null || pv.used_bytes === undefined
    ? null : pv.used_bytes / 1024 / 1024;
  setText(root, 'privacy-size', mb === null ? '—' : `${mb.toFixed(1)} MB`);
  setText(root, 'privacy-detail',
    `共 ${pv.sample_count ?? 0} 条录音${pv.local_only ? '，只存在本机' : ''}`);

  /* ★ 「清空」必须真的能用（2026-09-23）。
     隐私板块明说"这会删掉全部历史，无法恢复" —— 这是**产品承诺**，
     而原型里它只是个静态按钮。一个骗人的删除按钮比没有更糟：
     用户以为删干净了，实际录音还在磁盘上。
     （这一点是产品就绪度审计查出来的：`后端有清空接口（无）`。）

     两个细节：
     ① 二次确认走 `window.confirm`，而不是自己画弹窗 —— 少一份要维护的 UI，
        而且浏览器的确认框在"不可恢复"这件事上表达得很清楚。
     ② 完成后**如实报告删了什么**（几条记录、几个文件、多少 MB），
        而不是只说"已清空"。用户有权知道到底删掉了什么。 */
  const pbtn = root.querySelector('[data-b="purge-btn"]');
  if (pbtn && typeof source.purgeData === 'function') {
    pbtn.onclick = async () => {
      const n = pv.sample_count ?? 0;
      const sizeText = mb === null ? '未知大小' : `${mb.toFixed(1)} MB`;
      const okGo = window.confirm(
        `确定要清空所有录音与记录吗？\n\n` +
        `这会删除：\n` +
        `　· 全部练习历史与进步曲线\n` +
        `　· 你保存的全部跟唱模板\n` +
        `　· ${n} 个录音文件（${sizeText}）\n\n` +
        `删掉之后**无法恢复**。实验数据（研究样本）不会被删。`);
      if (!okGo) return;
      pbtn.disabled = true;
      const old = pbtn.textContent;
      pbtn.textContent = '正在清空…';
      try {
        const r = await source.purgeData({ scope: 'all' });
        const freed = (r.bytes_freed || 0) / 1024 / 1024;
        setText(root, 'purge-detail',
          `已清空：${r.sessions_deleted} 条练习记录、${r.templates_deleted} 个模板、` +
          `${r.files_deleted} 个录音文件（释放 ${freed.toFixed(1)} MB）。` +
          `剩余 ${r.remaining?.practice_sessions ?? 0} 条记录。` +
          `　${r.kept || ''}`);
        setText(root, 'privacy-size', '0.0 MB');
        setText(root, 'privacy-detail', '共 0 条录音，只存在本机');
        try { sessionStorage.removeItem('sl.sess'); } catch { /* 隐私模式 */ }
      } catch (e) {
        // 失败必须说出来。悄悄失败 = 用户以为删了，实际还在。
        setText(root, 'purge-detail',
          `⚠️ 清空失败：${e.message}。什么都没删掉，录音与记录仍在原处。`);
      } finally {
        pbtn.disabled = false;
        pbtn.textContent = old;
      }
    };
  }

  /* ★ 麦克风设备选择（2026-10-01）—— 原先那两个按钮（默认设备/外接麦克风）
     是死的：没有 handler，`openMic()` 也从来没收到过设备。
     为什么这不是装饰：产品要求「戴耳机跟着伴奏唱」，而**选错输入设备直接改分数**
     —— 内置麦会把外放的伴奏一起收进去，音高检测转而去追伴奏的贝斯线。

     做法：把设备列表**问浏览器要**，而不是写死"默认/外接"两个按钮 ——
     真实机器上可能有 3 个输入（内置麦、耳机麦、USB 麦），写死的两个必然对不上。
     ⚠️ 没授权之前 `label` 是空的（浏览器隐私规则），所以：
        · 授权前显示「设备 1 / 设备 2」这种**如实的占位名**，不编造"外接麦克风"；
        · 用户开过一次麦之后再回来，就能看到真名字。
        · 一句提示写清"唱过一次之后才会显示设备名"，免得用户以为坏了。 */
  const micSeg = root.querySelector('[data-b="mic-seg"]');
  if (micSeg) {
    const renderMics = async (mics, current) => {
      micSeg.replaceChildren();
      const mk = (label, id, title) => {
        const b = document.createElement('button');
        b.textContent = label;
        if (title) b.title = title;
        b.classList.toggle('on', (id || '') === (current || ''));
        b.onclick = async () => {
          try {
            await save({ recording: { device_id: id || '', device: label } });
            [...micSeg.querySelectorAll('button')].forEach((x) => x.classList.remove('on'));
            b.classList.add('on');
            setText(root, 'mic-note', id
              ? `已选择「${label}」。下一遍练习会用这个设备录音。`
              : '已恢复系统默认设备。下一遍练习生效。');
          } catch (e) {
            setText(root, 'mic-note', `⚠️ 保存失败：${e.message}（设备没换）`);
          }
        };
        return b;
      };
      micSeg.append(mk('默认设备', '', '用系统的默认输入设备'));
      mics.forEach((m) => micSeg.append(
        mk(m.label, m.deviceId,
           m.hasLabel ? `设备 id：${m.deviceId.slice(0, 12)}…`
                      : '设备名要等你允许过一次麦克风之后才会显示')));
      const unlabeled = mics.some((m) => !m.hasLabel);
      setText(root, 'mic-note', mics.length
        ? `${mics.length} 个输入设备${unlabeled
            ? '（名字要等你允许过一次麦克风之后才显示）' : ''}`
        : '没检测到输入设备 —— 检查一下系统里的麦克风。');
    };
    try {
      const { listMics } = await import('./live.js');
      const mics = await listMics();
      await renderMics(mics, rc.device_id || '');
      // 用户开麦授权后设备名才可见 ⇒ 监听一次设备变化就重画
      if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
        navigator.mediaDevices.addEventListener('devicechange', async () => {
          try { await renderMics(await listMics(), rc.device_id || ''); }
          catch { /* 拿不到就保持现状 */ }
        });
      }
    } catch (e) {
      setText(root, 'mic-note', `设备列表读取失败：${e.message}`);
    }
  }

  /* ★ 「导出」按钮（2026-10-01）—— 以前点了没反应。
     导出的是**当前演唱者的结构化记录**（历史每一条的分数 + 六指标 + 时间），
     不是音频文件：音频只能整目录打包，浏览器端做不了，而且体积是几十 MB。
     所以按钮的文字与说明都指向"记录"，并在导出后**如实说明导出了什么、
     没导出什么** —— 用户点"导出"时以为会拿到录音，只给 JSON 而不说清，
     和"清空"只删一半是同一种坏。

     ⚠️ 导出的是**本人的**数据（`getHistory(singerId)`），不是全库 ——
        多用户下全库导出就是数据泄露，而设置页明说"只存在你自己的电脑上"。 */
  const ebtn = root.querySelector('[data-b="export-btn"]');
  if (ebtn && typeof source.getHistory === 'function') {
    ebtn.onclick = async () => {
      const old = ebtn.textContent;
      ebtn.disabled = true;
      ebtn.textContent = '正在导出…';
      try {
        const sid = getSingerId();
        const hist = await source.getHistory(sid, 500);
        let prof = null;
        try { prof = await source.getProfile(sid); } catch { /* 画像取不到不影响导出 */ }
        const payload = {
          exported_at: new Date().toISOString(),
          singer_id: sid,
          note: '这是练习记录（分数与六指标），不含音频文件。'
                + '音频在 data/recordings/ 下，按目录整体备份。',
          sessions: hist,
          profile: prof,
        };
        const blob = new Blob([JSON.stringify(payload, null, 1)],
                              { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `singlens_${sid}_${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        setText(root, 'privacy-detail',
          `已导出 ${hist.length} 条练习记录（JSON，不含音频文件）。` +
          `音频在 data/recordings/ 下，需要的话整个目录备份。`);
      } catch (e) {
        setText(root, 'privacy-detail', `⚠️ 导出失败：${e.message}。没有下载任何文件。`);
      } finally {
        ebtn.disabled = false;
        ebtn.textContent = old;
      }
    };
  }

  return { bound: true, lo: rg.lo_midi, hi: rg.hi_midi };
}

/* ══════════════════════════════════════════════════════════
 * 02 开始练习
 * ══════════════════════════════════════════════════════════ */
/**
 * 这一屏原来**一个绑定都没有** —— 整屏是原型静态画面：曲名写死、四个分数
 * 写死、走带按钮全是死的。也就是说它不属于"接了一半"，是根本没接。
 *
 * G4-a（2026-09-23）：补上**开始之前那一态**（选内置曲目 / 选我的模板）。
 * G4-b（2026-09-23）：补上**实时管线** —— 麦克风采集 → WebSocket →
 *   实时音高曲线 / 逐音方块 / 四个滚动分 / Now Singing / 即时建议。
 *
 * ⚠️ 实时链路的三个"必须说清"的点（都会在界面上如实显示）：
 *   ① 输入采样率几乎不会是 22050（硬件决定），我们做线性降采样。
 *      这对**基频检测**够用，但会让高频泛音有混叠 ——
 *      所以实时面板**不算音色（brightness）**，那个走精算链路。
 *   ② `ScriptProcessorNode` 是废弃 API 且跑在主线程，UI 卡顿会丢块。
 *      换成 `AudioWorklet` 要独立文件 + 多一条加载失败路径，本机没法测那个路径。
 *   ③ 离开这一屏**必须释放麦克风** —— 不释放的话标签页上一直挂着录音红点，
 *      而用户完全不知道还在录。这是隐私问题，不是性能问题。
 *      为此 router 会在切屏时调用视图的 `unmount()`。
 */
async function s2(root, ctx) {
  void ctx;
  const { source, isMock } = await import('./data/index.js');
  const charts = await import('./charts.js');
  const q = new URLSearchParams(location.search);

  const setup = root.querySelector('[data-b="setup"]');
  const body = root.querySelector('[data-b="setup-body"]');
  if (!body) return { bound: false, reason: 'no setup container' };

  /* ── 实时链路的全部可变状态，集中在一处 ──
     为什么不用散落的闭包变量：切屏/重录/出错时都要清干净，
     散着写必然漏一个，而漏掉的那个通常就是"麦克风没释放"。 */
  const live = {
    uid: null, stream: null, mic: null, raf: 0, timer: 0,
    frames: [], refNotes: [], nowMs: 0, startedAt: 0,
    paused: false, running: false, t0Wall: 0,
    // 引导音前奏（2 秒 4 拍）也要算进"总长"—— 否则时间从第一秒起就是
    // 06.1 / 03.5，进度条会超过 100%。没有引导音时为 0。
    guideLeadMs: 0,
    guideVolume: 0.8,
    lyricsTimer: 0,
  };

  const stopLive = ({ silent = false } = {}) => {
    live.running = false;
    // 练习态样式（隐藏 dock/跑马灯、走带置顶）必须跟着退出。
    try { document.body.classList.remove('in-practice'); } catch { /* 无 body */ }
    if (live.raf) { cancelAnimationFrame(live.raf); live.raf = 0; }
    if (live.timer) { clearInterval(live.timer); live.timer = 0; }
    if (live.lyricsTimer) { clearInterval(live.lyricsTimer); live.lyricsTimer = 0; }
    /* ★★ 2026-10-17：**伴奏也必须停**。切屏后耳边还在响一段没人管的旋律，
       用户不知道从哪关 —— 与麦克风没释放同一类。
       ⚠️ 必须同时 cancel 那个 rAF（它每帧读 currentTime）。 */
    if (live.guideRaf) { cancelAnimationFrame(live.guideRaf); live.guideRaf = 0; }
    try { if (live.guide) { live.guide.pause(); live.guide.src = ''; } } catch { /* 已停 */ }
    live.guide = null;
    try { if (live.stream) live.stream.stop(); } catch { /* 已停 */ }
    try { if (live.mic) live.mic.stop(); } catch { /* 已停 */ }
    live.mic = null;
    /* ★ 2026-10-18：**作废当前会话的 uid**。
       为什么：`stream.stop()` 之后服务端的 `onclose` 还会发一条 `done`
       （这是给"完成"用的）；而"重录 / 重新开始这一遍"也会走 stopLive ——
       旧流那条 done 会把页面**导航到 s3**，把刚新建的会话当成"唱完了"。
       把 uid 置空，下面的 onMessage 包装器就会忽略旧流的消息。 */
    live.uid = null;
    // ⚠️ 浏览器标签页上的录音红点由**麦克风轨道**决定。
    //    上面 mic.stop() 已经停掉轨道；这里再查一遍并显式停，
    //    因为设备热插拔时轨道可能被换成新的、而旧句柄拿不到它。
    if (!silent) {
      navigator.mediaDevices && navigator.mediaDevices.enumerateDevices &&
        Promise.resolve().then(() => { /* 只做观测，不在这里动设备 */ });
    }
  };

  /**
   * 开麦/开流失败时：**留在选曲页**，把错误放在用户眼睛所在的位置。
   *
   * 为什么不能只写 `lv-advice`（2026-10-18 审查）：live 面板一旦显示，
   * 那条 advice 在 y≈1938；而用户看到的是「正在录」+ 空图。报错等于没报。
   */
  const showSetupError = (message) => {
    body.querySelectorAll(':scope > .deg').forEach((e) => e.remove());
    const box = document.createElement('div');
    box.className = 'deg';
    box.setAttribute('role', 'alert');
    box.style.cssText = 'margin:0 0 14px;font-size:13px';
    box.textContent = `⚠️ ${message}`;
    body.prepend(box);
    body.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    try { box.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    catch { /* 老浏览器 */ }
  };

  /** 真正进入练习态：隐藏 setup、显示 live、收起底部装饰、绑走带。 */
  const enterLive = (sess) => {
    body.hidden = true;
    const wrap = root.querySelector('[data-b="live-wrap"]');
    if (wrap) wrap.hidden = false;
    if (setup) setup.hidden = true;
    // 页头从"准备练习"切到"正在练习"：原来一直写着"正在练习"，
    // 而用户此刻连曲目都还没选（标题连字也是这么来的）。
    setText(root, 's2-state', '正在练习');
    try { document.body.classList.add('in-practice'); } catch { /* 无 body */ }
    wireTransport(sess);
  };

  const draw = () => {
    const refNotes = live.refNotes;
    const frames = live.frames;
    try {
      charts.drawLive({ refNotes, frames, nowMs: live.nowMs });
      charts.drawRoll({ refNotes, frames, nowMs: live.nowMs });
    } catch (e) {
      // 画图失败不该打断录音 —— 但也不能吞掉，否则用户看到"图卡住了"
      console.warn('[s2] 实时绘制失败：', e);
    }
  };

  const scheduleDraw = () => {
    if (live.raf) return;
    live.raf = requestAnimationFrame(() => { live.raf = 0; draw(); });
  };

  const fmtTime = (ms) => {
    const s = Math.max(0, ms) / 1000;
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:` +
           `${(s % 60).toFixed(1).padStart(4, '0')}`;
  };

  /** metrics 帧 → 右侧四个分 + Now Singing + 建议。 */
  const applyMetrics = (m) => {
    const put = (key, v) => {
      // null = 本次未评估（契约 §3.2）→ 显示「—」，**不显示 0**
      setText(root, `lv-${key}`, show(v, 0));
      setBar(root, `lv-${key}-bar`, v === null || v === undefined ? 0 : v);
    };
    put('pitch', m.pitch);
    put('rhythm', m.rhythm);
    put('dynamic', m.dynamic);
    put('vibrato', m.vibrato);

    const cn = m.current_note || {};
    /* 2026-10-21 皮肤层：没有稳定音高时不要把 76px 的「—」画成一根白条
       （仪表盘上看起来像加载进度条）。加一个 data-empty，把它缩成小占位。 */
    {
      const noteEl = root.querySelector('[data-b="lv-note"]');
      const hasNote = cn.pitch_midi != null;
      if (noteEl) {
        noteEl.textContent = hasNote ? midiName(cn.pitch_midi) : '—';
        noteEl.dataset.empty = hasNote ? '0' : '1';
      }
    }
    const VERDICT = {
      OK: '刚刚好，很准', SHARP: '偏高了一点', FLAT: '偏低了一点',
    };
    let vtxt = cn.verdict ? (VERDICT[cn.verdict] || '') : '';
    if (cn.cents_dev != null && Math.abs(cn.cents_dev) > 5) {
      vtxt += `（${cn.cents_dev > 0 ? '+' : ''}${Math.round(cn.cents_dev)} 音分）`;
    }
    setText(root, 'lv-verdict', vtxt || '还没听到稳定的音高');
    if (m.advice) setText(root, 'lv-advice', m.advice);
  };

  const onMessage = (msg) => {
    if (!msg || !msg.type) return;
    if (msg.type === 'pitch') {
      const d = msg.data || {};
      live.nowMs = d.t_ms || 0;
      // 只留最近 90 秒：早期帧对"看当前"没有价值，而无限增长会拖慢绘制。
      // （报告用的是服务端留存的完整音频，这里丢帧不影响评分。）
      live.frames.push([d.t_ms || 0, d.f0]);
      if (live.frames.length > 2000) live.frames.splice(0, live.frames.length - 2000);
      scheduleDraw();
    } else if (msg.type === 'metrics') {
      applyMetrics(msg.data || {});
    } else if (msg.type === 'done') {
      live.running = false;
      stopLive();
      setText(root, 'lv-advice', '这一遍结束了，正在精算…');
      // ★ 2026-10-19：微练习结果去独立 s9，不再混进完整报告 s3。
      let micro = false;
      try {
        const p = JSON.parse(sessionStorage.getItem('sl.practice') || 'null');
        micro = !!(p && p.madeUid
          && p.madeUid === sessionStorage.getItem('sl.sess'));
      } catch { /* ignore */ }
      location.hash = micro ? '#s9' : '#s3';
    } else if (msg.type === 'error') {
      const e = msg.data || {};
      live.running = false;
      stopLive();
      const el = root.querySelector('[data-b="lv-advice"]');
      if (el) {
        el.textContent = `⚠️ ${e.message || '实时链路出错了'}`;
        el.style.color = 'var(--acc-ink)';
      }
    }
  };

  /** 真正开始录音：开麦 → 开 WS → 每次拿到块就发上去。 */
  const beginCapture = async (sess, opts = {}) => {
    const rt = { ws: sess && sess.rt_window_ms, sr: sess && sess.sample_rate };
    void rt;
    live.uid = sess.session_uid;
    live.frames = [];
    live.nowMs = 0;
    live.paused = false;

    // 参考音高：模式 A 取内置曲目，模式 B 取模板 —— 服务端已经把
    // `sess.rt.ref_notes` 填好了，但那是**服务端**的；前端要自己拿一份来画。
    live.refNotes = await loadRefNotes(source, sess);

    /* 设备与引导音量的配置一次读出来：
       · `device_id` 给 openMic；
       · `guide_volume` 只影响引导音轨（4 拍 + 旋律），不碰麦克风、不影响评分。 */
    let dev = '';
    let guideVol = 0.8;
    try {
      const c = await source.getConfig();
      dev = (c.recording || {}).device_id || '';
      const gv = (c.feedback || {}).guide_volume;
      if (typeof gv === 'number' && isFinite(gv)) {
        guideVol = Math.max(0, Math.min(1, gv));
      }
    } catch { /* 读不到就用默认设备/默认音量 */ }
    live.guideVolume = guideVol;

    /* ★★ 2026-10-17：**放伴奏 + 倒数引导**（作者的原话："没办法听到伴奏，
       不知道从哪里开始唱"）。

       为什么放在 beginCapture 这里是**唯一正确**的位置：
         · 用户刚点完「开始」（有用户手势 ⇒ 浏览器允许 play()）
         · 麦克风采集已经在往下走（下面立刻要权限 + 开 WS），
           所以"放伴奏"必须和"开始采集"**同时**进行，不能先放完再采集 ——
           否则预备拍那 2 秒里的声音就漏了。
         · 采集照旧**立刻开始**，只是**告诉用户什么时候开口**。

       ⚠️ 模式 C（上传录音）没有实时页面，所以不需要引导；
          模式 B/D 的参考是模板，服务端用模板音符合成同一条 4 拍 + 旋律
          （`/api/template/{tid}/guide.wav`），与 A 共用播放/倒数的代码。
          不假装有伴奏：用户自己的原始录音不会被播放，放的是合成旋律。 */
    const cue = root.querySelector('[data-b="lv-cue"]');
    let guideAudio = null;
    let startGuide = null;
    const canVocal = !!(sess && sess.reference && sess.reference.has_vocal);
    const canOriginal = canVocal;   // 有原唱就能混出「原唱+伴奏」；没伴奏时退化为原唱
    const hasBacking = !!(sess && sess.reference && sess.reference.has_backing);
    let guideKind = ((opts.audioKind === 'vocal' || opts.audioKind === 'original')
      && canOriginal) ? 'original' : 'backing';
    const guideKindBtn = root.querySelector('[data-b="lv-guide-kind"]');
    /* ★ 2026-10-18：模式 B 也放引导音轨了。
       以前只有 A 有「4 拍预备 + 旋律」，B/D（用户自己的模板）一个音都不给，
       但界面照样说「跟着它唱」。现在模板也有 `/guide.wav`：
       服务端用模板音符合成 4 拍预备 + 旋律，和 A 走同一条播放路径。 */
    const wantsGuide = !isMock && sess && (
      (sess.mode === 'A' && (sess.reference_id || sess.reference?.reference_id)) ||
      (sess.mode === 'B' && sess.template_id)
    );
    live.guideLeadMs = wantsGuide ? 2000 : 0;
    // ★ 2026-10-23：参考音高也整体后移 2 秒，和引导音轨的 4 拍预备对齐。
    //   之前只把 2 秒加进“总时长”，阶梯图/服务端 current_note 仍从 0 秒开始，
    //   于是屏幕上的参考比耳朵听到的伴奏早 2 秒 —— 用户会以为是自己跟不上。
    if (live.guideLeadMs && live.refNotes.length) {
      live.refNotes = live.refNotes.map(([s, e, m]) => [
        s + live.guideLeadMs, e + live.guideLeadMs, m,
      ]);
    }
    if (cue) cue.hidden = !wantsGuide;
    if (wantsGuide) {
      const tr = Number(sess.transpose) || 0;
      const guideUrl = (kind) => {
        if (sess.mode === 'A') {
          const rid = sess.reference_id || sess.reference.reference_id;
          return `/api/reference/${encodeURIComponent(rid)}/guide.wav`
            + (tr ? `?transpose=${tr}` : '');
        }
        return `/api/template/${encodeURIComponent(sess.template_id)}/guide.wav`
          + `?singer_id=${encodeURIComponent(singerId())}`
          + `&kind=${encodeURIComponent(kind)}`
          + (tr ? `&transpose=${tr}` : '');
      };
      const nEl = root.querySelector('[data-b="lv-cue-n"]');
      const tEl = root.querySelector('[data-b="lv-cue-t"]');
      guideAudio = new Audio(guideUrl(guideKind));
      guideAudio.volume = Math.max(0, Math.min(1, Number(live.guideVolume) || 0.8));
      guideAudio.preload = 'auto';
      // 进度 → 倒数文案。前 2 秒是 4 拍预备（每拍 0.5s），第 5 拍开口。
      const tick = () => {
        if (!guideAudio || guideAudio.paused) return;
        const t = guideAudio.currentTime;
        if (nEl) {
          if (t < 2.0) nEl.textContent = String(Math.max(1, 4 - Math.floor(t / 0.5)));
          else nEl.textContent = '唱';
        }
        if (tEl) {
          tEl.textContent = t < 2.0
            ? '先听 4 下节拍，第 5 下开口唱'
            : (guideKind === 'original'
              ? '正在放原唱+伴奏 —— 跟着原曲唱，麦克风只录你的声音'
              : '跟着它唱 —— 屏幕上灰色的是它希望你唱的音');
        }
        live.guideRaf = requestAnimationFrame(tick);
      };
      guideAudio.addEventListener('playing', () => {
        if (nEl) nEl.textContent = '4';
        tick();
      });
      guideAudio.addEventListener('ended', () => {
        if (nEl) nEl.textContent = '唱';
        if (tEl) tEl.textContent = '伴奏放完了。点「完成」开始精算，或点右边重新开始这一遍。';
        if (live.guideRaf) { cancelAnimationFrame(live.guideRaf); live.guideRaf = 0; }
      });
      // 放不出来必须**说出来**（不静默）：用户会以为"这系统没伴奏"
      guideAudio.addEventListener('error', () => {
        if (nEl) nEl.textContent = '静音';
        if (tEl) {
          tEl.textContent = '伴奏取不到（服务端或网络）。你可以直接清唱 —— '
            + '但没法和它的调对齐，音准分会受影响。';
        }
      });
      // ★ 2026-10-23：**不要在这里 play()**。
      //   实测：伴奏先播，随后才 await getUserMedia/openMic/stream.start；
      //   等麦克风真正开始收块时，2 秒预备已经走掉一截，服务端的时间轴
      //   从“第一块音频”起算，于是整遍被记成晚开口 1.0–1.2 秒。
      //   现在把播放推迟到 mic/WS 都 ready（见下方 startGuide?.()），
      //   让 4 拍预备、用户开口、服务端 t=0 从同一刻开始。
      startGuide = () => {
        if (!guideAudio) return;
        guideAudio.play().catch((e) => {
          if (tEl) {
            tEl.textContent = `伴奏放不出来（${e.message}）—— `
              + '点下面的按钮重试，或直接清唱。';
          }
        });
      };
      if (tEl) tEl.textContent = '麦克风准备好后，先听 4 下节拍再开口';
      const again = root.querySelector('[data-b="lv-guide-again"]');
      if (again) {
        /* ★ 2026-10-18：原来只是 `currentTime=0; play()` —— 录音还在继续，
           第二遍伴奏会被**录进这一遍**，报告对不上任何一遍完整演唱。
           现在改成"重新开始这一遍"：停掉当前采集、作废这次会话、
           用同一个参考新建一次。按钮与确认文案都按这个意思写。 */
        again.onclick = async () => {
          if (!window.confirm('重新开始这一遍？\n\n刚才录到的内容会作废，'
                              + '系统会用同一个参考重新开一遍。')) return;
          const label = sess.reference_title || '';
          const mode = sess.mode;
          const referenceId = sess.reference_id;
          const templateId = sess.template_id;
          const kind = live.guideKind || 'backing';
          stopLive();
          try { sessionStorage.removeItem('sl.sess'); } catch { /* 隐私模式 */ }
          await start({ mode, referenceId, templateId, label, audioKind: kind });
        };
      }
      const paintKind = () => {
        if (!guideKindBtn) return;
        guideKindBtn.hidden = !canOriginal;
        if (!canOriginal) return;
        const label = hasBacking ? '原唱（带伴奏）' : '原唱';
        guideKindBtn.textContent = guideKind === 'original'
          ? `跟唱音轨：${label}` : '跟唱音轨：伴奏';
        guideKindBtn.title = '点一下切到'
          + (guideKind === 'original' ? '伴奏' : label) + '（从当前位置继续）';
      };
      if (guideKindBtn) {
        paintKind();
        guideKindBtn.onclick = () => {
          if (!canOriginal) return;
          const next = guideKind === 'original' ? 'backing' : 'original';
          const t = guideAudio ? guideAudio.currentTime : 0;
          const wasPlaying = !!(guideAudio && !guideAudio.paused);
          const switchedAt = performance.now();
          try { if (guideAudio) guideAudio.pause(); } catch { /* 已停 */ }
          if (live.guideRaf) { cancelAnimationFrame(live.guideRaf); live.guideRaf = 0; }
          const label = next === 'original'
            ? (hasBacking ? '原唱（带伴奏）' : '原唱') : '伴奏';
          if (tEl) {
            tEl.textContent = `正在切到${label} · 从 ${fmtClock(t)} 继续…`;
          }
          const a = new Audio(guideUrl(next));
          a.volume = Math.max(0, Math.min(1, Number(live.guideVolume) || 0.8));
          a.preload = 'auto';
          a.addEventListener('loadedmetadata', () => {
            // 切音轨时录音/进度还在走：把加载耗掉的时间补上，避免新音轨
            // 从旧位置开始、画面却已经往前走了。暂停时录音也停，不补。
            const lag = wasPlaying ? (performance.now() - switchedAt) / 1000 : 0;
            const target = Math.max(0, t + lag);
            try { a.currentTime = target; } catch { /* 元数据问题 */ }
            // ★ 作者要的是「切过去就从同一秒开始唱」：即使刚才暂停，
            //   切音轨也直接从当前位置起播；想停再点暂停。
            a.play().catch(() => { /* 用户可再点 */ });
            if (live.paused) {
              live.paused = false;
              const pb = root.querySelector('[data-b="lv-pause"]');
              if (pb) {
                const use = pb.querySelector('use');
                if (use) use.setAttribute('href', '#i-pause');
                pb.setAttribute('aria-label', '暂停');
                pb.title = '暂停';
                pb.style.opacity = '';
              }
              const st = root.querySelector('[data-b="lv-state"]');
              if (st) { st.textContent = '正在录'; st.classList.remove('paused'); }
            }
            if (tEl) tEl.textContent = `已切到${label} · 从 ${fmtClock(target)} 继续`;
          }, { once: true });
          a.addEventListener('playing', () => {
            if (a !== guideAudio) return;
            if (nEl) nEl.textContent = '4';
            tick();
          });
          a.addEventListener('ended', () => {
            if (a !== guideAudio) return;
            if (nEl) nEl.textContent = '唱';
            if (tEl) tEl.textContent = '音轨放完了。点「完成」开始精算，或点右边重新开始这一遍。';
            if (live.guideRaf) { cancelAnimationFrame(live.guideRaf); live.guideRaf = 0; }
          });
          a.addEventListener('error', () => {
            if (a !== guideAudio) return;
            if (tEl) tEl.textContent = '这条音轨取不到了；可以切回伴奏或直接清唱。';
          });
          guideAudio = a;
          live.guide = a;
          live.guideKind = next;
          guideKind = next;
          paintKind();
        };
      }
      live.guide = guideAudio;
      live.guideKind = guideKind;
    }

    /* ★ 2026-10-20：整首歌词跟着伴奏滚动。
       时间基准用 guideAudio.currentTime - 2s（前 2 秒是 4 拍预备）；
       没有 guide 时退回录音已过时间 - 2s。行级高亮，不假装字级。 */
    const lyWrap = root.querySelector('[data-b="lv-lyrics"]');
    const lyList = root.querySelector('[data-b="lv-lyrics-list"]');
    const lyLines = (sess && sess.reference && Array.isArray(sess.reference.lyrics))
      ? sess.reference.lyrics : [];
    if (lyWrap && lyList && lyLines.length) {
      lyWrap.hidden = false;
      lyList.innerHTML = lyLines.map((x, i) =>
        `<div class="ly-line" data-ly="${i}"><span class="t">${esc(String(x.text || ''))}</span></div>`).join('');
      const els = Array.from(lyList.querySelectorAll('.ly-line'));
      let cur = -2;
      const paint = () => {
        const t = (guideAudio && guideAudio.currentTime > 0)
          ? guideAudio.currentTime - 2.0
          : Math.max(0, (live.nowMs || 0) / 1000 - 2.0);
        let idx = -1;
        for (let i = 0; i < lyLines.length; i += 1) {
          if (t >= (Number(lyLines[i].start_ms) || 0) / 1000) idx = i;
          else break;
        }
        if (idx === cur) return;
        cur = idx;
        els.forEach((e, i) => e.classList.toggle('on', i === idx));
        if (idx >= 0 && els[idx]) {
          const e = els[idx];
          const er = e.getBoundingClientRect();
          const lr = lyList.getBoundingClientRect();
          const delta = er.top - lr.top - lyList.clientHeight / 2 + e.clientHeight / 2;
          lyList.scrollTop = Math.max(0, lyList.scrollTop + delta);
        }
      };
      paint();
      live.lyricsTimer = setInterval(paint, 240);
    } else if (lyWrap) {
      lyWrap.hidden = true;
    }

    let stream;
    try {
      /* ★ 用**这个会话自己的 uid** 过滤消息：重录 / 重新开始这一遍时，
         旧 WebSocket 的 `done` 可能在 stop 之后才到；不过滤就会把
         新会话误导航到 s3（实测：旧流的 done 让 s3 去请求**新**会话的报告）。 */
      stream = source.openStream(sess.session_uid, (msg) => {
        if (live.uid !== sess.session_uid) return;   // 旧流/已作废，丢弃
        onMessage(msg);
      });
    } catch (e) {
      stopLive();
      showSetupError(`实时连接开不起来：${e.message}`);
      return;
    }
    live.stream = stream;

    // mock 源自己会推送模拟帧，不需要麦克风 —— 这时**不能**去要权限，
    // 否则 `?src=mock` 在没麦克风的机器上永远进不去。
    if (isMock) {
      enterLive(sess);
      stream.start();
      live.running = true;
      live.t0Wall = Date.now();
      startClocks();
      setText(root, 'lv-advice',
        '（mock 模式：这里是模拟的实时数据，不是真的在录音）');
      return;
    }

    let mic;
    try {
      const { openMic } = await import('./live.js');
      /* ★ 用设置页选中的设备（2026-10-01）。
         以前 `openMic()` 不带参数 ⇒ 设置页那两个按钮（默认设备/外接麦克风）
         是死的，用户选了也没用；而"跟着伴奏唱要戴耳机"这条产品要求下，
         选对输入设备是**真的会影响分数**的（内置麦会收到外放伴奏）。
         设备/音量已在函数开头读好；读不到时 dev='' = 系统默认，不阻断开麦。 */
      mic = await openMic({ deviceId: dev });
    } catch (e) {
      // 开麦失败：**不能**切到 live（否则界面上是"正在录"+ 空图，
      // 而错误写在两千像素以下）。停掉伴奏/连接，留在 setup 上。
      const { micErrorMessage } = await import('./live.js');
      stopLive();
      showSetupError(micErrorMessage(e));
      return;
    }
    live.mic = mic;
    if (mic.sampleRateMismatch()) {
      // 如实说明：硬件给的不是 22050，我们做了降采样。
      console.info(`[s2] 麦克风采样率 ${mic.srcSampleRate}Hz → 降采样到 22050Hz`);
    }
    mic.onChunk((buf) => {
      if (live.paused || !live.running) return;
      stream.sendAudio(buf);
    });
    await mic.start();
    stream.start();
    live.running = true;
    // ★ 麦克风与 WebSocket 都就绪后，才播 4 拍预备。
    //   这样“用户听到的第 5 拍”和服务端 t=0 是同一个起点。
    if (startGuide) startGuide();
    // ★ 麦真的开起来了，才切 live。之前是在开麦**之前**就切：
    //   权限被拒时用户看到的是"正在录"，而 setup 已经没了。
    enterLive(sess);
    live.t0Wall = Date.now();
    startClocks();
  };

  /** 时间显示与进度条。**只做显示** —— 时间轴以服务端为准（见 live.js 的文件头）。 */
  const startClocks = () => {
    const refMs = live.refNotes.length
      ? live.refNotes[live.refNotes.length - 1][1] : 0;
    // 引用音高已经整体包含 4 拍预备（见 beginCapture 里的 guideLeadMs 偏移），
    // 所以这里**不要再加一次**，否则进度条分母会多 2 秒。
    const totalMs = refMs || 0;
    const tick = () => {
      if (!live.running) return;
      setText(root, 'lv-time', totalMs
        ? `${fmtTime(live.nowMs)} / ${fmtTime(totalMs)}`
        : fmtTime(live.nowMs));
      if (totalMs) setBar(root, 'lv-prog', (live.nowMs / totalMs) * 100);
    };
    tick();
    live.timer = setInterval(tick, 200);
  };

  /** 走带按钮。 */
  const wireTransport = (sess) => {
    const rec = root.querySelector('[data-b="lv-rec"]');
    const pause = root.querySelector('[data-b="lv-pause"]');
    const go = root.querySelector('[data-b="lv-go"]');
    if (rec) {
      rec.onclick = async () => {
        // 重录 = 停掉当前这一遍，回到选择界面重新开一个会话。
        // 不在这里"复用同一个 session_uid" —— 服务端那次的音频已经收了一半，
        // 混在一起算出来的报告会对应不上任何一遍完整的演唱。
        stopLive();
        try { sessionStorage.removeItem('sl.sess'); } catch { /* 隐私模式 */ }
        if (ctx && ctx.router && ctx.router.go) ctx.router.go('s2');
        else location.reload();
      };
    }
    if (pause) {
      pause.onclick = () => {
        live.paused = !live.paused;
        /* ★ 2026-10-18：按钮只留图标；暂停/继续靠图标（pause ↔ play）
           与 aria-label/title 表达，不再把文字塞进 36px 圆按钮里。 */
        try { if (live.guide) { if (live.paused) live.guide.pause(); else live.guide.play().catch(() => {}); } } catch {}
        { const _use = pause.querySelector('use');
          if (_use) _use.setAttribute('href', live.paused ? '#i-play' : '#i-pause');
          pause.setAttribute('aria-label', live.paused ? '继续' : '暂停');
          pause.title = live.paused ? '继续' : '暂停';
          const _s=root.querySelector('[data-b="lv-state"]');
          if (_s) { _s.textContent = live.paused ? '已暂停' : '正在录'; _s.classList.toggle('paused', live.paused); } }
        pause.style.opacity = live.paused ? '0.45' : '';
        const el = root.querySelector('[data-b="lv-advice"]');
        if (el) {
          el.textContent = live.paused
            ? '已暂停 —— 现在唱什么都不会被录进去。再点一下继续。'
            : '继续。';
        }
      };
    }
    if (go) {
      go.onclick = async () => {
        go.disabled = true;
        const el = root.querySelector('[data-b="lv-advice"]');
        if (el) el.textContent = '正在结束并精算…';
        stopLive();
        try {
          // finish 会把服务端攒下的音频拼起来跑精算管线。
          // ⚠️ 必须在 stop() 之后调 —— 先停采集，保证最后一块已经发出去，
          //    否则结尾那一两个字会丢（而用户只会觉得"最后一句没评上"）。
          await source.finishSession(sess.session_uid);
        } catch (e) {
          if (el) el.textContent = `⚠️ 结束失败：${e.message}`;
          go.disabled = false;
          return;
        }
        // ★ 2026-10-19：微练习去 s9；完整报告才去 s3。
        //   这里必须也判断一次：done 消息可能比 finish 晚到，
        //   只改 onMessage 会让 finish 先跳 s3、把 s9 覆盖掉。
        let micro = false;
        try {
          const p = JSON.parse(sessionStorage.getItem('sl.practice') || 'null');
          micro = !!(p && p.madeUid
            && p.madeUid === sessionStorage.getItem('sl.sess'));
        } catch { /* ignore */ }
        location.hash = micro ? '#s9' : '#s3';
      };
    }
  };

  /* 注册给 router：切屏时必须释放麦克风。
     不释放的后果不是"占资源"，而是**标签页一直挂着录音红点**，
     用户不知道还在录 —— 隐私问题。
     ⚠️ 用 ctx.onUnmount 而不是挂到函数对象上：那样 router 得知道
        "哪个视图有什么清理函数"，而现在责任落在**开资源的那一方**。 */
  if (ctx && typeof ctx.onUnmount === 'function') {
    ctx.onUnmount(() => { stopPreview(); stopLive(); stopSongAudio(); });
  }

  let boot = null;
  try {
    boot = await source.bootstrap();
  } catch (e) {
    body.innerHTML = `<div class="deg">读曲目列表失败：${e.message}</div>`;
    return { bound: false, error: e.message };
  }
  const refs = (boot && boot.references) || [];

  // 我的模板：读不到不算致命（可能是全新用户），但**要说清是"没有"还是"没读到"**
  let tpls = [];
  let tplErr = '';
  try {
    tpls = await source.listTemplates(singerId());
  } catch (e) {
    tplErr = e.message;
  }

  // 上次选了哪个（从 s1 的卡片过来时写在 sessionStorage 里）
  let wantMode = 'A';
  let wantRef = refs.length ? refs[0].reference_id : '';
  let transpose = 0;
  try {
    wantMode = sessionStorage.getItem('sl.mode') || 'A';
    wantRef = sessionStorage.getItem('sl.ref') || wantRef;
    transpose = Math.max(-6, Math.min(6,
      Number(sessionStorage.getItem('sl.transpose')) || 0));
  } catch { /* 隐私模式 */ }

  const start = async ({ mode, referenceId, templateId, label, audioKind = 'backing',
                         transpose: tr = transpose }) => {
    body.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    let s;
    try {
      s = await source.createSession({
        singerId: singerId(), mode, referenceId, templateId,
        transpose: mode === 'C' ? 0 : tr,
      });
    } catch (e) {
      body.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      /* 🛑 2026-10-17 修：原来这条错误是 `insertAdjacentHTML('beforeend', ...)` ——
         **插在整个设置列表的最下方**（这里 Steps 1–4、十几行），
         而用户点的是屏幕中部的「开始」⇒ 红字落在**折叠线以下**。
         ⇒ 表现就是**「点了没反应」**：请求发了、也失败了，
           只是报错在看不见的地方。（作者就是这么报上来的。）

         现在改成**插到最前面 + 滚进视野 + role=alert**：
         出错的位置就是用户眼睛（和读屏软件）所在的位置。
         ⚠️ 这条与"失败静默"是同一族：**报错了但用户看不到 ≈ 没报**。
         追这条时正好挖出 500 的真因（`create_session` 里的
         `UnboundLocalError`，见 server.py L2086 那段注释）。 */
      const box = document.createElement('div');
      box.className = 'deg';
      box.style.cssText = 'margin:0 0 14px;font-size:13px';
      box.setAttribute('role', 'alert');
      box.textContent = `开不了这次练习：${e.message}`;
      body.prepend(box);
      try { box.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
      catch { /* 老浏览器没有平滑滚动 */ }
      return;
    }
    try {
      // ★ 会话要记下来：s3 报告页 / s4 复练对比都靠它找到"这一次"。
      //   存在 sessionStorage 而不是 URL 里 —— 刷新后仍在，但不污染可分享的链接。
      sessionStorage.setItem('sl.sess', s.session_uid);
      sessionStorage.setItem('sl.mode', mode);
      sessionStorage.setItem('sl.last_ref_title', label || '');
    } catch { /* 隐私模式 */ }

    setText(root, 's2-title', label || '本次练习');
    // ★ 界面切换放到 beginCapture **开麦成功之后**（enterLive）：
    //   原来在这里就先隐藏 setup、显示 live；权限被拒时就变成
    //   "空图 + 正在录"，而错误在两千像素以下。
    await beginCapture(s, { audioKind });
    return s;
  };

  /* ══ ★ 模式 D：导入 MIDI（2026-10-10）════════════════════════════
     为什么需要这一路：作者要的是「唱我喜欢的歌 **并知道我走没走音**」。
     "从一整首歌里提旋律"已证走不通（§92/§93，根因是 PYIN 的单声源假设：
     混音里最强的周期性成分通常是贝斯，不是主唱 —— 实测音域跨度 40+ 半音）。
     而 **MIDI 是符号数据**：旋律是**读出来的**，不是猜的 ⇒ 判得了走音。

     ⚠️ 但"读出来"不等于"读对了"：真实 MIDI 的旋律轨未必是第 0 轨，
        有些连轨名都没有 ⇒ **自动推荐会挑错**。
        挑错的后果**不是报错**，而是安安静静给出一份错的参考 ——
        用户照着错的东西练，还以为是自己的问题。
     ⇒ 所以这一块守住三条（§95 定的）：
        ① 列出**所有轨**的摘要 + 推荐，让用户选 / 改
        ② **能试听** —— 听见"提出的旋律"是不是他要唱的那首
        ③ 确认之前**不落库**（MIDI 只是中间物，点了"就用这一轨"才存成模板）
     ⚠️ 换轨 = 重新解析 + **重新合成试听**（服务端保证），一次点击两件事都到位。 */
  const midiState = { fileId: '', chosen: null };

  const midiNote = (t) => setText(root, 'midi-note', t);

  /** 渲染 MIDI 面板：轨清单（全部）+ 推荐 + 试听 + 确认。 */
  const renderMidi = (info, filename) => {
    const panel = root.querySelector('[data-b="midi-panel"]');
    if (!panel) return;
    midiState.fileId = info.file_id || midiState.fileId;
    midiState.chosen = info.chosen_track;

    const tracks = info.tracks || [];
    const shown = (info.notes || []).length;
    const rng = (info.lo_midi != null && info.hi_midi != null)
      ? `${midiName(info.lo_midi)}–${midiName(info.hi_midi)}` : '—';

    const rows = tracks.map((t) => {
      const on = t.index === info.chosen_track;
      const tRng = t.note_count ? `${midiName(t.lo_midi)}–${midiName(t.hi_midi)}` : '—';
      const tag = t.index === info.suggested_track ? '<span class="tag">推荐</span>' : '';
      return `
        <button class="midi-row${on ? ' on' : ''}" data-act="midi-track:${t.index}"
                title="点这一轨就换用它，并重新生成试听">
          <span class="ix">轨 ${t.index}</span>
          <span class="nm">${esc(t.name)}${t.is_drum ? ' · 鼓轨' : ''}${tag}</span>
          <span class="ct">${t.note_count} 个音</span>
          <span class="rg">${tRng}</span>
          <span class="rs">${esc(t.reason)}</span>
        </button>`;
    }).join('');

    panel.innerHTML = `
      <div class="midi-head">
        <b>${esc(filename || info.filename || 'MIDI')}</b>
        · 提出 <b>${info.note_count || 0}</b> 个音 · 音域 ${rng}${
          info.note_count > shown ? `（图里只画前 ${shown} 个）` : ''}
      </div>
      <div class="midi-tracks">${
        rows || '<div class="plain">这个 MIDI 里没有可用的轨。</div>'}</div>
      <div class="midi-play">
        <span class="lb">试听 —— 现在这一轨提出来的，是不是你要唱的那首？</span>
        <audio data-b="midi-audio" controls preload="none"
               src="${esc(info.preview_url || '')}"></audio>
      </div>
      ${info.preview_url ? '' : `<div class="plain" style="color:var(--acc-ink)">
        试听没生成出来${info.preview_error ? `（${esc(info.preview_error)}）` : ''}
        —— 没有声音就没法确认，先别点「就用这一轨」。</div>`}
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px">
        <input data-b="midi-title" type="text"
               placeholder="给它起个名字，例如：不将就 · 副歌"
               style="flex:1;min-width:220px;padding:9px 12px;border:1px solid var(--line2);
                      border-radius:6px;font:inherit;font-size:13px">
        <button class="btn" data-act="midi-confirm">就用这一轨（存成模板）</button>
      </div>`;

    /* ⚠️ 后画的按钮必须**在这里**绑 —— 上面那次 `body.querySelectorAll`
       跑的时候它们还不存在。漏了这一句的表现是"点了没反应且不报错"。 */
    panel.querySelectorAll('button[data-act]').forEach((b2) => {
      b2.style.cursor = 'pointer';
      b2.onclick = () => onAct(b2);
    });

    /* 换轨之后自动把试听播一遍 —— 用户点另一轨**就是为了听它**。
       浏览器要求播放由用户手势触发，而这次点击正是那个手势，所以能播。
       播不了也不静默：告诉他按播放键（而不是让他以为试听坏了）。 */
    const au = panel.querySelector('[data-b="midi-audio"]');
    if (au && info.preview_url) {
      au.play().catch(() => {
        midiNote('试听已经生成好了 —— 按上面的播放键就能听'
                 + '（浏览器不允许自动播放，要点一下）。');
      });
    }
  };

  /** 换一轨：重新解析 + 重新出试听。 */
  const loadMidiTrack = async (ti) => {
    if (!midiState.fileId) {
      midiNote('⚠️ 那个 MIDI 已经不在了 —— 请重新上传。');
      return;
    }
    midiNote(`正在换到轨 ${ti}，并重新生成试听…`);
    try {
      const info = await source.midiTrack(midiState.fileId, ti);
      midiNote('点另一轨就能听它提出来的旋律；确认没问题再点「就用这一轨」。');
      renderMidi(info);
    } catch (e) {
      midiNote(`⚠️ 换轨失败：${e.message}`);
    }
  };

  /** 确认某一轨 → 存成模板 → **直接开练**（复用模式 B 的路径）。 */
  const confirmMidi = async (btn) => {
    const ti = midiState.chosen;
    if (ti === null || ti === undefined) {
      midiNote('⚠️ 先选一轨，再点「就用这一轨」。');
      return;
    }
    const tel = root.querySelector('[data-b="midi-title"]');
    const title = ((tel && tel.value) || '').trim();
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = '正在存…';
    try {
      const r = await source.midiConfirm({
        fileId: midiState.fileId, trackIndex: ti, title, singerId: singerId(),
      });
      const tid = r && r.template_id;
      if (!tid) throw new Error('服务端没有返回模板号');
      const label = r.title || title || 'MIDI 导入';
      midiNote(`已存成模板「${label}」（${r.note_count} 个音）。正在开始练习…`);
      /* ★ 存完**直接开练**：用户刚确认完参考，再让他回列表里找一遍
         是多余的点击。（`start` 会开麦克风 —— 这正是我们要的，
         因为他接下来就要跟着唱。） */
      await start({ mode: 'B', templateId: tid, label, transpose });
    } catch (e) {
      midiNote(`⚠️ 存模板失败：${e.message}。没有建立任何模板。`);
      btn.disabled = false;
      btn.textContent = old;
    }
  };

  // ── 渲染选择界面 ──
  // ⚠️ 行式列表，**不要用原型里的 `.mode`**：那是 s1 的大模式卡，
  //    带 `flex:1` 和大号 `.num`，放进这里每张都会撑成一整屏高。
  //    实测截图：内置曲目那一块整个空白、模板被挤到画面外 ——
  //    而探针当时是"通过"的（文字确实在 DOM 里，只是排到了看不见的地方）。
  const row = (title, meta, act) => `
    <div class="pickrow">
      <div class="pickmain">
        <div class="t">${esc(title)}</div>
        <div class="m">${esc(meta)}</div>
      </div>
      <div class="rowacts">
        <button class="go" data-act="${esc(act)}">开始</button>
      </div>
    </div>`;

  /* ★ 2026-10-18：模板行加「试听 / 改名」。
     原来只有「删 / 开始」；用户攒了模板之后只能看默认日期名，
     也不知道自己录的到底是什么样。试听与 B 模式共用服务端合成的引导音轨
     （4 拍预备 + 模板旋律），改名只改 title、不动音符。 */
  const tplRow = (t) => {
    const tid = t.template_id;
    return `<div class="pickrow" data-tpl="${esc(tid)}">
      <div class="pickmain">
        <div class="t">${esc(t.title || tid)}</div>
        <div class="m">我的模板 · ${esc(t.note_count || '?')} 个音 · ${esc(String(t.created_at || '').slice(0, 10))}${t.source_kind === 'song_vocal' ? ` · <b class="tpl-kind">歌曲参考 · 原调</b> · ${t.lyrics_count || 0} 行歌词` : ''}</div>
      </div>
      <div class="rowacts">
        <button class="pbtn" data-act="tpl-preview:${esc(tid)}"
          title="${t.source_kind === 'song_vocal' ? '试听真实伴奏（原调，带 2 秒预备）' : '试听这条模板的旋律（4 拍预备 + 合成旋律）'}">试听</button>
        <button class="pbtn" data-act="tpl-rename:${esc(tid)}"
          title="给这个模板改个名字">改名</button>
        ${(t.source_kind === 'song_vocal' && t.has_vocal)
          ? `<button class="pbtn" data-act="tpl-retry-lyrics:${esc(tid)}"
          title="用原唱人声重新识别歌词（旧报告不变，下次跟唱生效）">重识歌词</button>` : ''}
        <button class="pbtn" data-act="tpl-del:${esc(tid)}"
          title="删掉这个模板（连它的音符数据一起删，删了找不回来）">删</button>
        <button class="go" data-act="tpl:${esc(tid)}">开始</button>
      </div>
    </div>`;
  };

  const refCards = refs.map((r) => row(
    r.title || r.reference_id,
    `内置教材曲目 · ${r.note_count || '?'} 个音`,
    `ref:${r.reference_id}`)).join('');

  /* ★ 2026-10-20（待办 N5）：微练习模板会越攒越多（每练一句就新建一个
     `tpl_*`，标题都以「微练习 · 」开头）。它们和用户自己存的模板混在
     一个列表里，真正的模板会被挤到后面。现在分开：
       · 用户模板照常一行行显示；
       · 微练习片段收进一个默认收起的分组（仍然能试听/改名/删/开始）。
     为什么不直接隐藏/自动删：它们是「下一段」队列的落点，删了会让
     已生成的报告找不到参考；收起来是安全的最小改动。 */
  const isMicroTpl = (t) => String(t.title || '').startsWith('微练习');
  const userTpls = tpls.filter((t) => !isMicroTpl(t));
  const microTpls = tpls.filter(isMicroTpl);
  const tplCards = userTpls.map(tplRow).join('')
    + (microTpls.length
      ? `<details class="micro-tpls"><summary>微练习片段（${microTpls.length}）`
        + ' · 只用于「练这一句」，可试听 / 改名 / 删</summary>'
        + microTpls.map(tplRow).join('') + '</details>'
      : '');

  /* ══ 2026-10-18 版面重排：**先选路，再看这一条路的控件** ══════════
     原来四块「Step 1–4」全堆在一屏，用户选了 C 进来先看到 A 的六首歌，
     要滚 1100px 才找到上传框；一屏 2457px。
     现在用一个 4 个 tab 的**模式选择**，只渲染当前模式的 pane；
     入参来自 s1 的 `sl.mode`（C 就直接落在上传框）。
     ⚠️ `mode-tabs` 是按钮，不是 `data-act` —— 它们不触发"开始"，
        只切 pane；`onAct` 那条路径保持给真正的动作按钮。 */
  const MODE_META = {
    A: { title: '选一首内置曲目',
         guide: '选一首 → 点「开始」→ 系统先响 4 下节拍，第 5 下开口。戴耳机跟伴奏唱最准。' },
    B: { title: '选一个我的模板',
         guide: '选一个模板 → 「开始」→ 先响 4 下节拍；自己的模板放合成旋律，歌曲参考放真实伴奏。歌曲参考是原调，不能移调。' },
    C: { title: '上传一段录音',
         guide: '选一段录音 → 选「跟谁比」→ 上传分析。清唱最准；带伴奏的只能参考。' },
    D: { title: '导入 MIDI 当参考',
         guide: '传一个 MIDI → 试听、选对旋律轨 → 「就用这一轨」存成模板并开始。' },
    E: { title: '上传歌曲跟唱',
         guide: '上传一首歌 → 系统分出原唱和伴奏 → 你跟着伴奏唱，它拿原唱当参考。' },
  };
  const modeTabs = [['A', '内置曲目'], ['B', '我的模板'],
                    ['C', '上传录音'], ['D', '导入 MIDI'], ['E', '上传歌曲']]
    .map(([m, t]) => `<button class="mtab" type="button" role="tab"
      data-mode-tab="${m}" aria-selected="false">${m} · ${t}</button>`).join('');

  body.innerHTML = `
    <div class="mode-tabs" role="tablist" aria-label="练习方式">${modeTabs}</div>
    <div class="guide" data-b="setup-guide"></div>

    <div class="transpose" data-b="transpose-box">
      <span class="k">移调</span>
      <button class="pbtn" type="button" data-act="tr-down" aria-label="降半音">−</button>
      <b class="v" data-b="transpose-val" aria-live="polite">0</b>
      <button class="pbtn" type="button" data-act="tr-up" aria-label="升半音">+</button>
      <span class="unit">半音</span>
      <span class="plain" data-b="transpose-note">只改变参考音高；分数按移调后的参考算，不是改你的声音。</span>
    </div>

    <div class="mode-pane" data-mode-pane="A" hidden>
      <div class="eyebrow" style="font-size:10px">A · 内置曲目</div>
      <div class="picklist">${refCards ||
        '<div class="plain" style="font-size:12.5px">没有内置曲目。</div>'}</div>
      <div class="plain" style="font-size:12px;color:var(--ink3)">
        内置曲目是随包发的，不能删；想练别的歌用 B / C / D。
      </div>
    </div>

    <div class="mode-pane" data-mode-pane="B" hidden>
      <div class="eyebrow" style="font-size:10px">B · 我的模板</div>
      <div class="picklist" data-b="tpl-list">${tplCards}</div>
      <div class="who-form" style="margin-top:4px">
        <input data-b="new-tpl-name" aria-label="新模板的名字" type="text"
               placeholder="给新模板起个名字（先唱一遍，唱完再存）"
               style="flex:1;min-width:220px;padding:9px 12px;border:1px solid var(--line2);
                      border-radius:6px;font:inherit;font-size:13px">
        <button class="btn" data-act="new-tpl">新建模板（先唱一遍）</button>
      </div>
      <div class="plain" data-b="tpl-msg" style="margin-top:8px;font-size:12px;color:var(--ink3)"></div>
      ${(!userTpls.length && microTpls.length) ? `<div class="plain" style="margin-top:10px;font-size:12px;
                    color:var(--ink3)">你还没有自己存的模板；收起来的那一组是微练习片段，
                    点开可以用，但不是一首完整的歌。</div>` : ''}
      ${tplErr ? `<div class="plain" style="margin-top:10px;font-size:12px;
                    color:var(--acc-ink)">模板列表读取失败：${tplErr}</div>` : ''}
      ${(!tpls.length && !tplErr) ? `<div class="plain" style="margin-top:10px;font-size:12px;
                    color:var(--ink3)">你还没有模板。模板 = 你自己唱的一遍，
                    存下来之后就能跟着它练任意歌曲。</div>` : ''}
    </div>

    <div class="mode-pane" data-mode-pane="C" hidden>
      <div class="eyebrow" style="font-size:10px">C · 上传已经录好的</div>
      <div class="who-form">
        <input data-b="upload-file" aria-label="选择要上传的音频文件" type="file"
               accept="audio/*,.wav,.m4a,.mp3,.flac"
               style="flex:1;min-width:200px;font:inherit;font-size:13px">
        <select data-b="upload-ref" aria-label="这段录音对照的参考曲目"
                style="padding:8px 10px;border:1px solid var(--line2);border-radius:6px;
                       font:inherit;font-size:13px;max-width:260px">
          <option value="">— 拿什么跟它比？—</option>
          ${refs.map((r) => `<option value="ref:${r.reference_id}">内置：${
            String(r.title || r.reference_id).replace(/</g, '&lt;')}</option>`).join('')}
          ${tpls.map((t) => `<option value="tpl:${t.template_id}">我的模板：${
            String(t.title || t.template_id).replace(/</g, '&lt;')}</option>`).join('')}
        </select>
        <button class="btn" data-act="upload">上传并分析</button>
      </div>
      <div class="plain" data-b="upload-note" style="margin-top:10px;font-size:12px;color:var(--ink3)">
        支持 WAV / M4A / MP3 / FLAC，≤ 32 MB 且 ≤ 3 分钟。
        <b>要选一个"跟谁比"</b> —— 不选的话音准和节奏会是「本次未评估」。
        <b>清唱录音最准</b>；带伴奏的会先做谐波分离，评分仅供参考。
      </div>
    </div>

    <div class="mode-pane" data-mode-pane="D" hidden>
      <div class="eyebrow" style="font-size:10px">D · 导入 MIDI 当参考</div>
      <div class="who-form">
        <input data-b="midi-file" aria-label="选择 MIDI 文件作为参考" type="file"
               accept=".mid,.midi,audio/midi,audio/x-midi"
               style="flex:1;min-width:200px;font:inherit;font-size:13px">
        <button class="btn" data-act="midi-upload">解析这个 MIDI</button>
      </div>
      <div class="plain" data-b="midi-note" style="margin-top:10px;font-size:12px;color:var(--ink3)">
        想唱哪首歌，就找它的 <b>MIDI</b>（旋律的符号表示，不是录音）。
        传进来之后系统会列出<b>所有轨</b>，并猜一轨当旋律 ——
        但<b>一定要你亲耳听过、确认过才算数</b>。确认之后它就是你自己的一个模板，
        和内置曲目一样能练、能判走音。
      </div>
      <div data-b="midi-panel"></div>
    </div>

    <div class="mode-pane" data-mode-pane="E" hidden>
      <div class="eyebrow" style="font-size:10px">E · 上传歌曲，跟着原曲唱</div>
      <div class="who-form">
        <input data-b="song-file" aria-label="选择要跟唱的歌曲文件" type="file"
               accept="audio/*,.wav,.m4a,.mp3,.flac"
               style="flex:1;min-width:200px;font:inherit;font-size:13px">
        <button class="btn" data-act="song-inspect">读取这首歌曲</button>
      </div>
      <div class="plain" data-b="song-note" style="margin-top:10px;font-size:12px;color:var(--ink3)">
        支持 MP3 / M4A / WAV / FLAC。默认<b>整首</b>：系统自动分离原唱/伴奏、
        识别歌词，你跟着真实伴奏和滚动歌词唱；也可以只选 30–90 秒副歌。
        <b>戴耳机</b>跟唱最准（外放会把伴奏也录进麦克风）。
        原文件只存在你自己的电脑上。
      </div>
      <div data-b="song-library"></div>
      <div data-b="song-panel" hidden></div>
    </div>
  `;

  /* 切模式：只显示当前 pane；同时更新标题、引导与 `sl.mode`。
     为什么把 `sl.mode` 也写回去：s1 → s2 只写一次；在这里切换后，
     从 s2 去 s3 再回来，应该还是刚才那条路。 */
  const setMode = (mode, { scroll = false } = {}) => {
    if (!MODE_META[mode]) mode = 'A';
    wantMode = mode;
    try { sessionStorage.setItem('sl.mode', mode); } catch { /* 隐私模式 */ }
    body.querySelectorAll('[data-mode-pane]').forEach((p) => {
      p.hidden = p.dataset.modePane !== mode;
    });
    body.querySelectorAll('[data-mode-tab]').forEach((t) => {
      const on = t.dataset.modeTab === mode;
      t.classList.toggle('on', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    setText(root, 's2-title', MODE_META[mode].title);
    setText(root, 's2-state', '准备练习');
    setText(root, 'setup-guide', MODE_META[mode].guide);
    const trBox = root.querySelector('[data-b="transpose-box"]');
    if (trBox) trBox.hidden = mode === 'C' || mode === 'E';
    if (mode !== 'E') stopSongAudio();
    if (scroll) {
      const pane = body.querySelector(`[data-mode-pane="${mode}"]`);
      const first = pane && pane.querySelector('input, select, .pickrow');
      if (first && first.scrollIntoView) {
        try { first.scrollIntoView({ block: 'nearest' }); } catch { /* ignore */ }
      }
    }
  };
  body.querySelectorAll('[data-mode-tab]').forEach((t) => {
    t.addEventListener('click', () => {
      stopPreview();
      setMode(t.dataset.modeTab);
      if (t.dataset.modeTab === 'E') loadSongLibrary();
    });
  });
  setMode(wantMode, { scroll: true });

  /* ══ ★ 模式 E：上传歌曲（2026-10-20）══════════════════════════
     流程：选文件 → 读取时长/选段 → 后台分离 + 提取原唱参考 →
     试听原唱/伴奏/带倒数 → 确认成模板 → 复用模式 B 的录音与评分。
     ⚠️ 分离要 30–120 秒，**不能同步等**：每 1.2s 轮询一次进度。 */
  // var：setMode 初始化时会调 stopSongAudio → paintSongAudioButtons，
  // 那时这个 const 还没执行到（TDZ）。var 提升后是 undefined，guard 一下。
  var songPanel = root.querySelector('[data-b="song-panel"]');
  const songLib = root.querySelector('[data-b="song-library"]');
  let songState = null;
  let songTimer = null;
  // ⚠️ 必须是 var：`setMode()` 在 s2 初始化时会先调 `stopSongAudio()`，
  //    而那时下面的 `let` 还没执行到（TDZ）→ 整个 s2 接线失败。
  //    var 提升后初值是 undefined，`if (songAudio)` 安全。
  var songAudio = null;
  var songAudioKind = null;
  const SONG_AUDIO_LABEL = {
    vocal: '听原唱', instrumental: '听伴奏', guide: '4 拍 + 伴奏',
  };

  function paintSongAudioButtons() {
    if (!songPanel) return;
    songPanel.querySelectorAll('[data-act^="song-play:"]').forEach((b) => {
      const k = (b.dataset.act || '').slice(10);
      const base = SONG_AUDIO_LABEL[k] || '试听';
      const same = songAudio && songAudioKind === k;
      b.textContent = same && !songAudio.paused
        ? `⏸ 暂停（${base}）`
        : (same && songAudio.paused ? `▶ 继续（${base}）` : `▶ ${base}`);
    });
  }

  function stopSongAudio() {
    if (songAudio) {
      try { songAudio.pause(); } catch { /* 已停 */ }
      songAudio = null;
    }
    songAudioKind = null;
    paintSongAudioButtons();
  }
  const fmtClock = (ms) => {
    const s = Math.max(0, Math.round(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };
  const songErr = (msg) => {
    const n = root.querySelector('[data-b="song-note"]');
    if (n) { n.textContent = msg; n.style.color = 'var(--acc-ink)'; }
  };

  /* ★ 2026-10-20：E 屏「我上传过的歌」。
     作者实测：上传/分析后关掉页面再打开，E 屏只剩空文件框，
     以为上传的东西没了。已确认的模板其实在 B 列表，但用户不会去那里找；
     未确认的分析记录原来只在内存，服务重启就没了。现在两者都列出来。 */
  async function loadSongLibrary() {
    if (!songLib) return;
    let lib = { templates: [], jobs: [] };
    try {
      if (typeof source.songLibrary === 'function') {
        lib = await source.songLibrary(singerId());
      }
    } catch (e) {
      console.warn('[s2] 上传歌曲列表读取失败：', e.message);
      return;
    }
    const tpls = (lib && lib.templates) || [];
    const jobs = (lib && lib.jobs) || [];
    const n = tpls.length + jobs.length;
    if (!n) { songLib.innerHTML = ''; return; }
    const lv = (x) => ({ good: '好', ok: '一般', bad: '不足' }[x] || '—');
    const st = (x) => ({
      READY: '已分析，可继续', INSPECTED: '已读取，未分析',
      RUNNING: '分析中…', FAILED: '上次失败',
    }[x] || x);
    const rows = [];
    for (const t of tpls) {
      rows.push(`<div class="sl-row">
        <div class="sl-main"><b>${esc(t.title || '歌曲参考')}</b>
          <span>${fmtClock(t.duration_ms || 0)} · 参考质量${lv(t.quality_level)}
          · ${t.lyrics_count || 0} 行歌词 · 原唱参考</span></div>
        <div class="sl-acts">
          <button class="pbtn" type="button" data-sl-tpl="${esc(t.template_id)}">继续跟唱</button>
          ${t.has_vocal ? `<button class="pbtn" type="button" data-sl-retry-lyrics="${esc(t.template_id)}"
            title="用原唱人声重新识别歌词（旧报告不变，下次跟唱生效）">重识歌词</button>` : ''}
          <button class="pbtn" type="button" data-sl-del-tpl="${esc(t.template_id)}">删</button>
        </div></div>`);
    }
    for (const j of jobs) {
      rows.push(`<div class="sl-row">
        <div class="sl-main"><b>${esc(j.title || '未命名歌曲')}</b>
          <span>${fmtClock(j.duration_ms || 0)} · ${st(j.status)}
          ${j.lyrics_count ? ` · ${j.lyrics_count} 行歌词` : ''}
          ${j.progress ? ` · ${Math.round(j.progress)}%` : ''}</span></div>
        <div class="sl-acts">
          <button class="pbtn" type="button" data-sl-job="${esc(j.song_id)}">${
            j.status === 'READY' ? '继续' : (j.status === 'RUNNING' ? '看进度' : '继续读取')
          }</button>
          <button class="pbtn" type="button" data-sl-del-job="${esc(j.song_id)}">删</button>
        </div></div>`);
    }
    songLib.innerHTML = `<div class="song-lib">
      <div class="sl-head"><b>我上传过的歌</b><span>${n} 首 · 只在本机</span></div>
      <div class="sl-list">${rows.join('')}</div></div>`;
    songLib.querySelectorAll('[data-sl-tpl]').forEach((b) => {
      b.onclick = () => start({
        mode: 'B', templateId: b.dataset.slTpl,
        label: (tpls.find((x) => x.template_id === b.dataset.slTpl) || {}).title || '',
        transpose: 0,
      });
    });
    songLib.querySelectorAll('[data-sl-del-tpl]').forEach((b) => {
      b.onclick = async () => {
        if (!window.confirm('删掉这首歌的跟唱模板？伴奏/原唱/歌词副本会一起删，找不回来。')) return;
        try {
          await source.deleteTemplate(b.dataset.slDelTpl, singerId());
          await loadSongLibrary();
        } catch (e) { songErr(`删除失败：${e.message}`); }
      };
    });
    songLib.querySelectorAll('[data-sl-retry-lyrics]').forEach((b) => {
      b.onclick = async () => {
        if (typeof source.retryTemplateLyrics !== 'function') {
          songErr('当前数据源不支持重切歌词。'); return;
        }
        if (!window.confirm('重新识别这首歌的歌词？\\n\\n'
            + '只更新模板歌词，不会改旧报告；下次跟唱才生效。\\n'
            + '会跑一次 Whisper，可能需要几十秒。')) return;
        b.disabled = true;
        const old = b.textContent;
        b.textContent = '识别中…';
        try {
          const r = await source.retryTemplateLyrics(b.dataset.slRetryLyrics, singerId());
          songErr(`已重切 ${r.lyrics_count || 0} 行歌词；旧报告不变，下次跟唱生效。`);
          await loadSongLibrary();
        } catch (e) {
          songErr(`重切歌词失败：${e.message}`);
          b.disabled = false;
          b.textContent = old;
        }
      };
    });
    songLib.querySelectorAll('[data-sl-job]').forEach((b) => {
      b.onclick = async () => {
        try {
          const s = await source.songStatus(b.dataset.slJob, singerId());
          songState = {
            songId: s.song_id, duration: s.duration_ms || 0,
            title: s.title || '', start: s.start_ms || 0, end: s.end_ms || 0,
            info: { song_id: s.song_id, title: s.title,
                    duration_ms: s.duration_ms || 0,
                    suggested: { start_ms: 0, end_ms: s.duration_ms || 0 } },
          };
          if (s.status === 'READY') renderSongReady(s);
          else if (s.status === 'RUNNING' || s.status === 'QUEUED') {
            renderSongProgress(s);
            pollSong();
          } else if (s.status === 'INSPECTED') {
            renderSongInspect(songState.info);
          } else {
            songErr(s.error || '这首歌上次分析失败，可以重新选段再试。');
            renderSongInspect(songState.info);
          }
        } catch (e) { songErr(`打不开这首歌：${e.message}`); }
      };
    });
    songLib.querySelectorAll('[data-sl-del-job]').forEach((b) => {
      b.onclick = async () => {
        if (!window.confirm('删掉这首歌的分析记录？原始上传文件也会删。')) return;
        try {
          await source.songDelete(b.dataset.slDelJob, singerId());
          await loadSongLibrary();
        } catch (e) { songErr(`删除失败：${e.message}`); }
      };
    });
  }

  function songRange() {
    if (!songState) return;
    const start = songPanel.querySelector('[data-song="start"]');
    const len = songPanel.querySelector('[data-song="len"]');
    const lbl = songPanel.querySelector('[data-song="range-lbl"]');
    const maxLen = Math.min(600000, songState.duration);
    const full = len?.value === 'full';
    const qEl = songPanel.querySelector('[data-song="quality"]');
    const clean = !qEl || qEl.value === 'clean';
    songState.qualityMode = clean ? 'clean' : 'fast';
    let L = full ? maxLen : Math.min(Number(len?.value || 60) * 1000, maxLen);
    let S = full ? 0 : Math.max(0, Math.min(Number(start?.value || 0), songState.duration - L));
    songState.start = S;
    songState.end = S + L;
    songState.full = full;
    if (start) {
      start.disabled = full;
      start.max = String(Math.max(0, songState.duration - L));
      if (full) start.value = '0';
    }
    if (lbl) {
      const sep = L / 1000 * (clean ? 1.5 : 0.68);
      const eta = Math.max(15, Math.round(sep + L / 1000 * 0.18 + 8));
      lbl.textContent = (full
        ? `整首 ${fmtClock(0)} – ${fmtClock(L)} · 分析约 `
        : `${fmtClock(S)} – ${fmtClock(S + L)} · ${Math.round(L / 1000)} 秒 · 分析约 `)
        + `${Math.floor(eta / 60)}:${String(eta % 60).padStart(2, '0')}`
        + (clean ? ' · 四模型' : ' · 单模型');
    }
  }

  function renderSongInspect(info) {
    if (!songPanel) return;
    songState = {
      songId: info.song_id, duration: info.duration_ms,
      title: info.title || '', start: 0, end: info.duration_ms, info,
    };
    const maxLen = Math.min(600000, info.duration_ms);
    const canFull = info.duration_ms <= 600000;
    const segOpts = [30, 60, 90].filter((s) => s * 1000 <= maxLen);
    const opts = (canFull ? ['full'] : []).concat(segOpts.map(String));
    if (!opts.length) opts.push(String(Math.round(maxLen / 1000)));
    const chosen = canFull ? 'full' : (segOpts.includes(60) ? '60' : opts[0]);
    songState.full = chosen === 'full';
    songState.start = 0;
    songState.end = chosen === 'full' ? maxLen : Math.min(Number(chosen) * 1000, maxLen);
    songPanel.hidden = false;
    songPanel.innerHTML = `
      <div class="song-box">
        <div class="song-head">
          <b>${esc(info.title || '这首歌')}</b>
          <span>${fmtClock(info.duration_ms)}</span>
        </div>
        <label class="song-field">歌名
          <input data-song="title" type="text" maxlength="80"
                 value="${esc(info.title || '')}" aria-label="歌名">
        </label>
        <div class="song-range">
          <label>范围
            <select data-song="len" aria-label="整首还是只练一段">
              ${opts.map((v) => `<option value="${v}"${v === chosen ? ' selected' : ''}>${
                v === 'full' ? `整首（${fmtClock(info.duration_ms)}）` : `${v} 秒`
              }</option>`).join('')}
            </select>
          </label>
          <label>从
            <input data-song="start" type="range" min="0" step="1000"
                   max="${Math.max(0, info.duration_ms - 30000)}"
                   value="0" aria-label="从第几秒开始">
          </label>
          <label>分离
            <select data-song="quality" aria-label="分离质量">
              <option value="clean" selected>干净（四模型，推荐）</option>
              <option value="fast">快速（单模型）</option>
            </select>
          </label>
          <div class="plain" data-song="range-lbl"></div>
        </div>
        <div class="song-actions">
          <button class="btn dark" type="button" data-act="song-analyze">开始分析 →</button>
          <button class="btn" type="button" data-act="song-reset">换一首</button>
        </div>
        <div class="plain" style="font-size:11.5px;color:var(--ink3);margin-top:8px">
          默认<b>整首 · 干净分离</b>：四模型集成，明显减少单模型的高频「滋滋」声，
          但慢 2–3 倍（4–5 分钟的歌约 6–8 分钟）。赶时间选「快速（单模型）」。
          只想练副歌时选 30/60/90 秒，会快很多。分析完先让你试听确认。
        </div>
      </div>`;
    songPanel.querySelector('[data-song="start"]')?.addEventListener('input', songRange);
    songPanel.querySelector('[data-song="len"]')?.addEventListener('change', () => {
      songRange();
      const st = songPanel.querySelector('[data-song="start"]');
      if (st) st.value = String(songState.start);
    });
    songPanel.querySelector('[data-song="quality"]')?.addEventListener('change', songRange);
    songPanel.querySelector('[data-song="title"]')?.addEventListener('input', (e) => {
      songState.title = e.target.value;
    });
    songRange();
  }

  function renderSongProgress(job) {
    if (!songPanel) return;
    const p = Math.max(0, Math.min(100, Number(job.progress) || 0));
    songPanel.hidden = false;
    songPanel.innerHTML = `
      <div class="song-box">
        <div class="song-head"><b>${esc(job.title || songState?.title || '这首歌')}</b>
          <span>${p}%</span></div>
        <div class="song-prog"><i style="width:${p}%"></i></div>
        <div class="plain" data-song="stage">${esc(job.stage || '正在分析…')}</div>
        <div class="plain" style="font-size:11.5px;color:var(--ink3);margin-top:6px">
          分离 + 识别歌词要一会儿：30 秒副歌约 20–40 秒，
          整首 4–5 分钟约 3–4 分钟（CPU）。不要关页面；完成后先让你试听。
        </div>
      </div>`;
  }

  function renderSongReady(job) {
    if (!songPanel || !songState) return;
    const q = job.quality || {};
    const lv = q.level || 'ok';
    const lvText = { good: '好', ok: '一般', bad: '不足' }[lv] || '一般';
    const flags = (q.flags || []).map((x) => `<li>${esc(x)}</li>`).join('');
    const count = job.note_count || 0;
    const leak = (typeof q.leak_db === 'number') ? `${q.leak_db.toFixed(1)} dB` : '—';
    const hasPreview = !!source.songPreviewUrl(songState.songId, 'vocal');
    const bad = lv === 'bad';
    songPanel.hidden = false;
    songPanel.innerHTML = `
      <div class="song-box">
        <div class="song-head"><b>${esc(job.title || songState.title || '这首歌')}</b>
          <span>${fmtClock(job.start_ms)} – ${fmtClock(job.end_ms)}</span></div>
        <div class="song-quality q-${lv}">
          <b>参考质量：${lvText}</b>
          <s>${esc(q.summary || '')}</s>
          <em>原唱人声自动提取 · ${count} 个音 · 伴奏残留 ${leak}</em>
          ${flags ? `<ul>${flags}</ul>` : ''}
        </div>
        ${bad ? `<label class="song-force">
          <input data-song="force" type="checkbox">
          我知道参考可能不准，仍然使用（分数仅供参考）
        </label>` : ''}
        <div class="song-actions">
          <button class="btn dark" type="button" data-act="song-start"${bad ? ' disabled' : ''}>开始跟唱 →</button>
          <button class="btn" type="button" data-act="song-reinspect">换一段重新分析</button>
        </div>
        ${(job.lyrics && job.lyrics.length)
          ? `<div class="song-lyrics"><div class="ly-head"><b>歌词</b>
               <span>自动识别 ${job.lyrics.length} 行 · 可能有错</span></div>
             <div class="song-ly-scroll">${job.lyrics.map((x) =>
               `<div class="song-ly"><i>${fmtClock(x.start_ms)}</i>${esc(String(x.text || ''))}</div>`
             ).join('')}</div></div>`
          : `<div class="plain" style="font-size:11.5px;color:var(--ink3)">
               没识别出歌词（${esc(job.lyrics_error || '可能是纯音乐/人声太轻')}）——
               你仍然可以跟着伴奏唱，也可以把歌词粘在下面。
             </div>`}
        <details class="song-edit">
          <summary>歌词不对？粘贴正确歌词（一行一句）</summary>
          <textarea data-song="lyrics-edit" rows="5"
            placeholder="把音乐 App 里的歌词粘进来，一行一句；不填就用自动识别的。"
            aria-label="粘贴正确歌词">${(job.lyrics || []).map((x) => esc(String(x.text || ''))).join('\n')}</textarea>
          <div class="plain" style="font-size:11px;color:var(--ink3)">
            自动识别会出错（同音字/和声/重复段）。粘进来的歌词会按字数估算每行时间，
            滚动高亮不一定和原唱逐字对齐，但能跟着唱。
          </div>
        </details>
        ${hasPreview ? `<div class="song-audition">
          <button class="btn" type="button" data-act="song-play:vocal">▶ 听原唱</button>
          <button class="btn" type="button" data-act="song-play:instrumental">▶ 听伴奏</button>
          <button class="btn" type="button" data-act="song-play:guide">▶ 4 拍 + 伴奏</button>
        </div>` : `<div class="plain" style="font-size:12px;color:var(--ink3)">mock 模式没有真实音频，试听不可用。</div>`}
        <div class="plain" style="font-size:11.5px;color:var(--ink3);margin-top:8px">
          开始后会先响 4 下节拍，然后放<b>真实伴奏</b>；跟着唱，
          系统拿上面这段原唱当参考。练完在报告里看哪一句、哪几个音需要改。
          <b>戴耳机</b>：外放会让麦克风把伴奏也录进去，分数会不准。
        </div>
      </div>`;
    const force = songPanel.querySelector('[data-song="force"]');
    const go = songPanel.querySelector('[data-act="song-start"]');
    if (force && go) {
      force.addEventListener('change', () => { go.disabled = !force.checked; });
    }
    songState.quality = q;
    songState.lyrics = job.lyrics || [];
  }

  async function pollSong() {
    if (!songState) return;
    if (songTimer) { clearTimeout(songTimer); songTimer = null; }
    try {
      const job = await source.songStatus(songState.songId, singerId());
      if (job.status === 'READY') {
        renderSongReady(job);
        return;
      }
      if (job.status === 'FAILED') {
        songPanel.hidden = false;
        songPanel.innerHTML = `<div class="song-box"><div class="song-quality q-bad">
          <b>这一段分析失败</b><s>${esc(job.error || '未知错误')}</s>
          <em>换一段（比如副歌）再试；或者改用「导入 MIDI」</em></div>
          <div class="song-actions"><button class="btn" type="button"
            data-act="song-reinspect">换一段</button></div></div>`;
        return;
      }
      renderSongProgress(job);
      songTimer = setTimeout(pollSong, 1200);
    } catch (e) {
      songTimer = setTimeout(pollSong, 2000);
      console.warn('[s2] 歌曲进度轮询失败：', e.message);
    }
  }

  const doSongInspect = async (btn) => {
    const f = root.querySelector('[data-b="song-file"]')?.files?.[0];
    if (!f) { songErr('先选一首歌（MP3 / M4A / WAV / FLAC）。'); return; }
    if (f.size > 120 * 1024 * 1024) {
      songErr('文件太大了（>120 MB）。请用 MP3/M4A，或先剪掉前奏/尾奏。');
      return;
    }
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = '读取中…';
    try {
      const info = await source.songInspect(f, {
        title: f.name.replace(/\.[^.]+$/, '').slice(0, 80),
        singerId: singerId(),
      });
      const note = root.querySelector('[data-b="song-note"]');
      if (note) {
        note.style.color = 'var(--ink3)';
        note.textContent = '已读取。默认分析整首；赶时间可改成 30/60/90 秒。';
      }
      renderSongInspect(info);
      loadSongLibrary();
    } catch (e) {
      songErr(`读不了这首歌：${e.message}`);
    } finally {
      btn.disabled = false; btn.textContent = old;
    }
  };

  const doSongAnalyze = async (btn) => {
    if (!songState) return;
    const t = songPanel.querySelector('[data-song="title"]');
    if (t) songState.title = t.value.trim() || songState.title;
    songRange();
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = '正在开始…';
    stopSongAudio();
    try {
      const job = await source.songAnalyze(songState.songId, {
        startMs: songState.start, endMs: songState.end,
        title: songState.title, quality: songState.qualityMode || 'clean',
        singerId: singerId(),
      });
      renderSongProgress(job);
      pollSong();
    } catch (e) {
      songErr(`开不了分析：${e.message}`);
      btn.disabled = false; btn.textContent = old;
    }
  };

  const doSongStart = async (btn) => {
    if (!songState) return;
    const force = !!songPanel.querySelector('[data-song="force"]')?.checked;
    const ta = songPanel.querySelector('[data-song="lyrics-edit"]');
    const autoText = (songState.lyrics || []).map((x) => String(x.text || '')).join('\n').trim();
    const lyricsText = (ta && ta.value.trim() && ta.value.trim() !== autoText)
      ? ta.value.trim() : '';
    stopSongAudio();
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = '正在准备伴奏…';
    try {
      const r = await source.songConfirm(songState.songId, {
        title: songState.title, force, lyricsText, singerId: singerId(),
      });
      await start({ mode: 'B', templateId: r.template_id, label: r.title,
                    transpose: 0 });
    } catch (e) {
      songErr(`开始跟唱失败：${e.message}`);
      btn.disabled = false; btn.textContent = old;
    }
  };

  const doSongPlay = (kind) => {
    if (!songState) return;
    // 同一个音轨再点 = 暂停/继续；换一条 = 停旧的、播新的。
    if (songAudio && songAudioKind === kind) {
      if (songAudio.paused) {
        songAudio.play().catch((e) => songErr(`播放失败：${e.message}`));
      } else {
        songAudio.pause();
      }
      paintSongAudioButtons();
      return;
    }
    const url = source.songPreviewUrl(songState.songId, kind);
    if (!url) { songErr('mock 模式没有真实音频。'); return; }
    stopSongAudio();
    try {
      songAudio = new Audio(url);
      songAudioKind = kind;
      songAudio.onplay = paintSongAudioButtons;
      songAudio.onpause = paintSongAudioButtons;
      songAudio.onended = () => {
        songAudio = null; songAudioKind = null;
        paintSongAudioButtons();
      };
      songAudio.onerror = () => {
        songErr('这条音轨读不出来（预览失败）。');
        songAudio = null; songAudioKind = null;
        paintSongAudioButtons();
      };
      songAudio.play().catch((e) => songErr(`播放失败：${e.message}`));
      paintSongAudioButtons();
    } catch (e) {
      songErr(`播放失败：${e.message}`);
    }
  };

  // 动态按钮统一在面板里委托（body.innerHTML 之后不会自动重绑）
  songPanel?.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    e.preventDefault();
    onAct(b);
  });

  /** 参考移调：只改参考音高，不改用户声音；0 原调，范围 ±6。 */
  const setTranspose = (n) => {
    transpose = Math.max(-6, Math.min(6, Number(n) || 0));
    const val = root.querySelector('[data-b="transpose-val"]');
    if (val) val.textContent = transpose > 0 ? `+${transpose}` : String(transpose);
    try { sessionStorage.setItem('sl.transpose', String(transpose)); } catch { /* 隐私模式 */ }
  };
  setTranspose(transpose);

  /* 模板试听（2026-10-18）：与 B 模式共用 `/api/template/{tid}/guide.wav`。
     一次只放一条；切屏/再点一次都要停。 */
  let previewAudio = null;
  let previewTid = null;
  let previewBtn = null;
  const stopPreview = () => {
    if (previewAudio) {
      try { previewAudio.pause(); } catch { /* 已停 */ }
      previewAudio = null;
    }
    if (previewBtn && previewBtn.isConnected) previewBtn.textContent = '试听';
    previewTid = null;
    previewBtn = null;
  };
  const tplMsg = (text, warn = false) => {
    const el = root.querySelector('[data-b="tpl-msg"]');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = warn ? 'var(--acc-ink)' : 'var(--ink3)';
  };

  /* ★ 2026-10-10 从"内联 onclick"提成具名函数 `onAct`。
     为什么必须提：MIDI 那一块的按钮是**后画的**（要等解析回来才知道有几轨），
     内联写法只能绑到 `body` 里**当时已存在**的按钮上 ——
     于是动态画出来的按钮**点了没反应，而且不报错**，
     和"功能没做"长得一模一样（本项目最典型的那个坑）。
     现在静态与动态按钮共用这一条路径：两边都调 `onAct`。 */
  /* ⚠️ 必须是 async：G14 的「删模板」要在里面 `await source.deleteTemplate(...)`。
     调用方一律写成 `b.onclick = () => onAct(b)`（忽略返回值），
     所以改成 async 对外行为不变 —— 它本来就 `return start(...)` 一个 promise。 */
  const onAct = async (b) => {
      const act = b.dataset.act;
      /* ★ 模式 E：上传歌曲 */
      if (act === 'song-inspect') return doSongInspect(b);
      if (act === 'song-analyze') return doSongAnalyze(b);
      if (act === 'song-start') return doSongStart(b);
      if (act.startsWith('song-play:')) return doSongPlay(act.slice(10));
      if (act === 'song-reinspect') {
        stopSongAudio();
        if (songState?.info) renderSongInspect(songState.info);
        return;
      }
      if (act === 'song-reset') {
        stopSongAudio();
        songState = null;
        if (songPanel) { songPanel.hidden = true; songPanel.innerHTML = ''; }
        const f = root.querySelector('[data-b="song-file"]');
        if (f) f.value = '';
        const n = root.querySelector('[data-b="song-note"]');
        if (n) {
          n.style.color = 'var(--ink3)';
          n.textContent = '支持 MP3 / M4A / WAV / FLAC。系统会自动分离原唱和伴奏，'
            + '用原唱人声当参考，你跟着伴奏唱。';
        }
        return;
      }
      if (act === 'tr-down') { setTranspose(transpose - 1); return; }
      if (act === 'tr-up') { setTranspose(transpose + 1); return; }
      if (act === 'new-tpl') {
        const nameEl = root.querySelector('[data-b="new-tpl-name"]');
        const nm = (nameEl && nameEl.value || '').trim();
        // 新建模板 = 先按模式 B 唱一遍（不带 template_id，服务端会建成自比会话，
        // 音准/节奏本次不评 —— 本来也没法评，还没模板呢）。唱完在报告页存。
        try { sessionStorage.setItem('sl.pending_tpl_name', nm); } catch { /* ignore */ }
        return start({ mode: 'B', label: nm || '新建模板·第一遍', transpose: 0 });
      }
      if (act.startsWith('ref:')) {
        const rid = act.slice(4);
        const r = refs.find((x) => x.reference_id === rid);
        return start({ mode: 'A', referenceId: rid, label: r ? r.title : rid });
      }
      if (act.startsWith('tpl:')) {
        const tid = act.slice(4);
        const t = tpls.find((x) => x.template_id === tid);
        // ★ 2026-10-20：歌曲参考的伴奏是原调音频，不能移调；
        //   如果用户上一首设过 +2，这里必须归零，否则服务端会拒绝。
        if (t && t.source_kind === 'song_vocal') setTranspose(0);
        return start({ mode: 'B', templateId: tid, label: t ? t.title : tid });
      }
      /* ★ 2026-10-18：模板试听（合成旋律，不是原始录音）。
         点同一个按钮 = 停止；换一条 = 先停旧的。 */
      if (act.startsWith('tpl-preview:')) {
        const tid = act.slice(12);
        if (previewAudio && previewTid === tid && !previewAudio.paused) {
          stopPreview();
          return;
        }
        stopPreview();
        const url = `/api/template/${encodeURIComponent(tid)}/guide.wav`
          + `?singer_id=${encodeURIComponent(singerId())}`;
        const audio = new Audio(url);
        try {
          const c = await source.getConfig();
          const gv = ((c.feedback || {}).guide_volume);
          if (typeof gv === 'number' && isFinite(gv)) {
            audio.volume = Math.max(0, Math.min(1, gv));
          }
        } catch { /* 读不到就用默认音量 */ }
        previewAudio = audio;
        previewTid = tid;
        previewBtn = b;
        b.textContent = '停止';
        audio.onended = () => stopPreview();
        audio.onerror = () => {
          tplMsg('这个模板的试听音轨读不出来 —— 可能是服务端合成失败。', true);
          stopPreview();
        };
        tplMsg('正在试听模板旋律（4 拍预备 + 旋律）。');
        try {
          await audio.play();
        } catch (e) {
          tplMsg(`试听失败：${e.message}。`, true);
          stopPreview();
        }
        return;
      }

      /* ★ 2026-10-18：模板改名。只改 title，服务端不动音符。 */
      if (act.startsWith('tpl-rename:')) {
        const tid = act.slice(11);
        const t = tpls.find((x) => x.template_id === tid);
        const old = (t && t.title) || tid;
        const input = window.prompt('给这个模板起个新名字', old);
        if (input === null) return;
        const title = String(input).trim().slice(0, 60);
        if (!title) {
          tplMsg('模板名不能为空。', true);
          return;
        }
        if (title === old) {
          tplMsg('名字没有变。');
          return;
        }
        b.disabled = true;
        const oldTxt = b.textContent;
        b.textContent = '改…';
        try {
          await source.renameTemplate(tid, title, singerId());
          if (t) t.title = title;
          const rowEl = b.closest('.pickrow');
          const te = rowEl && rowEl.querySelector('.t');
          if (te) te.textContent = title;
          // C 的上传对照下拉是 mount 时写死的，改名后同步一下，
          // 否则同一个模板在 B 列表叫新名字、在 C 下拉里还是旧名字。
          const opt = root.querySelector(
            `[data-b="upload-ref"] option[value="tpl:${tid}"]`);
          if (opt) opt.textContent = `我的模板：${title}`;
          tplMsg(`已改名为「${title}」。`);
        } catch (e) {
          tplMsg(`改名失败：${e.message}。名字没变。`, true);
        } finally {
          b.disabled = false;
          b.textContent = oldTxt;
        }
        return;
      }

      /* ★ G52（2026-10-24）：旧歌曲模板重切歌词。
         只重跑 Whisper 更新 lyrics_json，不动音符、不改旧报告；
         按钮明确写“下次跟唱生效”，不要让人以为报告会立刻变。 */
      if (act.startsWith('tpl-retry-lyrics:')) {
        const tid = act.slice('tpl-retry-lyrics:'.length);
        const t = tpls.find((x) => x.template_id === tid);
        const name = (t && t.title) || tid;
        if (typeof source.retryTemplateLyrics !== 'function') {
          tplMsg('当前数据源不支持重切歌词。', true);
          return;
        }
        if (!window.confirm(`重新识别「${name}」的歌词？\n\n`
            + '只更新模板歌词，不会改旧报告；下次跟唱才生效。\n'
            + '会跑一次 Whisper，可能需要几十秒。')) return;
        b.disabled = true;
        const oldTxt = b.textContent;
        b.textContent = '识别中…';
        try {
          const r = await source.retryTemplateLyrics(tid, singerId());
          if (t) {
            t.lyrics_count = r.lyrics_count || 0;
            t.has_lyrics = !!(r.lyrics_count);
          }
          const rowEl = b.closest('.pickrow');
          const me = rowEl && rowEl.querySelector('.m');
          if (me && t) {
            me.innerHTML = me.innerHTML.replace(/\d+ 行歌词|未识别歌词/,
              `${t.lyrics_count || 0} 行歌词`);
          }
          tplMsg(`已重切 ${r.lyrics_count || 0} 行歌词；旧报告不变，下次跟唱生效。`);
        } catch (e) {
          tplMsg(`重切歌词失败：${e.message}`, true);
        } finally {
          b.disabled = false;
          b.textContent = oldTxt;
        }
        return;
      }

      /* ★ G14（2026-10-15）：删模板。
         ⚠️ 必须确认：这会连它存的音符一起删，删了找不回来。
            （`exp_*` 那几张表不动 —— 那是研究数据，与 `practice_store.purge`
              划的是同一条界线。） */
      if (act.startsWith('tpl-del:')) {
        const tid = act.slice(8);
        const t = tpls.find((x) => x.template_id === tid);
        const name = (t && t.title) || tid;
        if (!window.confirm(`删掉模板「${name}」？\n\n`
            + `这会连它存的音符一起删掉，删了就找不回来。\n`
            + `（你的练习记录和实验数据不受影响。）`)) return;
        b.disabled = true;
        const oldTxt = b.textContent;
        b.textContent = '删…';
        try {
          await source.deleteTemplate(tid, singerId());
          // 删成功：把这一行**直接从 DOM 拿掉** —— 不做整块重渲染，
          // 那要重绑一圈监听，而这里只需要少一行。
          tpls = tpls.filter((x) => x.template_id !== tid);
          const rowEl = b.closest('.pickrow');
          if (rowEl) rowEl.remove();
          // 模板全删光了要说一句，否则那一块**空着**，
          // 看起来像"列表坏了"而不是"你删完了"
          if (!tpls.length) {
            const list = root.querySelector('[data-b="tpl-list"]');
            if (list && !list.querySelector('.pickrow')) {
              list.insertAdjacentHTML('beforeend',
                '<div class="plain" style="font-size:12.5px">'
                + '你还没有模板。模板 = 你自己唱的一遍，'
                + '存下来之后就能跟着它练任意歌曲。</div>');
            }
          }
          tplMsg(`已删掉模板「${name}」。`);
        } catch (e) {
          // 失败要**说清原因**，不能静默 —— 否则用户以为删掉了，其实还在
          b.disabled = false;
          b.textContent = oldTxt;
          tplMsg(`删模板失败：${e.message}。它还在，可以再试一次。`, true);
        }
        return;
      }
      /* ★ 模式 C：上传已录好的（2026-10-01 接通）。
         为什么这条路以前是死的：首页第三张卡标着「离线分析 · File Input · WAV」，
         点进去却只看到「内置曲目 / 我的模板」—— **一个文件选择框都没有**。
         后端 `POST /api/upload` 一直是好的，`api.js` 的 `uploadRecording` 也一直存在，
         但界面上零调用。属于"后端通了、用户走不通"（和 G4-a 同一类）。

         ⚠️ 与 A/B 两路的关键差别：**不走实时管线**。
            A/B 是"进实时画面 → 开麦 → 唱 → finish"；C 是"选文件 → 上传 → 服务端
            在后台精算 → 轮询到 SCORED"。所以这里不能复用 `start()`，
            它会把界面切到实时画面并去开麦克风 —— 而上传路径根本不需要麦克风
            （用户可能就是在没有麦的机器上补分析一段手机录音）。 */
      if (act === 'upload') {
        const fel = root.querySelector('[data-b="upload-file"]');
        const f = fel && fel.files && fel.files[0];
        if (!f) {
          setText(root, 'upload-note',
            '⚠️ 先选一个音频文件，再点「上传并分析」。支持 WAV / M4A / MP3 / FLAC。');
          return;
        }
        const note = (t) => setText(root, 'upload-note', t);
        /* ★ 必须选一个"跟谁比"（2026-10-01）。
           不选的话服务端只能建成**自比**会话 —— 表现力照常评（它不需要参考），
           但音准/节奏会按契约降级成「本次未评估」。也就是：**上传了却拿不到音准分**。
           那还是"陪练"吗？所以这里强制选参考，并在说明里写清为什么。 */
        const refSel = root.querySelector('[data-b="upload-ref"]');
        const refVal = (refSel && refSel.value) || '';
        if (!refVal) {
          note('⚠️ 先选一个「拿什么跟它比」—— 内置曲目或你的模板都行。'
               + '不选的话系统没得比，音准与节奏只能是「本次未评估」。');
          refSel && refSel.focus();
          return;
        }
        const isRef = refVal.startsWith('ref:');
        const targetId = refVal.slice(4);
        const label = refSel.options[refSel.selectedIndex].textContent.replace(/^[^：]*：/, '');
        b.disabled = true;
        const old = b.textContent;
        b.textContent = '正在上传…';
        note(`正在上传 ${f.name}（${(f.size / 1048576).toFixed(1)} MB）…`);
        (async () => {
          try {
            const r = await source.uploadRecording(f, isRef
              ? { referenceId: targetId }
              : { templateId: targetId });
            const uid = r && (r.session_uid || (r.data && r.data.session_uid));
            if (!uid) throw new Error('服务端没有返回会话号');
            try {
              sessionStorage.setItem('sl.sess', uid);
              sessionStorage.setItem('sl.mode', 'C');
              sessionStorage.setItem('sl.last_ref_title', f.name);
            } catch { /* 隐私模式 */ }
            note('上传完成，正在后台分析…（这一段约几秒）');
            /* 轮询到出结果再跳报告页。
               ⚠️ 不轮询直接跳的话，s3 会在精算没完成时就去取报告，
                  用户看到的是一句"取报告失败" —— 而它其实马上就好。
                  契约里 `GET /session/{uid}/status` 就是为这个存在的，
                  数据层也一直有 `getSessionStatus`，只是界面里从来没人调。 */
            const t0 = Date.now();
            for (;;) {
              const st = await source.getSessionStatus(uid);
              const s = st && (st.status || (st.data && st.data.status));
              if (s === 'SCORED' || s === 'FAILED') break;
              if (Date.now() - t0 > 180000) throw new Error('分析超时（超过 3 分钟）');
              await new Promise((res) => setTimeout(res, 600));
            }
            note('分析完成，正在打开报告…');
            location.hash = '#s3';
          } catch (e) {
            note(`⚠️ 上传失败：${e.message}。没有建立任何会话。`);
          } finally {
            b.disabled = false;
            b.textContent = old;
          }
        })();
        return;
      }

      /* ★ 模式 D：导入 MIDI（2026-10-10）。
         与 A/B/C 三路的关系：它不是第五种"评分方式"，
         而是**给模式 B 造一个参考** —— 确认之后拿到的是模板，
         走的还是 B 的练习路径（对齐/评分/教练/练习计划一行都没改）。 */
      if (act === 'midi-upload') {
        const fel = root.querySelector('[data-b="midi-file"]');
        const f = fel && fel.files && fel.files[0];
        if (!f) {
          midiNote('⚠️ 先选一个 .mid / .midi 文件，再点「解析这个 MIDI」。');
          return;
        }
        b.disabled = true;
        const old = b.textContent;
        b.textContent = '正在解析…';
        midiNote(`正在解析 ${f.name}…`);
        (async () => {
          try {
            const info = await source.midiUpload(f);
            // 面板里已经写了文件名与音符数，这一行留给"下一步该干什么"
            midiNote('点另一轨就能听它提出来的旋律；确认没问题再点「就用这一轨」。');
            renderMidi(info, f.name);
          } catch (e) {
            // ⚠️ 失败必须**说出来**：静默的话用户只看到"点了没反应"
            midiNote(`⚠️ 这个 MIDI 读不出来：${e.message}`);
          } finally {
            b.disabled = false;
            b.textContent = old;
          }
        })();
        return;
      }
      if (act.startsWith('midi-track:')) return loadMidiTrack(+act.slice(11));
      if (act === 'midi-confirm') return confirmMidi(b);
  };

  body.querySelectorAll('button[data-act]').forEach((b) => {
    b.style.cursor = 'pointer';
    b.onclick = () => onAct(b);
  });

  /* ⚠️ 这里**不要**再写 `void 某个已删除的变量`。
     踩过的坑：`titleEl` 已经从上面删掉了，但这一行还留着 `void titleEl` ——
     它不报错、也让"未定义名"那类静态检查失效（`void x` 也算一次使用），
     只在运行时抛 `titleEl is not defined`，被 applyBindings 的 catch 兜住。
     表现是"开始态渲染了一半 + 一条错误横幅"（这次正是横幅把它指出来的）。
     教训：`void` 是给**有意不用**的变量消警告的，不是给"我忘了删"擦屁股的。 */
  /* 模式定位已由 `setMode(wantMode, { scroll: true })` 统一处理 ——
     不再为 D 单独写一段：四条路现在共用同一个"切到这条路并滚过去"的实现。 */
  if (wantMode === 'E') loadSongLibrary();

  /* ★ 微练习（2026-10-19）：s3 点「练这一句」后写 `sl.practice` 并跳 s2。
     这里只消费一次（started=true），避免从 s2 返回/切换时又自动开一遍；
     「再练一次」会把 started 重置为 false。 */
  try {
    const raw = sessionStorage.getItem('sl.practice');
    const practice = raw ? JSON.parse(raw) : null;
    if (practice && practice.templateId && !practice.started) {
      practice.started = true;
      sessionStorage.setItem('sl.practice', JSON.stringify(practice));
      start({ mode: 'B', templateId: practice.templateId,
              label: practice.title || '微练习 · 这一段' }).then((s) => {
        if (!s) return;
        practice.madeUid = s.session_uid;
        try { sessionStorage.setItem('sl.practice', JSON.stringify(practice)); } catch { /* ignore */ }
      }).catch(() => { /* start 自己已提示 */ });
    }
  } catch { /* 隐私模式 */ }

  void wantRef; void q;
  return { bound: true, refs: refs.length, templates: tpls.length };
}

/**
 * 取这次练习的参考音高（画"它希望你唱的音"那条阶梯）。
 *
 * 为什么前端要自己取一份：服务端的 `sess.rt.ref_notes` 是**给服务端**用的；
 * 前端画图需要 `[start_ms, end_ms, midi]` 这个形状，而它在
 * ① 模式 A → `bootstrap().references` 里有 `notes_midi`
 * ② 模式 B → 模板的音符序列（`GET /api/template` 的列表**不含**音符，只有个数）
 * 之间不一致。所以这里按模式分别取，取不到就返回空数组 ——
 * **空数组是有意义的**：drawLive/drawRoll 会画"没有参考"的空态，
 * 而不是画一条假的阶梯。
 */
async function loadRefNotes(source, sess) {
  const mode = sess.mode || (sess.template_id ? 'B' : 'A');
  // ★ 2026-10-18：参考移调后，实时图上的灰线也要跟着移；
  //   否则用户按 +2 唱，图还画原调，看起来像"每句都偏高"。
  const tr = Number(sess.transpose) || 0;
  try {
    if (mode === 'A' && sess.reference && sess.reference.reference_id) {
      const boot = await source.bootstrap();
      const r = (boot.references || []).find(
        (x) => x.reference_id === sess.reference.reference_id);
      if (r && Array.isArray(r.notes_midi)) {
        // 内置曲目的时值固定（`note_dur_ms`），按序展开成 [s, e, midi]
        const dur = r.note_dur_ms || 500;
        return r.notes_midi.map((m, i) => [i * dur, (i + 1) * dur, m + tr]);
      }
    }
    if (sess.template_id && typeof source.getTemplateNotes === 'function') {
      const ns = await source.getTemplateNotes(sess.template_id);
      if (Array.isArray(ns)) {
        return ns.filter((n) => n && n.pitch_midi != null)
                 .map((n) => [n.start_ms, n.end_ms, n.pitch_midi + tr]);
      }
    }
  } catch (e) {
    console.warn('[s2] 取参考音高失败（实时图将不显示参考阶梯）：', e.message);
  }
  return [];
}

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
/** MIDI 号 → 音名（Now Singing 那个大字）。 */
function midiName(midi) {
  const m = Math.round(Number(midi));
  if (!isFinite(m)) return '—';
  return NOTE_NAMES[((m % 12) + 12) % 12] + (Math.floor(m / 12) - 1);
}

/**
 * s9 · 微练习结果（2026-10-19）
 *
 * 只显示「这一句练得怎么样」：句子/时间、之前/这次/目标、过没过、
 * 听整句/朗读、再练/下一段/回完整报告。完整报告仍在 s3 —— 两件事解耦。
 */
async function s9(root, ctx) {
  const { source } = await import('./data/index.js');
  const set = (key, text) => {
    const n = root.querySelector(`[data-b="${key}"]`);
    if (n) n.textContent = text == null ? '' : String(text);
  };
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  let st = null;
  try { st = JSON.parse(sessionStorage.getItem('sl.practice') || 'null'); }
  catch { st = null; }
  if (!st || !st.madeUid || !st.card) {
    set('micro-title', '没有可显示的微练习记录');
    set('micro-pass', '先去完整报告点「练这一句」。');
    // 空态给一个真的出口：没有入口的空态等于一句废话。
    const act0 = root.querySelector('[data-b="micro-actions"]');
    if (act0) {
      act0.replaceChildren();
      const b = el('button', 'btn dark', '去评测报告 →');
      b.type = 'button';
      b.onclick = () => { location.hash = '#s3'; };
      act0.append(b);
    }
    return { bound: false };
  }
  let rep = null;
  let lastErr = null;
  // 精算在 finish 后异步跑；s2 一 finish 就跳 s9，直接取报告会撞 409
  // “报告还没准备好（ANALYZING）”。这里轮询到 SCORED，最多约 32 秒。
  for (let i = 0; i < 40; i++) {
    try {
      rep = await source.getReport(st.madeUid);
      break;
    } catch (e) {
      lastErr = e;
      const msg = String(e.message || '');
      if (!/ANALYZING|409|还没准备好|not ready/i.test(msg)) break;
      if (i === 0) {
        set('micro-title', '正在精算这一段…');
        set('micro-pass', '报告还没准备好，正在重试。');
      }
      await new Promise((r) => setTimeout(r, 800));
    }
  }
  if (!rep) {
    set('micro-title', '这次微练习报告读不到');
    set('micro-pass', lastErr ? lastErr.message : '未知错误');
    return { bound: false };
  }
  const before = st.card.before || {};
  const after = practiceAfter(rep, st);
  const success = st.card.success || {};
  const metric = success.metric || 'pitch_median_abs';
  const unitMap = {
    pitch_median_abs: ' 音分', tempo_ratio: '',
    onset_median: ' ms', dynamic_range: ' dB', beat_hit_rate: '%',
  };
  const unit = success.unit != null ? success.unit : (unitMap[metric] || '');
  const fmt = (v) => (typeof v === 'number' && Number.isFinite(v)
    ? (metric === 'tempo_ratio' ? v.toFixed(2) : String(Math.round(v * 10) / 10))
    : '—');
  const a = after ? after[metric] : null;
  const t = success.target;
  let pass = null;
  if (typeof a === 'number') {
    if (success.op === 'lte') pass = a <= t;
    else if (success.op === 'gte') pass = a >= t;
    else if (success.op === 'between' && Array.isArray(t)) pass = a >= t[0] && a <= t[1];
  }
  const queue = Array.isArray(st.queue) && st.queue.length ? st.queue : [st.card];
  const idx = Number.isInteger(st.index) ? st.index : 0;
  const next = pass === true && idx + 1 < queue.length ? queue[idx + 1] : null;
  const label = (st.card && st.card.label) || '这一段';
  const c = st.card.context || {};
  set('micro-meta', `微练习 · ${label}`
    + (c.time ? ` · ${c.time}` : ''));
  set('micro-title', `${label} · ${pass === true ? '过了'
    : (pass === false ? '还差一点' : '本次未测到')}`);

  const cbox = root.querySelector('[data-b="micro-context"]');
  if (cbox && c.line) {
    cbox.replaceChildren();
    const line = el('div', 'plan-context',
      `第 ${c.line_no || '?'} 句「${c.line}」`
      + (c.time ? ` · ${c.time}` : '')
      + (c.syllables ? ` · 问题在「${c.syllables}」` : ''));
    cbox.append(line);
  }
  const ab = root.querySelector('[data-b="micro-ab"]');
  if (ab) {
    ab.replaceChildren();
    const box = (k, v, cls) => {
      const d = el('div', 'practice-ab-box' + (cls ? ' ' + cls : ''));
      d.append(el('span', 'k', k));
      d.append(el('b', 'v mono', v));
      return d;
    };
    ab.append(box('之前', `${fmt(before[metric])}${unit}`));
    ab.append(box('这次', `${fmt(after ? after[metric] : null)}${unit}`,
      pass === true ? 'ok' : (pass === false ? 'bad' : '')));
    const targetText = Array.isArray(t) ? `${t[0]}–${t[1]}`
      : (t == null ? '' : `${t}${unit}`);
    if (targetText) ab.append(box('目标', targetText));
  }
  set('micro-pass', pass === true
    ? '目标达到了。回完整报告看看整首有没有一起变好。'
    : (pass === false
      ? '还没到目标。再练一次这一段，只盯一个动作。'
      : '这次没测到可比较的指标。'));
  const rbox = root.querySelector('[data-b="micro-reason"]');
  if (rbox) {
    rbox.textContent = pass === null
      ? '系统没把这次录音对到参考（多半是没进拍、起唱太晚或太短）。'
        + '先点「听整句」，跟着 4 拍从第 1 个字开始，再点完成。'
      : '';
  }
  set('micro-style', st.card.style_note ? ('风格说明：' + st.card.style_note) : '');
  const demoBox = root.querySelector('[data-b="micro-demo"]');
  if (demoBox) {
    demoBox.replaceChildren();
    const demo = st.card.demo || {};
    if (Array.isArray(demo.notes) && demo.notes.length) {
      const dl = el('button', 'btn', '▶ 听整句');
      dl.type = 'button';
      dl.onclick = async () => {
        dl.disabled = true;
        try { await playPlanDemo(demo); } catch { /* ignore */ }
        dl.disabled = false;
      };
      demoBox.append(dl);
    }
    if (c.line && typeof source.aiSingUrl === 'function') {
      const ai = el('button', 'btn', '🎤 AI 唱');
      ai.type = 'button';
      ai.title = '本地 DiffSinger 合成，约 5 秒一句；不影响评分';
      ai.onclick = async () => {
        if (ai.disabled) return;
        ai.disabled = true;
        const old = ai.textContent;
        ai.textContent = 'AI 正在唱…（约 5 秒）';
        try {
          const cfg = await source.getConfig();
          const voice = ((cfg && cfg.feedback) || {}).ai_voice || 'female';
          if (voice === 'off') {
            ai.textContent = 'AI 人声已关闭（去设置打开）';
            setTimeout(() => { ai.textContent = old; ai.disabled = false; }, 2200);
            return;
          }
          const url = source.aiSingUrl({ sessionUid: st.madeUid, voice });
          if (!url) throw new Error('mock 模式没有本地 DiffSinger');
          const audio = new Audio(url);
          audio.onended = () => {
            ai.textContent = old;
            ai.disabled = false;
          };
          await audio.play();
          ai.textContent = voice === 'male' ? '🎤 AI 唱（男声）' : '🎤 AI 唱（女声）';
        } catch (e) {
          ai.textContent = `⚠️ AI 唱失败：${e.message}`;
          setTimeout(() => { ai.textContent = old; ai.disabled = false; }, 3500);
        }
      };
      demoBox.append(ai);
    }
    if (demo.hint) demoBox.append(el('span', 'plain', demo.hint));
  }
  const actions = root.querySelector('[data-b="micro-actions"]');
  if (actions) {
    actions.replaceChildren();
    if (next) {
      const nb = el('button', 'btn', `下一段：${next.label}`);
      nb.type = 'button';
      nb.onclick = () => {
        nb.disabled = true;
        startPracticeAtIndex(source, st.originalUid, queue, idx + 1).catch(() => {
          nb.disabled = false;
        });
      };
      actions.append(nb);
    }
    const again = el('button', 'btn', '再练一次这一段');
    again.type = 'button';
    again.onclick = () => {
      st.started = false;
      st.madeUid = null;
      try { sessionStorage.setItem('sl.practice', JSON.stringify(st)); } catch { /* ignore */ }
      location.hash = '#s2';
    };
    const back = el('button', 'btn', '回完整报告');
    back.type = 'button';
    back.onclick = () => {
      try {
        sessionStorage.setItem('sl.sess', st.originalUid);
        sessionStorage.removeItem('sl.practice');
      } catch { /* ignore */ }
      location.hash = '#s3';
    };
    actions.append(again);
    actions.append(back);
  }
  void ctx;
  return { bound: true };
}

const BINDINGS = { s1, s2, s3, s4, s5, s6, s7, s8, s9 };


/** 由各视图在 render 之后调用（router 会传 root, ctx）。 */
export async function applyBindings(viewId, root, ctx) {
  const fn = BINDINGS[viewId];
  if (!fn) return null;
  try {
    return await fn(root, ctx);
  } catch (e) {
    console.error(`[bindings] ${viewId} 接线失败：`, e);
    // ★ 必须把失败**显示出来**。
    //   踩过的坑：接线中途抛异常时，前面已经填上的值留着、后面的一个没填 ——
    //   界面于是呈现"一半真数据 + 一半稿子里的死数字"的混合态。
    //   那比全空更危险：死数字看起来完全合理（78/86/91 本来就长得像分数），
    //   只有真去比对才知道那几个数从没被改写过。
    //   所以在页面顶部挂一条横幅，并把错误也写进 <title>（截图/探针都能看到）。
    try {
      const bar = document.createElement('div');
      bar.id = 'bind-error';
      bar.style.cssText =
        'position:fixed;left:0;right:0;bottom:0;z-index:9999;' +
        'background:#93241a;color:#fff;font:12px/1.5 Consolas,monospace;' +
        'padding:8px 14px;white-space:pre-wrap';
      bar.textContent = `⚠️ [#${viewId}] 数据接线失败：${e && e.message ? e.message : e}` +
        '　（页面上未改写的位置仍是原型稿里的示例数字，不是你的成绩）';
      document.body.append(bar);
      document.title = `⚠️ 接线失败 #${viewId} — ${document.title}`;
    } catch { /* DOM 不可用就算了，不能因为报错处理再抛一次 */ }
    return { error: e && e.message ? e.message : String(e) };
  }
}
