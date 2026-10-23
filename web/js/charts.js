/**
 * 画图函数 —— **手写文件**（2026-09-23 起，见下）。
 *
 * ⚠️ 这个文件曾经是 `tools/extract_charts.py` 的生成物，**现在不是了**。
 *    为什么改（这是一次真实的事故，不是洁癖）：
 *      生成器只抽原型里的 6 个函数（fit/drawRadar/drawEvidence/drawCmp/
 *      drawProg/drawScatter），而 `drawLive` / `drawRoll` / `rmsRange` /
 *      `hzToMidi` 是**手写**进来的（实时管线要按服务器时间轴滚动，
 *      原型那四行是 `requestAnimationFrame` 演示循环，搬不过来）。
 *      生成器的 docstring 还写着"已手写进 charts.js"—— 但它是**整文件重写**：
 *      谁照着文档跑一次 `extract_charts.py`，这 366 行就会**静默消失**，
 *      而 `extract_charts.py` 自己会打印"✅ 已写"。
 *      （同一天还踩过 `router.js` 被 `split_v7.py` 覆盖掉 unmount —— 同一类。）
 *
 *    现在的分工：
 *      · `docs/reference/charts_proto.js`  ← 仍然自动生成，**只作原型参照**
 *        （想看"原型的画法是什么样"或做视觉对照时用；它带原型示例数据，
 *          **不允许**用在产品页面上）
 *        ★ 2026-09-24 从 `web/js/` 移出：`/js` 是 StaticFiles 挂载，
 *          放在那里等于把原型的示例数据**公开可取**（实测 HTTP 200）。
 *          它和本文件**函数同名**（drawRadar/drawEvidence/…），
 *          误引一次，页面就会画出一条合成正弦当作用户的音量曲线。
 *      · 本文件 ← 手写。真实数据通路、null 语义、空态都在这里。
 *
 * ⚠️ **Canvas 不认 CSS 变量**（§1.6 踩过的坑）：
 *    `g.strokeStyle='var(--cyan)'` 是非法颜色值，浏览器**静默忽略**、
 *    保留默认黑色 —— 旁边 DOM 用 var() 正常，所以问题很隐蔽。
 *    所以这里一律用 `C` 里的具体色值。
 *
 * ★ 数据为空时的统一约定（2026-09-23）：
 *    **不画**稿子里的示例曲线。原型用 `Math.sin(...)` 合成假波形占位，
 *    在真系统里那等于**伪造用户的音量/颤音/节奏曲线** ——
 *    用户没有任何办法看出它是假的。取不到数据就隐藏画布 / 只画空网格。
 */

export const C ={acc:'#cf3f2a',accInk:'#8c1f13',warn:'#8f5a0c',good:'#0b6b57',ink:'#0c0d10',ink2:'#3a3b40',
         ink3:'#66655d',ink4:'#928f85',line:'#e0ddd3',line2:'#c3bfb3',ref:'#c9c5ba'};

export function fit(cv){
  const r=window.devicePixelRatio||1;
  // ⚠️ 不显示时 clientWidth/Height 是 0，会把画布设成 1×1（切回来一片空白）。
  //    兜底：退回 attribute 上已有的尺寸。
  const w=Math.max(1,Math.round(cv.clientWidth||cv.width||300));
  const h=Math.max(1,Math.round(cv.clientHeight||cv.height||150));
  if(cv.width!==Math.round(w*r)||cv.height!==Math.round(h*r)){
    cv.width=Math.round(w*r); cv.height=Math.round(h*r);
  }
  const g=cv.getContext('2d');
  g.setTransform(r,0,0,r,0,0);
  g.clearRect(0,0,w,h);
  return {g,w,h};
}

/**
 * 六维雷达图。
 *
 * ★ `vals` 里允许出现 **null**（= 本次未评估，契约 §3.2）。
 *   第一版把 null 当 0 画：顶点塌到圆心，标签还被染成红色
 *   （`vals[i]<0.65` 对 null 也成立）—— 用户看到的是"我颤音 0 分"。
 *   现在的处理：
 *     · 有值的轴照常连成多边形（null 处**断开**，不跨过去连一条假边）
 *     · 没值的轴只画轴线，标签写「未评估」并置灰，**不上红**
 *
 * ★ 完全没有数据时**不画**稿子里的示例值 `[0.62,…]`。
 *   那是原型用来占位的演示数据；真系统上画出它，等于凭空给了六个数。
 */
