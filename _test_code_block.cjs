'use strict';
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm');
const test=require('node:test');
const assert=require('node:assert/strict');
const source=readFileSync(join(__dirname,'ui/app.js'),'utf8');
const start=source.indexOf('  function formatBodyBlock('),end=source.indexOf('\n  /*',start);
assert.ok(start>=0&&end>start);
const context=vm.createContext({});
vm.runInContext(source.slice(start,end)+'\nthis.format=formatBodyBlock;',context);
const format=(body,start,end,kind='code',language='python')=>context.format(body,start,end,kind,language);

test('选中多行只生成一个 Python 块，代码缩进与空行原样保留',()=>{
  const code='def f():\n    a = 1\n\n\treturn a';
  const result=format(code,0,code.length);
  assert.equal(result.body,'```python\ndef f():\n    a = 1\n\n\treturn a\n```');
  assert.equal(result.body.slice(result.start,result.end),code);
});
test('选区在下一行开头结束时不把下一段放进代码，CRLF 不变',()=>{
  const result=format('a\r\n  b\r\n正文',0,8);
  assert.equal(result.body,'```python\r\na\r\n  b\r\n```\r\n正文');
  assert.equal(result.body.slice(result.start,result.end),'a\r\n  b');
});
test('空正文插入空块，光标进入内容行；已有正文中插入也不丢字符',()=>{
  const blank=format('',0,0);
  assert.equal(blank.body,'```python\n\n```');
  assert.equal(blank.start,10); assert.equal(blank.end,10);
  const mid=format('前后',1,1);
  assert.equal(mid.body,'前\n```python\n\n```\n后');
  assert.equal(mid.start,12); assert.equal(mid.end,12);
});
test('EOF 与末尾空行均能插入，继承当前文档的 CRLF',()=>{
  assert.equal(format('正文',2,2).body,'正文\n```python\n\n```');
  assert.equal(format('正文\r\n',4,4,'code','json').body,'正文\r\n```json\r\n\r\n```');
});
test('已有长反引号与波浪围栏不嵌套，点击不会重写语言或缩进',()=>{
  for(const body of ['````js\nconst s = "```";\n````','~~~python\n    print(1)\n~~~~~','  ```rust\n  fn main() {}\n  ```']) {
    for(const [s,e] of [[body.indexOf('\n')+1,body.indexOf('\n')+1],[0,body.length]]) {
      assert.equal(format(body,s,e).body,body);
    }
  }
});
test('代码中含三个或更多反引号时使用更长围栏',()=>{
  const code='const sample = "``` and `````";\n  sample;';
  assert.equal(format(code,0,code.length,'code','javascript').body,'``````javascript\nconst sample = "``` and `````";\n  sample;\n``````');
});
test('修改语言只修改当前块的开头，选区仍指向原代码',()=>{
  const body='前\n~~~~python linenos\n\tprint(1)\n~~~~\n后';
  const s=body.indexOf('\t'),e=s+9;
  const result=format(body,s,e,'language','typescript');
  assert.equal(result.body,'前\n~~~~typescript linenos\n\tprint(1)\n~~~~\n后');
  assert.equal(result.body.slice(result.start,result.end),'\tprint(1)');
  assert.equal(format('正文',0,0,'language','json').body,'正文','代码外选语言只改变下一次插入的默认值');
});
test('正文按钮解开当前代码块，保留缩进、CRLF、块外内容与选区',()=>{
  const body='前\r\n```python\r\n  a\r\n\tb\r\n```\r\n后';
  const s=body.indexOf('  a'),e=body.lastIndexOf('\r\n```');
  const result=format(body,s,e,0);
  assert.equal(result.body,'前\r\n  a\r\n\tb\r\n后');
  assert.equal(result.body.slice(result.start,result.end),'  a\r\n\tb');
  assert.equal(format('```python\n\n```',10,10,0).body,'');
});
test('跨代码边界和未闭合块保持原样并说明原因',()=>{
  for(const body of ['正文\n```python\na\n```\n下一段','```python\na','```\na\n```\n```\nb\n```']) {
    for(const kind of ['code','language',0]) {
      const result=format(body,0,body.length,kind);
      assert.equal(result.body,body);
      assert.ok(result.error,'不能静默改坏复杂代码范围');
    }
  }
});
test('引用、列表以及缩进中的已有围栏保持原样并提示',()=>{
  for(const body of ['> ```js\n> const x = 1\n> ```','- ```js\n  const x = 1\n  ```','> - ~~~md\n>   ## 示例\n>   ~~~','    ```py\n    x=1\n    ```','- 列表项\n\n  ```python\n  x = 1\n  ```']) {
    for(const kind of ['code','language',0]) {
      const result=format(body,0,body.length,kind);
      assert.equal(result.body,body); assert.ok(result.error);
    }
  }
});
test('光标位于列表延续段内的围栏时同样保护，离开列表后的顶层围栏仍能还原',()=>{
  const body='- 列表项\n\n  ```python\n  x = 1\n  ```';
  const result=format(body,body.indexOf('x ='),body.indexOf('x ='),0);
  assert.equal(result.body,body); assert.ok(result.error);
  assert.equal(format('- 列表项\n\n```python\nx = 1\n```',21,21,0).body,'- 列表项\n\nx = 1');
});
test('列表的懒延续段之后的代码也保留原样，普通段落后缩进围栏仍可编辑',()=>{
  const body='- 列表项\n延续文字\n\n  ```python\n  x = 1\n  ```';
  const result=format(body,body.indexOf('x ='),body.indexOf('x ='),0);
  assert.equal(result.body,body); assert.ok(result.error);
  const top='- 列表项\n\n普通段落\n\n  ```python\n  x = 1\n  ```';
  assert.equal(format(top,top.indexOf('x ='),top.indexOf('x ='),0).body,'- 列表项\n\n普通段落\n\n  x = 1');
});
test('章节和小标题仍完整保留已有代码，代码外照常生效',()=>{
  const body='````md\n## code\n```\n````\n\n正文';
  assert.equal(format(body,0,body.length,2).body,'````md\n## code\n```\n````\n\n## 正文');
});

