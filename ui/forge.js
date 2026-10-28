/* 造物台编辑器：独立保存会话，复用写作台图片上传与 Markdown 预览。 */
(function (root) {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const effects = ['lift', 'tilt', 'stack', 'zoom', 'scan', 'develop'];

  function chooseEffect(slug, requested, count) {
    let hash = 2166136261;
    for (const char of slug) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
    const effect = requested === 'auto' ? effects[hash % effects.length] : requested;
    return effect === 'stack' && count < 2 ? 'lift' : effect;
  }

  function moveImage(images, index, direction) {
    const next = [...images], target = index + direction;
    if (target >= 0 && target < next.length) [next[index], next[target]] = [next[target], next[index]];
    return next;
  }

  function safeHref(href) {
    if (/[\\\u0000-\u0020\u007f]/.test(href)) return false;
    if (/^\/(?!\/)/.test(href) || href.startsWith('#')) return true;
    if (!href.startsWith('https://')) return false;
    try { const url = new URL(href); return !!url.hostname && !url.username && !url.password; }
    catch { return false; }
  }

  function safePreviewHtml(html, slug) {
    const decode = value => value.replace(/&(?:amp|lt|gt|quot|#39);/g,
      entity => ({'&amp;':'&', '&lt;':'<', '&gt;':'>', '&quot;':'"', '&#39;':"'"}[entity]));
    const attribute = value => value.replace(/[&<>"']/g,
      char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
    // 只处理现有渲染器生成的固定标签；代码已转义，不能被当作活动标签。
    return html.replace(/<video\b[\s\S]*?<\/video>/gi, '')
      .replace(/<(a|img)\b[^>]*>/gi, (tag, kind) => tag.replace(
        kind.toLowerCase() === 'a' ? /\bhref="([^"]*)"/i : /\bsrc="([^"]*)"/i,
        (_all, encoded) => {
          const value = decode(encoded);
          if (kind.toLowerCase() === 'a') return `href="${attribute(safeHref(value) ? value : '#')}"`;
          let src = value;
          try {
            const url = new URL(value, 'https://desk.invalid');
            if (url.origin === 'https://desk.invalid' && url.pathname === '/api/project/image'
                && url.searchParams.get('slug') === slug) src = url.searchParams.get('src') || '';
          } catch { /* 无法解析的目标也只交给当前项目图片接口检查。 */ }
          return `src="${attribute('/api/project/image?slug=' + encodeURIComponent(slug) + '&src=' + encodeURIComponent(src))}"`;
        }));
  }

  function previewMarkdown(markdown, slug, codeBlocks) {
    const transform = text => text.replace(/!\[([^\]]*)\]\(\s*(?:<([^>\r\n]+)>|([^\s)]+))(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*\)/g, (_all, alt, angled, plain) =>
      `![${alt}](/api/project/image?slug=${encodeURIComponent(slug)}&src=${encodeURIComponent(angled || plain)})`)
      .replace(/(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\)/g,
        (_all, label, href) => `[${label}](${safeHref(href) ? href : '#'})`);
    const inline = text => {
      let output = '', offset = 0;
      for (const match of text.matchAll(/(?<!`)(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)/g)) {
        output += transform(text.slice(offset, match.index)) + match[0];
        offset = match.index + match[0].length;
      }
      return output + transform(text.slice(offset));
    };
    if (!codeBlocks) return inline(markdown);
    const { lines, blocks } = codeBlocks(markdown);
    const ranges = blocks.map(block => [lines[block.first].offset,
      lines[block.last].offset + lines[block.last].text.length + lines[block.last].newline.length]);
    for (const line of lines) if (/^(?: {4}|\t)/.test(line.text)) ranges.push([line.offset, line.offset + line.text.length + line.newline.length]);
    let output = '', offset = 0;
    for (const [first, last] of ranges.sort((a, b) => a[0] - b[0])) {
      if (last <= offset) continue;
      output += inline(markdown.slice(offset, Math.max(offset, first))) + markdown.slice(Math.max(offset, first), last);
      offset = last;
    }
    return output + inline(markdown.slice(offset));
  }

  function createSession(post) {
    let current = null, dirty = false, version = 0;
    return {
      get current() { return current; },
      get dirty() { return dirty; },
      open(value) { current = clone(value); dirty = false; version++; },
      edit() { dirty = true; version++; },
      async save(fields, body) {
        const target = current, editing = version;
        if (!target) return null;
        const page = target.kind === 'forge';
        const payload = clone({ frontmatter: fields, ...(page ? {} : { slug: target.slug, body,
          create: !!target.create }), base_revision: target.revision });
        const saved = await post(page ? '/api/forge' : '/api/project', payload);
        if (target === current) {
          Object.assign(current, saved, { create: false });
          if (editing === version) dirty = false;
        }
        return saved;
      },
    };
  }

  function create({ GET, POST, esc, toast, askForm, askConfirm, pickFiles, fileToDataURL,
                    renderMd, bodyCodeBlocks, insertAtCursor, refreshState, go, config }) {
    const session = createSession(POST);
    const $ = selector => document.querySelector(selector);
    const pageLabels = {
      title: '页面标题', description: '页面描述', eyebrow: '页头引言', tagline: '页头主句',
      topline: '顶部短句', objectLabel: '物件标记', objectNote: '物件说明',
      connectionTitle: '连接标题', connectionNote: '连接说明', arrivalLabel: '开场标记',
      arrivalFirst: '开场第一行', arrivalSecond: '开场第二行', arrivalEndFirst: '结尾第一行',
      arrivalEndSecond: '结尾第二行', worksEyebrow: '作品区引言', worksTitle: '作品区标题',
      worksNote: '作品区说明', notesTitle: '工作手记标题',
    };
    let gallery = [], links = [], notes = [], projects = [], mode = 'projects';
    let initialized = false, loaded = false, loading = 0, saving = false;
    const field = (key, label, value = '', options = '') =>
      `<label class="field"><span>${esc(label)}</span><input ${options.includes('type=') ? '' : 'type="text"'} data-forge-field="${key}" value="${esc(value)}" ${options}></label>`;
    const area = (key, label, value = '', rows = 2) =>
      `<label class="field"><span>${esc(label)}</span><textarea data-forge-field="${key}" rows="${rows}">${esc(value)}</textarea></label>`;
    const get = key => $(`[data-forge-field="${key}"]`)?.value ?? '';
    const imageURL = src => '/api/project/image?slug=' + encodeURIComponent(session.current.slug) + '&src=' + encodeURIComponent(src);
    const run = fn => async event => {
      const button = event?.currentTarget;
      if (button) button.disabled = true;
      try { await fn(event); } catch (error) { toast(error.message, true); }
      finally { if (button) button.disabled = button.id === 'forge-add-image' && gallery.length >= 6; }
    };
    const changed = () => {
      session.edit();
      $('#forge-status').textContent = '有未保存的修改';
    };
    const canSwitch = async () => !session.dirty || await askConfirm('保留当前编辑？',
      '当前还有未保存的修改。继续将放弃这些输入；你也可以取消并先保存。', { okText: '放弃修改并继续' });

    function init() {
      if (initialized) return;
      initialized = true;
      $('[data-view="forge"].view').innerHTML = `
        <div class="row tight forge-tabs"><button class="btn primary" id="forge-projects-tab">项目</button>
          <button class="btn" id="forge-page-tab">页面文案</button><span class="hint" id="forge-status" role="status" aria-live="polite"></span></div>
        <div class="split forge-split"><div id="forge-sidebar"><div class="row tight">
          <button class="btn primary sm" id="forge-new">＋ 新建项目</button><button class="btn sm" id="forge-reload">重新读取</button>
          </div><div class="list" id="forge-list"></div></div>
          <div class="stack" id="forge-editor"><div class="empty">选择一个项目，或新建作品。</div></div></div>`;
      $('#forge-projects-tab').addEventListener('click', run(async () => {
        if (mode === 'projects') return;
        if (!await canSwitch()) return;
        mode = 'projects'; session.open({ kind: 'empty' });
        $('#forge-sidebar').hidden = false;
        $('#forge-editor').innerHTML = '<div class="empty">选择一个项目，或新建作品。</div>';
        tabs(); await loadList();
      }));
      $('#forge-page-tab').addEventListener('click', run(async () => {
        if (mode === 'page') return;
        if (!await canSwitch()) return;
        await openPage();
      }));
      $('#forge-new').addEventListener('click', run(newProject));
      $('#forge-reload').addEventListener('click', run(async () => {
        if (!await canSwitch()) return;
        await loadList();
        if (session.current?.slug && !session.current.create) await openProject(session.current.slug, false);
      }));
      window.addEventListener('beforeunload', event => {
        if (!session.dirty) return;
        event.preventDefault(); event.returnValue = '';
      });
    }

    function tabs() {
      $('#forge-projects-tab').classList.toggle('primary', mode === 'projects');
      $('#forge-page-tab').classList.toggle('primary', mode === 'page');
      $('[data-view="forge"].view .forge-split').classList.toggle('forge-page-mode', mode === 'page');
      $('#forge-status').textContent = '';
    }

    async function loadList() {
      projects = (await GET('/api/projects')).items || [];
      const box = $('#forge-list'); box.replaceChildren();
      if (!projects.length) box.innerHTML = '<div class="empty">还没有项目。先记录一件真正做过的作品。</div>';
      for (const project of projects) {
        const button = document.createElement('button');
        button.className = 'list-item';
        button.innerHTML = `<strong>${esc(project.title)}</strong><span class="hint">${esc(project.status)} · 排序 ${esc(project.order)}</span>`;
        if (project.slug === session.current?.slug) button.setAttribute('aria-current', 'true');
        button.addEventListener('click', run(() => openProject(project.slug)));
        box.append(button);
      }
    }

    async function enter() {
      init();
      if (!loaded) { await loadList(); loaded = true; }
    }

    async function openProject(slug, confirm = true) {
      if (confirm && !await canSwitch()) return;
      const request = ++loading;
      const project = await GET('/api/project?slug=' + encodeURIComponent(slug));
      if (request !== loading) return;
      const fm = project.frontmatter;
      const modelVisual = fm.visual !== 'image' ? fm.visual
        : fm.preview?.wordmark ? 'site' : fm.preview?.write ? 'desk' : null;
      session.open({ ...project, modelVisual }); renderProject();
    }

    async function newProject() {
      if (!await canSwitch()) return;
      const answer = await askForm('新建项目', '填写固定的英文标识，保存后不能修改。照片展示至少需要一张带说明的封面。',
        [{ k: 'slug', label: '项目标识', placeholder: 'my-project' }], { okText: '开始编辑' });
      if (!answer) return;
      const slug = answer.slug.trim();
      if (slug.length > 60 || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) throw new Error('项目标识最多60字，请使用英文小写、数字和单个连字符');
      if (projects.some(project => project.slug === slug)) throw new Error('同名项目已存在，请从列表打开');
      loading++;
      session.open({ slug, create: true, body: '', frontmatter: {
        title: '', summary: '', tagline: '', category: '', status: '',
        order: projects.reduce((n, project) => Math.max(n, project.order), 0) + 1,
        tags: [], images: [], links: [], visual: 'image', effect: 'auto', preview: {},
      } });
      renderProject();
      changed();
    }

    function renderProject() {
      mode = 'projects'; tabs(); $('#forge-sidebar').hidden = false;
      const project = session.current, fm = project.frontmatter;
      const modelVisual = project.modelVisual || fm.visual;
      gallery = clone(fm.images || []); links = clone(fm.links || []);
      $('#forge-editor').innerHTML = `
        <div class="row spread"><h3>${project.create ? '新建作品' : esc(fm.title)}</h3><span class="hint">${esc(project.slug)}</span></div>
        ${field('title', '项目名称', fm.title, 'maxlength="60"')}
        ${area('summary', '项目摘要（最多 160 字）', fm.summary)}
        ${field('tagline', '一句话介绍', fm.tagline, 'maxlength="60"')}
        <div class="row">${field('category', '分类', fm.category)}${field('status', '当前状态', fm.status)}
          ${field('order', '排序（数字小的靠前）', fm.order, 'type="number" min="0" step="1"')}</div>
        ${field('tags', '标签（最多四个，用中文逗号分隔）', (fm.tags || []).join('，'))}
        <div class="row"><label class="field grow"><span>作品展示</span><select data-forge-field="visual">
          <option value="image">照片展示</option>${modelVisual === 'site' ? '<option value="site">原博客示意图</option>' : ''}
          ${modelVisual === 'desk' ? '<option value="desk">原写作台示意图</option>' : ''}</select></label>
          <label class="field grow"><span>图片动效</span><select data-forge-field="effect">
          ${[['auto', '自动选择'], ['lift', '轻抬'], ['tilt', '倾斜'], ['stack', '叠片'], ['zoom', '放大'], ['scan', '掠光'], ['develop', '显影']]
            .map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select></label></div>
        <div class="stack"><h3>项目图库</h3><span class="hint">最多六张，第一张是封面。叠片展示前三张；暗色替图可留空。</span>
          <div id="forge-images" class="stack"></div><button class="btn sm" id="forge-add-image">＋ 上传项目图片</button></div>
        <div class="stack"><h3>项目链接</h3><div id="forge-links" class="stack"></div><button class="btn sm" id="forge-add-link">＋ 添加链接</button></div>
        ${area('body', '作品正文（Markdown）', project.body || '', 12)}
        <div class="row tight"><button class="btn sm" id="forge-body-image">插入正文配图</button><button class="btn sm" id="forge-preview-button">预览作品</button></div>
        <div id="forge-preview" class="preview forge-preview" hidden></div>
        <div class="row"><button class="btn primary" id="forge-save">保存项目 <span class="kbd">Ctrl+S</span></button>
          <button class="btn" id="forge-save-publish">保存并去发布</button></div>
        <p class="hint">保存后仍需在「发布」中检查并明确发布，才会上线。</p>`;
      $('[data-forge-field="visual"]').value = fm.visual || 'image';
      $('[data-forge-field="effect"]').value = fm.effect || 'auto';
      renderGallery(); renderLinks(); bindFields();
      $('#forge-add-image').addEventListener('click', run(() => uploadImage('gallery')));
      $('#forge-body-image').addEventListener('click', run(() => uploadImage('body')));
      $('#forge-add-link').addEventListener('click', () => {
        if (links.length >= 4) return toast('最多四个项目链接', true);
        links.push({ label: '', href: '' }); changed(); renderLinks();
      });
      $('#forge-preview-button').addEventListener('click', previewProject);
      $('#forge-save').addEventListener('click', run(() => save(false)));
      $('#forge-save-publish').addEventListener('click', run(() => save(true)));
    }

    function bindFields() {
      // 重绘的输入控件分别监听，避免重绘嵌套列表后累积父级处理器。
      $('#forge-editor').querySelectorAll('[data-forge-field]').forEach(input => input.addEventListener('input', changed));
    }

    function renderGallery() {
      const box = $('#forge-images'); box.replaceChildren();
      gallery.forEach((image, index) => {
        const row = document.createElement('div'); row.className = 'forge-image-row card';
        row.innerHTML = `<img class="forge-thumb" src="${esc(imageURL(image.src))}" alt="${esc(image.alt || '待填写图片说明')}">
          <div class="stack grow"><b>${index === 0 ? '封面' : `图片 ${index + 1}`}</b>
          <label class="field"><span>图片说明</span><input type="text" data-image-alt value="${esc(image.alt)}"></label>
          <span class="hint">${esc(image.src)}</span><div class="row tight">
          <button class="btn sm" data-image-dark>选择暗色替图</button>${image.darkSrc ? '<button class="btn sm" data-image-clear>移除暗色替图</button>' : ''}
          <button class="btn sm" data-image-up ${index === 0 ? 'disabled' : ''} aria-label="上移图片 ${index + 1}">上移</button>
          <button class="btn sm" data-image-down ${index === gallery.length - 1 ? 'disabled' : ''} aria-label="下移图片 ${index + 1}">下移</button>
          <button class="btn sm" data-image-remove aria-label="移除图片 ${index + 1}">移除</button></div>
          ${image.darkSrc ? `<span class="hint">暗色替图：${esc(image.darkSrc)}</span>` : ''}</div>`;
        row.querySelector('[data-image-alt]').addEventListener('input', event => { image.alt = event.target.value; changed(); });
        for (const [selector, direction] of [['[data-image-up]', -1], ['[data-image-down]', 1]]) {
          row.querySelector(selector).addEventListener('click', () => {
            gallery = moveImage(gallery, index, direction); changed(); renderGallery();
          });
        }
        row.querySelector('[data-image-remove]').addEventListener('click', () => { gallery.splice(index, 1); changed(); renderGallery(); });
        row.querySelector('[data-image-dark]').addEventListener('click', run(() => uploadImage('dark', image)));
        row.querySelector('[data-image-clear]')?.addEventListener('click', () => { delete image.darkSrc; changed(); renderGallery(); });
        box.append(row);
      });
      $('#forge-add-image').disabled = gallery.length >= 6;
    }

    function renderLinks() {
      const box = $('#forge-links'); box.replaceChildren();
      links.forEach((link, index) => {
        const row = document.createElement('div'); row.className = 'row';
        row.innerHTML = `<label class="field"><span>链接名称 ${index + 1}</span><input type="text" data-link-label value="${esc(link.label)}"></label>
          <label class="field grow"><span>站内路径或 HTTPS 地址</span><input type="text" data-link-href value="${esc(link.href)}"></label>
          <button class="btn sm" aria-label="移除链接 ${index + 1}">移除</button>`;
        for (const key of ['label', 'href']) row.querySelector(`[data-link-${key}]`).addEventListener('input', event => { link[key] = event.target.value; changed(); });
        row.querySelector('button').addEventListener('click', () => { links.splice(index, 1); changed(); renderLinks(); });
        box.append(row);
      });
      $('#forge-add-link').disabled = links.length >= 4;
    }

    async function uploadImage(where, image) {
      if (where === 'gallery' && gallery.length >= 6) throw new Error('图库最多六张');
      const target = session.current;
      const files = await pickFiles();
      const selected = where === 'dark' ? files.slice(0, 1) : files;
      for (const file of selected) {
        if (target !== session.current) return;
        if (where === 'gallery' && gallery.length >= 6) break;
        const result = await POST('/api/project/upload', { slug: target.slug, name: file.name,
          data: await fileToDataURL(file), max_kb: config().max_kb || 300 });
        if (target !== session.current) return toast('图片已上传到原项目，请重新打开该项目查看', true);
        const alt = file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
        if (where === 'dark') image.darkSrc = result.rel;
        else if (where === 'gallery') {
          if (gallery.length >= 6) { toast('图库已有六张，此次图片已上传，可在正文中使用', true); break; }
          gallery.push({ src: result.rel, alt });
        }
        else insertAtCursor($('[data-forge-field="body"]'), `\n![${alt}](${result.rel})\n`);
        changed();
      }
      renderGallery();
    }

    function collectProject() {
      const fm = { ...session.current.frontmatter };
      for (const key of ['title', 'summary', 'tagline', 'category', 'status', 'visual', 'effect']) fm[key] = get(key);
      fm.order = Number(get('order'));
      fm.tags = get('tags').split(/[，,]/).map(value => value.trim()).filter(Boolean);
      fm.images = clone(gallery); fm.links = clone(links);
      return fm;
    }

    function previewProject() {
      const fm = collectProject(), box = $('#forge-preview');
      box.hidden = false;
      const effect = chooseEffect(session.current.slug, fm.effect, gallery.length);
      let visual;
      if (fm.visual === 'image') {
        visual = `<div class="forge-photo-preview forge-effect-${effect}" tabindex="0" aria-label="作品图片预览，悬停或聚焦查看动效">
          ${gallery.slice(0, effect === 'stack' ? 3 : 1).map(image => `<picture>${image.darkSrc ? `<source media="(prefers-color-scheme: dark)" srcset="${esc(imageURL(image.darkSrc))}">` : ''}
          <img src="${esc(imageURL(image.src))}" alt="${esc(image.alt)}"></picture>`).join('')}</div>`;
      } else {
        const p = fm.preview || {};
        visual = `<div class="forge-model forge-model-${esc(fm.visual)}"><div class="forge-model-bar">${esc(p.wordmark || p.name || fm.title)}</div>
          <small>${esc(p.nav || [p.write, p.publish, p.talks, p.gallery].filter(Boolean).join(' / '))}</small>
          <h2>${esc(p.title || fm.title)}</h2><p>${esc(p.subtitle || p.description || '')}</p>
          ${p.code ? `<pre><code>${esc(p.code)}</code></pre>` : ''}<small>${esc(p.first || p.saved || '')}</small></div>`;
      }
      const body = safePreviewHtml(renderMd(previewMarkdown(get('body'), session.current.slug, bodyCodeBlocks)), session.current.slug);
      box.innerHTML = `${visual}<h2>${esc(fm.title)}</h2><p>${esc(fm.tagline)}</p><p>${esc(fm.summary)}</p>${body}`;
    }

    async function openPage() {
      const request = ++loading;
      const page = await GET('/api/forge');
      if (request !== loading) return;
      session.open({ ...page, kind: 'forge' }); mode = 'page'; tabs();
      $('#forge-sidebar').hidden = true;
      const fm = page.frontmatter;
      notes = clone(fm.notes || []);
      $('#forge-editor').innerHTML = `<h3>页头与工作手记</h3>
        <div class="forge-page-fields">${Object.entries(pageLabels).map(([key, label]) => area(key, label, fm[key] || '')).join('')}</div>
        <h3>滚动提示</h3>${[0, 1, 2].map(index => area('stage-' + index, '提示 ' + (index + 1), fm.stages?.[index] || '')).join('')}
        <h3>工作手记</h3><div id="forge-notes" class="stack"></div><button class="btn sm" id="forge-add-note">＋ 添加手记</button>
        <div class="row"><button class="btn primary" id="forge-save">保存页面文案 <span class="kbd">Ctrl+S</span></button>
          <button class="btn" id="forge-save-publish">保存并去发布</button><button class="btn" id="forge-page-reload">重新读取</button></div>
        <p class="hint">保存后仍需在「发布」中检查并明确发布，才会上线。</p>`;
      renderNotes(); bindFields();
      $('#forge-add-note').addEventListener('click', () => { notes.push({ title: '', text: '' }); changed(); renderNotes(); });
      $('#forge-save').addEventListener('click', run(() => save(false)));
      $('#forge-save-publish').addEventListener('click', run(() => save(true)));
      $('#forge-page-reload').addEventListener('click', run(async () => { if (await canSwitch()) await openPage(); }));
    }

    function renderNotes() {
      const box = $('#forge-notes'); box.replaceChildren();
      notes.forEach((note, index) => {
        const row = document.createElement('div'); row.className = 'card stack';
        row.innerHTML = `<label class="field"><span>手记 ${index + 1} 标题</span><input type="text" data-note-title value="${esc(note.title)}"></label>
          <label class="field"><span>手记内容</span><textarea data-note-text rows="3">${esc(note.text)}</textarea></label>
          <div class="row tight"><button class="btn sm" data-note-up ${index === 0 ? 'disabled' : ''}>上移</button>
          <button class="btn sm" data-note-down ${index === notes.length - 1 ? 'disabled' : ''}>下移</button><button class="btn sm" data-note-remove>移除</button></div>`;
        for (const key of ['title', 'text']) row.querySelector(`[data-note-${key}]`).addEventListener('input', event => { note[key] = event.target.value; changed(); });
        for (const [key, direction] of [['up', -1], ['down', 1]]) row.querySelector(`[data-note-${key}]`).addEventListener('click', () => { notes = moveImage(notes, index, direction); changed(); renderNotes(); });
        row.querySelector('[data-note-remove]').addEventListener('click', () => { notes.splice(index, 1); changed(); renderNotes(); });
        box.append(row);
      });
    }

    async function save(publish = false) {
      if (saving || !session.current || session.current.kind === 'empty') return;
      saving = true;
      const target = session.current;
      const buttons = [...$('#forge-editor').querySelectorAll('#forge-save, #forge-save-publish')];
      buttons.forEach(button => { button.disabled = true; });
      try {
        let fields;
        if (target.kind === 'forge') {
          fields = { ...target.frontmatter };
          for (const key of Object.keys(pageLabels)) fields[key] = get(key);
          fields.stages = [0, 1, 2].map(index => get('stage-' + index));
          fields.notes = clone(notes);
        } else fields = collectProject();
        await session.save(fields, get('body'));
        toast('已保存到本地，发布后才会上线');
        if (target === session.current) {
          $('#forge-status').textContent = session.dirty ? '有未保存的修改' : '已保存到本地';
          if (target.kind !== 'forge') await loadList();
          if (target === session.current && !session.dirty && publish) go('publish');
        }
        await refreshState();
      } finally {
        saving = false; buttons.forEach(button => { if (button.isConnected) button.disabled = false; });
      }
    }

    return { init, enter, save };
  }

  const api = { create, createSession, chooseEffect, moveImage, previewMarkdown, safePreviewHtml };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DeskForge = api;
})(typeof window !== 'undefined' ? window : globalThis);