export function drawRadar(data){
  const cv=document.getElementById('radar'); if(!cv) return;
  const {g,w,h}=fit(cv);
  const cx=w/2, cy=h/2+2, R=Math.min(w,h)/2-44;
  const labels=['起伏','句内推进','节奏弹性','尾音','起头方式','音色'];
  // data = {vals:[(0..1)|null ×6]}；不传 = 没有数据 → 只画空网格
  const vals=(data&&Array.isArray(data.vals)&&data.vals.length===labels.length)
    ? data.vals.map(v=>(typeof v==='number'&&isFinite(v))?Math.max(0,Math.min(1,v)):null)
    : labels.map(()=>null);
  const n=labels.length;
  const ok=i=>vals[i]!==null;

  g.strokeStyle=C.line; g.lineWidth=1;
  for(let r=1;r<=4;r++){
    g.beginPath();
    for(let i=0;i<=n;i++){
      const a=-Math.PI/2+i*2*Math.PI/n;
      const x=cx+Math.cos(a)*R*r/4, y=cy+Math.sin(a)*R*r/4;
      i?g.lineTo(x,y):g.moveTo(x,y);
    }
    g.stroke();
  }
  g.strokeStyle=C.line2;
  for(let i=0;i<n;i++){
    const a=-Math.PI/2+i*2*Math.PI/n;
    g.beginPath(); g.moveTo(cx,cy); g.lineTo(cx+Math.cos(a)*R, cy+Math.sin(a)*R); g.stroke();
  }

  // 多边形：**逐段**连（遇到 null 就断开重起），不跨过未评估的轴。
  // 若只有 1 个评估项，画不出面，就只打点。
  const pt=i=>{
    const a=-Math.PI/2+i*2*Math.PI/n;
    return [cx+Math.cos(a)*R*vals[i], cy+Math.sin(a)*R*vals[i]];
  };
  const runs=[]; let cur=[];
  for(let i=0;i<n;i++){ if(ok(i)) cur.push(i); else if(cur.length){ runs.push(cur); cur=[]; } }
  if(cur.length) runs.push(cur);
  runs.forEach(run=>{
    if(run.length<2) return;
    g.beginPath();
    run.forEach((i,k)=>{ const [x,y]=pt(i); k?g.lineTo(x,y):g.moveTo(x,y); });
    g.strokeStyle=C.acc; g.lineWidth=1.8; g.stroke();
    if(run.length>=3){
      g.closePath(); g.fillStyle='#cf3f2a26'; g.fill();
    }
  });
  for(let i=0;i<n;i++){
    if(!ok(i)) continue;
    const [x,y]=pt(i);
    g.fillStyle=C.acc; g.fillRect(x-3,y-3,6,6);
  }

  g.font='10.5px "Plex Mono","Noto Sans SC",monospace'; g.textAlign='center'; g.textBaseline='middle';
  labels.forEach((L,i)=>{
    const a=-Math.PI/2+i*2*Math.PI/n;
    // 未评估：灰 + 「未评估」后缀。**不是红色** —— 没测过不等于差。
    const txt=ok(i)?L:`${L}·未评估`;
    g.fillStyle=!ok(i)?C.ink4:(vals[i]<0.65?C.accInk:C.ink2);
    g.fillText(txt, cx+Math.cos(a)*(R+18), cy+Math.sin(a)*(R+18));
  });
}

/**
 * 证据曲线（`canvas.ev[data-k]`）。
 *
 * ★ 三种曲线各自**只认自己那段数据**；拿不到就把 canvas 藏起来。
 *   第一版在没有数据时画的是**演示波形**（`0.5+0.05*Math.sin(x*0.085)`、
 *   一个写死的高斯峰、一条合成正弦）—— 在原型里这是占位，在真系统里
 *   这是**伪造用户的音量/颤音/节奏曲线**。用户没有任何办法看出它是假的。
 *
 * ★ RMS 的纵轴按本次的 p5/p95 归一化。第一版用写死的 `(v-0.32)/0.36` ——
 *   那是按某一份素材调出来的常数，换一份录音曲线就贴着底边或冲出画布。
 */
