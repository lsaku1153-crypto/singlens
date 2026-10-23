/**
 * 壳自身的交互（与"屏内容"无关的那些）。
 *
 * 逐条对应 v7 稿 `<script>` 里靠前的部分：
 *   · 底部胶囊导航的展开/收起/左右滚动/滚轮横滚
 *   · 跑马灯内容复制一份（配合 translateX(-50%) 无缝循环）
 *   · 分析中遮罩（原型阶段点一下关闭）
 *   · 专业术语开关（报告页与设置页同步）—— 见 shell.js 的 setPro
 *   · 侧栏「当前演唱者」常驻行（G3，2026-10-01）
 *
 * ⚠️ 与本文件无关的两件事已挪走：
 *   · `go(id)`  → js/router.js（改成哈希路由 + 按需加载视图）
 *   · 各页的 draw*() → 各自的 views/sN.js（B1-2/3 搬）
 */
import { $ } from './util/dom.js';
import { go, onChange, currentId } from './router.js';
import { getSingerId, onSingerChange } from './data/identity.js';
import { loadRoster } from './data/roster.js';

/**
 * 侧栏那一行「当前演唱者」—— **G3 多用户里最要紧的一个小东西**。
 *
 * ★ 为什么它必须常驻、而不能只放在设置页里：
 *   第 05 屏（成长档案）画的是**某一个人**的记录。换人入口如果只在设置页，
 *   用户在 s5 上没有任何办法知道屏幕上是谁的数据 ——
 *   而"这是谁的数据"和"数据对不对"是两件事，前者看不出来时后者无从判断。
 *   这和 §54 那条教训是同一个形状：界面上看着正常的错数据，比空数据更坏。
 *
 * ⚠️ 名字从**名册**里取（`loadRoster`），不能拿 id 拼一个"好看的"名字 ——
 *    拼出来的名字与设置页里显示的名字会是两个（用户改过名之后就对不上）。
 *    名册取不到时**如实显示 id**，不猜。
 */
async function refreshWho() {
  const btn = $('#whoBtn');
  const name = $('#whoName');
  if (!btn || !name) return;
  const sid = getSingerId();
  // 那一行上面的小字（"Singer · 当前演唱者"）。它在**自愈**时要换成警示语 ——
  // 自愈是"系统替你改了身份"，那件事必须说出来（见下面 healedFrom 的分支）。
  const label = btn.querySelector('.k');
  name.textContent = sid;
  btn.dataset.empty = '0';
  try {
    const { singers, current, healedFrom } = await loadRoster();
    const me = singers.find((s) => s.singer_id === current) || null;
    name.textContent = me ? me.name : current;
    const n = me && typeof me.sessions === 'number' ? me.sessions : null;
    btn.title = (me ? `${me.name}（${current}）` : current)
      + (n === null ? '' : ` · ${n} 次练习`)
      + ' —— 点击去设置页切换 / 新建演唱者';
    btn.dataset.empty = me ? '0' : '1';
    /* ★ 自愈要**看得见**（G3）。
       localStorage 里存的身份可能已经不存在了（在另一台设备上删掉了、
       换过库、手工改过 storage）。roster.js 会把身份切回一个真人 ——
       但"系统悄悄替你换了一个人"和"系统坏了"在界面上长得一样：
       用户只会发现曲线突然变成了别人的。所以这里把"原来是谁"写在脸上，
       直到刷新页面（下次打开时 localStorage 已经是有效的了，自然不再提示）。 */
    if (healedFrom) {
      if (label) label.textContent = '⚠️ 原身份已不存在 · 已切到';
      btn.dataset.heal = '1';
      btn.title = `原来的演唱者 ${healedFrom.from} 在名册里已经不存在了`
        + `（可能在别处被删掉），已自动切到 ${name.textContent}。`;
    } else {
      if (label) label.textContent = 'Singer · 当前演唱者';
      btn.dataset.heal = '0';
    }
  } catch (e) {
    // ⚠️ 名册取不到时**不能**退成空白：留一个 id 至少是诚实的，
    //    而空白会让人以为"当前没有演唱者"（那是另一种错误）。
    name.textContent = sid;
    btn.title = `名册读取失败（${e.message}）—— 当前身份仍是 ${sid}`;
    btn.dataset.empty = '1';
  }
}

