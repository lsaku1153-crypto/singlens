/**
 * 麦克风采集 → 22.05kHz 单声道 int16 分块（G4-b，2026-09-23）。
 *
 * ━━━ 为什么单独一个模块 ━━━
 *
 * 采集属于"浏览器能力"，不属于"数据源"。放在 DataSource 里的话，
 * `?src=mock` 就没法复用（mock 源不连后端，但仍然要能开麦克风、走同一条 UI 路径）。
 * 所以这一层只做一件事：**把麦克风变成一串 46ms 的 int16 块**，
 * 交给调用方决定发给谁。
 *
 * ━━━ 为什么要重采样到 22.05kHz ━━━
 *
 * 服务端的音高检测固定在 22050 Hz（`SR`），发上去的块必须与它同率，
 * 否则时间轴（`t_ms = 样本数 / SR`）会整体错位，而且音高会算错。
 * 而浏览器的 `AudioContext` 采样率通常是 44100/48000（由硬件决定，
 * **请求 22050 不保证给到**）。所以这里自己降采样。
 *
 * ━━━ 为什么用 ScriptProcessorNode（已废弃的 API）━━━
 *
 * 现代做法是 `AudioWorklet`，但它要求把处理代码放在**独立文件**里
 * （`addModule(url)`），于是多一个要部署的文件、多一条加载失败路径，
 * 而且在 `file://` 或某些静态托管下会因 MIME/CORS 直接失败。
 * 本项目不引打包器，`ScriptProcessorNode` 虽然被标记废弃但**所有主流浏览器仍支持**，
 * 且能用普通函数写。
 *
 * ⚠️ 已知代价：`ScriptProcessorNode` 的回调跑在**主线程**，UI 卡顿会丢块。
 *    所以回调里只做"拷贝 + 转换"，不做任何绘制或网络以外的事。
 *    真要在生产环境跑，应该换 AudioWorklet —— 这条写进待办。
 *
 * ━━━ 时钟：为什么不用 `Date.now()` ━━━
 *
 * 时间轴由**服务端**按收到的样本数推（`t_ms = n_samples / SR`），
 * 前端只负责把块按顺序发上去。所以这里**不产生任何时间戳** ——
 * 一旦前端也维护一套时间，两套时钟必然漂移，而漂移在界面上表现为
 * "音高曲线比参考条慢半拍"，很难查。
 */

export const TARGET_SR = 22050;
/** 46ms 一块 → 服务端约 21.7 帧/秒（契约要求 pitch 帧 ~20Hz）。 */
export const CHUNK_MS = 46;

/** getDisplayMedia / getUserMedia 的失败 → 契约里的业务错误码。 */
export function micErrorCode(err) {
  const n = (err && err.name) || '';
  if (n === 'NotAllowedError' || n === 'SecurityError') return 'MIC_DENIED';
  if (n === 'NotFoundError' || n === 'DevicesNotFoundError') return 'MIC_DENIED';
  return 'MIC_DENIED';
}

export function micErrorMessage(err) {
  const n = (err && err.name) || '';
  if (n === 'NotAllowedError' || n === 'SecurityError') {
    return '浏览器拒绝了麦克风权限。点地址栏那个麦克风图标允许一下，再点一次「开始」。';
  }
  if (n === 'NotFoundError' || n === 'DevicesNotFoundError') {
    return '没找到麦克风设备。插一个耳机麦克风，或者在系统设置里检查输入设备。';
  }
  if (n === 'NotReadableError' || n === 'TrackStartError') {
    return '麦克风被别的程序占着。关掉会议软件/录音软件再试。';
  }
  return `打不开麦克风：${(err && err.message) || err}`;
}

/**
 * 开一次采集。
 *
 * 返回一个句柄：
 *   onChunk(fn)  fn(int16ArrayBuffer) —— 每 46ms 一次
 *   start()      真正开始回调（在此之前不会触发 onChunk）
 *   stop()       停采集、释放麦克风与 AudioContext
 *   state()      'idle' | 'running' | 'stopped'
 *
 * ⚠️ 必须调 `stop()`。不释放的话，浏览器标签页上会一直挂着录音红点，
 *    而用户完全不知道还在录 —— 这是隐私层面的问题，不是性能问题。
 */
/**
 * 列出可用的麦克风输入设备（2026-10-01）。
 *
 * ⚠️ 浏览器的隐私规则：**没拿到过麦克风权限之前，`label` 是空字符串**
 *    （`enumerateDevices` 只给 deviceId，不给名字）。所以这里：
 *      · 没权限时如实返回空 label，由界面显示「设备 1 / 设备 2」而不是编一个名字；
 *      · 调用方在用户**已经开过一次麦**之后再列，才拿得到真名字。
 *    这不是可以绕过的，也不该绕 —— 谎报设备名比不显示更坏。
 */
export async function listMics() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return [];
  const all = await navigator.mediaDevices.enumerateDevices();
  return all.filter((d) => d.kind === 'audioinput')
            .map((d, i) => ({ deviceId: d.deviceId,
                              label: d.label || `设备 ${i + 1}`,
                              hasLabel: !!d.label }));
}

