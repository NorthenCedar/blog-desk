// 直接执行写作台实际函数，只替换网络和页面边界；无需浏览器或第三方依赖。
'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname, 'ui/app.js'), 'utf8');
const start = source.indexOf('  async function savePost(');
const end = source.indexOf('  async function newPost(', start);
assert.ok(start >= 0 && end > start, '必须能从源码中取得真实 savePost 函数');
const savePostSource = source.slice(start, end);

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function post(slug, revision) {
  return { slug, revision: revision.repeat(64), frontmatter: { title: slug }, body: slug };
}

function harness({ waitForList = false } = {}) {
  const original = post('article-a', 'a');
  const S = { post: original };
  const request = deferred();
  const list = deferred();
  const edited = { frontmatter: { title: '文章 A 的修改' }, body: '文章 A 的修改正文' };
  const calls = { requests: [], toasts: [], renders: [], navigation: [], lists: 0 };
  // 表单内容不依赖 S.post，模拟等待响应时用户仍能在当前表单继续输入。
  const form = { body: '文章 A 的修改正文' };
  const bodyField = {
    get value() { return form.body; },
    set value(value) { form.body = value; },
  };
  const context = vm.createContext({
    S,
    $: (selector) => {
      assert.equal(selector, '#f-body', '测试只提供当前文章正文表单');
      return bodyField;
    },
    collectPost: () => edited,
    validatePost: () => [],
    POST: (url, body) => { calls.requests.push({ url, body }); return request.promise; },
    toast: (message) => calls.toasts.push(message),
    loadPosts: async () => { calls.lists++; if (waitForList) await list.promise; },
    renderPostImages: () => calls.renders.push('images'),
    renderPostMeta: () => calls.renders.push('meta'),
    go: (page) => calls.navigation.push(page),
  });
  vm.runInContext(savePostSource + '\nthis.savePost = savePost;', context);
  return { S, original, edited, calls, form, request, list, save: context.savePost };
}

test('保存 A 等待响应时切换 B，不把 A 的版本或缓存写到 B，也不跳去发布页', async () => {
  const h = harness();
  const pending = h.save(true);
  const next = post('article-b', 'b');
  h.S.post = next;
  const before = structuredClone(next);
  h.form.body = '文章 B 尚未保存的输入';

  h.request.resolve({ revision: 'c'.repeat(64) });
  await pending;

  assert.deepEqual(next, before, 'B 的版本与内容缓存必须保持原样');
  assert.equal(h.form.body, '文章 B 尚未保存的输入');
  assert.equal(h.original.revision, 'c'.repeat(64));
  assert.equal(h.original.body, h.edited.body);
  assert.equal(h.original.frontmatter, h.edited.frontmatter);
  assert.equal(h.calls.requests[0].body.slug, 'article-a');
  assert.equal(h.calls.requests[0].body.base_revision, 'a'.repeat(64));
  assert.ok(h.calls.toasts.some(message => message.includes('article-a')), '提示必须对应真正保存的文章');
  assert.deepEqual(h.calls.renders, []);
  assert.deepEqual(h.calls.navigation, []);
});

test('保存等待期间关闭文章，迟到的响应不会访问空编辑对象', async () => {
  const h = harness();
  const pending = h.save(true);
  h.S.post = null;
  h.request.resolve({ revision: 'c'.repeat(64) });
  await pending;

  assert.equal(h.S.post, null);
  assert.equal(h.original.revision, 'c'.repeat(64));
  assert.deepEqual(h.calls.renders, []);
  assert.deepEqual(h.calls.navigation, []);
});

test('仍在同一篇文章时正常更新版本、刷新元数据并跳转发布', async () => {
  const h = harness();
  const pending = h.save(true);
  h.form.body = '请求发出后继续输入的内容';
  h.request.resolve({ revision: 'c'.repeat(64) });
  await pending;

  assert.equal(h.S.post, h.original);
  assert.equal(h.original.revision, 'c'.repeat(64));
  assert.equal(h.form.body, '请求发出后继续输入的内容', '保存响应不能重填表单');
  assert.deepEqual(h.calls.renders, ['images', 'meta']);
  assert.deepEqual(h.calls.navigation, ['publish']);
});

