'use strict';
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm');
const test=require('node:test');
const assert=require('node:assert/strict');
const source=readFileSync(join(__dirname,'ui/app.js'),'utf8');
const start=source.indexOf('  function formatBodyBlock('),end=source.indexOf('\n  /*',start);
assert.ok(start>=0&&end>start,'正文标题格式操作必须存在');
const context=vm.createContext({});vm.runInContext(source.slice(start,end)+'\nthis.format=formatBodyBlock;',context);
const format=(body,start,end,level)=>context.format(body,start,end,level);
test('当前段落设置为 H2/H3 并能还原正文',()=>{
  assert.equal(format('第一段\n下一段',1,1,2).body,'## 第一段\n下一段');
  assert.equal(format('## 第一段',4,4,3).body,'### 第一段');
  assert.equal(format('### 第一段',4,4,0).body,'第一段');
  assert.equal(format('第一段',3,3,2).body,'## 第一段');
});
test('选择多行逐行设置，保留空行和 CRLF，不修改下一段',()=>{
  const result=format('段一\r\n\r\n段二\r\n下一段',0,10,2);
  assert.equal(result.body,'## 段一\r\n\r\n## 段二\r\n下一段');
  assert.equal(result.body.slice(result.start,result.end),'## 段一\r\n\r\n## 段二');
});
test('选区刚好停在下一行开头时不改下一行，正文末尾空段可设标题',()=>{
  assert.equal(format('前段\n后段',0,3,3).body,'### 前段\n后段');
  assert.equal(format('前段\n',3,3,2).body,'前段\n## ');
});
test('围栏代码与缩进代码不变，代码外正常设标题',()=>{
  const body='```md\n## 示例\n```\n\n    const a=1\n正文';
  assert.equal(format(body,0,body.length,3).body,'```md\n## 示例\n```\n\n    const a=1\n### 正文');
});
test('引用和列表内的围栏代码完整保留',()=>{
  for(const code of ['> ```js\n> const x = 1\n> ```','- ```js\n  const x = 1\n  ```','> - ~~~md\n>   ## 示例\n>   ~~~']){
    const body=code+'\n\n普通段落';
    assert.equal(format(body,0,body.length,2).body,code+'\n\n## 普通段落');
  }
});
