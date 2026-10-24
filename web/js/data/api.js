/**
 * apiSource —— 真实后端实现。
 *
 * 契约 §3.1 的 12 个端点。
 *
 * ━━━ 关于这一层的职责（重要）━━━
 *
 * `source.js` 里的 `DataSource` 是 UI 唯一的依赖，它的方法签名和返回形状
 * 是**前端内部的契约**。后端的传输形状（HTTP 响应体）是**另一份契约**。
 * 两者的差异**由这一层吸收** —— 这就是适配层存在的意义。
 *
 * 实测发现两边有 8 处不一致（后端的形状本身是合理的，只是和 UI 约定的不同），
 * 处理原则：
 *   · UI 确实需要但后端没给的数据  → 后端补（/references、reference 对象、
 *     progress 的 labels、experiment 的 weights/r2/rho）
 *   · 只是命名/嵌套层级不同        → 在这一层翻译（本文件）
 *
 * 这样 UI 一行都不用改，后端也保持契约形状。
 */
import { DataSource, unwrap, ApiError, SessionStatus } from './source.js';
// ★ 身份的唯一入口（`web/js/data/identity.js`）—— 别再各处自己读 storage，
//   那样"换人"就会有漏网之鱼（见该文件头部的说明）。
//   ⚠️ 是 `getSingerId()` 不是 `singerId()`：那个名字在 `bindings.js` 里，
//      api 适配层不该反向依赖界面层。
import { getSingerId } from './identity.js';

const BASE = '/api';

async function req(method, path, body) {
  const init = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(BASE + path, init);
  return unwrap(resp);
}

export class ApiSource extends DataSource {
  async bootstrap() {
    const [config, refs, singers] = await Promise.all([
      req('GET', '/config'),
      req('GET', '/references'),
      // ★ G3：真实演唱者名册（2026-10-01）。
      //   这里以前硬编码 `[{singer_id:'S01', name:'我（S01）'}]` ——
      //   于是**不管库里有多少人、当前该是谁**，前端永远认为是同一个人：
      //   两个人的记录会串在一起，而且界面上没有任何换人入口。
      //   现在名册来自库（`app_singer`），每个 singer_id 都是真实存在的
      //   （后端对不存在的演唱者返回 404，不再是"空列表"）。
      this.listSingers().catch((e) => {
        // 名册取不到**不能**让 bootstrap 整体失败 —— 那会让 8 屏全部空掉。
        // 但也不能装作成功：调用方（roster.js）会看到空名册并如实显示。
        console.warn('[api] 演唱者名册读取失败：', e.message);
        return [];
      }),
    ]);
    /* ★ 把 `/api/references` 的音符形状翻译成 **DataSource 契约的形状**
       （2026-09-24 修；契约 §7.2：形状差异由适配层吸收，UI 不碰 HTTP 细节）。

       后端给的是 `notes: [{index, midi}, …]` + `duration_ms`；
       而 UI（`bindings.js` 的 `loadRefNotes`）按 `notes_midi` / `note_dur_ms` 消费 ——
       mock 源就是那么写的。**两边字段名不一致的后果是静默的**：
       模式 A 的实时图里"它希望你唱的音"那条阶梯**永远不画**，
       而页面上看不出任何异常（`loadRefNotes` 取不到就返回空数组，
       `drawLive` 会画"没有参考"的空态）。

       为什么这个洞活了这么久：`?src=mock` 下 `notes_midi` 是有的，
       于是**只有真实后端 + 模式 A** 才会露出来 —— 而默认数据源以前正是 mock。
       （审计原话："mock 源把这条完美遮住了"。） */
    const references = (refs || []).map((r) => {
      const midis = Array.isArray(r.notes) && r.notes.length
        ? r.notes.map((n) => (n && typeof n.midi === 'number' ? n.midi : null))
                .filter((m) => m !== null)
        : (Array.isArray(r.notes_midi) ? r.notes_midi : []);
      // 内置曲目的时值是均匀的 ⇒ 用总时长除以音符数；有 note_dur_ms 就优先用它
      const dur = r.note_dur_ms
        || (r.note_count && r.duration_ms ? Math.round(r.duration_ms / r.note_count) : 500);
      return { ...r, notes_midi: midis, note_dur_ms: dur };
    });
    return { singers, references, config };
  }

