'use strict';
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=readFileSync(join(__dirname,'ui/app.js'),'utf8');
const start=source.indexOf('  function formatBodyBlock('),end=source.indexOf('\n  /*',start);
const context=vm.createContext({});
vm.runInContext(source.slice(start,end),context);
const format=(...args)=>{assert.equal(typeof context.formatBodyInline,'function');return context.formatBodyInline(...args);};
for(const [kind,marker,ending=marker] of [['strong','**'],['em','*'],['strike','<del>','</del>']]){
  test(`${kind} 选中文字可应用、再次点击可还原，外围字节不变`,()=>{
    const before='前文 文字 后文',result=format(before,3,5,kind);
    assert.equal(result.body,`前文 ${marker}文字${ending} 后文`);
    assert.equal(result.body.slice(result.start,result.end),'文字');
    const restored=format(result.body,result.start,result.end,kind);assert.equal(restored.body,before);
    assert.equal(format(marker+'文字'+ending,0,marker.length+ending.length+2,kind).body,'文字');
  });
  test(`${kind} 空选区插入可替换文字；空白与CRLF保留`,()=>{
    const result=format('前后',1,1,kind);assert.ok(result.body.includes(marker));assert.ok(result.end>result.start);
    const raw='前\r\n  文字 \r\n后',result2=format(raw,3,8,kind);
    assert.equal(result2.body,`前\r\n  ${marker}文字${ending} \r\n后`);
  });
}
test('加粗和斜体可以嵌套，切换斜体不会误删粗体标记',()=>{
  const a=format('**文字**',2,4,'em');assert.equal(a.body,'***文字***');
  assert.equal(format(a.body,a.start,a.end,'em').body,'**文字**');
  assert.equal(format('***文字***',3,5,'strong').body,'*文字*');
});
test('跨行选择逐行设置格式，空行与代码不被当正文修改',()=>{
  const result=format('甲\r\n\r\n乙',0,6,'strong');assert.equal(result.body,'**甲**\r\n\r\n**乙**');
  for(const raw of ['```cpp\nint x = 1;\n```','`a * b`','    code']){
    const result=format(raw,0,raw.length,'strong');assert.equal(result.body,raw);assert.ok(result.error);
  }
});
for(const kind of ['strong','em','strike'])test(`跨行应用再取消${kind}可回到原始CRLF`,()=>{
  const raw='甲\r\n\r\n乙',a=format(raw,0,raw.length,kind),b=format(a.body,a.start,a.end,kind);assert.equal(b.body,raw);
});
test('整个嵌套选区不会错误删除另一种格式',()=>{
  assert.equal(format('~~**文字**~~',0,10,'strong').body,'~~文字~~');
  assert.equal(format('**~~文字~~**',0,10,'strike').body,'**文字**');
});
test('中文句中在加粗斜体内加删除线时标签在最外层，可再次取消',()=>{
  const raw='这是***文字***。',result=format(raw,5,7,'strike');
  assert.equal(result.body,'这是<del>***文字***</del>。');
  assert.equal(format(result.body,result.start,result.end,'strike').body,raw);
});
test('行内代码只接受相同长度的完整反引号，引用缩进代码保留',()=>{
  const raw='`a ``` b`',result=format(raw,7,8,'strong');assert.equal(result.body,raw);assert.ok(result.error);
  const quoted='>     code';assert.equal(format(quoted,6,10,'strong').body,quoted);
  const unmatched='前 `` 普通 `a+b`';assert.equal(format(unmatched,9,12,'strong').body,unmatched);
});
