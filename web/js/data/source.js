/**
 * dataSource —— 前端与后端之间唯一的接缝。
 *
 * ★ 这是整个前端骨架最关键的抽象 ★
 *
 * mockSource 和 apiSource 实现完全相同的接口。接后端时只换实现，
 * UI 代码一行都不用动。这就是"前端骨架不会和后端对不上"的技术保证。
 *
 * 所有方法的返回值必须严格符合 docs/接口契约.md：
 *   · 字段名、类型、可空性一个不差
 *   · null 表示"本次未评估"，绝不用 0 代替
 *   · 所有时间戳是毫秒整数，相对本次录音起点
 *   · 所有分数是 0-100 浮点，保留 1 位小数
 *
 * @typedef {Object} DataSource
 */

// ────────────────────────────────────────────────────────────
// 枚举（与契约、DB 的 CHECK 约束三边一致）
// ────────────────────────────────────────────────────────────
export const Mode = Object.freeze({ A: 'A', B: 'B', C: 'C' });

export const SessionStatus = Object.freeze({
  CREATED: 'CREATED',
  RECORDING: 'RECORDING',
  PRECISE_PENDING: 'PRECISE_PENDING',
  SCORED: 'SCORED',
  FAILED: 'FAILED',
});

export const ProblemKind = Object.freeze({
  PITCH: 'PITCH', RHYTHM: 'RHYTHM', DYNAMIC: 'DYNAMIC',
});

export const ErrorCode = Object.freeze({
  MIC_DENIED: 'MIC_DENIED',
  TOO_NOISY: 'TOO_NOISY',
  NO_VOICE: 'NO_VOICE',
  TOO_SHORT: 'TOO_SHORT',
  RANGE_SATURATED: 'RANGE_SATURATED',
  REF_MISSING: 'REF_MISSING',
  /* 2026-10-18：精算还没完成（HTTP 409）。**不是**"记录不存在" ——
     s3 必须轮询，不能清 sl.sess。 */
  CONFLICT: 'CONFLICT',
  /* 两遍练的不是同一首，不能直接比。 */
  NOT_COMPARABLE: 'NOT_COMPARABLE',
});

/** 六个表现力指标的规范 id（全项目统一，UI/DB/后端都用这套命名） */
export const METRIC_IDS = Object.freeze([
  'dynamic_range',
  'phrase_dynamics',
  'rubato',
  'vibrato',
  'onset_style',
  'brightness',
]);

/**
 * DataSource 接口定义。
 * 这里不写实现，只写"必须有哪些方法、每个方法返回什么"。
 * mockSource 与 apiSource 都必须满足它。
 */
export class DataSource {
  /** @returns {Promise<{singers: Array, references: Array, config: Object}>} */
  async bootstrap() { throw new Error('not implemented'); }

  /* ── 演唱者名册（G3 多用户）────────────────────────────────
     ★ 这四个方法不是"顺手加的功能"，是**"屏幕上这份数据是谁的"的答案**。
       加它们之前，前端只有 `bootstrap()` 里写死的一个演唱者，
       两个人的记录会串在一起，而界面上没有任何入口能看出来或改回去。

     ⚠️ 四个方法在 mock 源里也必须有 —— 缺一个的表现不是报错，
        而是 `?src=mock` 下那张卡整块空着，而"空着"和"没接线"长得一样
        （这个坑 s2 已经踩过一次，见 mock.js 里 listTemplates 的注释）。 */
  /** 名册 + 每人练习次数 @returns {Promise<Array<{singer_id,name,sessions,templates}>>} */
  async listSingers() { throw new Error('not implemented'); }

  /** 新建或改名（同一个端点：给一个 singer_id 配一个名字） */
  async createSinger({ name, singerId }) { throw new Error('not implemented'); }

  /** 删除一位演唱者（练习记录 + 模板；**录音文件不删**，返回里会说明） */
  async deleteSinger(singerId) { throw new Error('not implemented'); }

  /** 开一次练习会话 @returns {Promise<{session_uid: string, status: string, reference: Object}>} */
  async createSession({ singerId, mode, referenceId }) { throw new Error('not implemented'); }

  /** 结束录音，触发精算 @returns {Promise<{session_uid: string, status: string}>} */
  async finishSession(sessionUid) { throw new Error('not implemented'); }

  /** 查精算进度 @returns {Promise<{status: string, step: number, step_total: number, step_label: string}>} */
  async getSessionStatus(sessionUid) { throw new Error('not implemented'); }

  /** 取完整报告 @returns {Promise<ScoreReport>} */
  async getReport(sessionUid) { throw new Error('not implemented'); }

  /** 复练对比 @returns {Promise<{base: ScoreReport, retry: ScoreReport, delta: Object}>} */
  async getComparison(sessionUid) { throw new Error('not implemented'); }

  /** 历史列表 @returns {Promise<Array<{session_uid, created_at, total_score, reference_title, retry_of_uid}>>} */
  async getHistory(singerId, limit = 20) { throw new Error('not implemented'); }

  /**
   * 删掉**某一次**练习（2026-10-15，审计发现缺这个）。
   *
   * 为什么需要：原来只有「清空全部」与「按演唱者全清」——
   * 用户攒了几十次记录之后，想删掉唱砸的那一次都做不到。
   * @returns {Promise<{deleted: boolean, templates_deleted: number, audio_files_deleted: number}>}
   */
  async deleteSession(sessionUid, singerId) { throw new Error('not implemented'); }

