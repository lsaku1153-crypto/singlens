/**
 * 演唱者名册 —— 侧栏那一行与 s7 的「演唱者」卡**共用同一份数据**（G3）。
 *
 * ★ 为什么要有这一层（而不是各自去查一次接口）：
 *   同一个名册有两个读者（常驻侧栏 + 设置页那张卡），各自查一次的话，
 *   就会出现"侧栏写着「同桌」，卡片里当前却是「我」"这种**同一屏内自相矛盾**。
 *   这个项目里那类矛盾出现过多次（s6 的"全部达标"配"还不能宣称"），
 *   根因都是"同一份事实有两处渲染、没有共同的来源"。
 *
 * ★ 它还负责一件事：**自愈**。
 *   localStorage 里存的 singer_id 可能已经不存在了（在另一台机器上删掉了、
 *   换过库、或者手工改过 storage）。那时所有 `/api/singer/{sid}/*` 都会 404，
 *   而用户看到的是一句"没有演唱者 XXX"——他却不知道该怎么改回去。
 *   这里发现"当前身份不在名册里"就切回名册第一位，并把这件事**记下来**
 *   （`healedFrom`），由界面如实告诉用户，而不是悄悄换一个人。
 */
import { source } from './index.js';
import { getSingerId, setSingerId } from './identity.js';

let cache = null;
/** 上一次自愈的信息（给界面显示用）：{from, to} 或 null */
let healedFrom = null;
/** 本次是不是**刚替你建了第一位演唱者**（空库自愈，见 loadRoster） */
let created = null;

/**
 * 取名册。`force=true` 时绕过缓存重新拉（新建/改名/删除之后必须这样）。
 * 返回 `{singers, current, healedFrom, source}`。
 */
export async function loadRoster({ force = false } = {}) {
  if (!cache || force) {
    // ★ 自愈判据要看**刚拉到的那份名册**，不能看 `cache`（2026-10-01 修）。
    //   第一版写成 `if (!cache || force) { cache = await fetchRoster(); if (cache.length === 0) … }`
    //   里层判的是 `cache.length` —— 而 `force=true` 时 `cache` 已经被换成新拉的了，
    //   看着没问题。真正的坑是：s7 每次 redraw 都用 `force: true`，
    //   于是**每次**都重新拉、每次都判空、每次都新建一个人 ——
    //   一行代码就能造出一堆演唱者。所以这里必须：
    //     ① 用局部变量 `fetched` 判断，语义清楚
    //     ② 建之前先**再确认一次**（避免并发/重复调用各建一个）
    let fetched = await fetchRoster();
    if (Array.isArray(fetched) && fetched.length === 0
        && typeof source.createSinger === 'function') {
      created = null;
      try {
        const r = await source.createSinger({ name: '我' });
        /* ⚠️ 形状要按**适配层实际返回的**取，不能想当然（2026-10-01 踩过）：
           `api.js` 的 `createSinger` 返回的是 `{created, renamed_from, singer}` ——
           新 id 在 **`r.singer.singer_id`** 里，不在 `r.singer_id`。
           第一版按 `r.singer_id` 读 ⇒ 建是建成功了，但这里拿不到 id ⇒
           `created` 恒为 null、`setSingerId` 也没被调 ⇒ 界面以为"自动登记失败"，
           而库里**其实已经建好了**一个人。表现就是 s7 上那句自相矛盾的
           「名册是空的，而且自动登记没成功」（库里明明有 1 行）。
           这又是一次"契约漂移"：写的和读的形状不一致，而且**不报错**。 */
        const sid = (r && (r.singer_id
                           || (r.singer && r.singer.singer_id)
                           || (r.data && r.data.singer_id))) || null;
        if (sid) {
          setSingerId(sid);
          created = sid;
          fetched = await fetchRoster();     // 建完再拉一次，拿到真名册
        }
      } catch {
        // 建不出来就保持空名册，由界面如实说"名册空" —— 不编一个假身份。
        fetched = [];
      }
    }
    cache = fetched;
  }
  const singers = cache || [];
  let current = getSingerId();
  if (singers.length && !singers.some((s) => s.singer_id === current)) {
    // 当前身份在名册里找不到 ⇒ 切回第一位，并记下"原来是谁"。
    healedFrom = { from: current, to: singers[0].singer_id };
    setSingerId(singers[0].singer_id);
    current = singers[0].singer_id;
  }
  return { singers, current, healedFrom, created, source: srcName() };
}

async function fetchRoster() {
  if (typeof source.listSingers === 'function') {
    const rows = await source.listSingers();
    if (Array.isArray(rows)) return rows;
  }
  // 数据源没实现 listSingers（老实现/桩）→ 退回 bootstrap。
  // ⚠️ 这不是"两条路"，是**降级**：拿不到名册时至少别让侧栏空着。
  const boot = await source.bootstrap();
  return (boot && boot.singers) || [];
}

function srcName() {
  try { return source.constructor.name; } catch { return '?'; }
}

/** 当前这位演唱者的整行（找不到返回 null → 界面显示 id 而不是猜一个名字）。 */
export async function currentSinger() {
  const { singers, current } = await loadRoster();
  return singers.find((s) => s.singer_id === current) || null;
}

export async function switchSinger(singerId) {
  setSingerId(singerId);
  return loadRoster();
}

/** 新建。`name` 必填；`singerId` 不填则由后端生成（用户不该去发明内部 id）。 */
export async function createSinger(name, singerId) {
  const r = await source.createSinger({ name, singerId });
  await loadRoster({ force: true });
  return r;
}

export async function renameSinger(singerId, name) {
  const r = await source.createSinger({ name, singerId });   // 同一个端点：新建/改名
  await loadRoster({ force: true });
  return r;
}

/**
 * 删除。**只删库、不删录音文件**（§58.3）—— 后端如实回报删了什么，这里原样带回给界面。
 * 删掉的如果是当前这位，自动切到名册里剩下的第一位（否则界面会停在一个
 * 已经不存在的身份上，所有查询都是 404）。
 */
export async function removeSinger(singerId) {
  const r = await source.deleteSinger(singerId);
  const wasCurrent = getSingerId() === singerId;
  const list = await loadRoster({ force: true });
  if (wasCurrent && list.singers.length) setSingerId(list.singers[0].singer_id);
  return { ...r, switchedTo: wasCurrent && list.singers.length
    ? list.singers[0].singer_id : null };
}

/** 手工让缓存失效（比如 `?src=` 变了、或在别处改过名册） */
export function invalidateRoster() { cache = null; }