export async function openMic({ chunkMs = CHUNK_MS, deviceId = '' } = {}) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw Object.assign(new Error('这个浏览器不支持麦克风采集'), { name: 'NotFoundError' });
  }
  // 关掉浏览器自带的降噪/自动增益：它们会**改变音量包络**，
  // 而力度起伏（dynamic_range）正是我们要测的六个指标之一。
  // 实测影响：开 AGC 时"一直大声唱"和"先小后大"的包络会被拉平。
  const audio = {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
    channelCount: 1,
  };
  /* ★ 指定设备（2026-10-01）：设置页原先那两个按钮（默认设备/外接麦克风）
     是死的，`deviceId` 从来没有传到过这里。
     ⚠️ 传 `deviceId` 时必须用 `exact` —— 用 `ideal` 的话浏览器**可以忽略它**
        然后静默用默认设备，于是"我明明选了外接麦，录进来还是内置麦"，
        而界面上看不出来（这正是本项目最忌讳的静默失效）。
        `exact` 找不到设备会直接抛 `OverconstrainedError`，让它响。
     ⚠️ 空字符串**不能**传 `exact: ''`（那不是"默认设备"，是"一个空 id"），
        所以不给设备时走原来的分支（约束里不带 deviceId = 系统默认）。 */
  if (deviceId) audio.deviceId = { exact: deviceId };
  const stream = await navigator.mediaDevices.getUserMedia({ audio });

  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  // 硬件采样率由系统决定，请求 22050 不保证给到 ⇒ 拿到实际值再自己降采样
  const srcSr = ctx.sampleRate;
  const src = ctx.createMediaStreamSource(stream);

  const inPerChunk = Math.max(256, Math.round(srcSr * chunkMs / 1000));
  const node = ctx.createScriptProcessor(1024, 1, 1);

  const cbs = [];
  const ratio = srcSr / TARGET_SR;
  const outLen = Math.max(1, Math.floor(inPerChunk / ratio));
  let acc = new Float32Array(0);
  let state = 'idle';

  node.onaudioprocess = (ev) => {
    if (state !== 'running') return;
    const inp = ev.inputBuffer.getChannelData(0);
    // 累积到够一块再发：块太小会让服务端帧率不稳、太大则延迟高
    const merged = new Float32Array(acc.length + inp.length);
    merged.set(acc, 0);
    merged.set(inp, acc.length);
    let off = 0;
    while (merged.length - off >= inPerChunk) {
      const slice = merged.subarray(off, off + inPerChunk);
      off += inPerChunk;
      cbs.forEach((fn) => fn(toInt16Mono(slice, ratio, outLen)));
    }
    acc = merged.slice(off);
  };

  // ⚠️ ScriptProcessorNode 必须连到 destination 才会被调用，
  //    但**不能真的把麦克风播出去**（那是啸叫）——
  //    所以串一个静音增益节点，输出全 0。
  const mute = ctx.createGain();
  mute.gain.value = 0;
  src.connect(node);
  node.connect(mute);
  mute.connect(ctx.destination);

  return {
    srcSampleRate: srcSr,
    onChunk(fn) { cbs.push(fn); },
    async start() {
      if (ctx.state === 'suspended') await ctx.resume();
      state = 'running';
    },
    stop() {
      state = 'stopped';
      node.onaudioprocess = null;
      try { node.disconnect(); } catch { /* 已断开 */ }
      try { mute.disconnect(); } catch { /* 已断开 */ }
      try { src.disconnect(); } catch { /* 已断开 */ }
      stream.getTracks().forEach((t) => t.stop());
      // close() 是异步的，但不必等 —— 不 close 会泄漏一个 AudioContext，
      // 浏览器对同时存在的 context 数量有上限（Chrome 是 6 个左右）。
      try { ctx.close(); } catch { /* 已关 */ }
    },
    state() { return state; },
    /** 实测到的输入采样率 —— 与 22050 不同时要在界面上说明（见 s2 的提示）。 */
    sampleRateMismatch() { return Math.abs(srcSr - TARGET_SR) > 1; },
  };
}

/**
 * Float32 [-1,1] → 线性降采样 → Int16。
 *
 * 为什么不在浏览器里做"高质量重采样"：音高检测关心的是**基频**，
 * 而线性降采样会把高频镜像折回到低频（混叠）。但 22050 的奈奎斯特频率是
 * 11kHz，人声基频最高也就 1kHz 左右 —— 混叠主要落在 5–11kHz 的泛音区，
 * 对基频检测影响很小。而做正经抗混叠滤波要多写几十行、每次回调都跑一遍，
 * 在主线程上是实打实的开销。
 * ⚠️ 代价要如实记着：**如果以后要把上传音频拿去算音色/明亮度（brightness），
 *    这个降采样就不够用了** —— 那些指标看的就是高频泛音。
 *    目前实时链路只算音准/节奏/力度，够用；精算链路走整段音频，不受影响。
 */
function toInt16Mono(f32, ratio, outLen) {
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const p = i * ratio;
    const i0 = Math.floor(p);
    const i1 = Math.min(f32.length - 1, i0 + 1);
    const v = f32[i0] + (f32[i1] - f32[i0]) * (p - i0);   // 线性插值
    const c = Math.max(-1, Math.min(1, v));
    out[i] = c < 0 ? c * 0x8000 : c * 0x7fff;
  }
  return out.buffer;
}
