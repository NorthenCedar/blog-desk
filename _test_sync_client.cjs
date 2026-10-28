// 执行真实启动函数，检查同步等待期间不能编辑，失败后仍可使用本地内容。
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, 'ui/app.js'), 'utf8');
const start = source.indexOf('  async function boot()');
const end = source.indexOf("  if (document.readyState", start);
assert.ok(start >= 0 && end > start);

function deferred() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}

function harness({ config, sync = Promise.resolve({ updated: true, message: '已同步' }), tags } = {}) {
  const main = { inert: false };
  const nav = { inert: false };
  const reads = [];
  const toasts = [];
  const context = {
    S: { cfg: {} }, window: {}, location: { hash: '' }, VIEWS: { write: {} },
    forgeEditor: { init() {} },
    $: selector => selector === 'main.work' ? main : nav, $$: () => [],
    GET: async () => config ? config.promise : {},
    POST: async () => sync,
    toast: message => toasts.push(message),
    loadTags: async () => { reads.push('tags'); if (tags) await tags.promise; },
    refreshState: async () => {},
    go: () => {},
    loadPosts: async () => reads.push('posts'),
  };
  for (const name of ['Shell','Keys','Write','Tags','Log','Talks','Photos','Comments','Publish','Shortcuts','Settings']) {
    context['init' + name] = () => {};
  }
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\nthis.boot = boot;', context);
  return { ...context, main, nav, reads, toasts };
}

test('从首次异步配置读取开始就阻止编辑，直到同步和内容初始化全部完成', async () => {
  const config = deferred();
  const tags = deferred();
  const h = harness({ config, tags });
  const pending = h.boot();
  const blockedAtConfig = h.main.inert && h.nav.inert;
  config.resolve({});
  await new Promise(resolve => setImmediate(resolve));
  const blockedAtTags = h.main.inert && h.nav.inert;
  tags.resolve();
  await pending;
  assert.equal(blockedAtConfig, true, '等待配置时仍可点击会抢在同步前开始写作');
  assert.equal(blockedAtTags, true, '内容尚未初始化完不能提前开始编辑');
  assert.equal(h.main.inert, false);
  assert.equal(h.nav.inert, false);
  assert.equal(h.window.__deskReady, true);
});

test('同步完成前不读取旧内容，完成后再开放编辑', async () => {
  const sync = deferred();
  const h = harness({ sync: sync.promise });
  const pending = h.boot();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(h.reads, []);
  assert.equal(h.main.inert, true);
  sync.resolve({ updated: true, message: '已同步云端内容' });
  await pending;
  assert.deepEqual(h.reads, ['tags', 'posts']);
  assert.equal(h.main.inert, false);
});

test('同步网络失败时保留说明并继续打开本地内容', async () => {
  const h = harness({ sync: Promise.reject(new Error('离线')) });
  await h.boot();
  assert.equal(h.main.inert, false);
  assert.equal(h.nav.inert, false);
  assert.equal(h.window.__deskReady, true);
  assert.deepEqual(h.reads, ['tags', 'posts']);
  assert.ok(h.toasts.some(message => message.includes('离线')));
});

function manualSyncHarness(failTags = false, tagsWait) {
  const tagsStart = source.indexOf('  async function loadTags()');
  const tagsEnd = source.indexOf('  async function loadPosts()', tagsStart);
  const syncStart = source.indexOf('  async function syncRemote()');
  const syncEnd = source.indexOf('  /* ══════════════════ 十三', syncStart);
  assert.ok(tagsStart >= 0 && tagsEnd > tagsStart && syncStart >= 0 && syncEnd > syncStart);
  const buttons = new Map(['#btn-sync-remote', '#btn-push', '#btn-commit-only']
    .map(selector => [selector, { disabled: false }]));
  buttons.set('main.work', { inert: false });
  buttons.set('#nav', { inert: false });
  const form = { body: '尚未保存的输入', selected: ['随笔'] };
  const context = {
    S: { tags: ['随笔'], post: { frontmatter: { tags: ['随笔'] } } }, form,
    $: selector => buttons.get(selector),
    POST: async () => ({ updated: true, message: '已同步云端内容' }),
    GET: async () => { if (failTags) throw new Error('读取失败'); if (tagsWait) await tagsWait.promise; return { items: ['随笔', '新标签'] }; },
    loadPublish: async () => {},
    toasts: [],
  };
  context.toast = message => context.toasts.push(message);
  vm.createContext(context);
  vm.runInContext(source.slice(tagsStart, tagsEnd) + source.slice(syncStart, syncEnd)
    + '\nthis.syncRemote = syncRemote;', context);
  return context;
}

test('手动同步等待最新标签期间不能抢先打开文章，完成后恢复现有表单', async () => {
  const tagsWait = deferred();
  const h = manualSyncHarness(false, tagsWait);
  const pending = h.syncRemote();
  await new Promise(resolve => setImmediate(resolve));
  const blocked = h.$('main.work').inert && h.$('#nav').inert;
  tagsWait.resolve();
  await pending;
  assert.equal(blocked, true, '文件已更新但标签仍旧时不能允许打开文章');
  assert.equal(h.$('main.work').inert, false);
  assert.equal(h.$('#nav').inert, false);
  assert.equal(h.form.body, '尚未保存的输入');
});

test('手动同步刷新标签缓存，随后打开云端新文章时可以保留新标签；现有输入不重载', async () => {
  const h = manualSyncHarness();
  await h.syncRemote();
  assert.deepEqual(Array.from(h.S.tags), ['随笔', '新标签']);
  assert.equal(h.form.body, '尚未保存的输入');
  assert.deepEqual(h.form.selected, ['随笔']);
});

test('内容已同步但最新标签读取失败时清除旧缓存并准确提示，避免用旧名册保存新文章', async () => {
  const h = manualSyncHarness(true);
  await h.syncRemote();
  assert.deepEqual(Array.from(h.S.tags), []);
  assert.ok(h.toasts.some(message => message.includes('内容已同步') && message.includes('标签')));
  assert.equal(h.form.body, '尚未保存的输入');
});