export function drawEvidence(data){
  document.querySelectorAll('canvas.ev').forEach(cv=>{
    const k=cv.dataset.k;
    const has={
      rms:!!(data&&data.rms_envelope&&data.rms_envelope.v&&data.rms_envelope.v.length>=2),
      vib:!!(data&&data.vibrato_spectrum&&data.vibrato_spectrum.v&&data.vibrato_spectrum.v.length>=2),
      rubato:!!(data&&data.onset_dev_trend&&data.onset_dev_trend.dev&&data.onset_dev_trend.dev.length>=2),
    }[k];
    // 没有数据 ⇒ 藏掉整块 canvas，不画假线。
    cv.hidden=!has;
    if(!has) return;
    if(!cv.clientWidth) return;      // 关着的 <details>：展开时会重画（见 bindings.js）
    cv.hidden=false;
    const {g,w,h}=fit(cv);
    g.strokeStyle=C.line; g.lineWidth=1;
    for(let i=1;i<4;i++){const y=h*i/4;g.beginPath();g.moveTo(0,y);g.lineTo(w,y);g.stroke();}

    if(k==='rms'){
      const env=data.rms_envelope, v=env.v;
      // 纵轴按本次的 p5/p95 归一化（留 8% 余量），不写死常数。
      const lo=(typeof env.p5==='number')?env.p5:Math.min(...v);
      const hi=(typeof env.p95==='number')?env.p95:Math.max(...v);
      const span=(hi-lo)||1, y0=lo-span*0.08, y1=hi+span*0.08;
      const Y=x=>h-(x-y0)/(y1-y0)*h;
      g.strokeStyle=C.acc; g.lineWidth=2; g.beginPath();
      for(let x=0;x<=w;x++){
        const i=Math.min(v.length-1,Math.round(x/w*(v.length-1)));
        const y=Y(v[i]);
        x?g.lineTo(x,y):g.moveTo(x,y);
      }
      g.stroke();
      [[hi,'P95',C.ink3],[lo,'P5',C.ink4]].forEach(([val,L,c])=>{
        const y=Y(val);
        g.strokeStyle=c; g.setLineDash([5,4]); g.lineWidth=1;
        g.beginPath(); g.moveTo(0,y); g.lineTo(w,y); g.stroke(); g.setLineDash([]);
        g.fillStyle=c; g.font='9px "Plex Mono",Consolas,monospace'; g.fillText(L, 8, y-5);
      });
      // ⚠️ 这里原来写死一句「音量范围很窄 →」。那是**结论**，不是图注 ——
      //    结论归 comments_by_metric 说，图只负责把这一遍的量画出来。
      g.fillStyle=C.ink4; g.font='9px "Plex Mono",Consolas,monospace';
      g.fillText(`${(hi-lo).toFixed(1)} dB`, w-56, 12);
    }
    if(k==='vib'){
      const sp=data.vibrato_spectrum, v=sp.v, f=sp.f||[];
      const fmax=f.length?f[f.length-1]:14;
      const vmax=Math.max(...v)||1;
      const bw=w/v.length;
      v.forEach((val,i)=>{
        const bh=val/vmax*0.88*h;
        g.fillStyle='#cf3f2a33';
        g.fillRect(i*bw, h-bh, Math.max(1,bw-1.8), bh);
      });
      const peakHz=(typeof sp.peak_hz==='number')?sp.peak_hz:null;
      if(peakHz!==null){
        const px=peakHz/fmax*w;
        g.strokeStyle=C.acc; g.setLineDash([4,3]); g.lineWidth=1.2;
        g.beginPath(); g.moveTo(px,0); g.lineTo(px,h); g.stroke(); g.setLineDash([]);
        g.fillStyle=C.accInk; g.font='600 10px "Plex Mono","Noto Sans SC",monospace';
        g.fillText('每秒抖约 '+peakHz.toFixed(1)+' 次', Math.min(px+7,w-96), 14);
      }
      g.fillStyle=C.ink4; g.font='9px "Plex Mono",Consolas,monospace';
      g.fillText('SLOW', 6, h-6); g.textAlign='right';
      g.fillText('FAST', w-6, h-6); g.textAlign='left';
    }
    if(k==='rubato'){
      const od=data.onset_dev_trend;
      const dev=od.dev, res=od.residual||[], N=dev.length;
      const all=dev.concat(res).filter(x=>typeof x==='number');
      const amax=Math.max(1,...all.map(Math.abs));
      const mid=h/2, sc=(h/2)/amax;
      g.strokeStyle=C.acc; g.lineWidth=2; g.beginPath();
      const X=i=>10+i*((w-20)/(N-1));
      dev.forEach((t,i)=>{ const y=mid-t*sc; i?g.lineTo(X(i),y):g.moveTo(X(i),y); });
      g.stroke();
      res.forEach((r,i)=>{
        const y=mid-((dev[i]||0)+(r||0))*sc;
        g.fillStyle=C.ink4; g.fillRect(X(i)-1.8, y-1.8, 3.6, 3.6);
      });
      g.fillStyle=C.accInk; g.font='600 10px "Plex Mono","Noto Sans SC",monospace';
      g.fillText('你自由发挥的走势（不扣分）', 10, 14);
      g.fillStyle=C.ink4; g.font='10px "Plex Mono","Noto Sans SC",monospace';
      g.fillText('灰点 = 真正的不稳', 10, h-6);
    }
  });
}