  /* ── 演唱者名册（G3）──────────────────────────────────────
     ⚠️ 形状必须与 mock 源一字不差：`{singer_id, name, sessions, templates}`。
        少一个字段的表现不是报错，而是界面上某一格永远空着。 */
  async listSingers() {
    const r = await req('GET', '/singer');
    return r.singers || [];
  }

  async createSinger({ name, singerId }) {
    const body = { name };
    if (singerId) body.singer_id = singerId;
    const r = await req('POST', '/singer', body);
    return { created: !!r.created, renamed_from: r.renamed_from ?? null,
             singer: r.singer };
  }

  /**
   * 删除一位演唱者。
   * ⚠️ 返回里**必须**把"没删什么"带回去（`files_deleted` / `skipped_reason`）：
   *    录音文件是故意不删的（文件名里没有可靠的演唱者信息），
   *    界面上不说清楚，用户会以为录干净了 —— 而磁盘上还在。
   */
  async deleteSinger(singerId) {
    return req('DELETE', `/singer/${encodeURIComponent(singerId)}`);
  }

  /**
   * 开一次练习会话。
   *
   * ★ `templateId`（2026-09-23 补）：模式 B 两遍制的第二遍必须带上第一遍建的模板，
   *   否则服务端只能拿"这次演唱自己"当参考 —— 自比必然完美，
   *   音准/节奏恒 100（服务层会把这种情况显式降级成 null）。
   *   带了模板才是真的"跟第一遍比"。
   *
   * ⚠️ `referenceId` 与 `templateId` 不能同时给（服务端会 400）：
   *   前者是内置曲目（模式 A），后者是用户模板（模式 B），两条路互斥。
   */
  async createSession({ singerId, mode, referenceId, templateId, transpose = 0 }) {
    const body = { mode, singer_id: singerId };
    if (templateId) body.template_id = templateId;
    else if (referenceId) body.reference_id = referenceId;
    // 2026-10-18：移调只用于现场练习（A/B）；服务端会 clamp ±12。
    if (transpose) body.transpose = Number(transpose) || 0;
    return req('POST', '/session', body);
  }

  /**
   * 把某一次练习存成「跟唱模板」（模式 B 的第一遍）。
   *
   * `title` 现在由用户自己填 —— 默认的「我的模板 MM-DD HH:MM」在
   * "想练什么歌都行"的场景下根本分不清哪首是哪首（用户会攒一堆模板）。
   */
  createTemplate({ sessionUid, title, referenceId, singerId }) {
    const body = {
      session_uid: sessionUid,
      title,
      singer_id: singerId || getSingerId(),
    };
    if (referenceId) body.reference_id = referenceId;
    return req('POST', '/template', body);
  }

  /**
   * AI 陪练：把报告里的「练这一句」变成一段可跟唱的短模板。
   *
   * 后端从 `practice_card.segment` 取参考音，归一化到 0 起点后存成模板；
   * 前端随后用现有 B 模式录一小段，练完回到报告看 before/after。
   */
  practiceSegment(sessionUid, singerId, segmentIndex = 0, kind = 'note') {
    return req('POST', '/practice/segment', {
      session_uid: sessionUid,
      singer_id: singerId || getSingerId(),
      segment_index: Number(segmentIndex) || 0,
      kind: kind || 'note',
    });
  }

  /** AI 人声试听 URL（G43）：服务端按句渲染并缓存，前端直接 `<audio>` 播。 */
  aiSingUrl({ sessionUid, singerId, voice } = {}) {
    const q = new URLSearchParams({
      uid: sessionUid || '',
      singer_id: singerId || getSingerId(),
    });
    if (voice) q.set('voice', voice);
    return `/api/ai-sing?${q.toString()}`;
  }

  /** 我的模板列表（不含音符序列，列表页用不上）。 */
  async listTemplates(singerId) {
    const r = await req('GET', `/template?singer_id=${encodeURIComponent(singerId || '')}`);
    return r.templates || [];
  }

  /**
   * 取某个模板的**音符序列**（画实时参考阶梯要用）。
   *
   * `GET /api/template` 的**列表**故意不含音符（列表页用不上，省流量），
   * 所以这里走单条接口。返回 `[{start_ms, end_ms, pitch_midi}, …]`。
   */
  async getTemplateNotes(templateId) {
    const r = await req('GET', `/template/${encodeURIComponent(templateId)}?notes=true`);
    return Array.isArray(r.notes) ? r.notes : [];
  }

