'use strict';
// 「圆点」按钮的规格：给选中的每一行加 "- "，整段都已有圆点时取消；代码、标题、引用、表格与空行不动。
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=readFileSync(join(__dirname,'ui/app.js'),'utf8');
const start=source.indexOf('  function formatBodyBlock('),end=source.indexOf('\n  /*',start);
const context=vm.createContext({});
vm.runInContext(source.slice(start,end),context);
const bullet=(body,from=0,to=body.length)=>{assert.equal(typeof context.formatBodyBullet,'function');return context.formatBodyBullet(body,from,to);};

test('多行选择逐行加「- 」圆点，CRLF 原样保留',()=>{
  const raw='甲\r\n乙\r\n丙',result=bullet(raw);
  assert.equal(result.body,'- 甲\r\n- 乙\r\n- 丙');
  assert.ok(!/[^\r]\n/.test(result.body));
  assert.equal(result.body.slice(result.start,result.end),'- 甲\r\n- 乙\r\n- 丙');
});

test('整段都有圆点时再点一次取消，回到原文',()=>{
  const raw='- 甲\r\n- 乙';
  assert.equal(bullet(raw).body,'甲\r\n乙');
  assert.equal(bullet(bullet('甲\r\n乙').body).body,'甲\r\n乙');
});

test('混着有的行不会被写成「- - 」',()=>{
  assert.equal(bullet('- 甲\r\n乙').body,'- 甲\r\n- 乙');
  assert.equal(bullet('甲\r\n- 乙').body,'- 甲\r\n- 乙');
});

test('空行跳过，不错位到下一段',()=>{
  assert.equal(bullet('甲\r\n\r\n乙').body,'- 甲\r\n\r\n- 乙');
});

test('只有光标没有选区时只管当前行，光标跟着文字走',()=>{
  const result=bullet('甲乙\r\n丙丁',1,1);
  assert.equal(result.body,'- 甲乙\r\n丙丁');
  assert.equal(result.start,3);
  assert.equal(result.end,3);
});

test('围栏代码、缩进代码、行内代码都不动',()=>{
  const fenced='```c\nint x = 1;\n```\n正文';
  assert.equal(bullet(fenced).body,'```c\nint x = 1;\n```\n- 正文');
  const indented='    代码行\n正文';
  assert.equal(bullet(indented).body,'    代码行\n- 正文');
  assert.equal(bullet('`a` 行').body,'- `a` 行');
});

test('标题、引用、表格行不动：加圆点只会把它们写坏',()=>{
  assert.equal(bullet('## 标题\n正文').body,'## 标题\n- 正文');
  assert.equal(bullet('> 引用\n正文').body,'> 引用\n- 正文');
  assert.equal(bullet('| a | b |\n正文').body,'| a | b |\n- 正文');
});

test('选中的全是空行/代码时给出提示且内容不变',()=>{
  for(const raw of ['```c\nint x = 1;\n```','\r\n\r\n','## 标题']){
    const result=bullet(raw);
    assert.equal(result.body,raw);
    assert.ok(result.error);
  }
});

test('空正文里点圆点直接给出一个空圆点，光标停在后面',()=>{
  const result=bullet('',0,0);
  assert.equal(result.body,'- ');
  assert.equal(result.start,2);
});

test('末行没有换行符也能加圆点',()=>{
  assert.equal(bullet('甲\n乙').body,'- 甲\n- 乙');
});