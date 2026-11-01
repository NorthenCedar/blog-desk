'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'ui/app.js'),'utf8');
function load(){
 const first=source.indexOf('  function indentBodyText('),last=source.indexOf('\n  /*',first);
 assert.ok(first>=0&&last>first,'尚未实现四空格缩进');
 const context=vm.createContext({});vm.runInContext(source.slice(first,last),context);return context;
}
for(const [raw,start,end,outdent,want,from,to]of [
 ['甲乙',1,1,false,'甲    乙',5,5],['abc',1,2,false,'a    c',5,5],['',0,0,false,'    ',4,4],
 ['甲\r\n乙\r\n后',0,6,false,'    甲\r\n    乙\r\n后',4,14],
 ['a\n\nb',0,4,false,'    a\n    \n    b',4,16],
 ['    abc',6,6,true,'abc',2,2],['  abc',1,1,true,'abc',0,0],['abc',1,1,true,'abc',1,1],
 ['\tabc',2,2,true,'\tabc',2,2],['    a\r\n  b\r\nc',0,12,true,'a\r\nb\r\nc',0,6],
])test(`正文缩进保留内容和位置 ${JSON.stringify([raw,start,end,outdent])}`,()=>{
 const result=load().indentBodyText(raw,start,end,outdent);assert.equal(result.body,want);assert.equal(result.start,from);assert.equal(result.end,to);
});

test('正文键盘边界只接管三种正文，保留选区方向、滚动和输入事件；其他控件及输入法不接管',()=>{
 const context=load(),handlers={},calls=[];
 const root={addEventListener(name,fn){handlers[name]=fn;}};
 context.applyBodyEdit=(body,result)=>{calls.push(result);body.value=result.body;};
 context.bindBodyIndentKeys(root);
 const body={value:'a\nb\n后',selectionStart:0,selectionEnd:4,selectionDirection:'backward',scrollTop:80,scrollLeft:7,
  matches(selector){return selector.includes('#f-body');},setSelectionRange(start,end,direction){this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;}};
 const event=(extra={})=>({key:'Tab',target:body,preventDefault(){this.defaultPrevented=true;},...extra});
 const first=event();handlers.keydown(first);assert.equal(first.defaultPrevented,true);assert.equal(body.value,'    a\n    b\n后');
 assert.equal(body.selectionDirection,'backward');assert.equal(body.scrollTop,80);assert.equal(body.scrollLeft,7);
 for(const extra of [{ctrlKey:true},{altKey:true},{metaKey:true},{isComposing:true},{keyCode:229},{key:'Enter'}]){const e=event(extra);handlers.keydown(e);assert.ok(!e.defaultPrevented);}
 const field=event({target:{...body,matches:()=>false}});handlers.keydown(field);assert.ok(!field.defaultPrevented);
 handlers.compositionstart({target:body});const ime=event();handlers.keydown(ime);assert.ok(!ime.defaultPrevented);handlers.compositionend({target:body});
 assert.equal(calls.length,1);
});