export function drawCmp(id,mode,data){
  const cv=document.getElementById(id); if(!cv) return;
  /* ★ 没有真实包络就**不画**（2026-09-23）。
     原来这里退回"解析正弦波"（`mode==='flat'` 画平线、否则画拱形）——
     那是原型的示意图，**与用户唱成什么样毫无关系**。
     而 s4 这一屏正是要证明"真的变好了"，画一条合成的拱形是最不能接受的一种假。
     现在：拿不到数据就藏掉画布，由页面上的文字说明"还不能对比"。 */
  const env = data && data.rms_envelope && data.rms_envelope.v
            && data.rms_envelope.v.length >= 2 ? data.rms_envelope.v : null;
  if(!env){ cv.hidden=true; return; }
  cv.hidden=false;
  const {g,w,h}=fit(cv);
  g.strokeStyle=C.line; g.lineWidth=1;
  for(let i=1;i<4;i++){const y=h*i/4;g.beginPath();g.moveTo(0,y);g.lineTo(w,y);g.stroke();}

  /* ★ 数据用 `data.rms_envelope`（响度包络，与 s3 的证据图同一来源）。
     为什么用响度而不是音高：
       本屏的叙事是"音量几乎是一条直线 → 出现先渐强后渐弱的形状"，
       而音量正是**表现力**维度的直接证据；音高要在 s3 看。

     ⚠️ 单位是 **dB，而且是负数**（实测 −20 ~ −9 dB）。
        踩过的坑：第一版直接按"0~1 的包络"去画（`y = h - v*h`），
        负值被 clamp 成 0 ⇒ **曲线整条贴在画布底边**，看着像一条直线。
        截图才发现（探针只看 data-b 的数值，看不见 canvas 里的像素）。
        正确做法：用这一份数据自己的 p5/p95 归一化 ——
        画的是"这次演唱内部的相对起伏"，两份各自归一化正好可比。 */
  const lo = (data.rms_envelope.p5 ?? Math.min(...env));
  const hi = (data.rms_envelope.p95 ?? Math.max(...env));
  const span = (hi - lo) || 1;
  const norm = (v) => Math.max(0.06, Math.min(0.94, 0.08 + 0.84 * ((v - lo) / span)));

  g.lineWidth=2.4; g.lineJoin='round'; g.beginPath();
  for(let x=0;x<=w;x++){
    const t=x/w;
    const v = norm(env[Math.min(env.length-1, Math.round(t*(env.length-1)))]);
    const y=h-Math.max(0,Math.min(1,v))*h;
    x?g.lineTo(x,y):g.moveTo(x,y);
  }
  g.strokeStyle = mode==='flat' ? C.warn : C.acc;
  g.stroke();
  if(mode!=='flat'){
    // 末点标记跟着**真实**末值走，不再钉在合成拱形的顶点上
    const ex=w-4, ey=h-norm(env[env.length-1])*h;
    g.fillStyle=C.acc; g.fillRect(ex-8,ey-8,6,6);
  }
}

/**
 * 从一份报告里取出"这段演唱的音量起伏幅度"（dB，p95 − p5）。
 * s4 的「音量有变化了吗」那一行用它 —— 它必须与图上画的是**同一个量**，
 * 否则又会出现"图上一条平线、旁边写着起伏 9.6 dB"的自相矛盾。
 */
export function rmsRange(report){
  const e = report && report.rms_envelope;
  if(!e || !e.v || !e.v.length) return null;
  const lo = (e.p5 ?? Math.min(...e.v));
  const hi = (e.p95 ?? Math.max(...e.v));
  return Math.round((hi - lo) * 10) / 10;
}

