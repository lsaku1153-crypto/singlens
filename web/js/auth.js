/** 产品登录态。应用启动前先问服务端，而不是相信 localStorage。 */
import { setSingerId, getSingerId } from './data/identity.js';

let user = null;

function nextUrl() {
  return location.pathname + location.search + location.hash;
}

function loginUrl() {
  return '/login?next=' + encodeURIComponent(nextUrl());
}

export function currentUser() {
  return user;
}

export function isAdmin() {
  return !!user && user.role === 'admin';
}

/**
 * 在 router.start() 之前调用。
 * 返回 null 表示正在跳转登录页，调用方应停止启动。
 */
export async function requireAuth() {
  try {
    const r = await fetch('/api/auth/me', {
      credentials: 'same-origin', cache: 'no-store',
    });
    if (r.status === 401) {
      location.replace(loginUrl());
      return null;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    if (!j || !j.ok || !j.data) throw new Error('登录态响应格式不对');
    user = j.data.user || null;
    if (!user) {
      location.replace(loginUrl());
      return null;
    }
    // 服务端是唯一身份来源。普通账号的 singer 已由账号固定；
    // 本机没启用登录时 user.singer_id 可能是 null，保留旧选择。
    if (user.singer_id) setSingerId(user.singer_id);
    document.body.dataset.role = user.role || 'singer';
    document.body.classList.toggle('auth-admin', user.role === 'admin');
    document.body.classList.toggle('auth-singer', user.role !== 'admin');
    document.dispatchEvent(new CustomEvent('singlens:auth', { detail: user }));
    return user;
  } catch (e) {
    // 不把技术异常显示给用户；登录页会给出人话。
    console.warn('[auth] 读取登录态失败：', e);
    location.replace(loginUrl());
    return null;
  }
}

/** 给设置页/侧栏用：退出后清掉本地身份指针，避免下一个账号看到上一个人的会话指针。 */
export async function logout() {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
    });
  } catch { /* 断网也要回登录页 */ }
  try { localStorage.removeItem('sl.singer'); } catch { /* ignore */ }
  location.replace('/login');
}

export function currentSingerId() {
  return (user && user.singer_id) || getSingerId();
}
