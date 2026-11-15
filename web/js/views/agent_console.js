/**
 * Agent 控制台（T4）。
 *
 * 只做四件事：把问题发出去、把 SSE 里的工具调用一张张画出来、把答案排版、
 * 出错时说清楚。**不重新评分、不改报告、不落任何服务端状态。**
 *
 * 为什么用 fetch 读流而不是 EventSource：EventSource 只能 GET，而这条端点
 * 要带 JSON body；fetch + ReadableStream 是浏览器里唯一能干这件事的组合。
 */
import { getSingerId } from '../data/identity.js';
import { readSSE } from '../data/sse.js';

const log = document.getElementById('log');
const form = document.getElementById('form');
const input = document.getElementById('q');
const sendBtn = document.getElementById('send');
const sessEl = document.getElementById('sess');
const chipsEl = document.getElementById('chips');

const params = new URLSearchParams(location.search);
let uid = params.get('sess') || '';
if (!uid) {
  try { uid = sessionStorage.getItem('sl.sess') || ''; } catch { uid = ''; }
}

let busy = false;
let ctrl = null;

/**
 * 本次页面里的对话（只存在内存，不写 sessionStorage）。
 * 刷新即丢 —— 这是隐私设计不是 bug：聊天里有歌词与问题，不该在浏览器里留档。
 * 服务端同样不存；每次请求都靠这一份历史 + 服务端的抽取式摘要拼上下文。
 */
const history = [];
const HISTORY_LIMIT = 12;

/** 每个 run 的临时容器，done 之后不再往里塞东西。 */
let current = null;

const MODE_LABEL = {
  agent: 'Agent',
  agent_repair: 'Agent · 重写一次后通过',
  rule_fallback: '规则版',
};

const STOP_LABEL = {
  answered: '正常收口',
  empty_answer: '模型没有给出回答',
  budget_steps: '步数用完，强制收口',
  budget_tools: '调用次数用完，强制收口',
  duplicate_calls: '重复调用，强制收口',
  deadline: '超时，强制收口',
  verify_failed: '校验没过，退规则版',
  llm_error: '模型调用失败',
};

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function scrollToEnd() {
  log.scrollTop = log.scrollHeight;
}

function addQuestion(text) {
  log.appendChild(el('div', 'q', text));
  current = { tools: 0, box: el('div', 'run'), deltaEl: null };
  log.appendChild(current.box);
  current.thinking = el('div', 'thinking', '正在查这份报告');
  current.box.appendChild(current.thinking);
  scrollToEnd();
}

function addTool(data) {
  if (!current) return;
  if (current.thinking && current.thinking.isConnected) {
    current.thinking.remove();
    current.thinking = null;
  }
  const row = el('div', `step${data.ok === false ? ' bad' : ''}`);
  row.appendChild(el('span', 'dot'));
  row.appendChild(el('b', 'name', String(data.name || '工具')));
  const args = String(data.args || '').trim();
  if (args && args !== '{}') row.appendChild(el('span', 'args', args));
  current.box.appendChild(row);
  current.tools += 1;
  scrollToEnd();
}

/** 增量文本：边生成边显示；`done` 时用最终答案替换（防止工具轮的半截文本留下）。 */
function addDelta(text) {
  if (!current || !text) return;
  if (current.thinking && current.thinking.isConnected) {
    current.thinking.remove();
    current.thinking = null;
  }
  if (!current.deltaEl) {
    current.deltaEl = el('div', 'a delta');
    current.box.appendChild(current.deltaEl);
  }
  current.deltaEl.appendChild(document.createTextNode(String(text)));
  scrollToEnd();
}