test('POST 已完成但列表刷新仍等待时切换文章，最终不渲染或跳转旧文章', async () => {
  const h = harness({ waitForList: true });
  const pending = h.save(true);
  h.request.resolve({ revision: 'c'.repeat(64) });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.lists, 1);
  const next = post('article-b', 'b');
  h.S.post = next;
  const before = structuredClone(next);
  h.list.resolve();
  await pending;

  assert.deepEqual(next, before);
  assert.deepEqual(h.calls.renders, []);
  assert.deepEqual(h.calls.navigation, []);
});

test('409 失败保留原版本、缓存和未保存输入，允许用户复制合并', async () => {
  const h = harness();
  const before = structuredClone(h.original);
  const pending = h.save(true);
  h.form.body = '尚未保存的输入';
  h.request.reject(new Error('HTTP 409：文件已被 AI 更新'));
  await assert.rejects(pending, /409/);

  assert.deepEqual(h.original, before);
  assert.equal(h.form.body, '尚未保存的输入');
  assert.equal(h.calls.lists, 0);
  assert.deepEqual(h.calls.renders, []);
  assert.deepEqual(h.calls.navigation, []);
});

function actualFunctions(first, afterLast) {
  const begin = source.indexOf('  ' + first);
  const finish = source.indexOf('  ' + afterLast, begin);
  assert.ok(begin >= 0 && finish > begin, '必须从源码取得实际的收集、保存和新建函数');
  return source.slice(begin, finish);
}

function collectionHarness(kind) {
  const isTalk = kind === 'talks';
  const original = isTalk
    ? { id: 'talk-existing', date: '2026-10-20', text: '旧说说', href: '', images: [], videos: [] }
    : { date: '2026-10-20', version: 'v10.2', changes: ['旧日志'] };
  const target = {
    items: isTalk ? [original] : [original,
      { date: '2026-10-19', version: 'v9.99', changes: ['较低主版本'] },
      { date: '2026-10-18', version: 'v10.1.99', changes: ['三段版本不参与两段版本递增'] }],
    revision: 'a'.repeat(64),
    next_version: 'v10.3',
  };
  const S = isTalk ? { talks: target, talkIdx: 0 } : { log: target, logIdx: 0 };
  const fields = Object.fromEntries((isTalk
    ? ['#t-date', '#t-text', '#t-href']
    : ['#l-date', '#l-ver']).map(selector => [selector, { value: '', focus() {} }]));
  let changes = [];
  let fills = 0;
  const requests = [];
  const replies = [deferred(), deferred()];
  const fill = () => {
    fills++;
    const entry = target.items[isTalk ? S.talkIdx : S.logIdx];
    fields[isTalk ? '#t-date' : '#l-date'].value = entry.date;
    if (isTalk) {
      fields['#t-text'].value = entry.text;
      fields['#t-href'].value = entry.href;
    } else {
      fields['#l-ver'].value = entry.version;
      changes = [...entry.changes];
    }
  };
  const context = vm.createContext({
    S,
    $: (selector) => {
      if (selector === '#t-imgs') return { querySelector: () => null };
      if (selector === '#l-changes') return { querySelector: () => ({ focus() {} }) };
      assert.ok(selector in fields, '测试只提供当前编辑表单：' + selector);
      return fields[selector];
    },
    crypto: { randomUUID: () => 'new-entry' },
    today: () => '2026-10-20',
    readChanges: () => [...changes],
    POST: (url, body) => {
      const index = requests.length;
      requests.push({ url, body: structuredClone(body) });
      assert.ok(replies[index], '测试只会保存旧条目和新条目各一次');
      return replies[index].promise;
    },
    toast() {},
    renderTalkList() {}, renderLogList() {},
    fillTalkForm: fill, fillLogForm: fill,
    loadTalks() { assert.fail('保存响应不能重新加载并覆盖当前表单'); },
    loadLog() { assert.fail('保存响应不能重新加载并覆盖当前表单'); },
  });
  vm.runInContext(isTalk
    ? actualFunctions('function collectTalks(', 'async function deleteTalk(') +
      '\nthis.save = saveTalk; this.create = newTalk;'
    : actualFunctions('function collectLogItems(', 'async function deleteLog(') +
      '\nthis.save = saveLog; this.create = newLog;', context);
  fill();
  return {
    S, target, original, fields, requests, replies,
    save: context.save, create: context.create,
    setChanges: values => { changes = [...values]; },
    get changes() { return [...changes]; },
    get fills() { return fills; },
  };
}