export function initShell() {
  /* 底部导航：默认收起成胶囊；点一下展开成指令甲板，再点 / 点空白 / Esc 收回。
     ★ 2026-10-21：旧逻辑只会 `add('open')`，而 prts.css 又把 open 的 max-width
       收窄 —— 于是按第二下时“右半截没了、左半截还在”。现在是一个明确的
       open/close 状态机：`#dock` 与 `#dockInner` 同步，aria-expanded 同步，
       收起来时整体变成一颗胶囊。 */
  const dock = $('#dock');
  const dockInner = $('#dockInner');
  const dockScroll = $('#dockScroll');
  const dockMain = $('#dockMain');
  const setDock = (open) => {
    if (!dockInner) return;
    dockInner.classList.toggle('open', !!open);
    dock?.classList.toggle('open', !!open);
    dockMain?.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      const active = dockInner.querySelector('#dockPage button.on');
      if (active && active.scrollIntoView) {
        try { active.scrollIntoView({ block: 'nearest', inline: 'center' }); }
        catch { /* 老浏览器 */ }
      }
    }
  };
  if (dock && dockInner) {
    dockMain?.addEventListener('click', (e) => {
      e.stopPropagation();
      setDock(!dockInner.classList.contains('open'));
    });
    document.addEventListener('click', (e) => {
      if (!dock.contains(e.target)) setDock(false);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') setDock(false);
    });
    $('#dockPrev')?.addEventListener('click', (e) => {
      e.stopPropagation();
      dockScroll.scrollBy({ left: -300, behavior: 'smooth' });
    });
    $('#dockNext')?.addEventListener('click', (e) => {
      e.stopPropagation();
      dockScroll.scrollBy({ left: 300, behavior: 'smooth' });
    });
    dockScroll?.addEventListener('wheel', (e) => {
      if (dockScroll.scrollWidth <= dockScroll.clientWidth) return;
      e.preventDefault();
      dockScroll.scrollLeft += e.deltaY;
    }, { passive: false });
    /* ★ 2026-10-18：胶囊里的 8 个按钮原来**全是死的**（只有"导航"能展开）。
       现在接上路由；点完收起胶囊。激活态跟侧栏一起由 onChange 同步。 */
    document.querySelectorAll('#dockPage button').forEach((b) => {
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        go(b.dataset.s);
        setDock(false);
      });
    });
    onChange((id) => {
      document.querySelectorAll('#dockPage button').forEach((b) => {
        b.classList.toggle('on', b.dataset.s === id);
      });
    });
    /* 给 CSS 一个"现在在哪一屏"的钩子：s1 是落地页（保留跑马灯/胶囊/竖排 rail），
       s2–s8 是工具页（收起这些常驻装饰，把视口还给内容）。 */
    const markScreen = (id) => {
      document.body.dataset.screen = id || 's1';
      // 切屏后底部导航回到胶囊，避免展开的甲板挡住新页面
      setDock(false);
      /* ★ 2026-10-20：切屏后回到顶部。
         原来在成长档案滚到 2000px 再点侧栏进报告，页面**保持**在 2000px，
         用户落到报告的中间（常常是一片分项卡片），看上去像"这屏坏了"。
         同一处顺手把手机顶栏里当前项滚进视野 —— 7 个导航项在 390px 下
         只露出 4 个，当前项常常在屏幕外（截图实测：设置页高亮项看不到）。 */
      try { window.scrollTo({ top: 0, left: 0, behavior: 'auto' }); } catch { /* 老浏览器 */ }
      const active = id
        ? document.querySelector(`aside nav button[data-s="${id}"]`) : null;
      const nav = active && active.closest('nav');
      if (active && nav && nav.scrollWidth > nav.clientWidth + 2) {
        /* ★ 只在"当前项真的看不全"时才滚，且只滚到刚好露出来。
           原来是**永远居中**：进 s3 时把 01 挤到只露半个"式"字，
           看起来像导航坏了。nearest 的语义是"已经在视野里就不动"。 */
        const aL = active.offsetLeft;
        const aR = aL + active.offsetWidth;
        const vL = nav.scrollLeft;
        const vR = vL + nav.clientWidth;
        if (aL < vL + 6 || aR > vR - 6) {
          const left = Math.max(0, aL - 26);
          try { nav.scrollTo({ left, behavior: 'smooth' }); }
          catch { nav.scrollLeft = left; }
        }
      }
    };
    onChange(markScreen);
    markScreen(currentId());
  }

  /* 跑马灯：内容复制一份，配合 translateX(-50%) 无缝循环 */
  const mqIn = $('#mqIn');
  if (mqIn) mqIn.innerHTML += mqIn.innerHTML;

  /* 分析中遮罩：原型阶段点任意处关闭 */
  const veil = $('#veil');
  if (veil) veil.addEventListener('click', () => veil.classList.remove('on'));

  /* ── 侧栏「当前演唱者」常驻行（G3）──
     点击去 s7（那里那张卡能切换/新建/改名/删除）。
     换人之后这里必须跟着变：`onSingerChange` 由 identity.js 在写入时触发，
     **只有它一处**能改当前身份 —— 两个写入口必然有一天不同步。 */
  const whoBtn = $('#whoBtn');
  if (whoBtn) {
    whoBtn.addEventListener('click', () => go('s7'));
    onSingerChange(() => { refreshWho(); });
    refreshWho();
  }

  /* 专业术语：按钮 + 恢复上次的偏好（见上面两段的说明） */
  bindProButton();
  restoreProFromConfig();

  /* 2026-10-21 皮肤层：跟随指针的信号光。
     不是"装饰动画"—— 它只在指针移动时走 rAF，停下就结束；
     触屏 / 减少动效偏好直接不启动，避免白白耗电。 */
  initSignalSpot();
}