/** 把 Agent 提议的偏好落库 —— 只有用户点「记住」才会走到这里。 */
async function savePreference(text, row) {
  if (!uid) return;
  try {
    const res = await fetch(`/api/session/${encodeURIComponent(uid)}/agent/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'save_preference',
        singer_id: getSingerId() || '',
        text,
      }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        detail = (body && (body.message || (body.error && body.error.message))) || detail;
      } catch { /* 非 JSON 保留状态码 */ }
      throw new Error(detail);
    }
    row.replaceWith(el('div', 'remember-msg', '已记住：之后作为画像背景，不是这次测量。'));
  } catch (e) {
    row.replaceWith(el('div', 'remember-msg bad', `没记住：${(e && e.message) || e}`));
  }
}

/** 工具只能提议（propose_preference），落库必须人点。 */
function addProposals(list) {
  if (!current || !Array.isArray(list) || !list.length) return;
  const box = el('div', 'remember');
  box.appendChild(el('div', 'remember-label', '要记住这条偏好吗？只在你点「记住」时写入画像。'));
  for (const text of list) {
    const row = el('div', 'remember-row');
    row.appendChild(el('span', 'remember-text', String(text)));
    const yes = el('button', 'chip', '记住');
    yes.type = 'button';
    const no = el('button', 'chip', '忽略');
    no.type = 'button';
    yes.onclick = () => {
      yes.disabled = true;
      no.disabled = true;
      savePreference(String(text), row);
    };
    no.onclick = () => row.replaceWith(el('div', 'remember-msg', '已忽略。'));
    row.appendChild(yes);
    row.appendChild(no);
    box.appendChild(row);
  }
  current.box.appendChild(box);
}

function addAnswer(data) {
  if (!current) return;
  if (current.thinking && current.thinking.isConnected) {
    current.thinking.remove();
    current.thinking = null;
  }
  if (current.deltaEl) {
    current.deltaEl.remove();
    current.deltaEl = null;
  }
  current.box.appendChild(el('div', 'a', String(data.answer || '（没有拿到回答）')));

  const mode = String(data.mode || '');
  const stop = String(data.stop_reason || '');
  const meta = el('div', 'meta');
  meta.appendChild(el('span', mode === 'rule_fallback' ? 'rule' : '',
    MODE_LABEL[mode] || mode || '未知'));
  if (stop) meta.appendChild(document.createTextNode(` · ${STOP_LABEL[stop] || stop}`));
  if (typeof data.steps === 'number' && data.steps > 0) {
    meta.appendChild(document.createTextNode(` · ${data.steps} 步`));
  }
  if (data.run_id) {
    meta.appendChild(document.createTextNode(' · '));
    const a = el('a', '', '查看 trace');
    a.href = `/api/agent/runs/${encodeURIComponent(data.run_id)}`;
    a.target = '_blank';
    a.rel = 'noopener';
    meta.appendChild(a);
  }
  current.box.appendChild(meta);
  if (data.verify_reason) {
    current.box.appendChild(el('div', 'meta dim',
      `退规则版原因：${String(data.verify_reason).slice(0, 120)}`));
  }
  addProposals(data.proposals);
  scrollToEnd();
}

function addError(text, login) {
  if (!current) return;
  if (current.thinking && current.thinking.isConnected) {
    current.thinking.remove();
    current.thinking = null;
  }
  if (current.deltaEl) {
    current.deltaEl.remove();
    current.deltaEl = null;
  }
  const box = el('div', 'err', text);
  if (login) {
    box.appendChild(document.createTextNode(' '));
    const a = el('a', '', '去登录');
    // 登录后回到这个控制台（保留 sess 参数），而不是回首页。
    a.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
    box.appendChild(a);
  }
  current.box.appendChild(box);
  scrollToEnd();
}

// 删掉旧的 readStream 实现，统一用 ../data/sse.js。

async function ask(text) {
  const message = String(text || '').trim();
  if (!message || busy) return;
  if (!uid) {
    addQuestion(message);
    addError('没有会话 id：先完成一次练习，或从报告页的「问教练」入口进来。');
    return;
  }

  busy = true;
  sendBtn.disabled = true;
  sendBtn.textContent = '查询中';
  input.value = '';
  const prior = history.slice(-HISTORY_LIMIT);
  history.push({ role: 'user', content: message });
  addQuestion(message);
  ctrl = new AbortController();

  try {
    const res = await fetch(`/api/session/${encodeURIComponent(uid)}/agent/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, singer_id: getSingerId() || '', history: prior }),
      signal: ctrl.signal,
    });

    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        detail = (body && (body.message || (body.detail && body.detail.message))) || detail;
      } catch { /* 非 JSON 就保留状态码 */ }
      addError(String(detail), res.status === 401 || res.status === 403);
      return;
    }
    if (!res.body) {
      addError('浏览器不支持流式读取。');
      return;
    }

    let final = null;
    await readSSE(res, (event, data) => {
      if (event === 'delta') addDelta(data.text);
      else if (event === 'tool') addTool(data);
      else if (event === 'done') { final = data; addAnswer(data); }
    });
    if (!final) {
      // 流结束但没有 done：服务端崩了/连接被中间层切断。
      // 不提示的话页面会停在"查询中"之后空着，用户以为已经答完了。
      addError('连接中断，没拿到回答。可以再问一次。');
      return;
    }
    if (final.answer) {
      history.push({ role: 'assistant', content: String(final.answer) });
      if (history.length > HISTORY_LIMIT) {
        history.splice(0, history.length - HISTORY_LIMIT);
      }
    }
  } catch (e) {
    if (e && e.name === 'AbortError') addError('已中断。');
    else addError(`请求失败：${(e && e.message) || e}`);
  } finally {
    busy = false;
    sendBtn.disabled = false;
    sendBtn.textContent = '发送';
    ctrl = null;
    if (current && current.thinking && current.thinking.isConnected) {
      current.thinking.remove();
      current.thinking = null;
    }
    scrollToEnd();
  }
}

form.addEventListener('submit', (e) => {
  e.preventDefault();
  ask(input.value);
});

// 离开页面时不留下一个还在跑的请求（服务端会随之取消 worker）
window.addEventListener('pagehide', () => {
  if (ctrl) ctrl.abort();
});

// 初始状态与推荐问题
if (uid) {
  sessEl.textContent = `会话 ${uid}`;
} else {
  sessEl.textContent = '没有会话';
}

const PRESETS = ['我最需要改哪里？', '节奏哪里不稳？', '接下来练什么？'];
for (const text of PRESETS) {
  const b = el('button', 'chip', text);
  b.type = 'button';
  b.onclick = () => ask(text);
  chipsEl.appendChild(b);
}

input.focus();