export function drawProg(data){
  const cv=document.getElementById('prog'); if(!cv) return;
  // data = {series:{total:[],expr:[],pitch:[]}}；任一为 null 表示那次未评估，
  // 图上按『断线』处理（不能补 0 —— 契约 §3.2）
  const sp=(data&&data.series)||{};
  /* ★ 没有序列就**不画**（2026-09-23）。
     原来这里退回 12 个写死的分数（61…78 / 34…71 / 79…86）——
     那是原型用来占位的"进步故事"。在没有练习记录的用户屏幕上画出它，
     等于凭空告诉他"你从 61 分进步到了 78 分"。 */
  const has=(a)=>Array.isArray(a)&&a.some(v=>typeof v==='number');
  if(!has(sp.total)&&!has(sp.expr)&&!has(sp.pitch)){ cv.hidden=true; return; }
  cv.hidden=false;

  const {g,w,h}=fit(cv);
  const padL=34, padR=14, padT=18, padB=26;
  const iw=w-padL-padR, ih=h-padT-padB;
  g.strokeStyle=C.line; g.lineWidth=1;
  for(let i=0;i<=4;i++){
    const y=padT+ih*i/4;
    g.beginPath(); g.moveTo(padL,y); g.lineTo(w-padR,y); g.stroke();
    g.fillStyle=C.ink4; g.font='9px "Plex Mono",Consolas,monospace';
    g.fillText(String(100-i*25), 6, y+3);
  }
  /* ⚠️ X 的步长必须按**真实点数**走。
     原型写死 `N=12, X=i=>padL+iw*i/(N-1)` —— 只练了 3 次的人，
     曲线会画在画布最左边 2/11 的位置上，右边一大片空白，
     看着像"数据丢了"。 */
  const total=has(sp.total)?sp.total:[];
  const emo  =has(sp.expr)?sp.expr:[];
  const pitch=has(sp.pitch)?sp.pitch:[];
  const NPT=Math.max(total.length,emo.length,pitch.length);
  const X=i=>(NPT>1?padL+iw*i/(NPT-1):padL+iw/2), Y=v=>padT+ih*(1-v/100);
  /* ★ 2026-10-20：`dotsOnly` = 不同参考曲目混在一起时**只打点、不连线**。
     同一屏里"小星星 60 分"和"自建模板 30 分"本来不可比；连通起来会画出一条
     假的"我在退步"。dot 模式至少能看到分布，且不会暗示趋势。 */
  const dotsOnly = !!(data && data.dotsOnly);
  /* ★ 折线必须在 null 处【断开】（2026-09-23 修）。
     原来是把数组直接 lineTo 过去 —— 而 `Y(null)` 是 NaN，
     canvas 收到 NaN 坐标会**静默跳过该线段**，看起来"没画错"，
     但实际效果是"跳过那一次、把前后两次直接连起来"：
     用户看到一条平滑上升的线，而中间那次其实是【没评估】。
     这正是契约 §3.2 要防的误读（"没测"被读成"考了 0 分"或"进步了"）。
     正确做法：遇到 null 就 moveTo 开启新的一段。 */
  const draw=(arr,color,width)=>{
    g.strokeStyle=color; g.lineWidth=width; g.lineJoin='round'; g.lineCap='round';
    let pen=false;
    g.beginPath();
    arr.forEach((v,i)=>{
      if(v===null||v===undefined||Number.isNaN(v)){ pen=false; return; }
      const x=X(i), y=Y(v);
      if(pen) g.lineTo(x,y); else { g.moveTo(x,y); pen=true; }
    });
    if(!dotsOnly) g.stroke();
  };
  draw(pitch,'#c9cfcc',2);
  draw(total,'#0c0d10',2.4);
  draw(emo,'#c93a2a',2.4);
  emo.forEach((v,i)=>{
    if(v===null||v===undefined||Number.isNaN(v)) return;   // null 不画点
    g.fillStyle=C.acc; g.fillRect(X(i)-2.4,Y(v)-2.4,4.8,4.8);
  });
  if(dotsOnly){
    // 全部模式下三组都打点：颜色与图例一致，大小略小以免糊成一片
    const dot=(arr,color,r)=>{
      g.fillStyle=color;
      arr.forEach((v,i)=>{
        if(v===null||v===undefined||Number.isNaN(v)) return;
        g.fillRect(X(i)-r,Y(v)-r,r*2,r*2);
      });
    };
    dot(pitch,'#c9cfcc',1.8);
    dot(total,'#0c0d10',2.2);
    dot(emo,C.acc,2.0);
  }
  const lastTotal=[...total].reverse().find(v=>v!==null&&v!==undefined&&!Number.isNaN(v));
  if(lastTotal!==undefined){
    const li=total.lastIndexOf(lastTotal);
    g.fillStyle='#0c0d10'; g.fillRect(X(li)-4,Y(lastTotal)-4,8,8);
  }
  /* ★ 2026-10-20：X 轴刻度。
     原来写死 `W1 … W{len}` —— 而 labels 是**日期**、23 次练习集中在两天里，
     写着 W1/W23 等于告诉用户"你练了 23 周"。现在：同一天用 HH:MM，
     否则用 MM-DD；只标首/中/末三个，避免文字挤成一排。 */
  const xLabels = (data && Array.isArray(data.labels)) ? data.labels : [];
  g.fillStyle=C.ink3; g.font='9px "Plex Mono",Consolas,monospace';
  if(xLabels.length===NPT && NPT>0){
    const idxs = NPT===1 ? [0]
      : (NPT===2 ? [0,1] : [0,Math.floor((NPT-1)/2),NPT-1]);
    idxs.forEach((i,k)=>{
      const txt=String(xLabels[i]||'');
      if(!txt) return;
      if(k===0){ g.textAlign='left'; g.fillText(txt,padL,h-8); }
      else if(k===idxs.length-1){ g.textAlign='right'; g.fillText(txt,w-padR,h-8); }
      else { g.textAlign='center'; g.fillText(txt,X(i),h-8); }
    });
    g.textAlign='left';
  } else {
    g.fillText('W1', padL, h-8);
    g.textAlign='right'; g.fillText('W'+Math.max(1,total.length), w-padR, h-8);
    g.textAlign='left';
  }

  /* ★ 换参考处画一条竖直虚线（2026-09-23）。
     为什么必须有：不同参考曲目的分数**不可比** —— 模式 A《小星星》≈64、
     模式 B 自建模板 ≈33，把两个数连成一条上升/下降的线是**假结论**。
     折线本身不能在这里断（那会看不出时间顺序），所以改为**标出来**：
     竖虚线 + 一个 REF 记号，让"这里换了首歌"一眼可见。
     ★ 2026-10-20：只在**变化不多于 2 处**时逐条标；再多就只画线、
     不再写"换曲目"（实测 23 次练习里 20 多次各属不同模板，
     标签会叠成一排乱码）。文案由图注说明"虚线=换过参考"。 */
  const refs = (data && data.reference_id) || [];
  let changes = 0;
  for(let i=1;i<refs.length;i++){
    if((refs[i]||null) === (refs[i-1]||null)) continue;
    changes++;
    const x=X(i);
    g.strokeStyle=C.warn; g.setLineDash([3,3]); g.lineWidth=1;
    g.beginPath(); g.moveTo(x,padT); g.lineTo(x,padT+ih); g.stroke(); g.setLineDash([]);
    if(changes<=2 && i<refs.length){
      g.fillStyle=C.accInk; g.font='9px "Plex Mono",Consolas,monospace';
      g.textAlign='center'; g.fillText('换曲目', x, padT-5); g.textAlign='left';
    }
  }
  if(changes>2){
    g.fillStyle=C.ink3; g.font='9px "Plex Mono","Noto Sans SC",monospace';
    g.fillText('虚线 = 换过参考，分数不可直接比', padL, padT-6);
  }
}

