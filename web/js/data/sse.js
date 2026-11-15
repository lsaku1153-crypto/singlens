/**
 * SSE 读取（共用）：按空行分事件，`data:` 可能多行。
 *
 * 为什么单独一份：Agent 控制台与报告页追问都走同一条流，
 * 解析器写两遍迟早不一致（一个处理 `\r\n`、一个不处理之类）。
 */

/** 从 fetch 的 Response 读事件；每个事件调 onEvent(event, data)。 */
export async function readSSE(res, onEvent) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    let cut;
    while ((cut = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      let event = 'message';
      const dataLines = [];
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
      }
      if (!dataLines.length) continue;
      let data;
      try { data = JSON.parse(dataLines.join('\n')); } catch { continue; }
      onEvent(event, data);
    }
  }
}

/**
 * POST 一条 JSON 并读 SSE；返回 `done` 事件的 data（没有 done 就是 null）。
 * 非 2xx 抛 Error，带 `status`，调用方自己决定跳登录还是显示"去登录"。
 */
export async function postSSE(url, body, onEvent, signal) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
    credentials: 'same-origin',
    signal,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const b = await res.json();
      message = (b && (b.message || (b.error && b.error.message))) || message;
    } catch { /* 非 JSON 保留状态码 */ }
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  if (!res.body) {
    const err = new Error('浏览器不支持流式读取。');
    err.status = 0;
    throw err;
  }
  let final = null;
  await readSSE(res, (event, data) => {
    if (event === 'done') final = data;
    if (onEvent) onEvent(event, data);
  });
  return final;
}
