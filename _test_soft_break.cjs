'use strict';
/* 预览换行：和线上站点保持一致（段内单个换行渲染成 <br>）。
   2026-11-04 站主报「本地写作台正常，推到线上换行全没了」后加。 */
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const source = readFileSync(join(__dirname, 'ui/app.js'), 'utf8');

/** 按 `^  ` 缩进的两空格函数边界切一段出来（写作台里这些函数都是 IIFE 内的同级声明）。 */
function sliceFunction(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start > 0, `找不到 ${name}`);
  const next = source.indexOf('\n  /* ', start);
  const nextFn = source.indexOf('\n  function ', start + 10);
  const end = Math.min(...[next, nextFn].filter((n) => n > 0));
  return source.slice(start, end > 0 ? end : source.length);
}

const context = vm.createContext({
  // esc 在写作台里是模块级箭头函数，这里给一个等价实现（只测换行，不管转义细节）。
  document: { createElement: () => ({ set innerHTML(v) { this._v = v; }, get value() { return this._v; } }) },
});
context.esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
context.videoMarkup = () => '<video></video>';

vm.runInContext(sliceFunction('bodyCodeBlocks'), context);
vm.runInContext(sliceFunction('renderMd'), context);
const render = (src) => context.renderMd(src, '');

test('段落里的单个换行渲染成 <br>', () => {
  const html = render('1.int a = 1;\nint a = 2;\n打印看看');
  assert.equal(html, '<p>1.int a = 1;<br>int a = 2;<br>打印看看</p>');
});

test('空行仍然分段，不会被并成一行', () => {
  const html = render('第一段第一行\n第一段第二行\n\n第二段');
  assert.equal(html, '<p>第一段第一行<br>第一段第二行</p>\n<p>第二段</p>');
});

test('代码围栏里的换行原样保留，不塞 <br>', () => {
  const html = render('```c\nint a = 1;\nint b = 2;\n```');
  assert.ok(!html.includes('<br>'), html);
  assert.ok(html.includes('int a = 1;'));
});

test('列表项各自独立，不因换行规则而串行', () => {
  const html = render('- 甲\n- 乙');
  assert.equal(html, '<ul><li>甲</li><li>乙</li></ul>');
});

test('加粗等行内语法跨行仍然生效', () => {
  const html = render('**重点**\n下一行');
  assert.equal(html, '<p><strong>重点</strong><br>下一行</p>');
});
test('段落续行的行首缩进用不换行空格保住（与站点同一规则）', () => {
  const html = render("1.int x = 0144;\n  int x = 0x64;\n  third;");
  assert.equal(html, '<p>1.int x = 0144;<br>\u00A0\u00A0int x = 0x64;<br>\u00A0\u00A0third;</p>');
});

test('分隔符后面是空格时不算缩进（4 空格起不碰）', () => {
  const html = render('第一行\n    四个空格的行');
  assert.equal(html, '<p>第一行<br>四个空格的行</p>');
});

test('段落首行的缩进不保留（与站点一致）', () => {
  const html = render('  首行有缩进\n第二行');
  assert.equal(html, '<p>首行有缩进<br>第二行</p>');
});
