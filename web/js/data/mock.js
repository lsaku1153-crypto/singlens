/**
 * mockSource —— 契约的"可执行版本"。
 *
 * 它产出的每一个字段都必须与 docs/接口契约.md 一字不差。
 * 骨架阶段 UI 全靠它渲染；后端做好后换成 apiSource，UI 不动一行。
 *
 * ⚠️ 这里的数据是【假】的，但【形状】是真的。造假数据时唯一允许偷懒的是数值，
 *    绝不允许偷懒字段名、类型和可空性。
 *
 * 特意包含的边界情况（用来验证 UI 的降级路径）：
 *   · MOCK_REPORT_NO_VIBRATO —— metrics_excluded 非空、vibrato 相关字段为 null
 *   · MOCK_REPORT_RETRY      —— 带 retry_of_uid，用于复练对比
 *   · evidence 缺失的情况     —— evidence 为 null 时 UI 不显示"展开看证据"
 */
import { DataSource, SessionStatus, Mode, ProblemKind } from './source.js';

// ── 确定性伪随机（保证每次刷新截图一致）────────────────────
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}
const r1 = (x) => Math.round(x * 10) / 10;

// ── 曲目 ──────────────────────────────────────────────────
const REF_TWINKLE = {
  reference_id: 'REF_TWINKLE_1',
  mode: 'A',
  title: '小星星 · 第一句',
  note_count: 7,
  notes_midi: [60, 60, 67, 67, 69, 69, 67],
  pitch_low_midi: 60,
  pitch_high_midi: 69,
  duration_ms: 3600,
};

// ── NoteSegment 生成（严格符合契约 §2.1）──────────────────
function makeSegments(seed, { excludeVibrato = false } = {}) {
  const rand = rng(seed);
  return REF_TWINKLE.notes_midi.map((ref, i) => {
    const dev = (rand() - 0.45) * 60;            // 音分偏差，个别音符 > 25
    const onsetDev = (rand() - 0.5) * 120;       // 起始偏差 ms
    const dur = 420 + Math.floor(rand() * 120);
    const isLong = dur > 500;                    // 只有长音才可能测到颤音
    return {
      note_index: i,
      ref_note_index: i,
      start_ms: i * 500,
      end_ms: i * 500 + dur,
      pitch_midi: r1(ref + dev / 100),
      pitch_cents_dev: r1(dev),
      onset_dev_ms: r1(onsetDev),
      rms_mean: r1((0.16 + rand() * 0.05) * 1000) / 1000,
      rms_peak: r1((0.22 + rand() * 0.06) * 1000) / 1000,
      onset_rise_ms: r1(30 + rand() * 45),
      vibrato_rate: (!excludeVibrato && isLong) ? r1(4.8 + rand() * 1.8) : null,
      vibrato_depth: (!excludeVibrato && isLong) ? r1((0.45 + rand() * 0.35) * 100) / 100 : null,
      centroid_mean: r1(1150 + rand() * 380),
      jump_suppressed: rand() > 0.85,
      align_method: 'DTW',
    };
  });
}

// ── ProblemSegment（严格符合契约 §2.4，含后端聚合规则）────
function makeProblems(segments) {
  const out = [];
  // 规则：连续 ≥2 个音符 |cents_dev| > 25 → PITCH
  let run = [];
  segments.forEach((s, i) => {
    if (Math.abs(s.pitch_cents_dev) > 25) run.push(i);
    else { if (run.length >= 2) out.push(pitchProblem(segments, run)); run = []; }
  });
  if (run.length >= 2) out.push(pitchProblem(segments, run));
  // 规则：phrase_dynamics 偏低 → DYNAMIC（这里直接给一条演示用）
  out.push({
    kind: ProblemKind.DYNAMIC,
    start_ms: 22000, end_ms: 31500,
    note_range: [12, 19],
    severity: 'MEDIUM',
    headline: '副歌整段「满天都是小星星」',
    detail: '这段你全程用一样大的声音唱完了，而前面主歌你还唱出了起伏 —— 说明副歌你没放开。',
  });
  const order = { HIGH: 0, MEDIUM: 1 };
  return out.sort((a, b) => order[a.severity] - order[b.severity]).slice(0, 3); // 最多 3 条
}

function pitchProblem(segments, idxs) {
  const segs = idxs.map((i) => segments[i]);
  const avg = r1(segs.reduce((a, s) => a + s.pitch_cents_dev, 0) / segs.length);
  return {
    kind: ProblemKind.PITCH,
    start_ms: segs[0].start_ms,
    end_ms: segs[segs.length - 1].end_ms,
    note_range: [idxs[0], idxs[idxs.length - 1]],
    severity: Math.abs(avg) > 35 ? 'HIGH' : 'MEDIUM',
    headline: `第 ${idxs[0] + 1}–${idxs[idxs.length - 1] + 1} 个音`,
    detail: `这几个音整体${avg < 0 ? '低' : '高'}了约 ${Math.abs(avg).toFixed(0)} 音分。`
          + '不是你不会唱 —— 这几个音刚好卡在你换声的地方，换个呼吸位置再试试。',
  };
}