  /**
   * 删除一条模板。
   *
   * ⚠️ 必须带 `singerId`（2026-09-24，审计 §3.2）：服务端现在做**归属校验**，
   *    不带就 400、带错就 403 —— 模板是用户自己唱出来的东西，删错不可恢复。
   *    （这一层不是鉴权，本服务没有账号体系；它挡的是"没带身份的误删"与
   *      "跨演唱者删除"。真正的访问控制要等 G3 多用户。）
   */
  deleteTemplate(templateId, singerId) {
    const q = singerId ? `?singer_id=${encodeURIComponent(singerId)}` : '';
    return req('DELETE', `/template/${encodeURIComponent(templateId)}${q}`);
  }

  /**
   * 给模板改名（2026-10-18）。
   * 只改 title；音符、reference_id、source_session_uid 都不动 —— 模板身份不变。
   * 与 deleteTemplate 一样必须带 singerId：服务端做归属校验。
   */
  renameTemplate(templateId, title, singerId) {
    return req('PATCH', `/template/${encodeURIComponent(templateId)}`,
               { title, singer_id: singerId || '' });
  }

  /** G52：旧歌曲模板重切歌词（只重跑 Whisper，不动音符/分数）。 */
  retryTemplateLyrics(templateId, singerId) {
    return req('POST',
      `/template/${encodeURIComponent(templateId)}/lyrics/retry`,
      { singer_id: singerId || '' });
  }

  finishSession(uid) { return req('POST', `/session/${uid}/finish`); }

  /**
   * 删掉**某一次**练习（2026-10-15）。
   *
   * ⚠️ `singerId` **必填**（服务端会 400）：这是破坏性操作，
   *    必须说清删的是谁的；带错就 403（与 `deleteTemplate` 同一层保护）。
   */
  deleteSession(sessionUid, singerId) {
    const q = `?singer_id=${encodeURIComponent(singerId || '')}`;
    return req('DELETE', `/session/${encodeURIComponent(sessionUid)}${q}`);
  }

  /**
   * 后端返回 {status, progress:{step,total,label}, error}
   * UI 契约要 {status, step, step_total, step_label} —— 在这里摊平。
   */
  async getSessionStatus(uid) {
    const r = await req('GET', `/session/${uid}/status`);
    const p = r.progress || {};
    return {
      status: r.status,
      step: p.step ?? null,
      step_total: p.total ?? null,
      step_label: p.label ?? null,
      error: r.error ?? null,
    };
  }

  /**
   * ★ 带上 `singer_id`（2026-10-01）：让后端校验"这次会话是不是当前这位的"。
   *   不带的话，localStorage 里的演唱者与 sessionStorage 里的会话可能不同步
   *   （换人会清 `sl.sess`，但直接用 `?sess=` 打开、或旧标签页留下的值盖不住），
   *   于是会拿上一个人的会话 uid 去取报告 —— 轻则一条 403/404 日志，
   *   重则**看到别人的报告**。后端因此返回 403 FORBIDDEN。
   */
  getReport(uid) {
    return req('GET', `/session/${uid}/report?singer_id=${encodeURIComponent(getSingerId())}`);
  }

  /**
   * 后端返回 {base, current, delta}；UI 契约要 {base, retry, delta}。
   * 语义相同（current 就是这次复练），只是命名不同。
   */
  async getComparison(uid) {
    const r = await req('GET',
      `/session/${uid}/compare?singer_id=${encodeURIComponent(getSingerId())}`);
    return { base: r.base, retry: r.current, delta: r.delta };
  }

  /** 后端返回 {singer_id, count, sessions:[...]}；UI 契约要数组。 */
  async getHistory(singerId, limit = 20) {
    const r = await req('GET', `/singer/${singerId}/history?limit=${limit}`);
    return (r.sessions || []).map(s => ({
      session_uid: s.session_uid,
      created_at: s.created_at,      // 后端已格式化成 ISO 串（曾经发裸 epoch 浮点）
      total_score: s.total,          // 后端叫 total，UI 契约叫 total_score
      pitch_score: s.pitch,
      rhythm_score: s.rhythm,
      expr_score: s.expr,
      mode: s.mode,
      // ★ 参考身份要带上：成长档案靠它区分"换了首歌"，
      //   否则不同曲目的分数会被当成同一条趋势
      reference_id: s.reference_id ?? null,
      reference_title: s.reference_title ?? null,
      retry_of_uid: s.retry_of_uid ?? null,
    }));
  }

