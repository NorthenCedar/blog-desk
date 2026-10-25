/* 写作台 · 前端控制器
   ══════════════════════════════════════════════════════════════════════════
   这个文件只做三件事：
     ① 把界面接到本机的 Python 服务（同源 /api/*，只监听 127.0.0.1）；
     ② 桌面窗口那三个键 + 标题栏拖动（走 pywebview 桥；没有桥就降级为普通网页）；
     ③ 一个够用的 Markdown 预览器。

   一行文件读写都不用在这里写 —— 所有磁盘、git、构建、推送都在 Python 那边。
   站规：**不许有任何外部请求**（CDN / 字体 / 图片）。所以这里没有外域 fetch。
   ══════════════════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  /* ══════════════════ 一、工具 ══════════════════ */

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const esc = (s) =>
    String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));

  const pad2 = (n) => String(n).padStart(2, '0');
  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  };
  const stamp = () => {
    const d = new Date();
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  };
  /** 线上留言的时间戳（ISO/UTC）→ 本机可读时间（站主不用 ISO 戳）。 */
  function localTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  }
  const kb = (n) => (n >= 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');

  /** 「图片名 → alt 兜底」：站规要求每个 <img> 的 alt 非空。 */
  const altFromName = (name) =>
    String(name).replace(/\.[a-z0-9]+$/i, '').replace(/[_-]+/g, ' ').trim() || '图片';

  /* ══════════════════ 二、与后端说话 ══════════════════ */

  async function req(path, { method = 'GET', body } = {}) {
    const init = { method, headers: {} };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    const r = await fetch(path, init);
    let j = null;
    try { j = await r.json(); } catch { /* 可能是空响应 */ }
    if (!r.ok || (j && j.error)) {
      throw new Error((j && j.error) || `请求失败（HTTP ${r.status}）`);
    }
    return j || {};
  }
  const GET = (p) => req(p);
  const POST = (p, body) => req(p, { method: 'POST', body: body || {} });

  /* ══════════════════ 三、提示条 / 对话框 ══════════════════ */

  let toastTimer = 0;
  function toast(msg, isErr) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = 'toast on' + (isErr ? ' err' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = 'toast' + (isErr ? ' err' : ''); }, isErr ? 6000 : 2600);
  }

  /**
   * 应用内对话框。
   * ★ 为什么不用 window.prompt / confirm：在 WebView2 里不保证弹得出来（也难看）。
   * 返回：点「确定」→ 一个 { root, values } 对象；取消/点遮罩/Esc → null。
   */
  function dialog({ title, descHTML = '', bodyHTML = '', okText = '确定',
                    cancelText = '取消', danger = false, wide = false }) {
    return new Promise((resolve) => {
      const mask = document.createElement('div');
      mask.className = 'mask on';
      mask.innerHTML =
        `<div class="dialog${wide ? ' wide' : ''}">
           <h3></h3>
           ${descHTML ? '<div class="desc"></div>' : ''}
           <div class="dbody"></div>
           <div class="acts">
             <button class="btn" data-x="cancel"></button>
             <button class="btn ${danger ? 'danger' : 'primary'}" data-x="ok"></button>
           </div>
         </div>`;
      mask.querySelector('h3').textContent = title;
      if (descHTML) mask.querySelector('.desc').innerHTML = descHTML;
      mask.querySelector('.dbody').innerHTML = bodyHTML;
      mask.querySelector('[data-x=cancel]').textContent = cancelText;
      mask.querySelector('[data-x=ok]').textContent = okText;
      document.body.appendChild(mask);

      let settled = false;
      const done = (v) => {
        if (settled) return;
        settled = true;
        document.removeEventListener('keydown', onKey, true);
        mask.remove();
        resolve(v);
      };
      const read = () => ({
        root: mask,
        values: Object.fromEntries(
          $$('[data-k]', mask).map((el) => [el.dataset.k, el.type === 'checkbox' ? el.checked : el.value])
        ),
      });
      mask.querySelector('[data-x=cancel]').onclick = () => done(null);
      mask.querySelector('[data-x=ok]').onclick = () => done(read());
      mask.addEventListener('mousedown', (e) => { if (e.target === mask) done(null); });
      const onKey = (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); done(null); }
        else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); done(read()); }
      };
      document.addEventListener('keydown', onKey, true);
      const first = mask.querySelector('input, textarea, select');
      if (first) setTimeout(() => { first.focus(); first.select?.(); }, 40);
    });
  }

  const askConfirm = async (title, descHTML, opts = {}) =>
    !!(await dialog({ title, descHTML, okText: opts.okText || '确定', danger: !!opts.danger }));

  /** 表单对话框：fields = [{k,label,placeholder,value,type,hint}] */
  async function askForm(title, descHTML, fields, opts = {}) {
    const body = fields.map((f) => {
      const input = f.type === 'textarea'
        ? `<textarea data-k="${f.k}" rows="${f.rows || 4}" placeholder="${esc(f.placeholder || '')}">${esc(f.value || '')}</textarea>`
        : `<input type="text" data-k="${f.k}" placeholder="${esc(f.placeholder || '')}" value="${esc(f.value || '')}">`;
      return `<label class="field">${f.label ? `<span>${esc(f.label)}</span>` : ''}${input}
        ${f.hint ? `<span class="hint">${f.hint}</span>` : ''}</label>`;
    }).join('');
    const r = await dialog({ title, descHTML, bodyHTML: `<div class="stack">${body}</div>`, okText: opts.okText });
    return r ? r.values : null;
  }

  const copyText = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // WebView2 / 无权限时的兜底
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;left:-9999px';
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch { ok = false; }
      ta.remove();
      return ok;
    }
  };

  /** 文件选择。用户取消时也要 resolve（否则 await 永远挂着）。 */
  function pickFiles(accept = 'image/*') {
    return new Promise((resolve) => {
      const inp = $('#file-pick');
      inp.value = '';
      inp.accept = accept;
      let settled = false;
      const finish = (files) => {
        if (settled) return;
        settled = true;
        window.removeEventListener('focus', onFocus);
        resolve(files);
      };
      const onFocus = () => setTimeout(() => finish(Array.from(inp.files || [])), 400);
      inp.addEventListener('change', () => finish(Array.from(inp.files || [])), { once: true });
      window.addEventListener('focus', onFocus, { once: true });
      inp.click();
    });
  }

  /**
   * 给按钮套一层「跑着的时候别再来一次」。
   * ★ 为什么需要：文件选择这件事在浏览器里可能触发多次回调/一次双击
   *   （实测无头 Chrome 下同一张图会被入库两遍，多出一个 `名字-2.jpg`）。
   *   入库是有副作用的写盘动作，必须幂等。
   */
  function guard(btn, fn) {
    let busy = false;
    return async (...args) => {
      if (busy) return;
      busy = true;
      if (btn) btn.disabled = true;
      try {
        return await fn(...args);
      } finally {
        busy = false;
        if (btn) btn.disabled = false;
      }
    };
  }

  const fileToDataURL = (file) =>
    new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = () => rej(new Error('读不出这个文件'));
      fr.readAsDataURL(file);
    });

  /**
   * 缩略图挑选器。返回被选中的 item，或 null（取消）。
   * ★ 为什么不用 dialog()：这里需要第三条出口（点某一张就结束）。
   *   往 dialog 里塞回调会让它的清理逻辑（Esc/遮罩/键盘）漏掉。
   */
  function pickFromGrid(title, descHTML, items) {
    return new Promise((resolve) => {
      const mask = document.createElement('div');
      mask.className = 'mask on';
      const cell = 'border:1px solid var(--hair);border-radius:7px;padding:0;width:80px;height:80px;' +
                   'overflow:hidden;cursor:pointer;background:var(--code-bg);flex:none';
      mask.innerHTML =
        `<div class="dialog wide">
           <h3>${esc(title)}</h3>
           <div class="desc">${descHTML}</div>
           <div style="display:flex;flex-wrap:wrap;gap:7px;max-height:48vh;overflow:auto">
             ${items.map((p, i) => `<button type="button" data-i="${i}" title="${esc(p.album + ' / ' + p.name)}"
                 style="${cell}"><img src="/api/file?path=${encodeURIComponent(p.path)}"
                 alt="${esc(p.name)}" loading="lazy"
                 style="width:100%;height:100%;object-fit:cover;display:block"></button>`).join('')}
           </div>
           <div class="acts"><button class="btn" data-x="cancel">取消</button></div>
         </div>`;
      document.body.appendChild(mask);
      let settled = false;
      const done = (v) => {
        if (settled) return;
        settled = true;
        document.removeEventListener('keydown', onKey, true);
        mask.remove();
        resolve(v);
      };
      const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } };
      document.addEventListener('keydown', onKey, true);
      mask.querySelector('[data-x=cancel]').addEventListener('click', () => done(null));
      mask.addEventListener('mousedown', (e) => { if (e.target === mask) done(null); });
      $$('[data-i]', mask).forEach((b) =>
        b.addEventListener('click', () => done(items[+b.dataset.i])));
    });
  }

  function insertAtCursor(ta, text) {
    const s = ta.selectionStart ?? ta.value.length;
    const e = ta.selectionEnd ?? s;
    const before = ta.value.slice(0, s);
    const after = ta.value.slice(e);
    const lead = before && !before.endsWith('\n') ? '\n' : '';
    const add = lead + text + '\n';
    ta.value = before + add + after;
    const pos = (before + add).length;
    ta.focus();
    ta.setSelectionRange(pos, pos);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function formatBodyBlock(body, start, end, level, language = 'python') {
    const { lines, blocks } = bodyCodeBlocks(body);
    const first = Math.max(0, lines.findLastIndex(line => line.offset <= start));
    const last = Math.max(first, lines.findLastIndex(line => line.offset < end || line.offset <= start));
    if (level === 'code' || level === 'language' || level === 0) {
      const hits = blocks.filter(block => block.first <= last && block.last >= first);
      const block = hits[0];
      const unchanged = { body, start, end };
      if (block) {
        if (hits.length !== 1 || first < block.first || last > block.last) {
          return { ...unchanged, error:'选区跨越了代码块边界，请只选择一个完整代码块或块内代码。' };
        }
        if (block.nested || !block.closed) {
          return { ...unchanged, error:'引用、列表、缩进中的围栏或未闭合代码块请在源码中调整，当前内容已保留。' };
        }
        const open = lines[block.first], close = lines[block.last];
        if (level === 'code') return unchanged;
        if (level === 'language') {
          if (!/^(python|javascript|typescript|json|bash|text)$/.test(language)) return unchanged;
          const head = /^( {0,3})(`{3,}|~{3,})([ \t]*)([^ \t]*)(.*)$/.exec(open.text);
          const replacement = head[1] + head[2] + head[3] + language + head[5];
          const after = open.offset + open.text.length;
          const delta = replacement.length - open.text.length;
          const move = pos => pos >= after ? pos + delta : Math.min(pos, open.offset + replacement.length);
          return { body:body.slice(0, open.offset) + replacement + body.slice(after), start:move(start), end:move(end) };
        }
        const contentStart = open.offset + open.text.length + open.newline.length;
        // 最后一个换行分隔正文与关闭围栏；块外的换行始终原样保留。
        const content = body.slice(contentStart, close.offset).replace(/\r?\n$/, '');
        const move = pos => open.offset + Math.max(0, Math.min(content.length, pos - contentStart));
        return { body:body.slice(0, open.offset) + content + body.slice(close.offset + close.text.length), start:move(start), end:move(end) };
      }
      if (level === 'language') return unchanged;
      if (level === 'code') {
        const newline = lines[first].newline || lines.find(line => line.newline)?.newline || '\n';
        const s = start === end ? start : lines[first].offset;
        const e = start === end ? end : lines[last].offset + lines[last].text.length;
        const content = body.slice(s, e);
        const longest = Array.from(content.matchAll(/`+/g)).reduce((length, match) => Math.max(length, match[0].length), 0);
        const fence = '`'.repeat(Math.max(3, longest + 1));
        const lang = /^(python|javascript|typescript|json|bash|text)$/.test(language) ? language : 'python';
        const lead = s > 0 && body[s - 1] !== '\n' ? newline : '';
        const tail = e < body.length && body[e] !== '\r' && body[e] !== '\n' ? newline : '';
        const prefix = lead + fence + lang + newline;
        const replacement = prefix + content + newline + fence + tail;
        return { body:body.slice(0, s) + replacement + body.slice(e), start:s + prefix.length, end:s + prefix.length + content.length };
      }
    }
    let fence = null;
    const output = lines.map((line, index) => {
      // 只为识别代码边界剥去容器前缀，输出始终保留原行。
      const probe = line.text.replace(/^(?: {0,3}>[ \t]?)+/, '').replace(/^\s*(?:[-+*]|\d+[.)])[ \t]+/, '');
      const marker = /^\s*(`{3,}|~{3,})(.*)$/.exec(probe);
      const inCode = !!fence || !!marker || /^(?: {4}|\t)/.test(line.text);
      if (marker) {
        if (!fence) fence = marker[1];
        else if (marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null;
      }
      if (index < first || index > last || inCode || (!line.text.trim() && start !== end) || /^\s*</.test(line.text)) return line.text + line.newline;
      const text = line.text.replace(/^ {0,3}#{1,6}(?:[ \t]+|$)/, '');
      return (level ? '#'.repeat(level) + ' ' : '') + text + line.newline;
    });
    const result = output.join('');
    const before = output.slice(0, first).join('').length;
    if (start === end) {
      const diff = output[first].length - (lines[first].text + lines[first].newline).length;
      const cursor = Math.max(before, start + diff);
      return { body:result, start:cursor, end:cursor };
    }
    return { body:result, start:before, end:output.slice(0, last+1).join('').length - lines[last].newline.length };
  }

  // 同一份边界信息用于代码操作与预览；容器内的代码只识别并保护，不改其源码。
  function bodyCodeBlocks(body) {
    const lines = [];
    for (const match of body.matchAll(/([^\r\n]*)(\r?\n|$)/g)) {
      if (!match[0] && body && !body.endsWith('\n')) continue;
      lines.push({ text:match[1], newline:match[2], offset:match.index });
    }
    const blocks = [];
    let block = null;
    let listIndent = null;
    let listBlank = false;
    for (const [index, line] of lines.entries()) {
      const probe = line.text.replace(/^(?: {0,3}>[ \t]?)+/, '').replace(/^\s*(?:[-+*]|\d+[.)])[ \t]+/, '');
      const marker = /^(\s*)(`{3,}|~{3,})(.*)$/.exec(block?.nested ? probe : line.text);
      if (block) {
        if (marker && (block.nested || marker[1].length <= 3) && marker[2][0] === block.marker[0] && marker[2].length >= block.marker.length && !marker[3].trim()) {
          block.last = index; block.closed = true; block = null;
        }
        continue;
      }
      const list = /^( *)(?:[-+*]|\d+[.)])[ \t]+/.exec(line.text);
      if (list) { listIndent = list[0].length; listBlank = false; }
      else if (!line.text.trim()) listBlank = true;
      else if (listIndent !== null) {
        // 无空行的普通文字可能仍是列表的懒延续；不能因此把后续围栏误当顶层。
        if (/^ */.exec(line.text)[0].length < listIndent && (listBlank || /^(?:#{1,6}\s|>|`{3,}|~{3,}|[-*_]{3,})/.test(line.text))) listIndent = null;
        listBlank = false;
      }
      const opening = /^(\s*)(`{3,}|~{3,})(.*)$/.exec(probe);
      if (!opening || (opening[2][0] === '`' && opening[3].includes('`'))) continue;
      block = { first:index, last:lines.length - 1, marker:opening[2], closed:false,
        nested:probe !== line.text || listIndent !== null || !/^ {0,3}(`{3,}|~{3,})/.test(line.text),
        language:opening[3].trim().split(/[ \t]/)[0] };
      blocks.push(block);
    }
    return { lines, blocks };
  }

  function applyBodyEdit(body, result) {
    if (result.error) { toast(result.error, true); return; }
    body.focus();
    if (body.value !== result.body) {
      // 只替换变化范围，通过 Chromium/WebView2 的编辑事务保留输入与格式操作的撤销历史。
      const before = body.value;
      let first = 0, oldEnd = before.length, newEnd = result.body.length;
      while (first < oldEnd && first < newEnd && before[first] === result.body[first]) first++;
      while (oldEnd > first && newEnd > first && before[oldEnd - 1] === result.body[newEnd - 1]) { oldEnd--; newEnd--; }
      body.setSelectionRange(first, oldEnd);
      let inserted = false;
      try { inserted = document.execCommand('insertText', false, result.body.slice(first, newEnd)); } catch { /* 保留原内容与撤销记录 */ }
      if (!inserted) { toast('当前编辑器无法执行可撤销的格式操作，请使用源码编辑。', true); return; }
    }
    body.setSelectionRange(result.start, result.end);
  }

  /* ══════════════════ 四、迷你 Markdown ══════════════════
     只覆盖站上文章真正会用的语法；**不求全**，求可预期。
     图片路径：正文写 `![](./名字.jpg)`（相对文章目录）⇒ 换成 /api/file?path=<绝对路径>。
     ★ 先抽代码块/行内代码，再转义 HTML —— 顺序反了就会把代码里的 < > 当标签。 */

  function renderMd(src, baseDir) {
    const fences = [];
    const inlines = [];
    const links = [];
    const clips = [];
    let s = String(src || '').replace(/\r\n?/g, '\n');

    // ★ 抽取顺序有讲究：围栏代码 → 行内代码 → 图片 → 链接。
    //   图片必须排在链接前面，否则链接规则会先吃掉 `![alt](url)` 的尾巴。
    //   全都抽成 \u0000XXn\u0000 占位符 —— 这种字符不含 HTML 特殊字符，转义后仍然在。
    const codeBlocks = bodyCodeBlocks(s);
    for (const block of codeBlocks.blocks.slice().reverse()) {
      // nested 只限制源码格式操作；预览仍抽取容器内的闭合代码，避免当作正文或图片解析。
      if (!block.closed) continue;
      const open = codeBlocks.lines[block.first], close = codeBlocks.lines[block.last];
      fences.push(s.slice(open.offset + open.text.length + open.newline.length, close.offset).replace(/\n$/, ''));
      s = s.slice(0, open.offset) + `\n\u0000F${fences.length - 1}\u0000\n` + s.slice(close.offset + close.text.length);
    }
    s = s.replace(/`([^`\n]+)`/g, (_m, code) => {
      inlines.push(code);
      return `\u0000I${inlines.length - 1}\u0000`;
    });
    // 只放行写作台生成的本地视频，不允许正文任意 HTML 绕过预览的转义。
    s = s.replace(/<video\b([^>]*)>[\s\S]*?<\/video>/gi, (html, attrs) => {
      const u = /\bsrc="(\/videos\/[0-9a-f]{64}\.(?:mp4|webm))"/.exec(attrs)?.[1];
      if (!u) return html;
      const label = /\baria-label="([^"]*)"/.exec(attrs)?.[1] || '视频';
      const decode = document.createElement('textarea');
      decode.innerHTML = label;
      clips.push(videoMarkup(u, decode.value, true));
      return `\n\u0000V${clips.length - 1}\u0000\n`;
    });
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_m, alt, u) => {
      links.push({ img: true, alt, u });
      return `\u0000L${links.length - 1}\u0000`;
    });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_m, txt, u) => {
      links.push({ img: false, txt, u });
      return `\u0000L${links.length - 1}\u0000`;
    });

    s = esc(s);

    const imgURL = (u) => {
      if (!baseDir) return u;
      if (/^(https?:|\/|data:)/.test(u)) return u;
      const dir = String(baseDir).replace(/[\\/]+$/, '');
      const rel = u.replace(/^\.\//, '');
      return '/api/file?path=' + encodeURIComponent(dir + '/' + rel);
    };

    const inline = (t) =>
      t
        .replace(/\u0000L(\d+)\u0000/g, (_m, i) => {
          const L = links[+i];
          if (!L) return '';
          return L.img
            ? `<img src="${esc(imgURL(L.u))}" alt="${esc(L.alt || '')}" loading="lazy">`
            : `<a href="${esc(L.u)}" target="_blank" rel="noopener noreferrer">${esc(L.txt)}</a>`;
        })
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
        .replace(/~~([^~]+)~~/g, '<del>$1</del>')
        .replace(/\u0000I(\d+)\u0000/g, (_m, i) => `<code>${esc(inlines[+i])}</code>`);

    const lines = s.split('\n');
    const out = [];
    let para = [];
    let list = null;      // { tag, items: [] }
    let quote = [];
    let table = null;     // { head: [], rows: [] }

    // ★ 行首的 `>` 在上面的 esc() 里已经变成 `&gt;` 了（esc 会把 > 转义）。
    //   所以引用块必须同时认这两种写法 —— 只认 `>` 的话引用永远解析不出来。
    const QUOTE = /^(?:>|&gt;)\s?(.*)$/;

    const flushPara = () => {
      if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; }
    };
    const flushList = () => {
      if (list) {
        out.push(`<${list.tag}>${list.items.map((x) => `<li>${inline(x)}</li>`).join('')}</${list.tag}>`);
        list = null;
      }
    };
    const flushQuote = () => {
      if (quote.length) {
        out.push(`<blockquote>${quote.map((x) => `<p>${inline(x)}</p>`).join('')}</blockquote>`);
        quote = [];
      }
    };
    const flushTable = () => {
      if (table) {
        const h = table.head.map((c) => `<th>${inline(c)}</th>`).join('');
        const b = table.rows.map((r) =>
          `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('');
        out.push(`<table><thead><tr>${h}</tr></thead><tbody>${b}</tbody></table>`);
        table = null;
      }
    };
    const flushAll = () => { flushTable(); flushPara(); flushList(); flushQuote(); };
    const cells = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());

    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i];
      const line = raw.trim();

      if (/^\u0000V\d+\u0000$/.test(line)) {
        flushAll();
        out.push(clips[+line.replace(/\u0000V|\u0000/g, '')]);
        continue;
      }

      if (/^\u0000F\d+\u0000$/.test(line)) {
        flushAll();
        const idx = +line.replace(/\u0000F|\u0000/g, '');
        out.push(`<pre><code>${esc(fences[idx])}</code></pre>`);
        continue;
      }
      if (!line) { flushAll(); continue; }

      // 表格：本行是 | … | 且下一行是分隔行
      if (/^\|.*\|$/.test(line) && table === null) {
        const next = (lines[i + 1] || '').trim();
        if (/^\|[\s:|-]+\|$/.test(next) && /-/.test(next)) {
          flushAll();
          table = { head: cells(line), rows: [] };
          i++;
          continue;
        }
      }
      if (table) {
        if (/^\|.*\|$/.test(line)) { table.rows.push(cells(line)); continue; }
        flushTable();
      }

      let m;
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
        flushAll();
        const lv = m[1].length;
        out.push(`<h${lv}>${inline(m[2])}</h${lv}>`);
        continue;
      }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) { flushAll(); out.push('<hr>'); continue; }
      if ((m = line.match(QUOTE))) {
        flushPara(); flushList(); flushTable();
        quote.push(m[1]);
        continue;
      }
      if (quote.length && !QUOTE.test(line)) flushQuote();
      if ((m = line.match(/^[-*+]\s+(.*)$/))) {
        flushPara(); flushTable(); flushQuote();
        if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; }
        list.items.push(m[1]);
        continue;
      }
      if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {
        flushPara(); flushTable(); flushQuote();
        if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [] }; }
        list.items.push(m[1]);
        continue;
      }
      flushList();
      para.push(line);
    }
    flushAll();
    return out.join('\n');
  }

  /* ══════════════════ 五、桌面窗口桥 ══════════════════ */

  const bridge = () => (window.pywebview && window.pywebview.api) || null;

  function initShell() {
    const lightIds = ['btn-close', 'btn-min', 'btn-max'];
    const fnOf = { 'btn-close': 'win_close', 'btn-min': 'win_min', 'btn-max': 'win_max' };
    const tipOf = {
      'btn-close': '关闭', 'btn-min': '最小化（到任务栏）', 'btn-max': '最大化 / 还原',
    };

    const sync = () => {
      const has = !!bridge();
      document.body.classList.toggle('bare', !has);
      for (const id of lightIds) {
        const el = document.getElementById(id);
        el.title = has ? tipOf[id] : `${tipOf[id]} —— 浏览器模式下不可用（想要真窗口请双击桌面上的「写作台」）`;
      }
    };

    for (const id of lightIds) {
      const el = document.getElementById(id);
      el.addEventListener('click', async () => {
        const api = bridge();
        if (!api) {
          toast('现在是浏览器模式，没有窗口控制键。双击桌面上的「写作台」才是真窗口。', true);
          return;
        }
        try {
          const r = await api[fnOf[id]]();
          if (r && r.ok === false) toast('窗口操作失败：' + (r.error || '未知原因'), true);
        } catch (e) {
          toast('窗口操作失败：' + e.message, true);
        }
      });
    }

    // ── 拖动窗口（无边框窗口的标题栏行为）──
    // 2026-10-09 重写，一次解决两件事：**只能拖侧栏那条黑边**、以及**拖起来发抖**。
    //
    // ① 整条顶栏都能拖
    //    原来只监听 .titlebar 的事件 —— 那是侧栏里的一条，只有 232px 宽，
    //    右边一大片（.work-head 那一带）按下去毫无反应。
    //    现在在 document 上判断"按下点是不是落在顶部那一条里"，横跨整个窗口宽度；
    //    按钮/输入框这类可交互元素排除在外，免得把点击抢走。
    //
    // ② 抖动是两个原因叠在一起
    //    · 用 clientX/clientY 算位移 —— 它相对**窗口**，窗口一动它自己就变，
    //      于是"窗口动 → 坐标变 → 又算出一个位移"成了正反馈，来回振荡；
    //    · 用 win.x 累加位移 —— 读窗口位置是异步的、会滞后，滞后一次就把误差叠一次。
    //    改成 screenX/screenY（相对**屏幕**，窗口怎么动都不影响它）+ **绝对定位**
    //    （每帧直接把目标坐标发过去，不累加）。
    //
    // ③ ★ 单位：屏幕坐标与窗口坐标**本来就是同一把尺子，一个系数都不用乘**。
    //    依据（pywebview 自己的源码，两头都换算过）：
    //      webview/platforms/winforms.py
    //        get_position() → int(i.Left / scale)     ← 读出来时**除**过 scale
    //        Form.move()    → "# Input x/y are in logical pixels" → x * scale ← 写进去时**乘**回来
    //    ⇒ 窗口坐标对外一律是**逻辑像素（DIP）**。而 MouseEvent.screenX/screenY
    //      是 CSS px，Windows 下 1 CSS px ≡ 1 DIP，所以两者恒等（系数 = 1）。
    //    上一版在这里多乘了一次 devicePixelRatio：本机 144 DPI（150% 缩放），
    //    于是窗口一拖就跑出鼠标的 1.5 倍速 —— 站主：「拖动不跟鼠标，会比鼠标快一点」。
    //    ⚠ 别"顺手"再把 devicePixelRatio 加回来：它不是屏幕缩放比，别拿它当单位换算，
    //      页面缩放（zoom）一旦非 100% 它还会混进 zoom 因子，越算越歪。
    const topBand = () => {
      const tb = $('#titlebar');
      const head = document.querySelector('.work-head');
      const a = tb ? tb.getBoundingClientRect().height : 46;
      const b = head ? head.getBoundingClientRect().bottom : 0;
      return Math.max(a, b, 46);
    };
    const INTERACTIVE =
      'button, a, input, select, textarea, label, [contenteditable="true"], .no-drag';

    let drag = null;   // {sx, sy, wx, wy}；wx/wy 取回来之前不动（都是"逻辑像素"，见上面 ③）
    let want = null;   // 最近一次算出的目标坐标（rAF 合并，只发最后一个）
    let raf = 0;
    let lastPtr = { x: 0, y: 0 };

    const flush = () => {
      raf = 0;
      const api = bridge();
      if (!api || !drag || !want) return;
      const to = want;
      want = null;
      api.win_moveto(to.x, to.y);
    };

    document.addEventListener('pointerdown', (e) => {
      const api = bridge();
      if (!api || e.button !== 0) return;
      if (document.querySelector('.mask')) return;      // 有对话框时不要抢
      if (e.clientY > topBand()) return;                 // 只认顶部那一条
      if (e.target.closest && e.target.closest(INTERACTIVE)) return;

      drag = { sx: e.screenX, sy: e.screenY, wx: null, wy: null };
      lastPtr = { x: e.screenX, y: e.screenY };
      document.body.classList.add('dragging');
      // 起点位置是异步取回来的：回来时把基准重新对齐到"此刻"，
      // 否则这几毫秒里鼠标已经动了，窗口会先跳一下。
      api.win_pos().then((p) => {
        if (!drag) return;
        if (!p || p.ok === false || p.maximized) {
          // 最大化时不拖（move 对最大化窗口无效，硬拖只会显得"拖不动"）
          drag = null;
          document.body.classList.remove('dragging');
          return;
        }
        drag.wx = p.x;
        drag.wy = p.y;
        drag.sx = lastPtr.x;
        drag.sy = lastPtr.y;
      }).catch(() => {
        drag = null;
        document.body.classList.remove('dragging');
      });
    });

    document.addEventListener('pointermove', (e) => {
      if (!drag) return;
      lastPtr = { x: e.screenX, y: e.screenY };
      if (drag.wx === null) return;                      // 起点还没回来，先别动
      want = {
        x: Math.round(drag.wx + (e.screenX - drag.sx)),
        y: Math.round(drag.wy + (e.screenY - drag.sy)),
      };
      if (!raf) raf = requestAnimationFrame(flush);
    });

    const endDrag = () => {
      if (!drag) return;
      drag = null;
      want = null;
      document.body.classList.remove('dragging');
    };
    document.addEventListener('pointerup', endDrag);
    document.addEventListener('pointercancel', endDrag);
    window.addEventListener('blur', endDrag);

    sync();
    window.addEventListener('pywebviewready', sync);
    setTimeout(sync, 1500);

    // ★ 浏览器模式的说明要**现在就发**，不能放到 boot() 的最后。
    //   原来那句在 boot 尾部 `setTimeout(…, 900)`：而 boot 里要先等 refreshState()
    //   去问 GitHub（可能好几秒），于是这条提示会在用户已经点了几下之后才跳出来，
    //   把用户正在看的提示（比如"已保存"）盖掉。
    if (!bridge()) {
      setTimeout(() => toast('浏览器模式：功能都能用，只有那三个窗口键不可用。'
        + '双击桌面上的「写作台」才是真窗口。'), 500);
    }
  }

  /* ══════════════════ 六、状态与路由 ══════════════════ */

  const S = {
    view: 'write',
    cfg: {},
    state: null,
    tags: [],
    tagInfo: null,     // /api/tags 的整份返回（标签管理页用）
    posts: [],
    post: null,        // { slug, dir, frontmatter, body }
    talks: null,       // { items, title, intro, head, newline }
    talkIdx: -1,
    log: null,
    logIdx: -1,
    albums: [],
    album: '',
    photos: [],
    comments: [],
  };

  const VIEWS = {
    write: { title: '写作', hint: '文章 → content/posts/<slug>/index.md', enter: () => loadPosts() },
    tags: { title: '标签', hint: 'src/tag-registry.ts —— 站上标签的唯一事实来源',
            enter: () => loadTagManager() },
    publish: { title: '发布', hint: 'npm run verify → 提交 → 推送 main', enter: () => loadPublish() },
    log: { title: '日志', hint: 'content/pages/log.md', enter: () => loadLog() },
    talks: { title: '说说', hint: 'content/pages/talks.md ＋ src/assets/talks/', enter: () => loadTalks() },
    photos: { title: '相册照片', hint: 'content/gallery/<相册>/（原图不部署，构建时出 webp）', enter: () => loadPhotos() },
    comments: { title: '留言', hint: '线上 Cloudflare D1（删除需要管理密钥）', enter: () => loadComments() },
    shortcuts: { title: '快捷入口', hint: '本机目录与线上站点', enter: null },
    settings: { title: '设置', hint: '仓库路径 / 站点地址 / 管理密钥', enter: () => loadSettings() },
  };

  function go(view) {
    if (!VIEWS[view]) view = 'write';
    S.view = view;
    // 记进 hash：刷新/重开能回到同一页（也方便逐页做冒烟检查）
    try { history.replaceState(null, '', '#' + view); } catch { /* 忽略 */ }
    $$('#nav button').forEach((b) => {
      if (b.dataset.view === view) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    $$('.view').forEach((v) => v.classList.toggle('on', v.dataset.view === view));
    $('#view-title').textContent = VIEWS[view].title;
    $('#view-hint').textContent = VIEWS[view].hint || '';
    $('.work-body').scrollTop = 0;
    const enter = VIEWS[view].enter;
    if (enter) Promise.resolve(enter()).catch((e) => toast(e.message, true));
  }

  /* ══════════════════ 七、写作 ══════════════════ */

  async function loadTags() {
    const r = await GET('/api/tags');
    S.tags = r.items || [];
    S.tagInfo = r;          // 管理页要的：usage / unused / doc 漂移
  }

  async function loadPosts() {
    const r = await GET('/api/posts');
    S.posts = r.items || [];
    renderPostList();
  }

  function renderPostList() {
    const box = $('#post-list');
    if (!S.posts.length) {
      box.innerHTML = '<div class="empty">还没有文章。点上面的「＋ 新建文章」。</div>';
      return;
    }
    box.innerHTML = '';
    for (const p of S.posts) {
      const b = document.createElement('div');
      b.className = 'item';
      b.dataset.slug = p.slug;
      if (S.post && S.post.slug === p.slug) b.setAttribute('aria-selected', 'true');
      b.innerHTML =
        `<div class="t">${esc(p.title || '（无标题）')}</div>
         <div class="m">
           <span>${esc(p.date || '无日期')}</span>
           ${p.draft ? '<span class="badge draft">草稿</span>' : ''}
           ${p.broken ? '<span class="badge draft">读不出</span>' : ''}
           <span>${p.words || 0} 字</span>
         </div>`;
      b.addEventListener('click', () => openPost(p.slug).catch((e) => toast(e.message, true)));
      box.appendChild(b);
    }
  }

  function renderTagChips(selected) {
    const sel = selected || [];
    const box = $('#tag-chips');
    box.innerHTML = '';
    if (!S.tags.length) {
      box.innerHTML = '<span class="hint">读不到标签注册表（src/tag-registry.ts）</span>';
      return;
    }
    for (const t of S.tags) {
      const b = document.createElement('button');
      b.className = 'chip';
      b.dataset.tag = t;
      b.textContent = t;
      b.setAttribute('aria-pressed', sel.includes(t) ? 'true' : 'false');
      b.addEventListener('click', () => {
        const on = b.getAttribute('aria-pressed') === 'true';
        if (!on && $$('[aria-pressed=true]', box).length >= 3) {
          toast('标签最多 3 个（站上的硬约束）', true);
          return;
        }
        b.setAttribute('aria-pressed', on ? 'false' : 'true');
      });
      box.appendChild(b);
    }
  }

  const chosenTags = () => $$('#tag-chips [aria-pressed=true]').map((b) => b.dataset.tag);

  /* ── 标签管理（增 / 删 / 看谁在用）────────────────────────────────
     ★ 站的唯一事实来源是 `src/tag-registry.ts`；这个面板只碰它。
     ★ 两条硬约束直接摆到界面上，不让人猜：
        · 新标签 2–5 字、不加层级（docs/content-model.md §4）；
        · 「每个登记标签至少有一篇文章用它」—— 所以
          · 孤儿标签（登记了没人用）会让 `npm run verify` 直接红 ⇒ 标出来；
          · 正被文章用的标签**删不掉**（删了那些文章构建失败）⇒ 按钮直接禁用。
     ★ 文档镜像 `docs/content-model.md` §4 的表格**新增时自动补行**（2026-10-09 站主授权）。
       「含义边界」那一列只能人写，所以补进去的行先落一个占位，等你去填。
       删除仍然不代改 —— 本来就被「在用不许删」挡着，要走人工。 */

  async function loadTagManager() {
    await loadTags();
    renderTagManager();
  }

  function renderTagManager() {
    const info = S.tagInfo || { items: [], usage: {}, doc: {} };
    const tags = info.items || [];
    $('#tag-count').textContent = tags.length ? `${tags.length} 个` : '';

    const box = $('#tag-list');
    box.innerHTML = '';
    if (!tags.length) {
      box.innerHTML = '<div class="empty">读不到标签注册表（src/tag-registry.ts）。</div>';
    } else {
      for (const t of tags) {
        const used = (info.usage || {})[t] || [];
        const row = document.createElement('div');
        row.className = 'tagrow' + (used.length ? '' : ' orphan');
        row.innerHTML =
          `<span class="name">${esc(t)}</span>
           <span class="use">${used.length
             ? `用在 ${used.length} 篇：${esc(used.join('、'))}`
             : '没有文章用它 —— 现在跑构建就会红（每个登记标签至少要有一篇用它）'}</span>`;
        const del = document.createElement('button');
        del.className = 'btn danger sm';
        del.textContent = '删除';
        del.disabled = used.length > 0;
        del.title = used.length
          ? `还有 ${used.length} 篇文章在用它，删了那些文章会构建失败`
          : '从注册表里删掉';
        del.addEventListener('click', () => removeTag(t).catch((e) => toast(e.message, true)));
        row.appendChild(del);
        box.appendChild(row);
      }
    }

    const miss = (info.doc || {}).missing || [];
    const extra = (info.doc || {}).extra || [];
    const note = $('#tag-note');
    const parts = [];
    if (miss.length) {
      // 正常情况下这里**不该出现**（新增标签会自动往 §4 表尾补一行）。
      // 真出现了说明补行没成功（文档被挪走 / §4 表格换了形状），或者是从前留下的。
      parts.push('⚠ §4 表格里还缺：'
        + miss.map((x) => `<code>${esc(x)}</code>`).join('、')
        + '。新增标签本来会自动补行，所以这属于异常 —— 手动看一眼 '
        + '<code>docs/content-model.md</code>。');
    }
    if (extra.length) {
      // 反方向：加过又删掉的标签会在表里留下一行。删标签不代改文档（那是站主点头的方向），
      // 所以这里只报出来，让人顺手划掉那一行。
      parts.push('§4 表格里多出：'
        + extra.map((x) => `<code>${esc(x)}</code>`).join('、')
        + '（注册表里没有它们，多半是加过又删掉了）—— 顺手把那一行也划掉。');
    }
    if (parts.length) note.innerHTML = parts.join('<br>');
    else if (tags.length) note.textContent = '注册表与 docs/content-model.md §4 的表格一致。';
  }

  /** 增/删之后：注册表变了 ⇒ 写作页的标签 chip 也得跟着重建。 */
  async function reloadTagsEverywhere() {
    S.tagInfo = await GET('/api/tags');
    S.tags = S.tagInfo.items || [];
    renderTagManager();
    if (S.post) {
      const on = chosenTags();
      renderTagChips(on.length ? on : (S.post.frontmatter.tags || []));
    }
  }

  async function addTag() {
    const el = $('#tag-new');
    const name = el.value.trim();
    if (!name) { el.focus(); return; }
    const r = await POST('/api/tags', { name });
    el.value = '';
    await reloadTagsEverywhere();
    // ★ 后端会在写注册表的同时往 docs/content-model.md §4 表尾补一行；
    //   补没补上要如实说 —— 失败了就是"要你自己去补"，不能含糊过去。
    const doc = (r || {}).doc || {};
    if (doc.appended) {
      toast(`已登记「${name}」，并在 §4 表尾补了一行（「含义边界」留了占位，去填一下）`);
    } else {
      toast(`已登记「${name}」，但 §4 表格没补上：${doc.note || '原因未知'} —— 手动补一行`, true);
    }
  }

  async function removeTag(name) {
    const used = ((S.tagInfo || {}).usage || {})[name] || [];
    if (used.length) {
      toast(`「${name}」还有 ${used.length} 篇文章在用，不能删`, true);
      return;
    }
    const ok = await askConfirm(`删掉标签「${name}」？`,
      '会从 <code>src/tag-registry.ts</code> 的 <code>TAGS</code> 里去掉它。<br>'
      + '它当前没有文章在用，所以不会连累任何文章。',
      { okText: '删掉', danger: true });
    if (!ok) return;
    await POST('/api/tags/delete', { name });
    await reloadTagsEverywhere();
    toast(`已删掉「${name}」`);
  }

  function initTags() {
    $('#btn-refresh-tags').addEventListener('click', () => loadTagManager()
      .then(() => toast('重新读过了')).catch((e) => toast(e.message, true)));
    $('#btn-add-tag').addEventListener('click', () => addTag().catch((e) => toast(e.message, true)));
    $('#tag-new').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') addTag().catch((err) => toast(err.message, true));
    });
  }

  function updateSummaryCount() {
    const n = $('#f-summary').value.length;
    const el = $('#summary-count');
    el.textContent = `${n} / 80`;
    el.style.color = n > 80 ? 'var(--danger)' : '';
  }

  async function openPost(slug) {
    const p = await GET('/api/post?slug=' + encodeURIComponent(slug));
    S.post = { slug, dir: p.dir, frontmatter: p.frontmatter || {}, body: p.body || '', revision:p.revision };
    const fm = S.post.frontmatter;

    $('#editor-empty').style.display = 'none';
    $('#editor-form').style.display = '';
    $('#f-title').value = fm.title || '';
    $('#f-date').value = String(fm.date || '').slice(0, 10);
    $('#f-draft').value = fm.draft ? 'true' : 'false';
    $('#f-summary').value = fm.summary || '';
    $('#f-slug').value = slug;
    $('#f-updated').value = String(fm.updated || '').slice(0, 10);
    $('#f-cover').value = fm.cover || '';
    $('#f-coveralt').value = fm.coverAlt || '';
    $('#f-body').value = S.post.body;
    $('#preview').style.display = 'none';

    renderTagChips(fm.tags || []);
    updateSummaryCount();
    renderPostImages();
    renderPostMeta();
    renderPostList();
  }

  function renderPostMeta() {
    const p = S.posts.find((x) => x.slug === S.post?.slug);
    $('#post-meta').textContent = p
      ? `content/posts/${S.post.slug}/index.md ｜ ${p.images.length} 张图 ｜ 正文 ${p.words} 字`
      : `content/posts/${S.post?.slug}/index.md`;
  }

  function renderPostImages() {
    const box = $('#post-imgs');
    box.innerHTML = '';
    const p = S.posts.find((x) => x.slug === S.post?.slug);
    const dir = S.post?.dir;
    const imgs = (p && p.images) || [];
    if (!dir || !imgs.length) {
      box.innerHTML = '<span class="hint">（这个目录里还没有图 —— 点「＋ 插入图片」会存进这里）</span>';
      return;
    }
    for (const name of imgs) {
      const b = document.createElement('button');
      b.type = 'button';
      b.title = `插入 ![](${altFromName(name)}) —— ${name}`;
      b.innerHTML = `<img src="/api/file?path=${encodeURIComponent(dir + '/' + name)}" alt="${esc(name)}">`;
      b.addEventListener('click', () => {
        insertAtCursor($('#f-body'), `![${altFromName(name)}](./${name})`);
        toast('已插到光标处');
      });
      box.appendChild(b);
    }
  }

  /** 收集表单 → frontmatter。★ 以原 frontmatter 为底，保住 slug 之类表单没有的键。 */
  function collectPost() {
    const fm = Object.assign({}, S.post.frontmatter);
    const setOrDrop = (k, v) => {
      if (v === '' || v === undefined || v === null) delete fm[k];
      else fm[k] = v;
    };
    setOrDrop('title', $('#f-title').value.trim());
    setOrDrop('date', $('#f-date').value.trim());
    setOrDrop('summary', $('#f-summary').value.trim());
    setOrDrop('updated', $('#f-updated').value.trim());
    setOrDrop('cover', $('#f-cover').value.trim());
    setOrDrop('coverAlt', $('#f-coveralt').value.trim());
    const tags = chosenTags();
    if (tags.length) fm.tags = tags; else delete fm.tags;
    if ($('#f-draft').value === 'true') fm.draft = true; else delete fm.draft;
    return { frontmatter: fm, body: $('#f-body').value };
  }

  /** 站上 schema 的同款校验（先在这里拦住，省得构建时才炸）。 */
  function validatePost(fm) {
    const bad = [];
    if (!fm.title) bad.push('title 不能空');
    if (!fm.date) bad.push('date 不能空');
    else if (!/^\d{4}-\d{2}-\d{2}$/.test(fm.date)) bad.push('date 要写成 YYYY-MM-DD');
    if (!fm.summary) bad.push('summary 不能空');
    else if (fm.summary.length > 80) bad.push(`summary 最多 80 字（现在 ${fm.summary.length}）`);
    if (!fm.tags || !fm.tags.length) bad.push('tags 至少选 1 个');
    else if (fm.tags.length > 3) bad.push('tags 最多 3 个');
    if (fm.updated && fm.date && fm.updated < fm.date) bad.push('updated 不能早于 date');
    if (fm.cover && !fm.coverAlt) bad.push('有 cover 就一定要填 coverAlt');
    if (!fm.cover && fm.coverAlt) bad.push('coverAlt 要配 cover 才有意义（要么都填，要么都别填）');
    return bad;
  }

  async function savePost(thenPublish) {
    if (!S.post) return;
    const target = S.post;
    const { frontmatter, body } = collectPost();
    const bad = validatePost(frontmatter);
    if (bad.length) {
      toast('还差一点：' + bad.join('；'), true);
      return;
    }
    const saved = await POST('/api/post', { slug: target.slug, frontmatter, body, base_revision: target.revision });
    target.revision = saved.revision;
    target.frontmatter = frontmatter;
    target.body = body;
    toast('已保存 content/posts/' + target.slug + '/index.md');
    await loadPosts();
    // 等待保存期间可以打开另一篇，迟到的响应不能改它的缓存或带走页面。
    if (S.post !== target) return;
    renderPostImages();
    renderPostMeta();
    if (thenPublish) go('publish');
  }

  async function newPost() {
    const v = await askForm(
      '新建文章',
      'slug 是网址里那一段（小写字母、数字、单个连字符）。中文标题下一步再改也行。',
      [
        { k: 'slug', label: 'slug（URL，英文小写）', placeholder: 'my-new-post', value: 'my-new-post' },
        { k: 'title', label: 'title 标题', placeholder: '文章标题', value: '未命名' },
      ],
      { okText: '创建' }
    );
    if (!v) return;
    const slug = String(v.slug || '').trim();
    const title = String(v.title || '').trim() || '未命名';
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) {
      toast('slug 只能用小写字母、数字、单个连字符', true);
      return;
    }
    try {
      const fm = { title, date: today(), summary: '待填', draft: true };
      // 标签注册表读不到时宁可不写 tags，也别写一个不在册的值（构建会拒）
      if (S.tags[0]) fm.tags = [S.tags[0]];
      await POST('/api/post', { slug, create: true, frontmatter: fm });
      await loadPosts();
      await openPost(slug);
      toast('建好了（先当草稿存着，改完把「draft 草稿」改成「否」）');
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function deletePost() {
    if (!S.post) return;
    const ok = await askConfirm(
      '删除这篇文章？',
      `会连同它目录里的图一起删：<code>content/posts/${esc(S.post.slug)}/</code>。<br>` +
      '删完还要去「发布」推送，线上才会消失。',
      { okText: '删除', danger: true }
    );
    if (!ok) return;
    await POST('/api/post/delete', { slug: S.post.slug, base_revision: S.post.revision });
    S.post = null;
    $('#editor-form').style.display = 'none';
    $('#editor-empty').style.display = '';
    $('#preview').style.display = 'none';
    await loadPosts();
    toast('已删除（还没推）');
  }

  async function uploadPostImage() {
    if (!S.post) { toast('先在左边打开一篇文章', true); return; }
    const files = await pickFiles();
    if (!files.length) return;
    for (const f of files) {
      try {
        const data = await fileToDataURL(f);
        const r = await POST('/api/upload', {
          data, where: 'post', slug: S.post.slug, name: f.name, max_kb: S.cfg.max_kb || 300,
        });
        insertAtCursor($('#f-body'), `![${altFromName(f.name)}](${r.rel})`);
        toast(`${r.name}：${(r.report && r.report.note) || '已入库'}`, false);
      } catch (e) {
        toast(`${f.name} 没进去：${e.message}`, true);
      }
    }
    await loadPosts();
    renderPostImages();
  }

  const videoPreviewURL = (src) => '/api/video?name=' + encodeURIComponent(src.slice('/videos/'.length));
  function videoMarkup(src, caption, preview = false) {
    const url = preview ? videoPreviewURL(src) : src;
    return `<video controls preload="none" playsinline src="${esc(url)}" aria-label="${esc(caption)}" style="display:block;max-width:100%;height:auto"><a href="${esc(url)}">下载视频：${esc(caption)}</a></video>`;
  }

  async function uploadOwnerVideo(where) {
    if (where === 'post' && !S.post) return toast('先打开一篇文章', true);
    if (where === 'talks' && S.talkIdx < 0) return toast('先选一条说说', true);
    const target = where === 'post' ? S.post : S.talks.items[S.talkIdx];
    const files = await pickFiles('.mp4,.webm');
    for (const file of files) {
      if (!/\.(mp4|webm)$/i.test(file.name) || !file.size || file.size > 25 * 1024 * 1024) {
        toast(`${file.name}：只接受单个不超过 25 MiB 的 MP4 / WebM`, true);
        continue;
      }
      const answer = await askForm('视频说明', 'MP4 建议使用 H.264 + AAC；上传保留原文件，保存后再去发布。', [
        {k:'caption', label:'视频内容说明', value:altFromName(file.name)},
      ], {okText:'上传并插入'});
      if (!answer) continue;
      const caption = String(answer.caption || '').trim();
      if (!caption) { toast('视频说明不能为空', true); continue; }
      const uploaded = await POST('/api/video/upload', {
        data:await fileToDataURL(file), name:file.name, where, slug:where === 'post' ? target.slug : '',
      });
      if (target !== (where === 'post' ? S.post : S.talks.items[S.talkIdx])) {
        toast('视频已入库，但编辑对象已变化；未插入其他内容，请重新选择原条目', true);
        return;
      }
      if (where === 'post') insertAtCursor($('#f-body'), `\n\n${videoMarkup(uploaded.src, caption)}\n\n`);
      else {
        const item = S.talks.items[S.talkIdx];
        (item.videos ||= []).push({src:uploaded.src, alt:caption});
        renderTalkVideos(item.videos);
      }
      toast('视频已入库，记得保存当前内容后再发布');
    }
  }

  async function uploadCover() {
    if (!S.post) { toast('先在左边打开一篇文章', true); return; }
    const files = await pickFiles();
    if (!files.length) return;
    try {
      const f = files[0];
      const data = await fileToDataURL(f);
      const r = await POST('/api/upload', {
        data, where: 'post', slug: S.post.slug, name: f.name, max_kb: S.cfg.max_kb || 300,
      });
      $('#f-cover').value = r.rel;
      await loadPosts();
      renderPostImages();
      if (!$('#f-coveralt').value.trim()) {
        toast(`头图设为 ${r.name} —— 别忘了填 coverAlt（有头图必须写）`, true);
        $('#f-coveralt').focus();
      } else {
        toast(`头图设为 ${r.name}`);
      }
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function insertLink() {
    const v = await askForm('插入链接', '会插到光标处，写成 Markdown 的 <code>[文字](地址)</code>。', [
      { k: 'text', label: '显示的文字', placeholder: '看一下' },
      { k: 'url', label: '地址', placeholder: 'https://…' },
    ], { okText: '插入' });
    if (!v) return;
    const url = String(v.url || '').trim();
    if (!url) return toast('地址不能空', true);
    insertAtCursor($('#f-body'), `[${String(v.text || '').trim() || url}](${url})`);
  }

  function togglePreview(force) {
    const pre = $('#preview');
    const show = force === undefined ? pre.style.display === 'none' : force;
    if (show) {
      pre.innerHTML = renderMd($('#f-body').value, S.post?.dir) || '<span class="hint">（正文是空的）</span>';
      pre.style.display = '';
    } else {
      pre.style.display = 'none';
    }
  }

  function initWrite() {
    const body = $('#f-body'), language = $('#code-language');
    let composing = false;
    const ready = () => {
      if (composing) { toast('请先完成输入法组词，再设置格式。'); return false; }
      return true;
    };
    const syncLanguage = () => {
      const { lines, blocks } = bodyCodeBlocks(body.value);
      const first = lines.findLastIndex(line => line.offset <= body.selectionStart);
      const last = lines.findLastIndex(line => line.offset < body.selectionEnd || line.offset <= body.selectionStart);
      const block = blocks.find(block => !block.nested && block.first <= first && block.last >= last);
      const original = $('#code-language-original');
      if (block && !/^(python|javascript|typescript|json|bash|text)$/.test(block.language)) {
        original.textContent = `原语言（${block.language || '未指定'}）`;
        original.hidden = false; language.value = '';
      } else {
        original.hidden = true;
        if (block) language.value = block.language;
        else if (!language.value) language.value = 'python';
      }
    };
    const applyFormat = level => {
      if (!ready()) return;
      applyBodyEdit(body, formatBodyBlock(body.value, body.selectionStart, body.selectionEnd, level, language.value));
      syncLanguage();
    };
    body.addEventListener('compositionstart', () => { composing = true; });
    body.addEventListener('compositionend', () => { composing = false; syncLanguage(); });
    for (const event of ['select', 'click', 'keyup']) body.addEventListener(event, syncLanguage);
    $$('[data-body-level]').forEach(button => {
      button.addEventListener('mousedown', event => event.preventDefault());
      button.addEventListener('click', () => applyFormat(Number(button.dataset.bodyLevel)));
    });
    $('#btn-code-block').addEventListener('mousedown', event => event.preventDefault());
    $('#btn-code-block').addEventListener('click', () => applyFormat('code'));
    language.addEventListener('mousedown', event => { if (!ready()) event.preventDefault(); });
    language.addEventListener('change', () => applyFormat('language'));
    $('#btn-insert-video').addEventListener('click', guard($('#btn-insert-video'), () => uploadOwnerVideo('post')));
    $('#btn-new-post').addEventListener('click', () => newPost());
    $('#btn-refresh-posts').addEventListener('click', async () => {
      await loadPosts();
      if (S.post) await openPost(S.post.slug);
      toast('重新读过了');
    });
    $('#btn-save').addEventListener('click', () => savePost(false).catch((e) => toast(e.message, true)));
    $('#btn-save-close').addEventListener('click', () => savePost(true).catch((e) => toast(e.message, true)));
    $('#btn-del-post').addEventListener('click', () => deletePost().catch((e) => toast(e.message, true)));
    $('#btn-insert-img').addEventListener('click', guard($('#btn-insert-img'), uploadPostImage));
    $('#btn-cover-img').addEventListener('click', guard($('#btn-cover-img'), uploadCover));
    $('#btn-insert-link').addEventListener('click', () => insertLink());
    $('#btn-toggle-preview').addEventListener('click', () => togglePreview());
    $('#f-summary').addEventListener('input', updateSummaryCount);
    $('#f-body').addEventListener('input', () => {
      if ($('#preview').style.display !== 'none') togglePreview(true);
    });
    $('#f-title').addEventListener('input', () => { renderPostMeta(); });
  }

  /* ══════════════════ 八、日志 ══════════════════ */

  async function loadLog() {
    const r = await GET('/api/log');
    const keep = S.log && S.logIdx >= 0 ? S.log.items[S.logIdx] : null;
    S.log = { items: r.items || [], next_version: r.next_version || '', revision:r.revision };
    S.logIdx = keep
      ? S.log.items.findIndex((x) => x.date === keep.date && x.version === keep.version
          && JSON.stringify(x.changes) === JSON.stringify(keep.changes))
      : (S.log.items.length ? 0 : -1);
    renderLogList();
    if (S.logIdx >= 0) fillLogForm(); else showLogEmpty();
  }

  function renderLogList() {
    const box = $('#log-list');
    box.innerHTML = '';
    if (!S.log || !S.log.items.length) {
      box.innerHTML = '<div class="empty">还没有日志条目。</div>';
      return;
    }
    S.log.items.forEach((it, i) => {
      const b = document.createElement('div');
      b.className = 'item';
      b.dataset.i = String(i);
      if (i === S.logIdx) b.setAttribute('aria-selected', 'true');
      b.innerHTML =
        `<div class="t">${esc(it.version || '（无版本号）')}</div>
         <div class="m"><span>${esc(it.date || '')}</span>
         <span>${(it.changes || []).length} 条改动</span></div>`;
      b.addEventListener('click', () => { S.logIdx = i; renderLogList(); fillLogForm(); });
      box.appendChild(b);
    });
  }

  const showLogEmpty = () => {
    $('#log-empty').style.display = '';
    $('#log-form').style.display = 'none';
  };

  function fillLogForm() {
    const it = S.log.items[S.logIdx];
    if (!it) return showLogEmpty();
    $('#log-empty').style.display = 'none';
    $('#log-form').style.display = '';
    $('#l-date').value = String(it.date || '').slice(0, 10);
    $('#l-ver').value = it.version || '';
    const box = $('#l-changes');
    box.innerHTML = '';
    const ch = it.changes && it.changes.length ? it.changes : [''];
    ch.forEach((c) => addChangeRow(c));
  }

  function addChangeRow(value = '') {
    const box = $('#l-changes');
    const row = document.createElement('div');
    row.className = 'row tight';
    row.innerHTML =
      `<input type="text" class="grow" placeholder="改了什么（写清楚：为什么要改）" value="${esc(value)}">
       <button class="btn ghost sm" title="删掉这条">✕</button>`;
    row.querySelector('button').addEventListener('click', () => row.remove());
    row.querySelector('input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.ctrlKey) { e.preventDefault(); addChangeRow(''); row.nextElementSibling?.querySelector('input')?.focus(); }
    });
    box.appendChild(row);
    return row;
  }

  const readChanges = () =>
    $$('#l-changes input').map((i) => i.value.trim()).filter(Boolean);

  function collectLogItems() {
    // 以现有 items 为底，只替换当前这条
    const items = S.log.items.map((it) => ({
      date: String(it.date || ''),
      version: it.version || '',
      changes: (it.changes || []).map(String),
    }));
    if (S.logIdx >= 0) {
      items[S.logIdx] = {
        date: $('#l-date').value.trim(),
        version: $('#l-ver').value.trim(),
        changes: readChanges(),
      };
    }
    return items.filter((it) => it.date || it.version || (it.changes && it.changes.length));
  }

  async function saveLog() {
    if (S.logIdx < 0) return;
    const target = S.log;
    const entry = target.items[S.logIdx];
    const cur = {
      date: $('#l-date').value.trim(),
      version: $('#l-ver').value.trim(),
      changes: readChanges(),
    };
    if (!cur.date) return toast('date 不能空', true);
    if (!cur.version) return toast('version 不能空（例：v2.11）', true);
    if (!/^v\d+(\.\d+)*$/.test(cur.version)) return toast('version 要写成 v2.11 这样', true);
    if (!cur.changes.length) return toast('changes 至少要写一条', true);
    // ★ 找出会踩 YAML 坑的写法：**ASCII 冒号+空格**（"a: b"）会被读成映射，
    //   页面上就印出 [object Object]（站上真出过这个事故）。
    //   注意全角「：」不是坑 —— 别把它也算进来，那只是假警报。
    const risky = cur.changes.filter((c) => /:[ \t]/.test(c) || /:$/.test(c));
    const items = collectLogItems();
    const saved = await POST('/api/log', { items, base_revision: target.revision });
    target.revision = saved.revision;
    Object.assign(entry, cur);
    const versions = items.map(it => /^v(\d+)\.(\d+)$/.exec(it.version || '')).filter(Boolean);
    const last = versions.map(v => [Number(v[1]), Number(v[2])])
      .sort((a, b) => b[0] - a[0] || b[1] - a[1])[0] || [0, 0];
    target.next_version = `v${last[0]}.${last[1] + 1}`;
    const dup = items.filter((x) => x.version === cur.version).length > 1;
    // 不重新填表，保住请求期间继续输入的内容和新建条目。
    if (S.log === target) renderLogList();
    const warn = risky.length ? `　⚠ ${risky.length} 条带「冒号+空格」，已自动加引号保护` : '';
    toast((dup ? `已保存，但 ${cur.version} 出现了多条 —— 检查一下`
               : `已保存 ${cur.version}（${cur.changes.length} 条改动）`) + warn, dup);
  }

  async function newLog() {
    // 保存请求可能仍在等待，保留条目对象，响应才能同步到原来的那一条。
    const items = [...S.log.items];
    items.push({ date: today(), version: S.log.next_version || 'v2.11', changes: [''] });
    S.logIdx = items.length - 1;
    S.log.items = items;
    renderLogList();
    fillLogForm();
    $('#l-changes').querySelector('input')?.focus();
  }

  async function deleteLog() {
    if (S.logIdx < 0) return;
    const it = S.log.items[S.logIdx];
    const ok = await askConfirm('删掉这条日志？', `会从 <code>log.md</code> 里移除 <code>${esc(it.version || '')}</code>（${esc(it.date || '')}）这一条。`, { okText: '删除', danger: true });
    if (!ok) return;
    const items = S.log.items
      .filter((_x, i) => i !== S.logIdx)
      .map((x) => ({ date: String(x.date || ''), version: x.version || '', changes: (x.changes || []).map(String) }));
    await POST('/api/log', { items, base_revision: S.log.revision });
    S.logIdx = -1;
    await loadLog();
    toast('已删除');
  }

  function initLog() {
    $('#btn-new-log').addEventListener('click', () => newLog());
    $('#btn-refresh-log').addEventListener('click', () => loadLog().then(() => toast('重新读过了')).catch((e) => toast(e.message, true)));
    $('#btn-add-change').addEventListener('click', () => addChangeRow('').querySelector('input').focus());
    $('#btn-ver-auto').addEventListener('click', () => {
      $('#l-ver').value = S.log.next_version || '';
      toast('用的是现有最大版本号 +1');
    });
    $('#btn-save-log').addEventListener('click', () => saveLog().catch((e) => toast(e.message, true)));
    $('#btn-del-log').addEventListener('click', () => deleteLog().catch((e) => toast(e.message, true)));
  }

  /* ══════════════════ 九、说说 ══════════════════ */

  const talksDir = () => String(S.cfg.repo || '').replace(/[\\/]+$/, '') + '/src/assets/talks';

  async function loadTalks() {
    const r = await GET('/api/talks');
    const keep = S.talks && S.talkIdx >= 0 ? S.talks.items[S.talkIdx] : null;
    S.talks = { items: r.items || [], revision: r.revision };
    S.talkIdx = keep
      ? S.talks.items.findIndex((x) => x.date === keep.date && x.text === keep.text)
      : (S.talks.items.length ? 0 : -1);
    renderTalkList();
    if (S.talkIdx >= 0) fillTalkForm(); else showTalkEmpty();
  }

  function renderTalkList() {
    const box = $('#talk-list');
    box.innerHTML = '';
    if (!S.talks || !S.talks.items.length) {
      box.innerHTML = '<div class="empty">还没有说说。</div>';
      return;
    }
    S.talks.items.forEach((it, i) => {
      const b = document.createElement('div');
      b.className = 'item';
      if (i === S.talkIdx) b.setAttribute('aria-selected', 'true');
      const n = (it.images || []).length;
      const videos = (it.videos || []).length;
      b.innerHTML =
        `<div class="t">${esc(String(it.text || '').slice(0, 46) || '（空）')}</div>
         <div class="m"><span>${esc(it.date || '')}</span>${n ? `<span>${n} 张图</span>` : ''}
         ${videos ? `<span>${videos} 个视频</span>` : ''}${it.href ? '<span>带链接</span>' : ''}</div>`;
      b.addEventListener('click', () => { S.talkIdx = i; renderTalkList(); fillTalkForm(); });
      box.appendChild(b);
    });
  }

  const showTalkEmpty = () => {
    $('#talk-empty').style.display = '';
    $('#talk-form').style.display = 'none';
  };

  function fillTalkForm() {
    const it = S.talks.items[S.talkIdx];
    if (!it) return showTalkEmpty();
    $('#talk-empty').style.display = 'none';
    $('#talk-form').style.display = '';
    $('#t-date').value = String(it.date || '').slice(0, 10);
    $('#t-href').value = it.href || '';
    $('#t-text').value = it.text || '';
    renderTalkImages(it.images || []);
    renderTalkVideos(it.videos || []);
  }

  function renderTalkVideos(clips) {
    const box = $('#t-videos');
    box.innerHTML = clips.length ? '' : '<span class="hint">（这条还没配视频）</span>';
    clips.forEach((clip, index) => {
      const row = document.createElement('div');
      row.className = 'stack';
      row.innerHTML = videoMarkup(clip.src, clip.alt || '视频', true) +
        `<label class="field"><span>视频说明（必填）</span><input class="video-alt" data-i="${index}" value="${esc(clip.alt || '')}"></label>` +
        '<button class="btn ghost sm" style="align-self:flex-start">从这条说说移除（原件保留）</button>';
      row.querySelector('input').addEventListener('input', e => { clip.alt = e.target.value; });
      row.querySelector('button').addEventListener('click', () => { clips.splice(index, 1); renderTalkVideos(clips); });
      box.appendChild(row);
    });
  }

  function renderTalkImages(imgs) {
    const box = $('#t-imgs');
    box.innerHTML = '';
    if (!imgs.length) {
      box.innerHTML = '<span class="hint">（这条还没配图）</span>';
      return;
    }
    imgs.forEach((im, i) => {
      const row = document.createElement('div');
      row.className = 'imgrow';
      const src = String(im.src || '').replace(/^\.\//, '');
      row.innerHTML =
        `<img src="/api/file?path=${encodeURIComponent(talksDir() + '/' + src)}" alt="${esc(im.alt || '')}">
         <label class="field grow"><span>alt（必填，给读屏和不显示图时用）</span>
           <input type="text" data-i="${i}" class="alt-in" value="${esc(im.alt || '')}" placeholder="图里是什么"></label>
         <button class="btn ghost sm" title="从这条说说里移除（文件不删）">✕</button>`;
      row.querySelector('button').addEventListener('click', () => {
        imgs.splice(i, 1);
        renderTalkImages(imgs);
      });
      row.querySelector('.alt-in').addEventListener('input', (e) => { imgs[i].alt = e.target.value; });
      box.appendChild(row);
    });
  }

  /**
   * 收集说说列表。
   * ★ 返回 { items, cur }：因为下面 filter 会丢掉空条目、**索引就错位了**，
   *   所以"当前这条"必须在过滤之前抓出来（曾经用 items[S.talkIdx] 取，会取到别人）。
   */
  function collectTalks() {
    const all = S.talks.items.map((it) => ({
      ...it,
      date: String(it.date || ''),
      text: it.text || '',
      href: it.href || '',
      images: (it.images || []).map((im) => ({ src: String(im.src || '').replace(/^\.\//, ''), alt: im.alt || '' })),
    }));
    let cur = null;
    if (S.talkIdx >= 0) {
      const box = $('#t-imgs');
      const imgs = S.talks.items[S.talkIdx].images || [];
      cur = {
        ...S.talks.items[S.talkIdx],
        date: $('#t-date').value.trim(),
        text: $('#t-text').value.trim(),
        href: $('#t-href').value.trim(),
        images: imgs.map((im, i) => ({
          src: String(im.src || '').replace(/^\.\//, ''),
          alt: (box.querySelector(`.alt-in[data-i="${i}"]`)?.value ?? im.alt ?? '').trim(),
        })),
      };
      all[S.talkIdx] = cur;
    }
    return { items: all.filter((it) => it.date || it.text || (it.images && it.images.length)), cur };
  }

  async function saveTalk() {
    if (S.talkIdx < 0) return;
    const target = S.talks;
    const entry = target.items[S.talkIdx];
    const { items, cur } = collectTalks();
    if (!cur) return;
    if (!cur.date) return toast('date 不能空', true);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(cur.date)) return toast('date 要写成 YYYY-MM-DD', true);
    if (!cur.text) return toast('text 不能空', true);
    const noAlt = cur.images.filter((im) => !im.alt);
    if (noAlt.length) return toast(`有 ${noAlt.length} 张图没填 alt（站上每个 img 都必须有非空 alt）`, true);
    if ((cur.videos || []).some(clip => !String(clip.alt || '').trim())) return toast('视频说明不能为空', true);
    const saved = await POST('/api/talks', { items, base_revision: target.revision });
    target.revision = saved.revision;
    // 媒体列表可能在请求期间继续编辑，只同步本次保存的文字字段。
    Object.assign(entry, { date: cur.date, text: cur.text, href: cur.href });
    if (S.talks === target) renderTalkList();
    toast('已保存 ' + cur.date + ' 这条说说');
  }

  function newTalk() {
    const items = [...S.talks.items];
    items.push({ id:'talk-' + crypto.randomUUID(), date: today(), text: '', href: '', images: [], videos: [] });
    S.talkIdx = items.length - 1;
    S.talks.items = items;
    renderTalkList();
    fillTalkForm();
    $('#t-text').focus();
  }

  async function deleteTalk() {
    if (S.talkIdx < 0) return;
    const it = S.talks.items[S.talkIdx];
    const ok = await askConfirm('删掉这条说说？', `会移除 <code>${esc(it.date || '')}</code> 这条。<br>配图的文件**不会**自动删（可能别处还在用）。`, { okText: '删除', danger: true });
    if (!ok) return;
    const items = S.talks.items
      .filter((_x, i) => i !== S.talkIdx)
      .map((x) => ({
        ...x,
        date: String(x.date || ''), text: x.text || '', href: x.href || '',
        images: (x.images || []).map((im) => ({ src: im.src, alt: im.alt || '' })),
      }));
    await POST('/api/talks', { items, base_revision: S.talks.revision });
    S.talkIdx = -1;
    await loadTalks();
    toast('已删除');
  }

  async function uploadTalkImage() {
    if (S.talkIdx < 0) { toast('先在左边选一条说说', true); return; }
    const files = await pickFiles();
    if (!files.length) return;
    const imgs = S.talks.items[S.talkIdx].images || (S.talks.items[S.talkIdx].images = []);
    let added = 0;
    for (const f of files) {
      try {
        const data = await fileToDataURL(f);
        const r = await POST('/api/upload', {
          data, where: 'talks', name: f.name, max_kb: S.cfg.max_kb || 300,
        });
        imgs.push({ src: r.name || f.name, alt: altFromName(f.name) });
        added++;
      } catch (e) {
        toast(`${f.name} 没进去：${e.message}`, true);
      }
    }
    renderTalkImages(imgs);
    if (added) toast(`入库 ${added} 张（在 src/assets/talks/）—— 核对一下 alt`);
  }

  async function pickTalkImageFromGallery() {
    if (S.talkIdx < 0) { toast('先在左边选一条说说', true); return; }
    const r = await GET('/api/photos');
    const all = (r.items || []).slice(0, 120);
    if (!all.length) { toast('相册里没有照片', true); return; }
    const picked = await pickFromGrid(
      '从相册挑一张',
      '说说图必须放在 <code>src/assets/talks/</code> —— 只有那里参与构建期优化。' +
      '选一张会**复制**一份过去（相册原图不动）。',
      all
    );
    if (!picked) return;
    try {
      const up = await POST('/api/copy-to-talks', {
        path: picked.path, name: picked.name, max_kb: S.cfg.max_kb || 300,
      });
      const imgs = S.talks.items[S.talkIdx].images || (S.talks.items[S.talkIdx].images = []);
      imgs.push({ src: up.name, alt: altFromName(picked.name) });
      renderTalkImages(imgs);
      toast(`${picked.name} → src/assets/talks/${up.name}（${(up.report && up.report.note) || ''}）`);
    } catch (e) {
      toast(e.message, true);
    }
  }

  function initTalks() {
    $('#btn-talk-video').addEventListener('click', guard($('#btn-talk-video'), () => uploadOwnerVideo('talks')));
    $('#btn-new-talk').addEventListener('click', () => newTalk());
    $('#btn-refresh-talks').addEventListener('click', () => loadTalks().then(() => toast('重新读过了')).catch((e) => toast(e.message, true)));
    $('#btn-save-talk').addEventListener('click', () => saveTalk().catch((e) => toast(e.message, true)));
    $('#btn-del-talk').addEventListener('click', () => deleteTalk().catch((e) => toast(e.message, true)));
    $('#btn-talk-img').addEventListener('click', guard($('#btn-talk-img'), uploadTalkImage));
    $('#btn-talk-img-from-wall').addEventListener('click', guard($('#btn-talk-img-from-wall'), pickTalkImageFromGallery));
  }

  /* ══════════════════ 十、相册照片 ══════════════════ */

  async function loadPhotos() {
    const g = await GET('/api/gallery');
    S.albums = g.items || [];
    const sel = $('#photo-album');
    sel.innerHTML = S.albums
      .map((a) => `<option value="${esc(a.name)}">${esc(a.title || a.name)}（${a.count}）</option>`)
      .join('');
    if (!S.album || !S.albums.some((a) => a.name === S.album)) {
      S.album = S.albums.length ? S.albums[0].name : '';
    }
    sel.value = S.album;
    await loadAlbumPhotos();
  }

  async function loadAlbumPhotos() {
    if (!S.album) {
      $('#photo-wall').innerHTML = '<div class="empty">还没有相册（content/gallery/&lt;相册名&gt;/）</div>';
      $('#photo-count').textContent = '';
      return;
    }
    const r = await GET('/api/photos?album=' + encodeURIComponent(S.album));
    S.photos = r.items || [];
    renderWall();
  }

  /** 某张照片在所属相册 index.md 里的序号（1 起）—— 构建出的 webp 名靠它。 */
  function photoIndex(albumName, fileName) {
    const a = S.albums.find((x) => x.name === albumName);
    if (!a) return -1;
    const i = (a.images || []).findIndex((im) => String(im.src || '').replace(/^\.\//, '') === fileName);
    return i < 0 ? -1 : i + 1;
  }

  function renderWall() {
    const wall = $('#photo-wall');
    wall.innerHTML = '';
    const a = S.albums.find((x) => x.name === S.album);
    const registered = (a && a.images.length) || 0;
    $('#photo-count').textContent =
      `${S.photos.length} 张原图 ｜ index.md 里登记了 ${registered} 张`;
    if (!S.photos.length) {
      wall.innerHTML = '<div class="empty">这个相册还没有图片文件。</div>';
      return;
    }
    for (const p of S.photos) {
      const n = photoIndex(p.album, p.name);
      const d = document.createElement('div');
      d.className = 'photo';
      d.title = `${p.name}　${kb(p.size)}　${p.mtime}`;
      d.innerHTML =
        `<img src="/api/file?path=${encodeURIComponent(p.path)}" alt="${esc(p.name)}" loading="lazy">
         ${n > 0 ? `<div class="tag">${pad2(n)}</div>` : '<div class="tag" style="background:#b45309cc">未登记</div>'}
         <div class="cap">${esc(p.name)}</div>`;
      d.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        openPhotoMenu(e.clientX, e.clientY, p, n);
      });
      wall.appendChild(d);
    }
  }

  function openPhotoMenu(x, y, p, n) {
    const menu = $('#ctxmenu');
    const large = n > 0 ? `/gallery/${p.album}/${pad2(n)}-1280.webp` : '';
    const small = n > 0 ? `/gallery/${p.album}/${pad2(n)}-800.webp` : '';
    const alt = altFromName(p.name);
    const items = [];
    if (n > 0) {
      items.push(['复制 Markdown（大图 1280）', () => mdCopy(alt, large)]);
      items.push(['复制 Markdown（缩略 800）', () => mdCopy(alt, small)]);
      items.push(['复制线上地址（大图）', () => plainCopy(large)]);
    } else {
      items.push(['⚠ 这张没登记进 index.md —— 先加进去才会构建', () => plainCopy(p.name)]);
    }
    items.push(null);
    items.push(['复制文件名', () => plainCopy(p.name)]);
    items.push(['复制本地路径', () => plainCopy(p.path)]);
    items.push(['在资源管理器里打开', () => POST('/api/reveal', { path: p.path }).then(() => toast('已打开')).catch((e) => toast(e.message, true))]);
    items.push(null);
    items.push(['删除这张原图…', () => deletePhoto(p), 'danger']);

    menu.innerHTML = '';
    for (const it of items) {
      if (!it) { menu.insertAdjacentHTML('beforeend', '<div class="hr"></div>'); continue; }
      const [label, fn, cls] = it;
      const b = document.createElement('button');
      b.textContent = label;
      if (cls === 'danger') b.style.color = 'var(--danger)';
      b.addEventListener('click', () => { closeMenu(); fn(); });
      menu.appendChild(b);
    }
    menu.classList.add('on');
    const w = menu.offsetWidth;
    const h = menu.offsetHeight;
    menu.style.left = Math.min(x, window.innerWidth - w - 8) + 'px';
    menu.style.top = Math.min(y, window.innerHeight - h - 8) + 'px';
  }

  const closeMenu = () => $('#ctxmenu').classList.remove('on');

  const mdCopy = async (alt, url) => {
    const s = `![${alt}](${url})`;
    const ok = await copyText(s);
    toast(ok ? '已复制：' + s : '复制失败，请手动选', !ok);
  };
  const plainCopy = async (s) => {
    const ok = await copyText(s);
    toast(ok ? '已复制：' + (s.length > 46 ? s.slice(0, 46) + '…' : s) : '复制失败，请手动选', !ok);
  };

  async function deletePhoto(p) {
    const ok = await askConfirm(
      '删除这张原图？',
      `<code>${esc(p.album)}/${esc(p.name)}</code>（${kb(p.size)}）<br>` +
      '只删磁盘上的原图。如果它还写在 index.md 里，构建会报错 —— 记得同时把那一行删掉。',
      { okText: '删除', danger: true }
    );
    if (!ok) return;
    try {
      await POST('/api/image/delete', { path: p.path });
      await loadAlbumPhotos();
      toast('已删除原图');
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function uploadGalleryPhoto() {
    if (!S.album) { toast('先选一个相册', true); return; }
    const files = await pickFiles();
    if (!files.length) return;
    let done = 0;
    for (const f of files) {
      try {
        const data = await fileToDataURL(f);
        const r = await POST('/api/upload', {
          data, where: 'gallery', album: S.album, name: f.name, max_kb: 900,
        });
        done++;
        toast(`${r.name}：${(r.report && r.report.note) || '已入库'}`, false);
      } catch (e) {
        toast(`${f.name} 没进去：${e.message}`, true);
      }
    }
    if (done) {
      await loadPhotos();
      toast(`${done} 张进了 content/gallery/${S.album}/ —— ★ 还要把文件名写进该相册的 index.md（含 alt），不然不会出现在页面上`, true);
    }
  }

  function initPhotos() {
    $('#photo-album').addEventListener('change', (e) => {
      S.album = e.target.value;
      loadAlbumPhotos().catch((err) => toast(err.message, true));
    });
    $('#btn-refresh-photos').addEventListener('click', () => loadPhotos().then(() => toast('重新读过了')).catch((e) => toast(e.message, true)));
    $('#btn-upload-photo').addEventListener('click', guard($('#btn-upload-photo'), uploadGalleryPhoto));
  }

  /* ══════════════════ 十一、留言 ══════════════════ */

  async function loadComments() {
    const box = $('#cm-list');
    if (!S.cfg.has_token) {
      $('#cm-count').textContent = '';
      box.innerHTML =
        '<div class="empty">还没配管理密钥。去「设置」里填上和站点环境变量 <code>ADMIN_TOKEN</code> 一样的值，' +
        '才能读 / 删线上留言。</div>';
      return;
    }
    box.innerHTML = '<div class="empty">读取中…</div>';
    try {
      const r = await GET('/api/comments');
      S.comments = r.items || [];
      renderComments();
    } catch (e) {
      box.innerHTML = '';
      toast('读线上留言失败：' + e.message, true);
      box.innerHTML = `<div class="empty">读不出来：${esc(e.message)}</div>`;
    }
  }

  function renderComments() {
    const box = $('#cm-list');
    box.innerHTML = '';
    const total = S.comments.reduce((n, c) => n + 1 + (c.replies || []).length, 0);
    $('#cm-count').textContent = `共 ${total} 条（${S.comments.length} 条顶层）`;
    if (!S.comments.length) {
      box.innerHTML = '<div class="empty">没有留言。</div>';
      return;
    }
    for (const c of S.comments) {
      const el = document.createElement('div');
      el.className = 'card';
      el.style.padding = 'var(--sp-4)';
      el.innerHTML =
        `<div class="spread">
           <div class="row tight">
             <b>${esc(c.name || '匿名')}</b>
             <span class="hint">${esc(c.slug || '')}　${esc(localTime(c.createdAt))}</span>
           </div>
           <button class="btn danger sm" data-del="${c.id}">删除</button>
         </div>
         <div style="margin-top:var(--sp-3);line-height:1.75;white-space:pre-wrap">${esc(c.body || '')}</div>
         ${(c.replies || []).map((rp) => `
           <div style="margin:var(--sp-3) 0 0 var(--sp-5);padding-left:var(--sp-3);border-left:2px solid var(--hair)">
             <div class="spread">
               <div class="row tight"><b>${esc(rp.name || '匿名')}</b>
                 <span class="hint">回复 ${esc(rp.replyTo || '')}　${esc(localTime(rp.createdAt))}</span></div>
               <button class="btn danger sm" data-del="${rp.id}" data-kind="reply">删除</button>
             </div>
             <div style="margin-top:6px;line-height:1.75;white-space:pre-wrap">${esc(rp.body || '')}</div>
           </div>`).join('')}`;
      box.appendChild(el);
    }
    $$('[data-del]', box).forEach((b) => b.addEventListener('click', () => deleteComment(b)));
  }

  async function deleteComment(btn) {
    const id = Number(btn.dataset.del);
    const kind = btn.dataset.kind || 'comment';
    const ok = await askConfirm(
      '删除这条留言？',
      kind === 'comment'
        ? '它会从线上的 Cloudflare D1 里**永久删除**（连同它的回复）。这一步不可撤销。'
        : '这条回复会从线上永久删除。不可撤销。',
      { okText: '永久删除', danger: true }
    );
    if (!ok) return;
    try {
      await POST('/api/comment/delete', { id, kind });
      toast('已删除');
    } catch (e) {
      toast('删除失败：' + e.message, true);
    } finally {
      // 成没成**都重取一遍**（2026-10-09）：失败里有一类正是"你手上的列表过期了"
      // （接口回 404 = 本来就不在、409 = 刚刚被别处删掉）。这时若只弹个 toast，
      // 那行会一直杵在界面上、看着像"删不掉" —— 恰恰是接口那两个状态码想避免的观感。
      await loadComments();
    }
  }

  function initComments() {
    $('#btn-refresh-comments').addEventListener('click', () => loadComments());
  }

  /* ══════════════════ 十二、发布 ══════════════════ */

  async function refreshState() {
    try {
      S.state = await GET('/api/state');
    } catch (e) {
      S.state = null;
    }
    renderSideFoot();
    return S.state;
  }

  function renderSideFoot() {
    const st = S.state;
    const dot = $('#st-dot');
    dot.className = 'dot';
    if (!st) {
      $('#st-branch').textContent = '—';
      $('#st-clean').textContent = '读不到仓库状态';
      $('#st-remote').textContent = '';
      return;
    }
    $('#st-branch').textContent = st.branch + ' · ' + st.head_short;
    const bits = [];
    if (st.dirty_count) bits.push(`${st.dirty_count} 处改动未提交`);
    else bits.push('工作区干净');
    $('#st-clean').textContent = bits.join('　');

    const sync = st.sync;
    if (sync === 'synced') {
      dot.className = 'dot ok';
      $('#st-remote').textContent = `远端 ${st.remote_short} 内容一致`;
    } else if (sync === 'ahead') {
      dot.className = 'dot warn';
      $('#st-remote').textContent = `远端 ${st.remote_short}｜本地多 ${st.pending} 个提交待推`;
    } else if (sync === 'behind') {
      dot.className = 'dot warn';
      $('#st-remote').textContent = `云端有 ${st.incoming} 个新提交，可到发布页同步`;
    } else if (sync === 'diverged') {
      dot.className = 'dot err';
      $('#st-remote').textContent = '本地与云端都有新提交，需要合并';
    } else {
      dot.className = 'dot warn';
      $('#st-remote').textContent = '远端状态没问到' + (st.remote_err ? '（' + String(st.remote_err).slice(0, 28) + '）' : '');
    }
  }

  async function loadPublish() {
    await refreshState();
    const box = $('#pub-state');
    const st = S.state;
    $('#btn-sync-remote').disabled = !st || !st.clean || st.branch !== 'main' || st.sync !== 'behind';
    if (!st) {
      box.innerHTML = '<div class="empty">读不到仓库状态。</div>';
      return;
    }
    const syncText = {
      synced: ['ok', `与远端提交一致（远端 ${st.remote_short}）`],
      ahead: ['warn', `本地比远端多 ${st.pending} 个提交 —— 推了才上线`],
      behind: ['warn', st.clean
        ? `云端比本机多 ${st.incoming} 个提交，点击“同步云端内容”即可接收`
        : `云端比本机多 ${st.incoming} 个提交；本地有未提交改动，处理后再同步`],
      diverged: ['err', '本地与云端都有新提交，需要合并；两边内容均已保留'],
    }[st.sync] || ['warn', '远端状态没问到' + (st.remote_err ? '：' + st.remote_err : '')];
    const color = { ok: 'var(--accent-ink)', warn: 'var(--warn)', err: 'var(--danger)' }[syncText[0]];
    box.innerHTML =
      `<div class="row"><span class="hint" style="min-width:78px">分支</span>
         <b>${esc(st.branch)}</b><span class="hint">${esc(st.head_short)}</span></div>
       <div class="row"><span class="hint" style="min-width:78px">最近提交</span>
         <span>${esc(st.subject || '（空仓库）')}</span></div>
       <div class="row"><span class="hint" style="min-width:78px">工作区</span>
         <span>${st.clean ? '干净' : `有 ${st.dirty_count} 处改动`}</span></div>
       <div class="row"><span class="hint" style="min-width:78px">远端 main</span>
         <span style="color:${color}">${esc(syncText[1])}</span></div>
       <div class="row"><span class="hint" style="min-width:78px">说明</span>
         <span class="hint">启动时会同步干净且仅落后的 main。同步只接收已发布内容；本地有改动或历史分叉时保留现场，发布前仍会检查。</span></div>`;
    renderDiff(st);
    if (!$('#pub-msg').value.trim()) {
      $('#pub-msg').value = `写作台：内容更新（${stamp()}）`;
    }
  }

  function renderDiff(st) {
    const box = $('#pub-diff');
    const files = (st.dirty || []).map((l) => ({ code: l.slice(0, 2), path: l.slice(3) }));
    if (!files.length) {
      box.innerHTML = '<div class="empty">工作区没有改动 —— 要么已经提交，要么还没写。</div>';
      return;
    }
    box.innerHTML = files.map((f) => {
      const c = f.code;
      let cls = 'mod';
      let s = '~';
      if (c.includes('?') || c.includes('A')) { cls = 'add'; s = '＋'; }
      else if (c.includes('D')) { cls = 'del'; s = '－'; }
      else if (c.includes('R')) { cls = 'mod'; s = '→'; }
      return `<div class="f ${cls}"><span class="s">${s}</span><span>${esc(f.path)}</span></div>`;
    }).join('');
  }

  const STEP_NAMES = ['构建与校验', '提交', '推送'];

  function renderSteps(steps) {
    $('#pub-steps').innerHTML = steps.map((s, i) =>
      `<div class="step ${s.state}"><span class="n">${s.state === 'ok' ? '✓' : s.state === 'bad' ? '✕' : i + 1}</span>
       <span>${esc(s.name)}</span>${s.detail ? `<span class="hint">${esc(s.detail)}</span>` : ''}</div>`
    ).join('');
  }

  async function runPush(skipVerify) {
    const btnPush = $('#btn-push');
    const btnCommit = $('#btn-commit-only');
    const msg = $('#pub-msg').value.trim() || `写作台：内容更新（${stamp()}）`;
    btnPush.disabled = true;
    btnCommit.disabled = true;
    $('#pub-log').style.display = '';
    $('#pub-log').textContent = '开始…\n';
    renderSteps(STEP_NAMES.map((n) => ({ name: n, state: 'pending' })));

    try {
      if (skipVerify) {
        // 用户明确勾了「跳过构建」：后端会记下这一步是"已跳过"，其余照常提交 + 推
        renderSteps([{ name: '构建与校验', state: 'ok', detail: '已按你的选择跳过' },
                     { name: '提交', state: 'pending' }, { name: '推送', state: 'pending' }]);
        $('#pub-log').textContent += '已跳过构建（你勾的）—— 直接提交 + 推送…\n';
        const r = await POST('/api/push', { message: msg, skip_verify: true });
        showPushResult(r, true);
      } else {
        $('#pub-log').textContent += '跑 npm run verify（构建 + 测试）…\n';
        const r = await POST('/api/push', { message: msg, skip_verify: false });
        showPushResult(r, false);
      }
    } catch (e) {
      $('#pub-log').textContent += '\n✘ ' + e.message + '\n';
      toast(e.message, true);
      renderSteps([{ name: '出错', state: 'bad', detail: e.message.slice(0, 60) }]);
    } finally {
      btnPush.disabled = false;
      btnCommit.disabled = false;
    }
    await refreshState();
  }

  function showPushResult(r, _skipVerify) {
    const log = $('#pub-log');
    // 后端已经把「构建与校验 / 提交 / 推送」三步入到 steps 里（跳过的也会写明"已跳过"）
    const steps = (r.steps || []).map((s) => ({ name: s.name, state: s.ok ? 'ok' : 'bad', detail: s.detail || '' }));
    renderSteps(steps.length ? steps : STEP_NAMES.map((n) => ({ name: n, state: 'pending' })));

    let text = '';
    if (r.build) {
      text += `npm run verify：returncode=${r.build.returncode}｜页数=${r.build.pages ?? '—'}｜测试=${r.build.tests || '—'}`;
      if (r.build.env_fail) text += '（本机已知环境坑：tokens.test.ts 的 spawnSync EBUSY，不算真失败）';
      text += '\n';
      if (!r.build.ok && r.build.tail) text += '\n' + r.build.tail + '\n';
    }
    if (r.push && r.push.messages) text += '\n' + r.push.messages.join('\n') + '\n';
    if (r.error) text += '\n✘ ' + r.error + '\n';
    log.textContent = text || '（没有输出）';
    log.scrollTop = log.scrollHeight;

    if (r.ok) {
      toast(r.push && r.push.up_to_date ? '远端本来就是最新，没推' : '推上去了 —— 1–3 分钟后 Cloudflare 自动部署');
    } else if (!r.error) {
      toast('没有推成功，看下面的输出', true);
    }
  }

  async function commitOnly() {
    const msg = $('#pub-msg').value.trim() || `写作台：内容更新（${stamp()}）`;
    const btn = $('#btn-commit-only');
    btn.disabled = true;
    try {
      const c = await POST('/api/commit', { message: msg });
      toast(c.skipped ? '工作区没有改动' : `已提交 ${c.sha_short} ${c.subject}`);
      await loadPublish();
    } catch (e) {
      toast(e.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  function initPublish() {
    $('#btn-sync-remote').addEventListener('click', () => syncRemote());
    $('#btn-refresh-state').addEventListener('click', () => loadPublish().then(() => toast('刷新了')).catch((e) => toast(e.message, true)));
    $('#btn-push').addEventListener('click', async () => {
      const skip = $('#pub-skip').checked;
      if (!skip) {
        const ok = await askConfirm('构建 + 提交 + 推送',
          '会先跑 <code>npm run verify</code>（构建 + 测试）。过了才提交、才推。<br>' +
          '推上去就是发布 —— 1–3 分钟后线上生效。', { okText: '开始' });
        if (!ok) return;
      }
      runPush(skip);
    });
    $('#btn-commit-only').addEventListener('click', () => commitOnly());
  }

  async function syncRemote() {
    const buttons = ['#btn-sync-remote', '#btn-push', '#btn-commit-only'];
    const main = $('main.work');
    const nav = $('#nav');
    const previous = [main.inert, nav.inert];
    main.inert = true;
    nav.inert = true;
    buttons.forEach((selector) => { $(selector).disabled = true; });
    try {
      const result = await POST('/api/sync');
      if (result.updated) {
        try {
          await loadTags();
        } catch (e) {
          S.tags = [];
          toast('内容已同步，但最新标签读取失败；重新打开写作台后再编辑：' + e.message, true);
          return;
        }
      }
      toast(result.message, !result.updated && ['diverged', 'local_changes', 'in_progress'].includes(result.reason));
    } catch (e) {
      toast('同步未完成：' + e.message, true);
    } finally {
      buttons.forEach((selector) => { $(selector).disabled = false; });
      // 保留编辑框与版本；下次保存仍由既有的版本检查保护未保存内容。
      try { await loadPublish(); }
      finally { [main.inert, nav.inert] = previous; }
    }
  }

  /* ══════════════════ 十三、快捷入口 ══════════════════ */

  function initShortcuts() {
    const names = {
      repo: '仓库目录', posts: '文章目录', talks: '说说图片目录',
      gallery: '相册目录', public: '静态产物目录',
    };
    $$('[data-open]').forEach((b) => b.addEventListener('click', async () => {
      try {
        const r = await POST('/api/reveal', { target: b.dataset.open });
        toast('已打开' + (names[b.dataset.open] || '') + '：' + r.path);
      } catch (e) {
        toast(e.message, true);
      }
    }));
    $('#lnk-gh').href = 'https://github.com/NorthenCedar/personal';
    $('#lnk-cf').href = 'https://dash.cloudflare.com/';
  }

  /* ══════════════════ 十四、设置 ══════════════════ */

  async function loadSettings() {
    const c = await GET('/api/config');
    S.cfg = Object.assign(S.cfg, c);
    $('#s-repo').value = c.repo || '';
    $('#s-site').value = c.site || '';
    $('#s-maxkb').value = c.max_kb || 300;
    $('#s-token').value = '';
    $('#s-token').placeholder = c.has_token ? '已配置（留空则不改）' : '还没填';
    $('#settings-note').textContent = c.has_token ? '管理密钥已配置' : '还没配管理密钥 —— 删线上留言需要它';
    $('#lnk-site').href = c.site || '#';
    $('#brand-sub').textContent = (c.site || '').replace(/^https?:\/\//, '');
    $('#about-box').innerHTML =
      `写作台 · 给「北杉札记」用的本地编辑器。界面是纯 HTML/CSS/JS，跑在本机一个只监听 127.0.0.1 的小服务上；
       所有文件读写、git、构建、推送都在 Python 进程里做。<br><br>
       仓库：<code>${esc(c.repo || '')}</code><br>
       站点：<code>${esc(c.site || '')}</code><br><br>
       配色与字体是从站上 <code>src/styles/tokens.css</code> 镜像过来的 —— 那边改了 token，这边也要跟着改。`;
  }

  async function saveSettings() {
    const patch = {
      repo: $('#s-repo').value.trim(),
      site: $('#s-site').value.trim().replace(/\/+$/, ''),
      max_kb: Number($('#s-maxkb').value) || 300,
    };
    try {
      const r = await POST('/api/config', { patch });
      S.cfg = Object.assign(S.cfg, r.config);
      toast('设置存好了（存在 blog-desk/config.json）');
      await loadSettings();
      await refreshState();
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function saveToken() {
    const v = $('#s-token').value.trim();
    if (!v) { toast('没填东西 —— 想清空请点「清除」', true); return; }
    try {
      const r = await POST('/api/config', { patch: { admin_token: v } });
      S.cfg = Object.assign(S.cfg, r.config);
      toast('管理密钥存好了（只落在本机 config.json，不回传给界面）');
      await loadSettings();
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function clearToken() {
    const ok = await askConfirm('清除管理密钥？', '清掉之后就删不了线上留言了。', { okText: '清除', danger: true });
    if (!ok) return;
    try {
      const r = await POST('/api/config', { patch: { admin_token: '' } });
      S.cfg = Object.assign(S.cfg, r.config);
      toast('已清除');
      await loadSettings();
    } catch (e) {
      toast(e.message, true);
    }
  }

  function initSettings() {
    $('#btn-save-settings').addEventListener('click', () => saveSettings());
    $('#btn-save-token').addEventListener('click', () => saveToken());
    $('#btn-clear-token').addEventListener('click', () => clearToken());
  }

  /* ══════════════════ 十五、键盘 ══════════════════ */

  const saveCurrent = () => {
    if (S.view === 'write') return savePost(false);
    if (S.view === 'log') return saveLog();
    if (S.view === 'talks') return saveTalk();
    toast('这一页没有要保存的东西');
    return Promise.resolve();
  };

  function initKeys() {
    // hash 变了就切页 —— 浏览器前进/后退、手改地址栏都能用。
    // ★ 必须有这个：只读一次 location.hash（在 boot 里）的话，
    //   之后任何改 hash 的跳转都**不会**换页，看起来就是"点了没反应"。
    //   go() 用的是 replaceState（不触发本事件），所以不会打转。
    window.addEventListener('hashchange', () => {
      const v = (location.hash || '').slice(1);
      if (VIEWS[v] && v !== S.view) go(v);
    });

    window.addEventListener('keydown', (e) => {
      const mod = e.ctrlKey || e.metaKey;
      if (e.key === 'Escape') { closeMenu(); return; }
      if (!mod) return;
      const k = e.key.toLowerCase();
      if (k === 's' || (k === 'enter' && S.view !== 'write')) {
        e.preventDefault();
        Promise.resolve(saveCurrent()).catch((err) => toast(err.message, true));
      } else if (k === 'p' && S.view === 'write') {
        e.preventDefault();
        togglePreview();
      }
    });
    window.addEventListener('mousedown', (e) => {
      if (!e.target.closest('#ctxmenu')) closeMenu();
    });
    window.addEventListener('blur', closeMenu);
    window.addEventListener('resize', closeMenu);
  }

  /* ══════════════════ 十六、启动 ══════════════════ */

  async function boot() {
    initShell();
    initKeys();
    initWrite();
    initTags();
    initLog();
    initTalks();
    initPhotos();
    initComments();
    initPublish();
    initShortcuts();
    initSettings();
    $$('#nav button').forEach((b) => b.addEventListener('click', () => go(b.dataset.view)));

    $('main.work').inert = true;
    $('#nav').inert = true;
    try {
      try {
        S.cfg = Object.assign(S.cfg, await GET('/api/config'));
      } catch { /* 配置读不到也继续 */ }

      // 只在初次读取内容前自动同步；后台状态刷新保持只读，写作中不自动移动文件。
      try {
        const result = await POST('/api/sync');
        if (result.updated) toast(result.message);
      } catch (e) {
        toast('暂未同步云端，当前仍可编辑本地内容：' + e.message, true);
      }
      try { await loadTags(); } catch (e) { toast('读标签注册表失败：' + e.message, true); }
      try { await refreshState(); } catch { /* 已由 refreshState 内部兜住 */ }

      const start = (location.hash || '').slice(1);
      go(VIEWS[start] ? start : 'write');
      await loadPosts().catch((e) => toast(e.message, true));
    } finally {
      $('main.work').inert = false;
      $('#nav').inert = false;
    }

    // ★ 启动完毕的**显式信号**：界面测试（`_t8.py` 等）靠它决定「可以点了」。
    //   为什么不靠"某个元素出现了"：`#nav button` 之类是 index.html 里的**静态** HTML，
    //   页面一解析完就在了 —— 此时 app.js 可能还没执行、事件还没绑。
    //   测试若在这时 `.click()`，点击会被**静默吞掉**（没有异常、没有提示），
    //   表现成"点了没反应"的假红（本轮真踩到：标签页测试 ③ 一直 0 行）。
    //   静态元素存在 ≠ 应用就绪，所以给一个只有 boot() 走完才会置位的标志。
    window.__deskReady = true;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => boot().catch((e) => toast('启动失败：' + e.message, true)));
  } else {
    boot().catch((e) => toast('启动失败：' + e.message, true));
  }
})();
