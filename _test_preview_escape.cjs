'use strict';
/* 预览强调 + 反斜杠转义回归（2026-11-16）。
   站主报：本地写作台预览里 `int \*a` 会漏出反斜杠，`int\* p,q;` 那一句照样变斜体。
   站上是 CommonMark（反斜杠 + ASCII 标点 = 字面字符、转义过的星号不参与配对），预览必须一致。 */
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const source = readFileSync(join(__dirname, 'ui/app.js'), 'utf8');
const start = source.indexOf('  function mdProtectEscapes(');
const end = source.indexOf('  /* ══════════════════ 五、桌面窗口桥');
assert.ok(start > 0 && end > start, 'ui/app.js 里找不到 mdProtectEscapes / renderMd 的边界');
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context);
const inline = (text) => {
  assert.equal(typeof context.mdEmphasis, 'function');
  return context.mdEmphasis(text);
};

test('转义过的星号渲染成字面星号，不参与斜体配对', () => {
  assert.equal(inline('int \\*p = &i;'), 'int *p = &i;');
  assert.equal(
    inline('另外一种定义方法是int\\* p;这里还是p是指针而不是有int\\*这么一个类型；所以int\\* p,q;中的q还是一个普通的整形'),
    '另外一种定义方法是int* p;这里还是p是指针而不是有int*这么一个类型；所以int* p,q;中的q还是一个普通的整形'
  );
  assert.doesNotMatch(inline('int \\*p'), /<em>/);
  assert.doesNotMatch(inline('指针就是保存地址的变量，如int \\*p = &i;就是把i的地址赋给了p，可以说p指向i'), /<em>/);
});

test('复合赋值与乘法里的星号不再吃掉整句', () => {
  const line = '16.我们知道x \\*= a+b;是x = x\\*(a+b),但是在define函数中x \\*= a+b;是x = x\\*a +b;';
  const html = inline(line);
  assert.doesNotMatch(html, /<em>/);
  assert.doesNotMatch(html, /\\/);
  assert.equal(html, '16.我们知道x *= a+b;是x = x*(a+b),但是在define函数中x *= a+b;是x = x*a +b;');
});

test('正常的加粗、斜体、删除线仍然生效', () => {
  assert.equal(inline('**加粗**'), '<strong>加粗</strong>');
  assert.equal(inline('*斜体*'), '<em>斜体</em>');
  assert.equal(inline('***两者***'), '<strong><em>两者</em></strong>');
  assert.equal(inline('~~删除~~'), '<del>删除</del>');
  assert.equal(inline('前**加粗**后 *斜体* 尾'), '前<strong>加粗</strong>后 <em>斜体</em> 尾');
});

test('转义只认 ASCII 标点：八进制/十六进制写法里的反斜杠原样保留', () => {
  assert.equal(inline("1.int x = '\\144';是八进制数（\\后加上三个不大于7的数）"), "1.int x = '\\144';是八进制数（\\后加上三个不大于7的数）");
  assert.equal(inline("int x = '\\x64';"), "int x = '\\x64';");
});

test('其它标点转义按 CommonMark 还原成字面字符', () => {
  assert.equal(inline('\\*不是斜体\\*'), '*不是斜体*');
  assert.equal(inline('a \\_b\\_ c'), 'a _b_ c');
  assert.equal(inline('\\`code\\`'), '`code`');
  assert.equal(inline('反斜杠 \\\\ 本身'), '反斜杠 \\ 本身');
  assert.equal(inline('&lt; \\&lt; 已是实体'), '&lt; &lt; 已是实体');
});

test('转义与真实强调混排：转义的星号不破坏后面的真斜体', () => {
  assert.equal(inline('int \\*p 然后 *真的斜体* 结束'), 'int *p 然后 <em>真的斜体</em> 结束');
  assert.equal(inline('\\*a\\* 与 \\*b\\*'), '*a* 与 *b*');
});