  /**
   * 后端已经给了 labels 与 series（含 expr）。
   * series 里的 null 必须原样保留 —— UI 画折线时要在那里断开，
   * 补 0 会画出"这次唱得很差"的假象。
   */
  async getProgress(singerId) {
    const r = await req('GET', `/singer/${singerId}/progress`);
    return { labels: r.labels || [], times: r.times || [],
             series: r.series || {}, excluded_counts: r.excluded_counts || {} };
  }

  /**
   * 后端返回 {sessions, avg_total, profile:{...}}；
   * UI 契约要 {cluster_label, session_count, summary, chips, metrics}。
   * 后端没做聚类（不属于毕设范围），所以 cluster_label 用规则生成，
   * 并明确标注它不是聚类结果。
   */
  async getProfile(singerId) {
    const r = await req('GET', `/singer/${singerId}/profile`);
    const p = r.profile || {};
    const n = r.sessions || 0;
    if (!n) {
      return { cluster_label: '还没有数据', session_count: 0,
               summary: '练过至少一次之后，这里会显示你的演唱特点。',
               chips: [], metrics: {} };
    }
    const chips = [];
    if (p.style) chips.push(...String(p.style).split('，').filter(Boolean));
    if (p.vibrato_rate) chips.push(`颤音 ${p.vibrato_rate.toFixed(1)} Hz`);
    if (p.onset_style != null) chips.push(p.onset_style > 70 ? '起音偏柔' : '起音干脆');
    return {
      cluster_label: p.style || '还看不出明显类型',
      session_count: n,
      summary: `练了 ${n} 次，中位总分 ${r.avg_total ?? '—'}。` +
               `下面的特点是从这几次里取中位数得到的，样本越多越准。`,
      chips,
      // ★ normalized 直接来自后端（答案就在 `sessions[i].expressiveness.normalized` 里，
      //   是评分器**当时用的值**）。前端不许自己拿原始值再归一化一遍 ——
      //   那就等于同一个公式两份副本，改一处漏一处，且漏掉时不报错、
      //   只是界面横条与报告分数悄悄对不上。
      normalized: r.normalized || {},
      excluded_counts: r.excluded_counts || {},
      metrics: {
        dynamic_range: p.dynamic_range ?? null,
        brightness: p.brightness ?? null,
        vibrato_rate: p.vibrato_rate ?? null,
        onset_style: p.onset_style ?? null,
      },
    };
  }

