/**
 * 当前演唱者身份 —— **全项目唯一读写 `sl.singer` 的地方**（G3 多用户）。
 *
 * ★ 为什么必须收敛到一处（§58.1②、§58.1③）：
 *   改之前 `sl.singer` 有两个写入口（`bindings.js` 的 `singerId()` 读、
 *   `s1()` 每次进第 01 屏都往后写），而读入口散在 s3/s4/s5 各屏。
 *   两个写入口的必然结果是有一天不同步 —— 而不同步的表现不是报错，
 *   是**某几屏显示 A 的数据、另几屏显示 B 的**。这个项目里
 *   "同一件事有两份实现"已经踩过很多次（§54 的两个真相源、§60 的配置键名）。
 *
 * ★ 为什么从 `sessionStorage` 搬到 `localStorage`（§58.1③）：
 *   `sessionStorage` **关掉标签页就没了**，下次打开回落默认值 S01。
 *   对真实用户来说，那读起来就是"我的记录全没了" ——
 *   而身份属于"用户是谁"，本来就该跨会话保留。
 *
 * ⚠️ 搬运不能让旧的 `S01` 变成孤儿：读的时候**回退到 sessionStorage 里的旧值**，
 *    并把它提升到 localStorage。老用户升级一次、打开页面，
 *    身份还是原来那个（而不是悄悄换成别人）。
 */

const KEY = 'sl.singer';
/** 与后端 `app_singer` 里那个最先登记的演唱者一致（api 源的真实默认） */
export const DEFAULT_SINGER = 'S01';

const listeners = new Set();

function ls() {
  try { return window.localStorage; } catch { return null; }   // 隐私模式下会抛
}
function ss() {
  try { return window.sessionStorage; } catch { return null; }
}

/**
 * 当前演唱者 id。
 *
 * 顺序：localStorage → （旧的）sessionStorage → 默认值。
 * ★ 中间那一层是**迁移**，不是"两个真相源"：读到之后就立刻写进
 *   localStorage 并把 sessionStorage 那份删掉，所以它只可能生效一次。
 */
export function getSingerId() {
  const L = ls();
  if (L) {
    const v = L.getItem(KEY);
    if (v) return v;
  }
  const S = ss();
  if (S) {
    const legacy = S.getItem(KEY);          // 老版本存在这里（关标签页就丢）
    if (legacy) {
      if (L) { try { L.setItem(KEY, legacy); } catch { /* 配额满/隐私模式 */ } }
      try { S.removeItem(KEY); } catch { /* ignore */ }
      return legacy;
    }
  }
  return DEFAULT_SINGER;
}

/**
 * 换人。**只此一处**能改当前身份。
 *
 * ⚠️ 换人时必须把 `sl.sess`（"上一次练的是哪一条"）清掉：
 *    那是**上一个演唱者**的会话 uid，留着的话第 03/04 屏会拿它去查报告 ——
 *    表现是"我刚换到同桌，报告页显示的还是我自己的成绩"。
 *    静默串数据正是这个功能要消灭的东西，所以清理放在这里，
 *    而不是指望每个调用点自己记得清。
 */
export function setSingerId(id) {
  if (!id) return;
  const prev = getSingerId();
  const L = ls();
  if (L) { try { L.setItem(KEY, id); } catch { /* 隐私模式：至少本次会话内可用 */ } }
  const S = ss();
  if (S) { try { S.removeItem(KEY); } catch { /* ignore */ } }  // 旧位置不再写
  clearSessionPointer();
  if (prev !== id) listeners.forEach((fn) => { try { fn(id, prev); } catch { /* ignore */ } });
}

/** 清掉"当前看的是哪一次练习"（换人、清空数据都要清）。 */
export function clearSessionPointer() {
  const S = ss();
  if (S) { try { S.removeItem('sl.sess'); } catch { /* ignore */ } }
}

/** 订阅身份变化（sidebar / s7 卡都要跟着刷新）。返回取消订阅的函数。 */
export function onSingerChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
