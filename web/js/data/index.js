/**
 * 数据源开关 —— 全项目唯一决定"数据从哪来"的地方。
 *
 * ★ **默认 `api`（真实后端）**，要假数据必须显式写 `?src=mock`（2026-09-24 改）。
 *   为什么反过来：原来默认 mock —— 任何人直接打开这个系统，看到的都是
 *   **编出来的数据**（包括一张 78 分的报告），而界面上没有任何提示。
 *   判据是"真实用户自己打开能不能走通"：默认必须是真数据，
 *   假数据只能是**为了与原型对照**时主动选的。
 *
 * ⚠️ 这里和 `js/app.js` 的 `mode()` 曾经**各写了一份默认值**，
 *    而且改了一处忘了另一处 —— 徽标显示"真实后端"、数据其实是 mock，
 *    比没有徽标更坏（**界面在说谎**）。现在 app.js 从这里 import，
 *    只有这一处判断。
 *
 * 用法：
 *   /                      → 真实后端（api）
 *   /?src=mock             → 假数据（对照原型用）
 *   /?src=mock&slow=1500   → mock 且人为加 1.5 秒延迟（用来调 loading 态）
 */
import { MockSource } from './mock.js';
import { ApiSource } from './api.js';

const params = new URLSearchParams(location.search);
const which = params.get('src') === 'mock' ? 'mock' : 'api';

/** 当前模式（'api' | 'mock'）—— 给 app.js 的徽标用，避免第二份判断。 */
export const srcMode = which;

export const source = which === 'api' ? new ApiSource() : new MockSource();

if (which !== 'api') {
  const slow = Number(params.get('slow') || 0);
  if (slow > 0) source.latency = slow;
}

export const isMock = which !== 'api';

/** 调试用：把当前数据源和一份样例报告打到控制台，方便和契约逐字段核对 */
export async function selfCheck() {
  const report = await source.getReport('sess_mock_main');
  console.groupCollapsed(`[dataSource] ${which} · 自检`);
  console.log('数据源:', source.constructor.name);
  console.log('契约 §2.2 ScoreReport 顶层字段:',
    Object.keys(report).join(', '));
  console.log('契约 §2.1 NoteSegment 字段:',
    Object.keys(report.segments[0]).join(', '));
  console.log('可空字段当前值:', {
    'pitch.accuracy': report.pitch.accuracy,
    'expressiveness.vibrato_rate': report.expressiveness.vibrato_rate,
    'metrics_excluded': report.metrics_excluded,
  });
  console.groupEnd();
  return report;
}
