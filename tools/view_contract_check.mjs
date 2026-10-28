/**
 * 视图契约检查：每个 views/sN.js 必须导出 id/no/title 与 render()，
 * 有 mount() 的必须能接受 (root, ctx) 两个参数（router 会这么调）。
 *
 * 为什么需要：router 调 `mod.render(sec, ctx)` / `mod.mount(sec, ctx)`，
 *   但视图是脚本生成的 —— 参数对不上时 JS **不会报错**，
 *   只是静默走错分支（s1 点卡片不跳转就是这么来的，见 router.js 注释）。
 *   所以要在 Node 里把契约单独验一遍。
 *
 * 用法：node tools/view_contract_check.mjs
 */
import { readdir } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const DIR = path.resolve('web/js/views');
const files = (await readdir(DIR)).filter((f) => /^s\d\.js$/.test(f)).sort();

// 视图在 import 期不碰 DOM（只定义常量与函数），所以不需要 DOM 桩。
let bad = 0;
console.log('='.repeat(78));
console.log('视图契约检查');
console.log('='.repeat(78));
for (const f of files) {
  const mod = await import(pathToFileURL(path.join(DIR, f)).href);
  const problems = [];
  for (const k of ['id', 'no', 'title']) {
    if (typeof mod[k] !== 'string' || !mod[k]) problems.push(`缺 ${k}`);
  }
  if (typeof mod.render !== 'function') problems.push('缺 render()');
  if (mod.render && mod.render.length < 1) problems.push('render() 不接收 root');
  if (mod.mount && mod.mount.length < 2) {
    problems.push(`mount() 只接 ${mod.mount.length} 个参数（router 会传 root, ctx）`);
  }
  const exp = f.replace('.js', '');
  if (mod.id && mod.id !== exp) problems.push(`id="${mod.id}" 与文件名 ${exp} 不符`);
  if (problems.length) {
    bad++;
    console.log(`  ❌ ${f.padEnd(8)} ${problems.join('; ')}`);
  } else {
    console.log(`  ✅ ${f.padEnd(8)} id=${mod.id} no=${mod.no} ` +
                `title=${mod.title} render✓${mod.mount ? ' mount✓' : ''}`);
  }
}
console.log('\n' + '='.repeat(78));
console.log(bad ? `  ${bad} 个视图契约不符` : '  全部符合契约');
process.exit(bad ? 1 : 0);