  /**
   * ⚠️ 后端返回的字段名和 UI 契约**不一样**，必须在这里映射。
   *
   * 踩过的坑：这个方法原来是 `return req('GET','/experiment/summary')` ——
   * 直接把后端响应原样抛给界面。后果不是报错，是**静默显示错的数**：
   *   后端 `stats: {n_singers, n_sessions, n_ratings}`
   *   UI 要 `stats: {singers, samples, raters}`
   * → 第 06 屏四个统计格全变成"undefined 人 / undefined 条"。
   * 而 `weights` 后端是 `[{metric, weight, is_placeholder}]`、UI 要
   * `[{id, label, beta, p, significant}]` → 权重表会渲染出 `[object Object]`。
   * 前端数据层的全部意义就是"把后端的形状翻译成契约的形状"，
   * 少翻一层，界面就安静地骗人。
   */
  async getExperimentSummary() {
    const r = await req('GET', '/experiment/summary');
    const s = r.stats || {};
    const METRIC_LABELS = {
      dynamic_range: '音量起伏', phrase_dynamics: '句内推进', vibrato: '尾音控制',
      rubato: '节奏弹性', onset_style: '起音方式', brightness: '音色明亮度',
    };
    const weights = (r.weights || []).map((w) => ({
      id: w.metric ?? w.id,
      label: METRIC_LABELS[w.metric ?? w.id] || (w.metric ?? w.id),
      beta: w.weight ?? w.beta ?? null,
      // 后端还没做显著性检验（要等真人评分采集完）→ null，UI 显示「—」
      p: w.p ?? null,
      significant: w.significant ?? null,
      placeholder: !!w.is_placeholder,
    }));
    return {
      stats: {
        /* ⚠️ 这三个数现在来自**实验表**（exp_singer / exp_recording / exp_rating），
           不再拿练习会话数冒充（2026-09-24，见 server.py 的说明）。
           `singers` = 实验参与者数、`samples` = 实验录音条数、
           `raters` = **实际打过分的人数**（不是评分条数 —— 原来这里绑错了，
           会出现"3 个人打了 200 条"式的错读）。 */
        singers: s.singers ?? s.n_singers ?? r.n_singers ?? null,
        samples: s.samples ?? s.n_sessions ?? r.n_sessions ?? null,
        raters: s.raters ?? null,
        ratings: s.ratings ?? s.n_ratings ?? null,
        rho: r.rho ?? null,
        r2: r.r2 ?? null,
      },
      scatter: (r.scatter || []).map((p) => (Array.isArray(p)
        ? p : [p.rater_mean ?? p.x ?? null, p.system ?? p.y ?? null])),
      weights,
      r2: r.r2 ?? null,
      rho: r.rho ?? null,
      weight_version: r.weight_version ?? null,
      weights_are_placeholder: !!r.weights_are_placeholder,
      normalization_calibrated: !!r.normalization_calibrated,
      /* ★ 2026-10-18：这两组必须透传。s6 的「已移出评分」与说明靠它们；
         漏传的表现不是报错，而是"两句自相矛盾的话同时留在屏幕上"。 */
      scored_metrics: Array.isArray(r.scored_metrics) ? r.scored_metrics : [],
      dropped_metrics: Array.isArray(r.dropped_metrics) ? r.dropped_metrics : [],
      baselines: r.baselines || [],
      /* ★ 验收指标（2026-09-24 新增）：**必须透传**。
         它是"三条验收指标达标了吗"的唯一数据源 —— 界面不再自己写基准值。
         忘了透传的表现不是报错，而是验收条显示"取不到验收指标"，
         或者更糟：退回静态 HTML 里那句写死的"全部达标"（已经发生过一次）。 */
      benchmarks: Array.isArray(r.benchmarks) ? r.benchmarks : [],
      benchmarks_summary: r.benchmarks_summary ?? null,
      note: r.note || '',
    };
  }

  /* ── 歌曲导入（2026-10-20）─────────────────────────────────
     上传一首歌 → 分离人声/伴奏 → 用原唱当参考 → 跟唱评分。
     三步分开是为了让"选哪一段"在**重活之前**完成（见 server 的 ⑤c）。 */
  async songInspect(file, { title = '', singerId = '' } = {}) {
    const fd = new FormData();
    fd.append('file', file);
    const q = new URLSearchParams({
      singer_id: singerId || getSingerId(),
      title: title || '',
    });
    return req('POST', `/song/inspect?${q.toString()}`, fd);
  }

  async songAnalyze(songId, { startMs = 0, endMs = 0, title = '',
                              quality = 'clean', singerId = '' } = {}) {
    return req('POST', '/song/analyze', {
      song_id: songId, start_ms: startMs, end_ms: endMs,
      title: title || '', quality: quality || 'clean',
      singer_id: singerId || getSingerId(),
    });
  }

  async songStatus(songId, singerId = '') {
    const q = new URLSearchParams({ singer_id: singerId || getSingerId() });
    return req('GET', `/song/${encodeURIComponent(songId)}/status?${q.toString()}`);
  }

  async songLibrary(singerId = '') {
    const q = new URLSearchParams({ singer_id: singerId || getSingerId() });
    return req('GET', `/song/library?${q.toString()}`);
  }

  async songDelete(songId, singerId = '') {
    const q = new URLSearchParams({ singer_id: singerId || getSingerId() });
    return req('DELETE', `/song/${encodeURIComponent(songId)}?${q.toString()}`);
  }

  async songConfirm(songId, { title = '', force = false, lyricsText = '',
                              singerId = '' } = {}) {
    return req('POST', `/song/${encodeURIComponent(songId)}/confirm`, {
      title: title || '', force: !!force, lyrics_text: lyricsText || '',
      singer_id: singerId || getSingerId(),
    });
  }

  songPreviewUrl(songId, kind = 'instrumental') {
    const q = new URLSearchParams({
      kind, singer_id: getSingerId(),
    });
    return `/api/song/${encodeURIComponent(songId)}/preview.wav?${q.toString()}`;
  }

