/** 登录页：只做表单、错误提示与安全跳转。 */
const form = document.getElementById('loginForm');
const username = document.getElementById('username');
const password = document.getElementById('password');
const remember = document.getElementById('remember');
const errorBox = document.getElementById('formError');
const submit = document.getElementById('submitBtn');
const pwToggle = document.getElementById('pwToggle');
const caps = document.getElementById('caps');
const loginBody = document.getElementById('loginBody');
const registerBody = document.getElementById('registerBody');
const authModeLabel = document.getElementById('authModeLabel');
const registerEntry = document.getElementById('registerEntry');
const toRegister = document.getElementById('toRegister');
const toLogin = document.getElementById('toLogin');
const registerForm = document.getElementById('registerForm');
const registerError = document.getElementById('registerError');
const registerBtn = document.getElementById('registerBtn');
const authModeEn = document.getElementById('authModeEn');
const regName = document.getElementById('regName');
const regUser = document.getElementById('regUser');
const regPass = document.getElementById('regPass');
const regConfirm = document.getElementById('regConfirm');

function safeNext() {
  const v = new URLSearchParams(location.search).get('next') || '/';
  // 只允许站内绝对路径，挡掉 //evil.example 这类 open redirect。
  if (!v.startsWith('/') || v.startsWith('//')) return '/';
  if (v.startsWith('/login')) return '/';
  return v;
}

function showError(msg) {
  errorBox.textContent = msg || '登录失败，请稍后再试。';
  errorBox.hidden = false;
  form.classList.remove('shake');
  // 强制 reflow，让连续错误也能重新播放抖动
  void form.offsetWidth;
  form.classList.add('shake');
}

function clearError() {
  errorBox.hidden = true;
  errorBox.textContent = '';
  form.classList.remove('shake');
}

function showRegisterError(msg) {
  registerError.textContent = msg || '创建失败，请稍后再试。';
  registerError.hidden = false;
  registerForm.classList.remove('shake');
  void registerForm.offsetWidth;
  registerForm.classList.add('shake');
}

function clearRegisterError() {
  registerError.hidden = true;
  registerError.textContent = '';
  registerForm.classList.remove('shake');
}

function showRegister() {
  clearRegisterError();
  loginBody.hidden = true;
  registerBody.hidden = false;
  if (authModeLabel) authModeLabel.textContent = '练习注册';
  if (authModeEn) authModeEn.textContent = 'SIGN UP';
  document.title = '创建练习账号 · SingLens';
  // 手机上不强行把页面滚到输入框，先让用户看到“先建一个练习身份”。
  try { regName?.focus({ preventScroll: true }); } catch { regName?.focus(); }
}

function showLogin() {
  clearError();
  clearRegisterError();
  registerBody.hidden = true;
  loginBody.hidden = false;
  if (authModeLabel) authModeLabel.textContent = '练习登录';
  if (authModeEn) authModeEn.textContent = 'SIGN IN';
  document.title = 'AI 唱歌陪练 · 登录 · SingLens';
  username?.focus();
}

async function alreadyLoggedIn() {
  try {
    const r = await fetch('/api/auth/me', {
      credentials: 'same-origin', cache: 'no-store',
    });
    if (!r.ok) return false;
    const j = await r.json();
    return !!(j && j.ok && j.data && j.data.user);
  } catch {
    return false;
  }
}

pwToggle?.addEventListener('click', () => {
  const showing = password.type === 'text';
  password.type = showing ? 'password' : 'text';
  pwToggle.textContent = showing ? '显示' : '隐藏';
  pwToggle.setAttribute('aria-label', showing ? '显示密码' : '隐藏密码');
  pwToggle.setAttribute('aria-pressed', showing ? 'false' : 'true');
  password.focus();
});

toRegister?.addEventListener('click', showRegister);
toLogin?.addEventListener('click', showLogin);

async function checkRegisterAllowed() {
  if (!registerEntry) return;
  try {
    const r = await fetch('/api/auth/register', {
      credentials: 'same-origin', cache: 'no-store',
    });
    if (!r.ok) return;
    const j = await r.json();
    if (j && j.ok && j.data && j.data.allowed) registerEntry.hidden = false;
  } catch { /* 登录本身不受影响 */ }
}

registerForm?.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearRegisterError();
  const name = (regName.value || '').trim();
  const user = (regUser.value || '').trim();
  const pass = regPass.value || '';
  const confirm = regConfirm.value || '';
  if (!name) { showRegisterError('先给自己起一个练习名字。'); regName.focus(); return; }
  if (!user) { showRegisterError('再想一个账号。'); regUser.focus(); return; }
  if (pass.length < 10) { showRegisterError('密码至少 10 位。'); regPass.focus(); return; }
  if (pass !== confirm) { showRegisterError('两次输入的密码不一致。'); regConfirm.focus(); return; }
  registerBtn.disabled = true;
  registerBtn.setAttribute('aria-busy', 'true');
  const label = registerBtn.querySelector('span');
  if (label) label.textContent = '正在创建…';
  try {
    const r = await fetch('/api/auth/register', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, username: user, password: pass, confirm }),
    });
    let j = null;
    try { j = await r.json(); } catch { /* ignore */ }
    if (r.ok && j && j.ok) {
      if (label) label.textContent = '已创建';
      location.replace(safeNext());
      return;
    }
    const msg = j && j.error && j.error.message
      ? j.error.message
      : (r.status === 403 ? '注册只在本机开放。' : '创建失败，请换个账号再试。');
    showRegisterError(msg);
  } catch {
    showRegisterError('连不上服务。确认服务已经启动，再试一次。');
  } finally {
    registerBtn.disabled = false;
    registerBtn.removeAttribute('aria-busy');
    if (label && label.textContent === '正在创建…') label.textContent = '创建并开唱';
  }
});

const checkCaps = (e) => {
  if (!caps) return;
  const on = typeof e.getModifierState === 'function'
    && e.getModifierState('CapsLock');
  caps.hidden = !on;
};
password?.addEventListener('keydown', checkCaps);
password?.addEventListener('keyup', checkCaps);

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearError();
  const u = (username.value || '').trim();
  const p = password.value || '';
  if (!u) {
    showError('先填账号，才能找到你的练习记录。');
    username.focus();
    return;
  }
  if (!p) {
    showError('再填密码。');
    password.focus();
    return;
  }
  submit.disabled = true;
  submit.setAttribute('aria-busy', 'true');
  submit.querySelector('span').textContent = '正在进入…';
  try {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: u, password: p, remember: !!remember.checked,
      }),
    });
    let j = null;
    try { j = await r.json(); } catch { /* 非 JSON 也按失败处理 */ }
    if (r.ok && j && j.ok) {
      submit.querySelector('span').textContent = '已进入';
      location.replace(safeNext());
      return;
    }
    const msg = j && j.error && j.error.message
      ? j.error.message
      : (r.status === 429 ? '试得太多次了，10 分钟后再来。' : '账号或密码对不上，再试一次。');
    showError(msg);
    password.select();
  } catch {
    showError('连不上服务。确认服务已经启动，再试一次。');
  } finally {
    submit.disabled = false;
    submit.removeAttribute('aria-busy');
    if (!location.pathname.startsWith('/login')) return;
    const label = submit.querySelector('span');
    if (label && label.textContent === '正在进入…') label.textContent = '开始唱歌';
  }
});

(async () => {
  if (await alreadyLoggedIn()) {
    location.replace(safeNext());
    return;
  }
  checkRegisterAllowed();
  username?.focus();
})();