  /**
   * 进步曲线。series 里的值允许 null（那次没有该项数据），UI 必须断线而不是补 0。
   * @returns {Promise<{labels: string[], series: {total: number[], expr: number[], pitch: number[]}}>}
   */
  async getProgress(singerId) { throw new Error('not implemented'); }

  /** 风格画像 @returns {Promise<{cluster_label, session_count, summary, chips: string[], metrics: Object}>} */
  async getProfile(singerId) { throw new Error('not implemented'); }

  /** 实验数据（答辩用） @returns {Promise<{stats: Object, scatter: Array, weights: Array, r2: number, rho: number}>} */
  async getExperimentSummary() { throw new Error('not implemented'); }

  /** 上传已有录音（模式 C） @returns {Promise<{session_uid: string}>} */
  async uploadRecording(file) { throw new Error('not implemented'); }

  /* ── ★ 歌曲导入（模式 E，2026-10-20）──────────────────────────
     上传一首歌 → 后台分离人声/伴奏 → 原唱当参考 → 用户确认 →
     存成带伴奏的模板（之后完全复用模式 B 的录音/评分/陪练）。
     四步分开：挑选段落在**重活之前**完成；分离要几十秒，不能同步 HTTP。 */
  /** 上传并读取时长；返回 {song_id,title,duration_ms,suggested,limits} */
  async songInspect(file, opts = {}) { throw new Error('not implemented'); }
  /** 起后台分析；返回 job 状态 */
  async songAnalyze(songId, opts = {}) { throw new Error('not implemented'); }
  /** 轮询 {status,stage,progress,quality,preview,...} */
  async songStatus(songId, singerId = '') { throw new Error('not implemented'); }
  /** 「我上传过的歌」：已确认模板 + 未确认分析记录 */
  async songLibrary(singerId = '') { throw new Error('not implemented'); }
  /** 删掉未确认的上传分析 */
  async songDelete(songId, singerId = '') { throw new Error('not implemented'); }
  /** 确认质量 → 存成模板；返回 {template_id,title,quality} */
  async songConfirm(songId, opts = {}) { throw new Error('not implemented'); }
  /** 试听 URL：kind = vocal | instrumental | guide */
  songPreviewUrl(songId, kind = 'instrumental') { throw new Error('not implemented'); }

  /* ── ★ MIDI 导入（2026-10-10，第四条路）────────────────────────
     为什么这三条要写进**基类**：`DataSource` 的每个方法就是 UI 唯一的依赖面。
     不写的话，某个源上缺一个方法，调用时报的是
     `source.midiUpload is not a function` —— 那读起来像"功能坏了"，
     而实际是"这一路在这个源上根本没接"。
     写在这里，两个实现就都必须**显式**给出行为（实现，或显式抛错）。

     三条的分工（对应后端的三个端点，见 `web/server.py`）：
       midiUpload  —— 上传 .mid，拿回**轨清单 + 推荐 + 试听**
       midiTrack   —— 换一轨重新解析（**重新出试听**）
       midiConfirm —— 用户确认后存成**跟唱模板**（模式 B）

     ⚠️ `midiConfirm` 返回的是 `template_id`，不是会话 ——
        确认之后走的是既有的模式 B 练习路径（对齐/评分/教练一行都不用改）。 */

  /** 上传 MIDI（只解析，不落参考） @returns {Promise<Object>} */
  async midiUpload(file) { throw new Error('not implemented'); }

  /** 换一轨重新解析 @returns {Promise<Object>} */
  async midiTrack(fileId, trackIndex) { throw new Error('not implemented'); }

  /** 确认某一轨 → 存成模板 @returns {Promise<{template_id: string}>} */
  async midiConfirm({ fileId, trackIndex, title, singerId }) {
    void fileId; void trackIndex; void title; void singerId;
    throw new Error('not implemented');
  }

  /** 读设置 @returns {Promise<Object>} */
  async getConfig() { throw new Error('not implemented'); }

  /** 写设置 @returns {Promise<Object>} */
  async putConfig(patch) { throw new Error('not implemented'); }

  /**
   * 实时流。返回一个可关闭的句柄。
   * @param {string} sessionUid
   * @param {(msg: {type: 'pitch'|'metrics'|'done'|'error', data: Object}) => void} onMessage
   * @returns {{sendAudio: (buf: ArrayBuffer) => void, start: () => void, stop: () => void, close: () => void}}
   */
  openStream(sessionUid, onMessage) { throw new Error('not implemented'); }
}

/**
 * 统一响应包解包。契约 §3.2：{"ok": true, "data": …} / {"ok": false, "error": {…}}
 */
export class ApiError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.detail = detail;
  }
}

export async function unwrap(resp) {
  let body;
  try {
    body = await resp.json();
  } catch {
    throw new ApiError('BAD_RESPONSE', `服务端返回的不是 JSON（HTTP ${resp.status}）`);
  }
  if (!body.ok) {
    const e = body.error || {};
    throw new ApiError(e.code || 'UNKNOWN', e.message || '未知错误', e);
  }
  return body.data;
}