// ── Evidence（严格符合契约 §4.1，下采样 ≤120 点）─────────
function makeEvidence(seed) {
  const rand = rng(seed);
  const N = 100;
  const rmsT = [], rmsV = [];
  for (let i = 0; i < N; i++) {
    rmsT.push(Math.round(i * 360));
    rmsV.push(r1(0.44 + 0.05 * Math.sin(i * 0.19) + 0.022 * Math.sin(i * 0.71) + rand() * 0.01));
  }
  const f = [], v = [];
  for (let i = 0; i < 60; i++) {
    const hz = i / 60 * 14;
    f.push(r1(hz));
    v.push(r1(Math.exp(-Math.pow((hz - 5.7) / 0.85, 2)) * 0.82 + 0.02 + rand() * 0.015));
  }
  const oT = [], oDev = [], oTrend = [], oRes = [];
  for (let i = 0; i < 48; i++) {
    const trend = Math.sin(i / 48 * Math.PI * 2) * 7 + i * 0.28;
    const res = Math.sin(i * 4.7) * 2.6;
    oT.push(i * 500);
    oDev.push(r1(trend + res));
    oTrend.push(r1(trend));
    oRes.push(r1(res));
  }
  return {
    rms_envelope: { t: rmsT, v: rmsV, p95: 0.93, p5: 0.46, unit: 'dB' },
    vibrato_spectrum: { f, v, peak_hz: 5.7, unit: 'Hz' },
    onset_dev_trend: { t: oT, dev: oDev, trend: oTrend, residual: oRes, unit: 'ms' },
  };
}