test('说说保存 A 等待时新建 B，响应同步到原 A，保存 B 时不会把 A 回退', async () => {
  const h = collectionHarness('talks');
  h.fields['#t-text'].value = 'A 已保存的新文字';
  const pendingA = h.save();
  h.create();
  const created = h.target.items[1];
  h.fields['#t-text'].value = 'B 尚未保存的文字';
  const fillsBeforeResponse = h.fills;
  h.replies[0].resolve({ revision: 'b'.repeat(64) });
  await pendingA;

  assert.equal(h.target.items[0].text, 'A 已保存的新文字');
  assert.equal(h.target.items[1], created);
  assert.equal(h.S.talkIdx, 1);
  assert.equal(h.fields['#t-text'].value, 'B 尚未保存的文字');
  assert.equal(h.fills, fillsBeforeResponse, '迟到响应不能重填新条目的表单');
  assert.equal(h.target.revision, 'b'.repeat(64));

  const pendingB = h.save();
  assert.equal(h.requests[1].body.base_revision, 'b'.repeat(64));
  assert.equal(h.requests[1].body.items[0].text, 'A 已保存的新文字');
  assert.equal(h.requests[1].body.items[1].text, 'B 尚未保存的文字');
  h.replies[1].resolve({ revision: 'c'.repeat(64) });
  await pendingB;
});

test('日志保存 A 等待时新建 B，原 A 不回退，当前表单保留且下个版本按数字更新', async () => {
  const h = collectionHarness('log');
  h.fields['#l-ver'].value = 'v10.3';
  h.setChanges(['A 已保存的新改动']);
  const pendingA = h.save();
  await h.create();
  const created = h.target.items[3];
  h.fields['#l-ver'].value = 'v10.4';
  h.setChanges(['B 尚未保存的新改动']);
  const fillsBeforeResponse = h.fills;
  h.replies[0].resolve({ revision: 'b'.repeat(64) });
  await pendingA;

  assert.equal(h.target.items[0].version, 'v10.3');
  assert.equal(h.target.items[0].changes[0], 'A 已保存的新改动');
  assert.equal(h.target.items[3], created);
  assert.equal(h.S.logIdx, 3);
  assert.equal(h.fields['#l-ver'].value, 'v10.4');
  assert.deepEqual(h.changes, ['B 尚未保存的新改动']);
  assert.equal(h.fills, fillsBeforeResponse);
  assert.equal(h.target.next_version, 'v10.4', '应按已保存的两段版本最大值递增');

  const pendingB = h.save();
  assert.equal(h.requests[1].body.base_revision, 'b'.repeat(64));
  assert.equal(h.requests[1].body.items[0].version, 'v10.3');
  assert.deepEqual(h.requests[1].body.items[0].changes, ['A 已保存的新改动']);
  assert.equal(h.requests[1].body.items[3].version, 'v10.4');
  h.replies[1].resolve({ revision: 'c'.repeat(64) });
  await pendingB;
  assert.equal(h.target.next_version, 'v10.5');
  await h.create();
  assert.equal(h.fields['#l-ver'].value, 'v10.5', '保存完成后再新建应预填更新后的版本');
});