  /**
   * 上传一段已有录音（模式 C）。
   *
   * ★ 必须带 `singer_id`（2026-10-01 修）。原来只 append 了 file，
   *   于是服务端用它自己的默认值 `singer_id="singer_upload"` ——
   *   **上传的练习记在了一个占位演唱者名下**，用户回到成长档案里找不到自己刚传的那条，
   *   而库里却多出一个叫 `singer_upload` 的"人"（实测库里已经攒了 44 条）。
   *   这个洞一直存在，只是以前没人从界面上传过（模式 C 的入口是死的），
   *   所以直到把入口接通、并在服务端加了归属校验之后才暴露出来 ——
   *   表现是"报告页说这条记录属于别人"。
   */
  async uploadRecording(file, { referenceId = '', templateId = '' } = {}) {
    const fd = new FormData();
    fd.append('file', file);
    const q = new URLSearchParams({ singer_id: getSingerId() });
    /* ★ 必须带上"跟谁比"（2026-10-01）。
       不带的话服务端建成**自比**会话 —— 音准/节奏按契约降级成 null，
       用户上传一整首歌却拿不到音准分。`/api/upload` 一直支持这两个参数，
       只是前端从来没传过。 */
    if (referenceId) q.set('reference_id', referenceId);
    else if (templateId) q.set('template_id', templateId);
    return req('POST', `/upload?${q.toString()}`, fd);
  }

  /* ── ★ MIDI 导入（2026-10-10，第四条路）────────────────────────
     为什么要有这一路：作者要的是「**唱我喜欢的歌，并知道我走没走音**」。
     "从混音里提旋律"已证走不通（§92/§93：两首歌、13 个片段、4 种分离方法全败，
     根因是 PYIN 的单声源假设）。而 **MIDI 是符号数据** ——
     旋律是**读出来的**，不是猜的 ⇒ 它**能判走音**。

     这里只做传输，三点语义上的注意：

     ⚠️ ① `upload` 与 `track` 返回**同一个形状**（后端由 `_midi_payload()` 统一），
           所以界面用一份渲染代码就够 —— 不要把这两个当成两种东西。
     ⚠️ ② `preview_url` 现在是**同源相对路径**（`/exports/xxx.wav`），
           不是 `http://127.0.0.1:8010/...`。见 `web/server.py` 的说明。
     ⚠️ ③ `midiConfirm` 之后拿到的是 **模板**，不是会话 ——
           确认完要回到模式 B 的练习路径（选这个模板 → 开唱）。
           所以这个方法的返回值里**没有** `session_uid`，别去找。 */

  /** 上传 .mid：拿回轨清单 + 推荐 + 试听。**不落参考**（用户确认才落）。 */
  midiUpload(file) {
    const fd = new FormData();
    fd.append('file', file);
    return req('POST', '/midi/upload', fd);
  }

  /** 换一轨重新解析（用户改选）。服务端会**重新合成试听**。 */
  midiTrack(fileId, trackIndex) {
    return req('GET', `/midi/track/${encodeURIComponent(fileId)}` +
                      `?track_index=${encodeURIComponent(String(trackIndex))}`);
  }

  /**
   * 确认某一轨 → 存成**跟唱模板**。
   *
   * `title` 让用户能认出来是哪首歌：模板列表里会攒很多条，
   * 默认的「MIDI 导入 · 轨 2」在"我想练《不将就》"的场景下等于没名字。
   */
  midiConfirm({ fileId, trackIndex, title = '', singerId = '' }) {
    return req('POST', '/midi/confirm', {
      file_id: fileId,
      track_index: Number(trackIndex),
      title: String(title || '').trim(),
      singer_id: singerId || getSingerId(),
    });
  }

  getConfig() { return req('GET', '/config'); }

  putConfig(patch) { return req('PUT', '/config', patch); }

  // ── ★ LLM API key 管理（2026-10-07）──
  //   为什么要这四条：原来 key 只能靠环境变量或手写文件，
  //   用户在**界面上配不了** —— 而"答辩现场用真 LLM"就必须能当场配。
  //
  //   ⚠️ 后端**永不回显 key**：`getLlmKey` 只返回
  //      `{configured, source, hint, base, model, enabled}`；
  //      `hint` 是 `sk-…a1b2`（前 3 + 后 4），够辨认、不足以复原。
  //   ⚠️ `saveLlmKey` 会**先真调一次**验证 —— 不能用就直接 400、**不落盘**。
  //      所以"保存成功" == "真的能用"，不是"格式看着对"。
  //      （格式对但欠费/被撤销的 key 到处都是，不验的话答辩现场才发现。）
  getLlmKey() { return req('GET', '/llm/key'); }