// ── 报告 ──────────────────────────────────────────────────
function makeReport(seed, { excludeVibrato = false, retryOf = null, uid = null } = {}) {
  const rand = rng(seed);
  const segments = makeSegments(seed, { excludeVibrato });
  const dr = r1(6 + rand() * 8);
  const exprScore = r1(45 + rand() * 30);
  const excluded = excludeVibrato ? ['vibrato'] : [];
  const normalized = {
    dynamic_range: r1((dr - 4) / 10 * 100) / 100,
    phrase_dynamics: r1((50 + rand() * 35)) / 100,
    rubato: r1((60 + rand() * 30)) / 100,
    vibrato: excludeVibrato ? null : r1((70 + rand() * 25)) / 100,
    onset_style: r1((45 + rand() * 35)) / 100,
    brightness: r1((65 + rand() * 30)) / 100,
  };
  return {
    session_uid: uid || `sess_mock_${seed}`,
    retry_of_uid: retryOf,
    pitch: {
      score: r1(78 + rand() * 14),
      accuracy: r1((rand() - 0.5) * 20),
      stability: r1(8 + rand() * 12),
    },
    rhythm: {
      score: r1(84 + rand() * 12),
      accuracy: r1((rand() - 0.5) * 22),
      stability: r1(14 + rand() * 16),
      rubato: r1(12 + rand() * 14),
    },
    expressiveness: {
      dynamic_range: dr,
      phrase_dynamics: r1(4 + rand() * 6),
      rubato: r1(12 + rand() * 14),
      vibrato_rate: excludeVibrato ? null : r1(4.8 + rand() * 1.8),
      vibrato_depth: excludeVibrato ? null : r1((0.45 + rand() * 0.35) * 100) / 100,
      onset_style: r1(30 + rand() * 45),
      brightness: r1(1150 + rand() * 380),
      score: exprScore,
      weights: {
        dynamic_range: 0.34, phrase_dynamics: 0.27, rubato: 0.14,
        vibrato: excludeVibrato ? 0 : 0.19, onset_style: 0.09, brightness: 0.05,
      },
      normalized,
    },
    total: r1(68 + rand() * 16),
    weight_version: 'RIDGE_20260912',
    metrics_excluded: excluded,
    segments,
    problems: makeProblems(segments),
    /* ⚠️ 这段总评**必须与 v7 原型的冻结文案不一样**（2026-09-24 改）。
       原来它是逐字照抄原型那四段（"22 秒到 31 秒那段副歌…"）——
       于是探针没法区分两种情况：
         · 接线坏了、原型文案还留在页面上（**要报错**）
         · mock 数据源正常把假数据填进去了（**正常**）
       两者文本一模一样时，判据只能取其一。现在的做法：
         · 正文里明写"这是示例数据"，用户在界面上一眼能看出自己不在真实模式；
         · 措辞与原型不同，于是"原型文案还在"这种情况**能被探针抓到**。 */
    comments: '（示例数据，不是你的成绩 —— 当前是 ?src=mock 模式）\n'
            + '这一遍的音准和拍子都还行，问题主要在力度：整首几乎用同一个音量唱完。\n'
            + '下一遍只改这一件事：挑一句，前半句轻着唱、后半句放开，先做出对比来。',
    comment_source: 'deepseek-chat',
    comments_by_metric: {
      dynamic_range: {
        level: dr < 6 ? '太 平 了' : dr < 14 ? '还 行' : '有 起 伏',
        plain: '上面这条弯曲的线就是你的音量变化（越往上声音越大）。'
             + '两条虚线之间就是你这首歌用到的音量范围 —— 太窄了。整首歌基本是同一个大小声唱下来的。',
        action: '挑一句你最喜欢的，前半句小声哼、后半句放开唱。'
              + '就像讲故事讲到关键处会不自觉提高声音那样。先夸张一点都没关系，先做出对比来。',
      },
      phrase_dynamics: {
        level: '还 行',
        plain: '你在每一句内部是有一些起伏的，但幅度不大，句子听起来比较平。',
        action: '把每一句当成一句话来说：句头轻轻起、句中最用力、句尾收回来。先只改一句，唱熟了再推广到整首。',
      },
      rubato: {
        level: '很 好',
        plain: '你有几个地方稍微拖长了一点点。这不算唱错。这是你在跟着感觉走，听起来反而更自然、更像人唱的。'
             + '系统不会因为这种"故意拖一下"扣你的分。',
        action: '不用改。唯一要注意的是别拖太久 —— 拖到下一句都开始了，就真的变抢拍拖拍了。目前你的幅度很安全。',
      },
      vibrato: excludeVibrato
        ? { level: '本次未评估', plain: null, action: null }
        : {
            level: '很 好',
            plain: '你唱长音时声音有很自然的轻微波动 —— 大概每秒抖 5 到 6 次。这是好事。'
                 + '完全不抖的声音听起来会很僵硬、很像机器，专业歌手都是这样轻轻抖的。',
            action: '保持就好。这一项不用改。它已经是你声音里的一个优点了。',
          },
      onset_style: {
        level: '偏 柔',
        plain: '你每个字都是慢慢"滑"出来的，没有一下子蹦出来。所以听起来很温柔 —— 唱抒情歌特别合适。',
        action: '想唱有力量的歌（摇滚、励志那类），试试把每个字的开头唱得更干脆，'
              + '像喊人名字那样直接出口，不要慢慢滑进去。',
      },
      brightness: {
        level: '温 暖 厚 实',
        plain: '你的声音偏温暖、厚实，高音不太亮 —— 这是音色特点，不是缺点。这种声音唱抒情慢歌、民谣很有优势。',
        action: '想唱得更清亮，唱的时候把嘴型撑开一点、笑肌抬起来，声音会自然变亮。不用刻意用力。',
      },
    },
    evidence: makeEvidence(seed),
    meta: {
      mode: 'A',
      duration_ms: 36000,
      rt_latency_ms_p50: 118,
      rt_latency_ms_p95: 186,
      precise_cost_ms: 2330,
      created_at: '2026-09-14T15:30:12',
    },
    practice_plan: {
      kind: 'pitch_segment',
      focus: '第 8–9 个音这一处低了',
      why: '这两个音整体比参考低了约 40 音分。',
      steps: ['先听示范，把这一小段的音在心里唱一遍', '再跟着示范哼一遍', '最后只唱第 8–9 个音'],
      done: '这一小段的音分偏差从 40 降到 25 以内。',
      facts: [{ k: '这一处的偏差', v: 40, unit: '音分' }],
      demo: { kind: 'reference_phrase', notes: [{ midi: 67 }, { midi: 69 }] },
    },
    practice_card: {
      available: true,
      kind: 'pitch_segment',
      label: '第 8–9 个音',
      focus: '第 8–9 个音这一处低了',
      why: '这两个音整体比参考低了约 40 音分。',
      action: '先听示范，把这一小段的音在心里唱一遍',
      steps: ['先听示范，把这一小段的音在心里唱一遍', '再跟着示范哼一遍'],
      done: '这一小段的音分偏差从 40 降到 25 以内。',
      facts: [{ k: '这一处的偏差', v: 40, unit: '音分' }],
      segment: {
        from: 7, to: 8, start_ms: 3000, end_ms: 4000,
        notes: [{ start_ms: 3000, end_ms: 3500, pitch_midi: 67 },
                { start_ms: 3500, end_ms: 4000, pitch_midi: 69 }],
      },
      before: { notes: 2, pitch_median_abs: 40, pitch_hit_25: 0,
                pitch_hit_50: 100, onset_median: 20 },
      success: { metric: 'pitch_median_abs', op: 'lte', target: 25,
                 unit: '音分', label: '偏差降到 25 音分内' },
    },
  };
}

// ── 预置数据 ──────────────────────────────────────────────
const REPORT_MAIN = makeReport(20260914, { uid: 'sess_mock_main' });
const REPORT_RETRY = makeReport(20260913, { uid: 'sess_mock_retry', retryOf: 'sess_mock_main' });
const REPORT_NO_VIBRATO = makeReport(20260912, { excludeVibrato: true, uid: 'sess_mock_novib' });

/* ⚠️ 原来这里有两块写死的 `HISTORY` / `PROGRESS`，现在**删掉了**（G3，2026-10-01）。
   它们本来是"所有人的历史"，而 G3 之后历史/进步曲线**必须按演唱者分开** ——
   留着这两块的话，第 05 屏仍然会显示同一份数据，于是"换人生效了吗"
   在 mock 下无法验证（"没生效"与"数据恰好一样"长得一模一样）。
   取而代之的是下面 `#dataFor(sid)`：每个人一套**彼此不同**的假数据。
   （原来那份 10 点的曲线里刻意留了一个 null 用来验证"断线而不是补 0"，
     这个性质保留在 `#dataFor` 里：expr 的第 2 个点是 null。） */