const renderStart=source.indexOf('  function renderMd('),renderEnd=source.indexOf('\n  /*',renderStart);
context.esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
vm.runInContext(source.slice(renderStart,renderEnd)+'\nthis.render=renderMd;',context);
test('预览保留长围栏内的三个反引号，波浪围栏不变成普通段落',()=>{
  assert.equal(context.render('````python\nx = "```"\n````'),'<pre><code>x = &quot;```&quot;</code></pre>');
  assert.equal(context.render('~~~json\n{"a": 1}\n~~~'),'<pre><code>{&quot;a&quot;: 1}</code></pre>');
});
test('列表内已有代码仍抽取为代码预览，保留多行与源码缩进',()=>{
  const input='- item\n\n  ```python\n  print(1)\n    print(2)\n  ```';
  assert.equal(context.render(input),'<ul><li>item</li></ul>\n<pre><code>  print(1)\n    print(2)</code></pre>');
});
test('引用内已有代码继续抽取，原始行前缀与换行不会被合并成普通段落',()=>{
  const input='> ```python\n> print(1)\n>     print(2)\n> ```';
  assert.equal(context.render(input),'<pre><code>&gt; print(1)\n&gt;     print(2)</code></pre>');
});
test('嵌套长围栏预览保留内部反引号并转义 HTML，不把代码当视频或图片解析',()=>{
  const input='> - ~~~~html\n>   <script>alert("x")</script>\n>   const fence = "```";\n>   ![image](https://example.invalid/image.png)\n>   ~~~~';
  assert.equal(context.render(input),'<pre><code>&gt;   &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;\n&gt;   const fence = &quot;```&quot;;\n&gt;   ![image](https://example.invalid/image.png)</code></pre>');
});

function editorHarness(text, selectionStart=0, selectionEnd=selectionStart) {
  const controls=new Map();
  class Control {
    constructor() { this.listeners={}; this.value='python'; this.style={display:'none'}; }
    addEventListener(type,fn) { (this.listeners[type]??=[]).push(fn); }
    fire(type) { const event={preventDefault(){},type}; for(const fn of this.listeners[type]??[]) fn(event); }
    focus() {} setSelectionRange(s,e) { this.selectionStart=s; this.selectionEnd=e; }
  }
  const body=new Control(); body.text=text; body.setSelectionRange(selectionStart,selectionEnd);
  Object.defineProperty(body,'value',{get(){return this.text;},set(){assert.fail('格式操作不得通过 value 赋值抹掉撤销记录');}});
  controls.set('#f-body',body);
  const lookup=selector=>{ if(!controls.has(selector)) controls.set(selector,new Control()); return controls.get(selector); };
  lookup('#btn-code-block'); lookup('#code-language');
  const heading=[0,2,3].map(level=>{const button=new Control();button.dataset={bodyLevel:String(level)};return button;});
  const messages=[];
  const sandbox=vm.createContext({
    $:lookup,$$:()=>heading,toast:message=>messages.push(message),guard:(_button,fn)=>fn,
    document:{execCommand(command,_show,replacement){
      assert.equal(command,'insertText');
      body.text=body.text.slice(0,body.selectionStart)+replacement+body.text.slice(body.selectionEnd);
      return true;
    }},
    uploadPostImage(){},uploadCover(){},updateSummaryCount(){},
  });
  const initStart=source.indexOf('  function initWrite('),initEnd=source.indexOf('\n  /*',initStart);
  vm.runInContext(source.slice(start,end)+'\n'+source.slice(initStart,initEnd)+'\ninitWrite();',sandbox);
  return {body,controls,heading,messages};
}
test('真实按钮与语言选择器能对保留的多行选区操作，正文按钮可还原',()=>{
  const h=editorHarness('a\n  b',0,5);
  h.controls.get('#btn-code-block').fire('click');
  assert.equal(h.body.value,'```python\na\n  b\n```');
  const language=h.controls.get('#code-language'); language.value='json'; language.fire('change');
  assert.equal(h.body.value,'```json\na\n  b\n```');
  h.heading[0].fire('click');
  assert.equal(h.body.value,'a\n  b');
});
test('真实格式入口在输入法组词期间保留正文，完成后恢复操作',()=>{
  const h=editorHarness('正在组词',0,4);
  h.body.fire('compositionstart');
  for(const button of [...h.heading,h.controls.get('#btn-code-block')]) button.fire('click');
  const language=h.controls.get('#code-language'); language.value='json'; language.fire('change');
  assert.equal(h.body.value,'正在组词');
  h.body.fire('compositionend'); h.controls.get('#btn-code-block').fire('click');
  assert.equal(h.body.value,'```json\n正在组词\n```');
});