function initSignalSpot() {
  const spot = document.querySelector('.bgfx .spot');
  if (!spot) return;
  if (!window.matchMedia || !window.matchMedia('(pointer:fine)').matches) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  let tx = window.innerWidth * 0.68;
  let ty = window.innerHeight * 0.3;
  let x = tx;
  let y = ty;
  let raf = 0;
  const paint = () => {
    x += (tx - x) * 0.14;
    y += (ty - y) * 0.14;
    spot.style.transform = `translate3d(${x}px,${y}px,0)`;
    if (Math.abs(tx - x) > 0.4 || Math.abs(ty - y) > 0.4) {
      raf = requestAnimationFrame(paint);
    } else {
      raf = 0;
    }
  };
  const kick = () => { if (!raf) raf = requestAnimationFrame(paint); };
  spot.style.transform = `translate3d(${x}px,${y}px,0)`;
  window.addEventListener('pointermove', (e) => {
    tx = e.clientX;
    ty = e.clientY;
    kick();
  }, { passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && raf) { cancelAnimationFrame(raf); raf = 0; }
  });
}

/** 侧栏那一行的显隐（给外部用：比如名册为空时提示先建一个人） */
export const refreshWhoRow = refreshWho;

/** 分析中遮罩：显隐（B1-4 接真实分析进度时会用到） */
export const veilOn = (on) => $('#veil')?.classList.toggle('on', !!on);

/* ---------- 专业术语开关（报告页与设置页同步） ---------- */
let proOn = false;
export const isPro = () => proOn;

/**
 * 保存偏好用的回调 —— 由 `bindings.js`（唯一的数据层使用者）注册进来。
 *
 * ★ 为什么要有这个（2026-10-01）：报告页那个按钮原来**只切内存里的
 *   `proOn` 和 body 的 class，从来没存过**。于是"打开专业术语→刷新→又没了"，
 *   用户会认为这个开关记不住事。而 `shell.js` 是壳，不直接碰数据层
 *   （数据源只在 `bindings.js` 里 import），所以这里留一个口子给它。
 */
let proSaver = null;
export function setProPersister(fn) { proSaver = typeof fn === 'function' ? fn : null; }

export function setPro(on, { persist = false } = {}) {
  proOn = !!on;
  document.body.classList.toggle('pro', proOn);
  // 两个入口的选中态都要跟上：报告页的按钮 + 设置页的开关
  $('#proBtn')?.classList.toggle('on', proOn);
  $('#proSw2')?.classList.toggle('on', proOn);
  /* 只有**用户主动切换**时才写后端（`persist: true`）。
     初始化恢复偏好时不能写 —— 那会把"读到的值"再写回去，
     万一读失败就会把用户的偏好覆盖成默认值。 */
  if (persist && proSaver) proSaver(proOn);
}
export function togglePro() { setPro(!proOn, { persist: true }); }

/**
 * 启动时按**后端存下来的偏好**恢复术语开关（2026-10-01）。
 *
 * ★ 为什么必须补这一步：`show_jargon` 一直是存住的，但**从来没有人读它** ——
 *   用户上次打开了专业术语，刷新之后 `body.pro` 又是空的，四块解释再次消失。
 *   "设置能保存、但保存了没用"比"设置不保存"更难发现：界面上按钮是亮的，
 *   只有真去看那四块内容才知道没生效。
 */
export async function restoreProFromConfig() {
  try {
    const { source } = await import('./data/index.js');
    const cfg = await source.getConfig();
    setPro(!!((cfg.feedback || {}).show_jargon));
  } catch {
    // 读不到就保持关闭（默认值），不要瞎猜一个状态
    setPro(false);
  }
}

/* ---------- 报告页右上角那个「显示专业术语」按钮 ----------
   ★ 2026-10-01：它以前**完全没有绑定**（全仓只有本文件读过它的 class）。
   v7 稿原本是 `proBtn.onclick = () => setPro(...)`，拆壳（split_v7）时丢了，
   而 `.jargon` 是 `display:none`（要 `body.pro`）⇒ 那四块专业说法永远看不到。
   全仓只有这里读它，所以接线也放在这里 —— 和 `setPro` 挨着，改一处不会漏另一处。

   ⚠️ 为什么用**事件委托**挂在 document 上，而不是直接绑到按钮：
       `#proBtn` 在 `views/s3.js` 里，而 s3 是**路由按需渲染**的 ——
       每进一次报告页就是一个**全新**的按钮元素（`host.replaceChildren()`）。
       绑在按钮上就必须保证"每次渲染后都重新绑一次"，而那种约定迟早会漏
       （这个按钮丢失绑定就是这么来的）。挂在 document 上只做一次，
       进多少次报告页都有效 —— 而且 `initShell()` 与 s3 渲染谁先谁后都无所谓。
       实测过：直接 `addEventListener` 到按钮上时点击不触发，改成委托后正常。 */
function bindProButton() {
  document.addEventListener('click', (e) => {
    const btn = e.target && e.target.closest && e.target.closest('#proBtn');
    if (btn) togglePro();
  });
}

// 稿子里这两个开关是内联 onclick，这里保持同样的全局可用性
window.togglePro = togglePro;

/* ---------- 07 屏音域条：量程常量（B1-3 接设置时用） ---------- */
export const RANGE_MIN = 40;
export const RANGE_MAX = 84;