// ── 实现 ──────────────────────────────────────────────────
/* ── 演唱者名册（G3，2026-10-01）────────────────────────────
   ★ 为什么 mock 里也要有"两个人"：这是**唯一能在无后端时验证换人是否生效**的
     办法。如果 mock 里也只有一个人，"换人之后第 05 屏变了吗"就无从判断 ——
     而"没变"和"切换根本没接线"在界面上长得一模一样（s2 踩过同一个坑）。
     ⚠️ 名册可变（新建/改名/删除都作用在它上面），并且 `sessions` 会跟着
        `_historyFor()` 的长度走 —— 写死次数的话，新建一个人会显示
        "练了 12 次"，那是**编出来的使用量**。 */
const SINGER_SEED = [
  { singer_id: 'S01', name: '我（S01）' },
  { singer_id: 'S02', name: '同桌' },
];

export class MockSource extends DataSource {
  constructor() {
    super();
    this.latency = 120; // 模拟网络延迟，让 loading 状态可见
    this._singers = SINGER_SEED.map((s) => ({ ...s, templates: 0 }));
  }

  #wait(ms = this.latency) { return new Promise((r) => setTimeout(r, ms)); }

  async bootstrap() {
    await this.#wait();
    return {
      singers: await this.listSingers(),
      references: [REF_TWINKLE, { ...REF_TWINKLE, reference_id: 'REF_ODE_1', title: '欢乐颂 · 第一句' }],
      config: await this.getConfig(),
    };
  }

  /* ── 演唱者名册（G3）——形状与 `api.js` 一字不差 ──────────── */
  async listSingers() {
    await this.#wait(60);
    return this._singers.map((s) => ({
      singer_id: s.singer_id, name: s.name,
      sessions: this.#historyFor(s.singer_id).length,
      templates: (this._templates || []).length,     // mock 的模板不按人分（简化）
      created_at: '2026-09-01T09:00:00',
    }));
  }

  async createSinger({ name, singerId }) {
    await this.#wait(120);
    const nm = String(name || '').trim();
    if (!nm) throw new Error('请给这位演唱者起个名字。');
    if (this._singers.some((s) => s.name === nm && s.singer_id !== singerId)) {
      throw new Error(`已经有一位演唱者叫「${nm}」了。`);
    }
    const sid = singerId || `u_${Math.random().toString(16).slice(2, 10)}`;
    const hit = this._singers.find((s) => s.singer_id === sid);
    const renamedFrom = hit ? hit.name : null;
    if (hit) hit.name = nm;
    else this._singers.push({ singer_id: sid, name: nm, templates: 0 });
    return { created: !hit, renamed_from: renamedFrom,
             singer: { singer_id: sid, name: nm } };
  }

  async deleteSinger(singerId) {
    await this.#wait(120);
    const i = this._singers.findIndex((s) => s.singer_id === singerId);
    if (i < 0) throw new Error(`没有演唱者 ${singerId}。`);
    if (this._singers.length <= 1) throw new Error('这是最后一位演唱者。');
    const gone = this._singers[i];
    const n = this.#historyFor(singerId).length;
    this._singers.splice(i, 1);
    if (this._bySinger) delete this._bySinger[singerId];
    // 形状与后端一致（含"录音文件没删"的如实说明）——
    // 少了这两项，界面上那句"录音文件没有删…"在 mock 下会变成 undefined。
    return { deleted: true, singer_id: singerId, name: gone.name,
             sessions_deleted: n, templates_deleted: 0, singer_rows_deleted: 1,
             files_deleted: 0, bytes_freed: 0,
             skipped_reason: '录音文件没有删：文件名里没有可靠的演唱者信息，'
                           + '猜错会删掉别人的录音。（mock 数据源，本就没有文件）',
             kept: '实验数据（exp_* 表）未删除。' };
  }