  saveLlmKey(key, enable = true) {
    return req('POST', '/llm/key', { key, enable });
  }

  testLlmKey(key) { return req('POST', '/llm/key/test', key ? { key } : {}); }

  clearLlmKey() { return req('DELETE', '/llm/key'); }

  /**
   * 清空所有录音与记录（不可恢复）。
   * 后端要求显式 `confirm`，缺了会 400 —— 这是防误触，不是多余的门槛。
   */
  purgeData({ scope = 'all', singerId = null } = {}) {
    const body = { confirm: true, scope };
    if (scope === 'singer' && singerId) body.singer_id = singerId;
    return req('POST', '/privacy/purge', body);
  }

  /**
   * WebSocket 实时流。消息格式见契约 §3.3。
   * pitch 帧 ~20 Hz、metrics 帧 1 Hz。
   *
   * ⚠️ 帧率由**客户端发块的大小**控制：46ms 的块 → 21.7Hz，93ms 的块 → 10.8Hz。
   *    服务端每收到一块就回一帧。想要 20Hz 就必须发 ≤50ms 的块。
   */
  openStream(sessionUid, onMessage) {
    let ws = null;
    let stopped = false;        // 客户端主动 stop 过 ⇒ 这次 close 是**预期内**的
    let errReported = false;    // onerror 与 onclose 会**连着**触发，别报两遍
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return {
      start() {
        ws = new WebSocket(`${proto}://${location.host}${BASE}/session/${sessionUid}/stream`);
        ws.binaryType = 'arraybuffer';
        ws.onopen = () => ws.send(JSON.stringify({ type: 'start', session_uid: sessionUid, sample_rate: 22050 }));
        ws.onmessage = (ev) => {
          if (typeof ev.data === 'string') {
            try { onMessage(JSON.parse(ev.data)); }
            catch { /* 非 JSON 文本帧，忽略 */ }
          }
        };
        ws.onerror = () => {
          if (errReported) return;
          errReported = true;
          onMessage({ type: 'error', data: { code: 'WS_ERROR', message: '实时连接中断' } });
        };
        /* ★★ 2026-10-17 修：原来这里**无条件**报 `done`。
              ⇒ 网络一抖，`onclose` 就把"断了"报成"唱完了" ——
                这一遍的实时数据其实是断的，而界面显示正常结束。
                那是本项目最忌的一类错：**失败报成成功**（与 §54.4 同一条纪律）。

           ⇒ 现在分流：
              · 客户端 `stop()` 过（或 `close()` 主动关，那条已经把 onclose 置 null）
                ⇒ 是预期内的结束 ⇒ 报 `done`
              · 没 stop 就断了 ⇒ 报 `WS_DROPPED`，**并说明这一遍不完整、建议重唱**

           ⚠️ **不做自动重连**（这是刻意的，不是省事）：
              实时管线靠"客户端持续发音频块"，连接断了**音频流就断了**，
              重连只能接回一个空洞 —— 重连反而会让用户以为这一遍还连着。
              对"录一遍"这件事，**如实说断、让他重唱**才是对的行为。
              真要续传，得先把"已发到哪一块"记在服务端，那是另一件事。 */
        ws.onclose = () => {
          if (stopped) {
            onMessage({ type: 'done',
                        data: { session_uid: sessionUid, status: SessionStatus.RECORDING } });
          } else if (!errReported) {
            errReported = true;
            onMessage({ type: 'error', data: {
              code: 'WS_DROPPED',
              message: '实时连接断了（网络或服务端）。**这一遍的数据不完整，建议重唱。**',
            } });
          }
        };
      },
      sendAudio(buf) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(buf); },
      stop() {
        stopped = true;         // ← 先标记，再发消息：即便 send 时连接已断，也算"主动结束"
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' }));
      },
      close() { if (ws) { ws.onclose = null; ws.close(); ws = null; } },
    };
  }
}

export { ApiError };
