// 执行真实表单、请求与发布函数；网络和 DOM 是测试边界，不发布任何文章。
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, 'ui/app.js'), 'utf8');

function section(start, end) {
  const a = source.indexOf(start);
  const b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `必须取得真实函数：${start}`);
  return source.slice(a, b);
}

function form(draft, original = {}) {
  const values = {
    '#f-title': '我的 Python 备忘录', '#f-date': '2026-10-25',
    '#f-summary': '记录代码', '#f-updated': '', '#f-cover': '', '#f-coveralt': '',
    '#f-draft': draft, '#f-body': '```python\nprint(1)\n```\n',
  };
  const context = vm.createContext({
    S: { post: { frontmatter: original } },
    $: selector => ({ value: values[selector] }),
    chosenTags: () => ['个人', '备忘'],
  });
  vm.runInContext(section('  function collectPost()', '  async function savePost(') +
    '\nthis.collect = collectPost; this.validate = validatePost;', context);
  return { context, values };
}

for (const original of [{}, { draft: true }, { draft: false }]) {
  test(`非草稿保存明确保留 false，原字段为 ${JSON.stringify(original)}`, () => {
    const input = { ...original, custom: '保留字段' };
    const before = { ...input };
    const h = form('false', input);
    const result = h.context.collect();
    assert.equal(result.frontmatter.draft, false, '博客必填 draft，非草稿也不能删除');
    assert.equal(result.frontmatter.custom, '保留字段');
    assert.deepEqual(input, before, '收集表单不能修改原来的元数据');
    assert.equal(result.body, h.values['#f-body']);
    assert.equal(h.context.validate(result.frontmatter).length, 0);
  });
}

test('草稿仍然明确保存为 true', () => {
  const h = form('true', { draft: false });
  assert.equal(h.context.collect().frontmatter.draft, true);
});

function publishHarness({ status = 200, result, networkError } = {}) {
  const nodes = new Map();
  const calls = { requests: [], toasts: [], refreshes: 0 };
  const context = vm.createContext({
    $: selector => {
      if (!nodes.has(selector)) nodes.set(selector, { value: '内容更新', style: {}, textContent: '', disabled: false });
      return nodes.get(selector);
    },
    fetch: async (url, init) => {
      calls.requests.push({ url, init });
      if (networkError) throw networkError;
      return { ok: status >= 200 && status < 300, status, json: async () => result };
    },
    esc: text => String(text), stamp: () => '2026-10-26',
    toast: (message, error) => calls.toasts.push({ message, error }),
    refreshState: async () => { calls.refreshes++; },
  });
  const network = section('  async function req(', '  /* ══════════════════ 三、');
  const publish = section('  const STEP_NAMES =', '  async function commitOnly()');
  vm.runInContext(network + '\n' + publish + '\nthis.POST = POST; this.runPush = runPush;', context);
  return { context, nodes, calls };
}

test('构建失败时展示后端的真实错误，恢复按钮且不重复请求', async () => {
  const tail = 'content/posts/blog-desk/index.md\ndraft: Required';
  const h = publishHarness({ result: {
    ok: false, error: '构建/测试没过 —— 没有推送。看下面的输出。',
    steps: [{ name: '构建与校验', ok: false, detail: '校验失败' }],
    build: { ok: false, returncode: 1, pages: null, tests: null, tail },
  } });
  await h.context.runPush(false);
  assert.ok(h.nodes.get('#pub-log').textContent.includes(tail), '完整失败详情必须到达日志面板');
  assert.ok(h.nodes.get('#pub-steps').innerHTML.includes('构建与校验'));
  assert.ok(h.nodes.get('#pub-steps').innerHTML.includes('step bad'));
  assert.ok(h.calls.toasts.some(item => item.error && item.message.includes('没有推送')));
  assert.equal(h.nodes.get('#btn-push').disabled, false);
  assert.equal(h.nodes.get('#btn-commit-only').disabled, false);
  assert.equal(h.calls.requests.length, 1);
  assert.equal(h.calls.requests[0].url, '/api/push');
  assert.equal(JSON.parse(h.calls.requests[0].init.body).skip_verify, false);
  assert.equal(h.calls.refreshes, 1);
});

test('跳过构建后的推送失败也保留步骤和 Git 错误详情', async () => {
  const h = publishHarness({ result: {
    ok: false, error: '远端拒绝了推送',
    steps: [{ name: '推送', ok: false, detail: '远端已有新提交' }],
    push: { messages: ['Git: rejected non-fast-forward'] },
  } });
  await h.context.runPush(true);
  assert.ok(h.nodes.get('#pub-log').textContent.includes('Git: rejected non-fast-forward'));
  assert.ok(h.nodes.get('#pub-steps').innerHTML.includes('远端已有新提交'));
  assert.equal(JSON.parse(h.calls.requests[0].init.body).skip_verify, true);
});

test('普通保存冲突继续抛错，不能当成保存成功', async () => {
  const h = publishHarness({ status: 409, result: { error: '内容已有更新，请重新载入' } });
  await assert.rejects(h.context.POST('/api/post', { body: '未保存内容' }), /内容已有更新/);
});

test('网络失败继续显示错误并释放按钮', async () => {
  const h = publishHarness({ networkError: new Error('连接已断开') });
  await h.context.runPush(false);
  assert.ok(h.nodes.get('#pub-log').textContent.includes('连接已断开'));
  assert.equal(h.nodes.get('#btn-push').disabled, false);
});

test('发布成功仍显示后端步骤与成功提示', async () => {
  const h = publishHarness({ result: {
    ok: true, steps: [{ name: '推送', ok: true, detail: '远端 abcdef01' }],
    push: { messages: ['提交已推送'] },
  } });
  await h.context.runPush(false);
  assert.ok(h.nodes.get('#pub-log').textContent.includes('提交已推送'));
  assert.ok(h.nodes.get('#pub-steps').innerHTML.includes('step ok'));
  assert.ok(h.calls.toasts.some(item => !item.error && item.message.includes('推上去了')));
});