  /**
   * 每个演唱者一套**彼此不同**的假数据。
   *
   * ★ 为什么不能所有人共用一份：那样"切换演唱者"之后第 05 屏一点变化都没有，
   *   而**"没变化"与"切换没生效"在界面上完全一样** —— 探针也就无从判断。
   *   数值仍是假的（这是 mock 的定义），但**每个人不同**这件事是真的。
   */
  #dataFor(sid) {
    if (!this._bySinger) this._bySinger = {};
    if (!this._bySinger[sid]) {
      const special = sid === 'S01';
      // 用 id 算一个稳定的偏移量：同一个人每次刷新看到的曲线一样（截图可对比）
      const h = [...String(sid)].reduce((a, c) => a + c.charCodeAt(0), 0);
      const off = special ? 0 : (h % 13) - 6;
      const n = special ? 5 : 3 + (h % 4);
      const base = 62 + off;
      const labels = [], total = [], expr = [], pitch = [], reference_id = [];
      for (let i = 0; i < n; i++) {
        // 日期必须**合法**（月 ≤ 12、日 ≤ 28）：前端要按 `YYYY-MM-DD` 切字符串，
        // 造出 2026-13-15 这种日期不会报错，只会在历史列表里显示一个不存在的日子。
        labels.push(`${String(8 + (i % 4)).padStart(2, '0')}-${String(10 + i).padStart(2, '0')}`);
        total.push(Math.round((base + i * 2.4) * 10) / 10);
        // ★ 第 2 个点的情感分刻意留 null：契约 §3.2 要求 UI 在 null 处**断线**，
        //   而不是补 0（补 0 会画出一条"这次唱得很差"的假曲线）。
        //   这是原来那块写死的 PROGRESS 特意保留的边界情况，不能弄丢。
        expr.push(i === 1 ? null : Math.round((44 + off + i * 3.1) * 10) / 10);
        pitch.push(Math.round((78 + off / 2 + i * 1.2) * 10) / 10);
        reference_id.push('REF_TWINKLE_1');
      }
      const HIST = labels.map((d, i) => ({
        session_uid: `sess_mock_${sid}_${i}`,
        created_at: `2026-${d.slice(0, 2)}-${d.slice(3)}`,
        total_score: total[i], pitch_score: pitch[i],
        rhythm_score: Math.round((80 + off + i) * 10) / 10,
        expr_score: expr[i], mode: 'A',
        reference_title: '小星星 · 第一句', retry_of_uid: null,
      })).reverse();
      this._bySinger[sid] = {
        history: HIST,
        progress: { labels,
                    times: labels.map((_, i) => `10:${String(10 + i).padStart(2, '0')}`),
                    series: { total, expr, pitch, reference_id,
                              reference_title: labels.map(() => '小星星 · 第一句') } },
        profile: {
          cluster_label: special ? '平稳抒情型' : '力度起伏型',
          session_count: n,
          summary: `（示例数据 · ${sid}）练了 ${n} 次，平均总分 `
                 + `${Math.round(total.reduce((a, b) => a + b, 0) / n)}。`,
          chips: special ? ['音色温暖厚实', '起音偏柔', '有自然颤音']
                         : ['力度起伏明显', '起音干脆'],
          normalized: { dynamic_range: 0.5 + off / 40, phrase_dynamics: 0.7,
                        rubato: 0.6, vibrato: 0.65, onset_style: 0.4,
                        brightness: 0.9 },
          metrics: { dynamic_range: 8.2 + off, brightness: 1320 + off * 10 },
        },
      };
    }
    return this._bySinger[sid];
  }

  #historyFor(sid) { return this.#dataFor(sid).history; }

  async createSession({ mode, templateId }) {
    await this.#wait();
    return {
      session_uid: 'sess_mock_new',
      status: SessionStatus.CREATED,
      mode: templateId ? 'B' : (mode || 'A'),
      reference: REF_TWINKLE,
    };
  }

  /* ── 跟唱模板（模式 B 两遍制）──────────────────────────────
     mock 也要有这几个方法，否则 `?src=mock` 下 s2 的开始态永远空着 ——
     而"空着"和"接线没生效"在界面上长得一模一样，等于没法验证。
     实现是内存数组，行为与后端对齐：create 返回 template_id / reference_id。 */
  async listTemplates() {
    await this.#wait(60);
    if (!this._templates) {
      this._templates = [
        { template_id: 'tpl_mock_1', reference_id: 'tpl_mock_1',
          title: '我自己唱的·副歌', note_count: 31, duration_ms: 19156,
          quality_level: 'ok', created_at: '2026-09-20T21:03:00' },
        { template_id: 'tpl_mock_2', reference_id: 'tpl_mock_2',
          title: '同桌的原创小调', note_count: 18, duration_ms: 11200,
          quality_level: 'ok', created_at: '2026-09-18T19:40:00' },
      ];
    }
    return structuredClone(this._templates);
  }

  async createTemplate({ sessionUid, title }) {
    await this.#wait(200);
    const tid = 'tpl_mock_' + Math.random().toString(16).slice(2, 10);
    if (!this._templates) await this.listTemplates();
    this._templates.unshift({
      template_id: tid, reference_id: tid,
      title: (title || '').trim() || '未命名模板',
      note_count: 31, duration_ms: 19156, quality_level: 'ok',
      created_at: new Date().toISOString().slice(0, 19),
    });
    void sessionUid;
    return { template_id: tid, reference_id: tid, title,
             note_count: 31, duration_ms: 19156,
             quality: { level: 'ok', reasons: [] } };
  }

  async practiceSegment(sessionUid, singerId) {
    await this.#wait(120);
    void sessionUid; void singerId;
    const card = structuredClone(REPORT_MAIN.practice_card);
    const tid = 'tpl_mock_micro';
    if (!this._templates) await this.listTemplates();
    this._templates.unshift({
      template_id: tid, reference_id: tid, title: '微练习 · 第 8–9 个音',
      note_count: 2, duration_ms: 1000, quality_level: 'ok',
      created_at: new Date().toISOString().slice(0, 19),
    });
    return { template_id: tid, title: '微练习 · 第 8–9 个音',
             note_count: 2, duration_ms: 1000, card };
  }

  aiSingUrl() {
    // mock 模式没有本地 DiffSinger；返回空串，前端据此隐藏「AI 唱」。
    return '';
  }

  async deleteTemplate(templateId) {
    await this.#wait(80);
    if (!this._templates) await this.listTemplates();
    const before = this._templates.length;
    this._templates = this._templates.filter((t) => t.template_id !== templateId);
    return { deleted: this._templates.length < before };
  }

  async renameTemplate(templateId, title) {
    await this.#wait(80);
    if (!this._templates) await this.listTemplates();
    const t = this._templates.find((x) => x.template_id === templateId);
    if (!t) throw new Error('找不到这个模板');
    t.title = String(title || '').trim() || t.title;
    return { template_id: templateId, title: t.title };
  }

  /** 删掉某一次练习（2026-10-15）。mock 里只是把它从假历史里拿掉。 */
  async deleteSession(sessionUid, singerId) {
    void singerId;
    await this.#wait(80);
    const before = (this._history || []).length;
    this._history = (this._history || []).filter(
      (h) => h.session_uid !== sessionUid);
    return { deleted: this._history.length < before,
             templates_deleted: 0, audio_files_deleted: 0 };
  }

  /** 模板的音符序列（实时参考阶梯用）。mock 里生成一段与内置曲目同形的序列。 */
  async getTemplateNotes(templateId) {
    await this.#wait(60);
    const midi = REF_TWINKLE.notes_midi;
    const dur = 500;
    void templateId;
    return midi.map((m, i) => ({ start_ms: i * dur, end_ms: (i + 1) * dur,
                                pitch_midi: m }));
  }

  async finishSession() {
    await this.#wait(300);
    return { session_uid: 'sess_mock_new', status: SessionStatus.PRECISE_PENDING };
  }

  /** 模拟四步精算进度（对应契约 §5.3） */
  async getSessionStatus() {
    await this.#wait(80);
    const elapsed = Date.now() - (this._t0 || (this._t0 = Date.now()));
    const steps = ['提取音高轨迹', '切分音符', '与参考旋律对齐', '计算六个表现力指标'];
    const idx = Math.min(steps.length - 1, Math.floor(elapsed / 700));
    const done = elapsed >= 700 * steps.length;
    return {
      status: done ? SessionStatus.SCORED : SessionStatus.PRECISE_PENDING,
      step: done ? steps.length : idx + 1,
      step_total: steps.length,
      step_label: done ? '完成' : steps[idx],
    };
  }

  async getReport(uid) {
    await this.#wait();
    if (uid === 'sess_mock_novib') return structuredClone(REPORT_NO_VIBRATO);
    if (uid === 'sess_mock_retry') return structuredClone(REPORT_RETRY);
    return structuredClone(REPORT_MAIN);
  }

  async getComparison(uid) {
    await this.#wait();
    const base = REPORT_MAIN, retry = REPORT_RETRY;
    return {
      base, retry,
      delta: {
        total: r1(retry.total - base.total),
        expr: r1(retry.expressiveness.score - base.expressiveness.score),
        pitch: r1(retry.pitch.score - base.pitch.score),
        dynamic_range: r1(retry.expressiveness.dynamic_range - base.expressiveness.dynamic_range),
        phrase_dynamics: r1(retry.expressiveness.phrase_dynamics - base.expressiveness.phrase_dynamics),
      },
    };
  }

  /** 历史。★ 按 singerId 分开（G3）：换人之后第 05 屏必须真的换一份数据。 */
  async getHistory(singerId) {
    await this.#wait();
    return structuredClone(this.#historyFor(singerId || 'S01'));
  }

  async getProgress(singerId) {
    await this.#wait();
    return structuredClone(this.#dataFor(singerId || 'S01').progress);
  }

  async getProfile(singerId) {
    await this.#wait();
    return structuredClone(this.#dataFor(singerId || 'S01').profile);
  }

  async getExperimentSummary() {
    await this.#wait();
    const rand = rng(7);
    const scatter = [];
    for (let i = 0; i < 24; i++) {
      const x = 1.3 + rand() * 3.4;
      scatter.push({ rater_mean: r1(x), system: r1(Math.max(1, Math.min(5, x + (rand() - 0.5) * 0.9))) });
    }
    return {
      stats: { singers: 10, samples: 24, raters: 3, rho: 0.71 },
      scatter,
      weights: [
        { id: 'dynamic_range', label: '音量起伏', beta: 0.34, p: '< .01', significant: true },
        { id: 'phrase_dynamics', label: '句内推进', beta: 0.27, p: '< .01', significant: true },
        { id: 'vibrato', label: '尾音控制', beta: 0.19, p: '< .05', significant: true },
        { id: 'rubato', label: '节奏弹性', beta: 0.14, p: '= .11', significant: false },
        { id: 'onset_style', label: '起音方式', beta: 0.09, p: '= .28', significant: false },
        { id: 'brightness', label: '音色明亮度', beta: 0.05, p: '= .54', significant: false },
      ],
      r2: 0.63,
      /* ★ 验收指标（2026-09-24）：mock 是"内容完整、数字合理"的样例，
         所以三条在这里**全部达标**（ρ=0.71 > 0.6）。
         形状必须与 `api.js` 完全一致 —— 形状不同的后果不是报错，
         而是 `?src=mock` 下验收条整块空掉，而"空掉"和"没接线"长得一样。 */
      benchmarks: [
        { id: 'rt_latency', label: '实时管线端到端延迟', measured: 3.1,
          unit: 'ms', threshold: 200, cmp_text: '&lt; 200 ms',
          measured_text: 'P50 2.2ms / P95 3.1ms', decimals: 1, ok: true,
          source: 'docs/论文骨架与数据地图.md（样例值）' },
        { id: 'pitch_agreement', label: '双管线音高一致率', measured: 96.41,
          unit: '%', threshold: 95, cmp_text: '&gt; 95%',
          measured_text: '96.41%（八度错误率 0.00%）', decimals: 2, ok: true,
          source: 'design.md:548（样例值）' },
        { id: 'rho', label: '系统与人评分相关', measured: 0.71, unit: '',
          threshold: 0.6, cmp_text: '&gt; 0.6', decimals: 2, ok: true,
          pending: false, source: 'mock 样例' },
      ],
      benchmarks_summary: { ok: 3, total: 3, pending: 0 },
    };
  }

  async uploadRecording() { await this.#wait(400); return { session_uid: 'sess_mock_upload' }; }

  /* ── 歌曲导入（2026-10-20）：mock 只走通 UI，不假装有音频 ── */
  async songInspect(file, opts = {}) {
    await this.#wait(300);
    void file; void opts;
    return {
      song_id: 'song_mock00000001', title: '示例歌曲 · Mock',
      duration_ms: 180000,
      suggested: { start_ms: 30000, end_ms: 90000 },
      limits: { min_ms: 8000, max_ms: 180000 },
    };
  }

  async songAnalyze() { await this.#wait(300); return { song_id: 'song_mock00000001', status: 'QUEUED', progress: 2 }; }

  async songStatus() {
    await this.#wait(300);
    return {
      song_id: 'song_mock00000001', status: 'READY', stage: '准备就绪',
      progress: 100, title: '示例歌曲 · Mock', duration_ms: 180000,
      start_ms: 30000, end_ms: 90000, note_count: 36,
      quality: { level: 'ok', summary: 'mock 数据：真实后端会给出分离质量',
                 flags: [], leak_db: -22.0, notes: 36, coverage: 0.5 },
      preview: {},
      error: null, template_id: null,
    };
  }

  async songConfirm() {
    await this.#wait(300);
    return { template_id: 'tpl_mock_song', title: '示例歌曲 · Mock · 原唱参考',
             note_count: 36, quality: { level: 'ok' } };
  }

  async songLibrary() {
    await this.#wait(200);
    return { templates: [], jobs: [] };
  }

  async songDelete() { await this.#wait(200); return { deleted: true }; }

  songPreviewUrl() { return ''; }

  /* ── ★ MIDI 导入（2026-10-10，第四条路）────────────────────────
     ★ 为什么 mock 也要有：没有的话 `?src=mock` 下这一块永远是空的 ——
       而"空着"和"接线没生效"在界面上**长得一模一样**，等于没法验证。
       （模式 B 的模板列表、G3 的名册都踩过同一个坑，故此处照做。）

     形状与后端 `_midi_payload()` **一字不差**，尤其是：
       · `tracks[i].reason` 是**给人看的**一句话（用户凭什么选这一轨）
       · `preview_url` 是**同源相对路径**，与真实后端一致
       · `notes` 会被截断到 400 条，但 `note_count` 是**真实总数**

     ⚠️ 刻意造了一轨 **Bass 诱饵**（音域低、名字像伴奏）：
        验证"自动推荐"必须挑中 Melody —— 这是后端自检里那条
        「没被 Bass 诱饵骗」的同一个判据，界面上要能看出来。 */
  async midiUpload(file) {
    await this.#wait(500);
    void file;
    this._midiFile = this._midiFile || `mock${Math.random().toString(16).slice(2, 10)}`;
    this._midiPicked = null;
    return structuredClone(this.#midiInfo('自检用 MIDI（mock）'));
  }

  async midiTrack(fileId, trackIndex) {
    await this.#wait(350);
    this._midiFile = fileId || this._midiFile;
    this._midiPicked = Number(trackIndex);
    // 换轨之后**也要重新出试听** —— 用户改选就是为了听另一轨
    return structuredClone(this.#midiInfo());
  }

  async midiConfirm({ fileId, trackIndex, title }) {
    await this.#wait(300);
    // 形状与后端 `/api/midi/confirm` 一致：返回的是**模板**，不是会话
    const tid = 'tpl_mock_' + Math.random().toString(16).slice(2, 10);
    if (!this._templates) await this.listTemplates();
    const nm = String(title || '').trim() || `MIDI 导入 · 轨 ${trackIndex}`;
    this._templates.unshift({
      template_id: tid, reference_id: tid, title: nm,
      note_count: 14, duration_ms: 3500, quality_level: 'ok',
      created_at: new Date().toISOString().slice(0, 19),
    });
    void fileId;
    return { template_id: tid, title: nm, note_count: 14, duration_ms: 3500 };
  }

  /** mock 的三轨形状（Test 空轨 / Bass 诱饵 / Melody 真旋律）。 */
  #midiInfo(filename) {
    const TRUTH = REF_TWINKLE.notes_midi;          // 14 个音
    const DUR = 250;
    const chosen = this._midiPicked == null ? 2 : this._midiPicked;
    const mk = (midis, start) => midis.map((m, i) => ({
      index: i, pitch_midi: m, start_ms: start + i * DUR,
      end_ms: start + (i + 1) * DUR, velocity: 96,
    }));
    const bass = [36, 43, 36, 43, 36, 43, 36];
    const notes = chosen === 1 ? mk(bass, 0) : (chosen === 2 ? mk(TRUTH, 0) : []);
    const tracks = [
      { index: 0, name: 'Test', note_count: 0, lo_midi: 0, hi_midi: 0,
        mean_midi: 0, distinct_pitches: 0, chord_ratio: 0, is_drum: false,
        suggestion: 0, reason: '这一轨没有音符' },
      { index: 1, name: 'Bass', note_count: bass.length, lo_midi: 36, hi_midi: 43,
        mean_midi: 39.5, distinct_pitches: 2, chord_ratio: 0, is_drum: false,
        suggestion: -2.33, reason: '轨名像伴奏/鼓/贝斯' },
      { index: 2, name: 'Melody', note_count: TRUTH.length, lo_midi: 60, hi_midi: 69,
        mean_midi: 64.5, distinct_pitches: 7, chord_ratio: 0, is_drum: false,
        suggestion: 4.48, reason: '轨名像旋律、音域偏高' },
    ];
    const midis = notes.map((n) => n.pitch_midi);
    return {
      tracks, suggested_track: 2, chosen_track: chosen,
      notes, notes_preview: notes, note_count: notes.length,
      duration_ms: notes.length ? notes[notes.length - 1].end_ms : 0,
      lo_midi: midis.length ? Math.min(...midis) : null,
      hi_midi: midis.length ? Math.max(...midis) : null,
      // ⚠️ mock 里没有真实音频可放 —— 给一个**明显是 mock 的**路径，
      //    而不是编一个能播的地址（那会让"试听到底通没通"无法判断）。
      preview_url: notes.length ? '/exports/mock_midi_preview.wav' : null,
      file_id: this._midiFile, filename: filename || 'mock.mid',
    };
  }

  async getConfig() {
    await this.#wait(60);
    return {
      range: { lo_midi: 48, hi_midi: 84, preset: '男高音' },
      recording: { device: 'default', sample_rate: 22050, accompaniment_mode: true },
      feedback: { ai_comment: true, show_jargon: false, colorize_deviation: true },
      privacy: { record_dir: './data/recordings/', used_bytes: 18874368, sample_count: 24 },
    };
  }

  async putConfig(patch) { await this.#wait(80); return patch; }

  // ── LLM key（mock 模式）──
  //   ⚠️ mock **不假装能配 key** —— 直接说"mock 模式没有后端"。
  //      本来想返回 `{configured: true}` 让界面好看，但那是**在界面上撒谎**：
  //      用户会以为配上了，而实际什么也没发生。
  //      （本项目已经吃过好几次"界面正常但事情没做"的亏。）
  async getLlmKey() {
    await this.#wait(40);
    return { configured: false, source: 'none', hint: null,
             base: '(mock)', model: '(mock)', enabled: false,
             mock: true, note: 'mock 模式不连后端，配不了 key' };
  }

  async saveLlmKey() {
    await this.#wait(80);
    throw new Error('mock 模式没有后端，配置不了 API key —— 请用 ?src=api 打开。');
  }

  async testLlmKey() {
    await this.#wait(80);
    return { ok: false, reason: 'mock 模式没有后端' };
  }

  async clearLlmKey() {
    await this.#wait(80);
    return { cleared: false, enabled: false };
  }

  /**
   * 模拟实时流：按契约 §3.3 的频率推送。
   * pitch 帧 20 Hz（50 ms），metrics 帧 1 Hz。
   */
  openStream(sessionUid, onMessage) {
    let pitchTimer = null, metricsTimer = null, t = 0;
    const NOTES = REF_TWINKLE.notes_midi;
    const self = this;
    return {
      start() {
        pitchTimer = setInterval(() => {
          t += 50;
          const idx = Math.min(NOTES.length - 1, Math.floor(t / 500));
          const ref = NOTES[idx];
          const cents = Math.sin(t / 900) * 18 + (idx === 2 ? -30 : 0);
          onMessage({
            type: 'pitch',
            data: { t_ms: t, f0: r1(440 * Math.pow(2, (ref - 69 + cents / 100) / 12)),
                    ref_f0: r1(440 * Math.pow(2, (ref - 69) / 12)), cents_dev: r1(cents) },
          });
        }, 50);
        metricsTimer = setInterval(() => {
          const idx = Math.min(NOTES.length - 1, Math.floor(t / 500));
          onMessage({
            type: 'metrics',
            data: {
              t_ms: t,
              pitch: r1(80 + Math.sin(t / 1500) * 8),
              rhythm: r1(88 + Math.sin(t / 2100) * 5),
              dynamic: 54.0,
              vibrato: t < 1500 ? null : 28.0,     // 前 1.5 秒还没有可测的长音 → null
              current_note: { pitch_midi: NOTES[idx], cents_dev: r1(Math.sin(t / 900) * 18), verdict: 'OK' },
              advice: t < 2500 ? null : '你这一句从头到尾用一样大的声音在唱。',
            },
          });
        }, 1000);
      },
      sendAudio() { /* mock 不处理音频 */ },
      stop() { clearInterval(pitchTimer); clearInterval(metricsTimer); },
      close() { this.stop(); },
    };
  }
}