export function drawScatter(data){
  const cv=document.getElementById('scatter'); if(!cv) return;
  const {g,w,h}=fit(cv);
  const padL=36, padR=16, padT=16, padB=28;
  const iw=w-padL-padR, ih=h-padT-padB;
  g.strokeStyle=C.line; g.lineWidth=1;
  for(let i=0;i<=4;i++){
    const y=padT+ih*i/4;
    g.beginPath(); g.moveTo(padL,y); g.lineTo(w-padR,y); g.stroke();
    g.fillStyle=C.ink4; g.font='9px "Plex Mono",Consolas,monospace';
    g.fillText(String(5-i), 20, y+3);
  }
  g.strokeStyle=C.line2; g.setLineDash([5,4]); g.lineWidth=1.4;
  g.beginPath(); g.moveTo(padL,h-padB); g.lineTo(w-padR,padT); g.stroke(); g.setLineDash([]);
  g.fillStyle=C.ink4; g.font='10px "Plex Mono","Noto Sans SC",monospace';
  g.fillText('理想：完全一致', w-padR-92, padT+12);
  /* ★ "没有数据" 与 "没有传数据" 必须**都**留白（2026-09-23 两轮修完）。
     第一轮（截图时发现）：原来写的是 `data.scatter.length ? data.scatter : 原型点` ——
     于是**空数组也画 24 个示意点**。而同一屏旁边那段文字写着
     "还没有配对数据…现在横轴纵轴都是空的"，两者直接打架：
     图上一堆点、文字说没数据。用户只会信图。

     第二轮（本次）：第一轮把判据改成 `Array.isArray`，但**没有参数**时
     仍然退回原型点 —— 而 `bindings.js` 的 s6 在"取实验数据失败"那条路径上
     调的正是 `charts.drawScatter()`（无参）。于是屏幕上同时出现
     "取实验数据失败"和 24 个看起来很正常的散点。

     现在：**任何拿不到点的情形都留白**。`?src=mock` 不需要这条回退 ——
     mock 数据源自己会返回 24 个示例点（`data/mock.js`），走的是正常通路。
     少一条"看起来更丰富"的分支，少一处两套渲染互相打架。 */
  const pts = (data && Array.isArray(data.scatter)) ? data.scatter : [];
  pts.forEach(([x,y])=>{
    const px=padL+iw*(x-1)/4, py=h-padB-ih*(y-1)/4;
    g.fillStyle='rgba(11,107,87,.72)';
    g.fillRect(px-3,py-3,6,6);
  });
  if(pts.length===0){
    // 空态要**看着就是空的**，不能靠读者自己去数点的个数
    g.fillStyle=C.ink4; g.font='11px "Plex Mono","Noto Sans SC",monospace';
    g.textAlign='center';
    g.fillText('还没有配对数据（等真人盲评采集完）', padL+iw/2, padT+ih/2);
    g.textAlign='left';
  }
  g.fillStyle=C.ink3; g.font='10px "Plex Mono","Noto Sans SC",monospace';
  g.fillText('评分者均分 →', w/2-30, h-8);
  g.save(); g.translate(11,padT+ih/2); g.rotate(-Math.PI/2);
  g.fillText('系统评分 →', -32, 0); g.restore();
}

/* ══════════════════════════════════════════════════════════
   02 实时绘制（G4-b，2026-09-23）
   ══════════════════════════════════════════════════════════
   ⚠️ 与上面那批 draw* 不同，**这两个不是从原型逐字抽的** ——
      原型里的 `drawLive`/`drawRoll` 是**演示循环**：
      一个自增的 `phase` 驱动滚动、音高曲线是 `Math.sin` 合成的假数据、
      参考条永远循环同一句。逐字搬过来只会得到"看着一样、其实与数据无关"的图。
      这里只借它的**画法**（网格 / 参考阶梯 / 偏差分段着色），
      数据通路全部重写：滚动位置跟**服务器时间轴**（`t_ms`）对齐，
      参考条来自 `ref_notes`，音高曲线来自真实 `f0`。

   ⚠️ Canvas 不认 CSS 变量（§1.6）：颜色一律走 `C` 里的具体色值。
*/

/** 频率 → MIDI。前端本地实现（`f0` 回来的是 Hz，画图要按半音定位）。 */
export function hzToMidi(hz){
  return hz && hz > 0 ? 69 + 12 * Math.log2(hz / 440) : null;
}

