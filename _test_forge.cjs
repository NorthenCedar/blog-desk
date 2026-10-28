const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const api = fs.existsSync(__dirname + '/ui/forge.js') ? require('./ui/forge.js') : {};

test('项目保存绑定原快照，切换后不覆盖另一个项目', async () => {
  assert.equal(typeof api.createSession, 'function', '尚无造物台编辑会话');
  let finish;
  let payload;
  const session = api.createSession(async (path, value) => {
    payload = value;
    return new Promise(resolve => { finish = resolve; });
  });
  session.open({ slug: 'first', revision: 'old', frontmatter: {} });
  const fields = { title: '作品一', images: [{ src: './a.png', alt: '图' }] };
  session.edit();
  const saving = session.save(fields, '正文');
  fields.images[0].alt = '保存之后继续编辑';
  session.open({ slug: 'second', revision: 'second', frontmatter: {} });
  finish({ slug: 'first', revision: 'new', frontmatter: payload.frontmatter, body: '正文' });
  await saving;
  assert.equal(payload.frontmatter.images[0].alt, '图');
  assert.equal(session.current.slug, 'second');
  assert.equal(session.current.revision, 'second');
});

test('保存中继续编辑保持未保存标记，冲突保留版本与输入', async () => {
  assert.equal(typeof api.createSession, 'function', '尚无造物台编辑会话');
  let finish;
  const session = api.createSession(() => new Promise(resolve => { finish = resolve; }));
  session.open({ slug: 'first', revision: 'old', frontmatter: {} });
  session.edit();
  const saving = session.save({ title: '旧快照' }, '正文');
  session.edit();
  finish({ slug: 'first', revision: 'new', frontmatter: { title: '旧快照' } });
  await saving;
  assert.equal(session.dirty, true);
  assert.equal(session.current.revision, 'new');
  const conflict = api.createSession(async () => { throw new Error('外部更新冲突'); });
  conflict.open({ slug: 'first', revision: 'old', frontmatter: { title: '我的作品' } });
  conflict.edit();
  await assert.rejects(conflict.save({ title: '继续编辑' }, ''), /冲突/);
  assert.equal(conflict.current.revision, 'old');
  assert.equal(conflict.dirty, true);
});

test('图库排序保持亮暗图配对，移除封面后下一张成为封面', () => {
  assert.equal(typeof api.moveImage, 'function', '尚无造物台图库排序');
  const images = [{ src: './a.png', darkSrc: './a-dark.png', alt: '甲' }, { src: './b.png', alt: '乙' }];
  const moved = api.moveImage(images, 0, 1);
  assert.equal(moved[1].darkSrc, './a-dark.png');
  assert.equal(moved[0].src, './b.png');
  assert.equal(images[0].src, './a.png');
  assert.deepEqual(api.moveImage(images, 0, -1), images);
});

test('自动动效稳定，叠片不足两张降为轻抬', () => {
  assert.equal(typeof api.chooseEffect, 'function', '尚无造物台动效预览');
  assert.equal(api.chooseEffect('demo', 'stack', 1), 'lift');
  assert.equal(api.chooseEffect('demo', 'stack', 3), 'stack');
  assert.equal(api.chooseEffect('demo', 'auto', 3), api.chooseEffect('demo', 'auto', 3));
});

test('未保存正文预览只读取当前项目图片并禁用危险链接', () => {
  const result = api.previewMarkdown('![封面](./cover.png) ![外部](https://example.com/p.png) [坏](javascript:alert) [好](https://example.com/)', 'my-project');
  assert.ok(result.includes('/api/project/image?slug=my-project&src=.%2Fcover.png'));
  assert.ok(result.includes('src=https%3A%2F%2Fexample.com%2Fp.png'));
  assert.ok(result.includes('[坏](#)'));
  assert.ok(result.includes('[好](https://example.com/)'));
});

test('旧配图的Unicode与空格文件名通过当前项目接口预览', () => {
  const result = api.previewMarkdown('![作品](<./我的 作品.png>)', 'demo');
  assert.ok(result.includes('src=.%2F%E6%88%91%E7%9A%84%20%E4%BD%9C%E5%93%81.png'));
});

test('项目预览复用文章代码边界，不改代码里的图片和链接示例', () => {
  const source = fs.readFileSync(__dirname + '/ui/app.js', 'utf8');
  const start = source.indexOf('  function bodyCodeBlocks(');
  const end = source.indexOf('  function applyBodyEdit(', start);
  const codeBlocks = vm.runInNewContext(source.slice(start, end) + '\nbodyCodeBlocks;');
  const markdown = '````md\n```\n![示例](./example.png)\n````\n\n`[示例](javascript:literal)`\n\n![封面](./cover.png)';
  const result = api.previewMarkdown(markdown, 'demo', codeBlocks);
  assert.ok(result.includes('![示例](./example.png)'));
  assert.ok(result.includes('`[示例](javascript:literal)`'));
  assert.ok(result.includes('src=.%2Fcover.png'));
});

test('带title的链接经过真实Markdown渲染后仍无活动危险href', () => {
  const source = fs.readFileSync(__dirname + '/ui/app.js', 'utf8');
  const start = source.indexOf('  function bodyCodeBlocks(');
  const end = source.indexOf('  const bridge', start);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
  const context = vm.createContext({ esc });
  vm.runInContext(source.slice(start, end) + '\nthis.render=renderMd;', context);
  for (const href of ['javascript:alert%281%29', 'http://example.com/', 'https://u:p@example.com/']) {
    const markdown = `[click](${href} "title")`;
    const html = context.render(api.previewMarkdown(markdown, 'demo', context.bodyCodeBlocks));
    assert.ok(!html.includes(`href="${href}"`), html);
  }
});

test('暗色替图上传期间排序仍写回同一图库条目', async () => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const selected = {src:'./a.png', alt:'甲'};
  let gallery = [selected, {src:'./b.png', alt:'乙'}];
  const upload = pending.then(src => { selected.darkSrc = src; });
  gallery = api.moveImage(gallery, 0, 1);
  finish('./a-dark.png');
  await upload;
  assert.equal(gallery[1].darkSrc, './a-dark.png');
  assert.equal(gallery[0].src, './b.png');
});

test('实际预览最终HTML只含安全链接与当前项目图片', () => {
  assert.equal(typeof api.safePreviewHtml, 'function');
  const source = fs.readFileSync(__dirname + '/ui/app.js', 'utf8');
  const start = source.indexOf('  function bodyCodeBlocks('), end = source.indexOf('  const bridge', start);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
  const context = vm.createContext({ esc });
  vm.runInContext(source.slice(start, end) + '\nthis.render=renderMd;', context);
  for (const body of ['[click](javascript:alert%281%29 "title")', '``code ` [click](javascript:alert%281%29 "title") ``']) {
    const html = api.safePreviewHtml(context.render(api.previewMarkdown(body, 'demo', context.bodyCodeBlocks)), 'demo');
    assert.ok(!html.includes('href="javascript:'), html);
  }
  const html = api.safePreviewHtml(context.render('![remote](https://example.com/a.png "title") [good](https://example.com/?a=1&b=2 "title")'), 'demo');
  assert.ok(!html.includes('src="https://'), html);
  assert.ok(html.includes('/api/project/image?slug=demo&amp;src='), html);
  assert.ok(html.includes('href="https://example.com/?a=1&amp;b=2"'), html);
});
