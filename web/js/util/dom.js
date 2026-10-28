/** DOM 小工具。原生 JS，无依赖（设计文档 9.1：不引框架）。 */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** 建元素：el('div', {class:'a'}, '文字' | 子元素…) */
export function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(n.style, v);
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}

/** 数值显示：null → 「—」（契约 §3.2：null 表示本次未评估，不显示 0） */
export const fmt = (v, digits = 1, dash = '—') =>
  (v === null || v === undefined || Number.isNaN(v)) ? dash : Number(v).toFixed(digits);

/** 转义，防止把数据当 HTML 插进 DOM */
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