/**
 * 实时音高图：参考阶梯 + 你唱的曲线 + 偏差处橙色。
 *
 * 视觉上就是一个**横向滚动的钢琴卷**，但**没有时间轴标签** ——
 * 用户的注意力在"条"和"线"之间，不在具体秒数上（秒数看走带那行）。
 *
 * data = {
 *   refNotes: [[start_ms, end_ms, midi], …]   ← 参考音（模板或内置曲目）
 *   frames:   [[t_ms, f0|null], …]            ← 本会话收到的音高帧
 *   nowMs:    number                          ← 服务器时间轴上的"当前"
 *   msPerPx:  number                          ← 横向比例（默认 33.5 ⇒ 2.33s 一屏）
 * }
 *
 * 为什么 `msPerPx` 要做参数：原型写死 `segW=78`（像素/音），
 * 而真实曲目的音符时值不一样 —— 模板是用户自己唱的，节奏自由得多。
 * 固定像素/音会让慢歌挤成一团、快歌拉得太开。
 */
export function drawLive(data){
  const cv = document.getElementById('live'); if(!cv) return;
  const {g,w,h} = fit(cv);
  const d = data || {};
  const refNotes = d.refNotes || [];
  const frames = d.frames || [];
  const msPerPx = d.msPerPx || 33.5;
  const nowMs = (typeof d.nowMs === 'number') ? d.nowMs : 0;

  // ── 纵向范围：优先按参考音高定，没有参考时才退回采样范围 ──
  let lo = 57, hi = 71;
  const refMidis = refNotes.map(r => r[2]).filter(v => v != null);
  const frameMidis = frames.map(f => hzToMidi(f[1])).filter(v => v != null);
  const all = refMidis.concat(frameMidis);
  if (all.length){
    const mn = Math.min(...all), mx = Math.max(...all);
    const pad = Math.max(2, (mx - mn) * 0.25);
    lo = Math.floor(mn - pad); hi = Math.ceil(mx + pad);
  }
  const top = 30, bot = h - 28;
  const yOf = m => (hi === lo) ? (top + bot) / 2
                               : bot - (m - lo) / (hi - lo) * (bot - top);

  // ── 横向：屏幕右端 = nowMs，左端 = nowMs − 一屏时长 ──
  const spanMs = w * msPerPx;
  const t0 = Math.max(0, nowMs - spanMs);
  const xOf = ms => (ms - t0) / msPerPx;

  g.strokeStyle = C.line; g.lineWidth = 1;
  for(let m = lo; m <= hi; m++){
    const y = yOf(m); g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke();
  }
  // 音名标签（只标 C，避免糊成一片）
  if(h - top > 60){
    g.fillStyle = C.ink4; g.font = '9px "Plex Mono",Consolas,monospace';
    for(let m = lo; m <= hi; m++){
      if(((m % 12) + 12) % 12 === 0) g.fillText('C' + (Math.floor(m / 12) - 1), 6, yOf(m) - 5);
    }
  }

  g.save(); g.beginPath(); g.rect(0, 0, w, h); g.clip();

  // ── ① 参考阶梯（它希望你唱的音）──
  //    每遍都从参考自己的时间轴画，超出窗口的部分靠 clip 裁掉。
  if(refNotes.length){
    g.strokeStyle = C.ref; g.lineWidth = 2;
    // 是否"循环"：模板/内置曲目都是唱一遍就完，不在时间上循环 ——
    // 原型那个 `for k=-1..2` 的循环是为了演示无限滚动，真实版**不循环**：
    // 唱完就该看到参考条走完，而不是又从头来一遍（那会让人以为还要再唱）。
    g.beginPath();
    refNotes.forEach(([s, e, m], i) => {
      const x0 = xOf(s), x1 = xOf(e), y = yOf(m);
      if(i === 0) g.moveTo(x0, y); else g.lineTo(x0, y);
      g.lineTo(x1, y);
    });
    g.stroke();
  }

  // ── ② 你唱的曲线；偏差 > 25 音分的段用橙色 ──
  //    分段着色为什么要"断线重开"：一条 path 只有一个 strokeStyle，
  //    中途换色必须结束当前 path。原型用 `Math.abs(y-prev.y) > 26` 断线
  //    （那是为了遮住它对不上参考的假数据），真实版**不该断** ——
  //    真实音高本来就连续，断了反而像丢帧。只在换色时切段。
  if(frames.length){
    let curBad = null, started = false;
    g.lineWidth = 2.4; g.lineJoin = 'round';
    for(let i = 0; i < frames.length; i++){
      const [t, hz] = frames[i];
      const midi = hzToMidi(hz);
      if(midi == null){ started = false; curBad = null; continue; }  // 无声 → 断线
      const x = xOf(t);
      if(x < -4){ continue; }                    // 滚出左边的不画
      const y = yOf(midi);
      // 偏差：找这一刻的参考音高（没有参考就一律当"正常色"）
      let bad = false;
      for(const [s, e, m] of refNotes){
        if(t >= s - 120 && t <= e + 120){
          bad = Math.abs(midi - m) * 100 > 25; break;
        }
      }
      if(!started || bad !== curBad){
        g.stroke(); g.beginPath();
        g.strokeStyle = bad ? C.warn : C.ink;
        g.moveTo(x, y);
        curBad = bad; started = true;
      } else {
        g.lineTo(x, y);
      }
    }
    g.stroke();
  }
  g.restore();

  // ── 空态：别留一张空白图让人猜"是不是坏了" ──
  if(!frames.length){
    g.fillStyle = C.ink4; g.font = '11px "Plex Mono","Noto Sans SC",monospace';
    g.textAlign = 'center';
    g.fillText('还没收到声音 —— 开口唱，这里就会出现你的音高',
               w / 2, top + (bot - top) / 2);
    g.textAlign = 'left';
  }
}

/**
 * 逐音方块图：一个方块 = 一个参考音，方块位置 = 你唱到哪。
 *
 * data = {
 *   refNotes: [[start_ms, end_ms, midi], …]
 *   frames:   [[t_ms, f0|null], …]
 *   nowMs:    number
 * }
 *
 * ⚠️ 与原型的关键差别：原型是**一次性把整句画满**
 *   （`DEV=[0.05,-0.08,…]` 写死的偏差），真实版要**随唱随填** ——
 *   没唱到的音符画成灰的、唱过的按偏差着色。否则一进来就是满屏方块，
 *   用户分不清哪些是"已经唱过"、哪些是"还没到"。
 */
export function drawRoll(data){
  const cv = document.getElementById('roll'); if(!cv) return;
  const {g,w,h} = fit(cv);
  const d = data || {};
  const refNotes = d.refNotes || [];
  const frames = d.frames || [];
  const nowMs = (typeof d.nowMs === 'number') ? d.nowMs : 0;

  if(!refNotes.length){
    g.fillStyle = C.ink4; g.font = '11px "Plex Mono","Noto Sans SC",monospace';
    g.textAlign = 'center';
    g.fillText('这一遍没有参考音高（自比练习）—— 逐音对比需要先有一个模板',
               w / 2, h / 2);
    g.textAlign = 'left';
    return;
  }

  const top = 20, bot = h - 22;
  let lo = Math.min(...refNotes.map(r => r[2]));
  let hi = Math.max(...refNotes.map(r => r[2]));
  if(hi === lo){ hi = lo + 1; }
  const yOf = m => bot - (m - lo) / (hi - lo) * (bot - top);

  g.strokeStyle = C.line; g.lineWidth = 1;
  for(let m = Math.ceil(lo); m <= Math.floor(hi); m++){
    const y = yOf(m); g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke();
  }

  const seg = w / refNotes.length;
  const bw = Math.max(4, seg - 12), bh = 14;
  refNotes.forEach(([s, e, m], i) => {
    const x = i * seg + 6;
    // 这一刻用户实际唱到的音高（取窗口内的中位，抗单帧抖动）
    const inWin = frames.filter(f => f[0] >= s && f[0] <= e && hzToMidi(f[1]) != null)
                       .map(f => hzToMidi(f[1]));
    const notYet = nowMs < s;
    let y, fill, dev = null;
    if(inWin.length){
      inWin.sort((a, b) => a - b);
      const med = inWin[Math.floor(inWin.length / 2)];
      dev = (med - m) * 100;                  // 音分
      y = yOf(med);
      const a = Math.abs(dev) / 100;          // 半音
      fill = a > 0.3 ? C.warn : a > 0.15 ? C.acc : C.ink;
    } else {
      y = yOf(m);
      fill = notYet ? '#e8eae9' : C.line2;    // 还没唱到 / 唱了但没检测到音高
    }
    g.fillStyle = fill;
    g.fillRect(x, y - bh / 2, bw, bh);
    if(dev != null && Math.abs(dev) > 6){
      g.strokeStyle = C.line2; g.lineWidth = 1; g.setLineDash([2, 3]);
      g.beginPath(); g.moveTo(x + bw / 2, yOf(m)); g.lineTo(x + bw / 2, y); g.stroke();
      g.setLineDash([]);
    }
  });
}

/* ── 量程/标签：从设置里来（契约 config.range）────────────── */
export const RANGE_MIN = 40;
export const RANGE_MAX = 84;

export function updateRangeBand(root = document) {
  const on = root.querySelector('#rangeSeg button.on');
  if (!on) return;
  const lo = +on.dataset.lo, hi = +on.dataset.hi;
  const band = root.querySelector('#rangeBand');
  if (!band) return;
  band.style.left = ((lo - RANGE_MIN) / (RANGE_MAX - RANGE_MIN) * 100) + '%';
  band.style.width = ((hi - lo) / (RANGE_MAX - RANGE_MIN) * 100) + '%';
}
