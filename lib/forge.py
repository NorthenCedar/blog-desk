"""造物台内容：固定路径、原文保留、版本冲突与项目配图。"""
from __future__ import annotations

import hashlib
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from lib.content import Content, ContentConflict, ContentError, parse_frontmatter, split_frontmatter
from lib import images

PROJECT_FIELDS = ('title', 'summary', 'tagline', 'category', 'status', 'order', 'tags',
                  'visual', 'effect', 'images', 'links', 'preview')
PAGE_FIELDS = ('title', 'description', 'eyebrow', 'tagline', 'topline', 'objectLabel',
               'objectNote', 'connectionTitle', 'connectionNote', 'arrivalLabel',
               'arrivalFirst', 'arrivalSecond', 'arrivalEndFirst', 'arrivalEndSecond',
               'worksEyebrow', 'worksTitle', 'worksNote', 'notesTitle', 'stages', 'notes')
EFFECTS = ('auto', 'lift', 'tilt', 'stack', 'zoom', 'scan', 'develop')
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)),
            *(f'lpt{i}' for i in range(1, 10))}


def parse_fields(text):
    try:
        return parse_frontmatter(text)
    except yaml.YAMLError as error:
        raise ContentError('页面字段的格式无法安全读取或保存，请保留原文件并检查字段引用') from error


def require_text(value, label, maximum=None):
    if (not isinstance(value, str) or not value.strip()
            or (maximum and len(value.encode('utf-16-le', 'surrogatepass')) // 2 > maximum)):
        raise ContentError(f'{label}不能为空' + (f'，且最多 {maximum} 字' if maximum else ''))


def safe_link(value):
    if not isinstance(value, str) or not value or re.search(r'[\\\x00-\x20\x7f]', value):
        return False
    if value.startswith('/') and not value.startswith('//'):
        return True
    try:
        parsed = urlsplit(value)
        return (value.startswith('https://') and bool(parsed.hostname)
                and not parsed.username and not parsed.password and parsed.port != 0)
    except ValueError:
        return False


def markdown_prose(body):
    """校验实际正文引用，代码里的 Markdown 示例保持字面含义。"""
    prose, fence, offset, list_indent, list_blank = [], None, 0, None, False
    while offset < len(body):
        if offset == 0 or body[offset - 1] == '\n':
            end = body.find('\n', offset)
            end = len(body) if end < 0 else end + 1
            line = body[offset:end]
            quoted = re.sub(r'^(?: {0,3}>[ \t]?)+', '', line)
            probe = re.sub(r'^\s*(?:[-+*]|\d+[.)])[ \t]+', '', quoted)
            indent = len(quoted.expandtabs(4)) - len(quoted.expandtabs(4).lstrip(' '))
            if fence and quoted.strip() and ((fence[2] and quoted == line) or (fence[3] and indent < fence[3])):
                # 引用/列表容器已经结束，后面的正文不能继续当作其中的代码。
                fence = None
            marker = re.match(r'^(\s*)(`{3,}|~{3,})(.*)$', probe if fence and fence[1] else line)
            if fence:
                if (marker and (fence[1] or re.fullmatch(' {0,3}', marker[1]))
                        and marker[2][0] == fence[0][0] and len(marker[2]) >= len(fence[0])
                        and not marker[3].strip(' \t\r\n')):
                    fence = None
                prose.append('\n'); offset = end; continue
            item = re.match(r'^( *)(?:[-+*]|\d+[.)])[ \t]+', quoted)
            if item and (indent < 4 or list_indent is not None):
                list_indent, list_blank = len(item[0]), False
            elif not quoted.strip():
                list_blank = True
            elif list_indent is not None and indent < list_indent and (list_blank or re.match(r'^(?:#{1,6}\s|>|`{3,}|~{3,})', quoted)):
                list_indent, list_blank = None, False
            if indent >= (list_indent or 0) + 4:
                prose.append('\n'); offset = end; continue
            opening = re.match(r'^(\s*)(`{3,}|~{3,})(.*)$', probe)
            if opening and not (opening[2][0] == '`' and '`' in opening[3]):
                fence = (opening[2], probe != line or list_indent is not None, quoted != line, list_indent)
                prose.append('\n'); offset = end; continue
        if (body.startswith('<!--', offset)
                and (len(body[:offset]) - len(body[:offset].rstrip('\\'))) % 2 == 0):
            close = body.find('-->', offset + 4)
            end = len(body) if close < 0 else close + 3
            comment = body[offset + 4:end]
            if comment.startswith(('>', '->')) or '--!>' in comment:
                raise ContentError('项目注释的结束方式不明确，请使用完整的 <!-- 注释 -->')
            prose.append('\n' * body[offset:end].count('\n'))
            offset = end; continue
        if body[offset] == '`':
            run = re.match(r'`+', body[offset:])[0]
            backslashes = len(body[:offset]) - len(body[:offset].rstrip('\\'))
            if backslashes % 2:
                prose.append(run); offset += len(run); continue
            boundary = re.search(r'\r?\n[ \t]*\r?\n', body[offset + len(run):])
            limit = offset + len(run) + boundary.start() if boundary else len(body)
            closing = re.search(r'(?<!`)' + re.escape(run) + r'(?!`)', body[offset + len(run):limit])
            if closing:
                end = offset + len(run) + closing.end()
                prose.append('\n' * body[offset:end].count('\n'))
                offset = end; continue
            prose.append(run); offset += len(run); continue
        prose.append(body[offset]); offset += 1
    return ''.join(prose)


def markdown_destination(text, start):
    """先读取目标，再校验；单双引号、括号 title 都不能绕过目标检查。"""
    offset = start
    while offset < len(text) and text[offset].isspace():
        offset += 1
    if offset < len(text) and text[offset] == '<':
        end = text.find('>', offset + 1)
        value = text[offset + 1:end] if end >= 0 else text[offset + 1:]
    else:
        first, depth = offset, 0
        while offset < len(text):
            char = text[offset]
            if char == '\\' and offset + 1 < len(text):
                offset += 2; continue
            if char.isspace() or char == ')' and depth == 0:
                break
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            offset += 1
        value = text[first:offset]
    return unescape(re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])', r'\1', value))


def markdown_targets(prose):
    normalize = lambda label: re.sub(r'\s+', ' ', label).strip().casefold()
    definitions = {}
    for match in re.finditer(r'^ {0,3}\[([^\]]+)\]:[ \t]*', prose, re.M):
        definitions.setdefault(normalize(match[1]), markdown_destination(prose, match.end()))
    reference_labels = set()
    for start, char in enumerate(prose):
        if char != '[' or start in reference_labels:
            continue
        backslashes = len(prose[:start]) - len(prose[:start].rstrip('\\'))
        if backslashes % 2:
            continue
        offset, depth = start + 1, 1
        while offset < len(prose) and depth:
            if prose[offset] == '\\':
                offset += 2; continue
            if prose[offset] == '[':
                depth += 1
            elif prose[offset] == ']':
                depth -= 1
            offset += 1
        if depth:
            continue
        label = prose[start + 1:offset - 1]
        image = start > 0 and prose[start - 1] == '!'
        if prose[offset:offset + 1] == '(':
            yield image, markdown_destination(prose, offset + 1)
        elif prose[offset:offset + 1] != ':':
            if prose[offset:offset + 1] == '[':
                end = prose.find(']', offset + 1)
                if end < 0:
                    continue
                reference_labels.add(offset)
                label = prose[offset + 1:end] or label
            target = definitions.get(normalize(label))
            if target is not None:
                yield image, target


def patch_fields(text, fields, allowed, body=None):
    """仅替换有变化的顶层值；未知字段、未变字段、注释与正文保留原文。"""
    fm, old_body = split_frontmatter(text)
    old = parse_fields(fm)
    node = yaml.compose(fm)
    scalars, visited = [], set()
    def collect_scalars(value):
        if id(value) in visited:
            return
        visited.add(id(value))
        if value.id == 'scalar':
            scalars.append((value.start_mark.index, value.end_mark.index))
        elif value.id == 'sequence':
            for child in value.value:
                collect_scalars(child)
        elif value.id == 'mapping':
            for key, child in value.value:
                collect_scalars(key); collect_scalars(child)
    collect_scalars(node)
    spans = {key.value: (key.start_mark.index, value.end_mark.index, value.id)
             for key, value in node.value}
    if not old and fm.strip() == '{}':
        fm = ''
    edits, appended = [], []
    for key in allowed:
        if key not in fields or fields[key] == old.get(key) and key in old:
            continue
        rendered = yaml.safe_dump({key: fields[key]}, allow_unicode=True, sort_keys=False,
                                  width=1000).rstrip('\n')
        if key in spans:
            start, end, kind = spans[key]
            if end < start:
                raise ContentError('此字段引用了其他字段，无法安全改写；原文件未修改')
            if kind in ('mapping', 'sequence'):
                # 集合的 end_mark 可能越过后续顶层注释；把这些原文留在替换范围之外。
                tail = fm[start:end].splitlines(keepends=True)
                while tail and (tail[-1].startswith('#') or not tail[-1].strip()):
                    end -= len(tail.pop())
            if fm[start:end].endswith('\n'):
                rendered += '\n'
            if kind in ('mapping', 'sequence'):
                comments, position = [], start
                for line in fm[start:end].splitlines(keepends=True):
                    if line.lstrip(' \t').startswith('#') and not any(first <= position < last for first, last in scalars):
                        comments.append(line if line.endswith('\n') else line + '\n')
                    position += len(line)
                rendered = ''.join(comments) + rendered
            edits.append((start, end, rendered))
        else:
            appended.append(rendered)
    for start, end, rendered in sorted(edits, reverse=True):
        fm = fm[:start] + rendered + fm[end:]
    if appended:
        fm = (fm.rstrip('\n') + '\n' if fm else '') + '\n'.join(appended)
    prepared = parse_fields(fm)
    if any(prepared.get(key) != value for key, value in old.items() if key not in allowed):
        raise ContentError('此修改会影响其他字段的引用，原文件未修改；请先检查关联字段')
    return '---\n' + fm + '\n---\n' + (old_body if body is None else body)


class Forge:
    def __init__(self, repo):
        self.repo = Path(repo).resolve()
        self.content = Content(self.repo)

    def _inside(self, path):
        if not path.resolve().is_relative_to(self.repo) or path.resolve() != path.absolute():
            raise ContentError('项目路径超出仓库，未读取或修改文件')
        return path

    def project_dir(self, slug):
        if not isinstance(slug, str) or len(slug) > 60 or not SLUG.fullmatch(slug) or slug in RESERVED:
            raise ContentError('项目标识须为英文小写、数字和单个连字符，不能使用系统保留名')
        return self._inside(self.repo / 'content/projects' / slug)

    def _page_path(self):
        return self._inside(self.repo / 'content/pages/forge.md')

    def _read(self, path, slug=None):
        if not path.is_file():
            raise ContentError('还没有这个项目或造物台页面，请先确认仓库与项目标识')
        return self._value(path.read_bytes(), path, slug)

    @staticmethod
    def _value(raw, path, slug=None):
        text = raw.decode('utf-8')
        fm, body = split_frontmatter(text)
        out = dict(frontmatter=parse_fields(fm), body=body,
                   revision=hashlib.sha256(raw).hexdigest(), dir=str(path.parent))
        if slug is not None:
            out['slug'] = slug
        return out

    def read_project(self, slug):
        return self._read(self._inside(self.project_dir(slug) / 'index.md'), slug)

    def list_projects(self):
        root = self._inside(self.repo / 'content/projects')
        out = []
        if root.is_dir():
            for path in root.iterdir():
                if path.is_dir() and (path / 'index.md').is_file():
                    p = self.read_project(path.name)
                    out.append(dict(slug=path.name, **p['frontmatter']))
        return sorted(out, key=lambda p: (p.get('order', 0), p['slug']))

    def asset_path(self, slug, src):
        root = self.project_dir(slug)
        if (not isinstance(src, str) or not src.startswith('./') or '..' in src[2:]
                or not re.fullmatch(r'[\w .-]+', src[2:])):
            raise ContentError('图片只能使用当前项目中的相对路径（如 ./cover.jpg）')
        path = root / src[2:]
        if (not path.resolve().is_relative_to(root.resolve()) or not path.is_file()
                or path.suffix.lower() not in images.IMAGE_EXT):
            raise ContentError('图片不在当前项目目录内，或图片文件不存在')
        self._inside(path)
        return path

    def read_image(self, slug, src):
        path = self.asset_path(slug, src)
        data = path.read_bytes()
        ext = images.real_ext(data)
        if not ext:
            raise ContentError('文件内容不是能识别的图片，请重新上传')
        return data, ext

    def upload(self, slug, name, data, max_kb=300):
        root = self.project_dir(slug)
        # 复用现有图片格式识别、压缩和不覆盖同名文件的入库流程。
        if not images.real_ext(data):
            raise ContentError('文件内容不是能识别的图片，请重新选择')
        name = re.sub(r'[^\w.-]+', '-', images._safe_name(name))
        name = re.sub(r'\.{2,}', '.', name)
        if Path(name).stem.lower() in RESERVED:
            name = 'image-' + name
        root.mkdir(parents=True, exist_ok=True)
        path, report = images._save_image(root, name, data, max(30, min(4000, int(max_kb))), True)
        return dict(ok=True, rel='./' + path.name, name=path.name, report=report)

    def _validate_project(self, slug, fm, body):
        for key, label, limit in (('title', '项目名称', 60), ('summary', '项目摘要', 160),
                                  ('tagline', '一句话介绍', 60), ('category', '分类', None),
                                  ('status', '当前状态', None)):
            require_text(fm.get(key), label, limit)
        if type(fm.get('order')) is not int or fm['order'] < 0:
            raise ContentError('排序须为大于或等于 0 的整数')
        if fm.get('visual', 'image') not in ('site', 'desk', 'image') or fm.get('effect', 'auto') not in EFFECTS:
            raise ContentError('请选择已有展示方式和图片动效')
        for key, maximum in (('tags', 4), ('images', 6), ('links', 4)):
            if not isinstance(fm.get(key, []), list) or len(fm.get(key, [])) > maximum:
                label = {'tags': '标签', 'images': '图片', 'links': '链接'}[key]
                raise ContentError(f'{label}最多 {maximum} 项')
        for tag in fm.get('tags', []):
            require_text(tag, '标签')
        if fm.get('visual', 'image') == 'image' and not fm.get('images'):
            raise ContentError('照片展示至少需要一张有说明的封面，第一张图片就是封面')
        for item in fm.get('images', []):
            if not isinstance(item, dict):
                raise ContentError('请补全项目图片')
            require_text(item.get('alt'), '图片说明')
            self.read_image(slug, item.get('src'))
            if 'darkSrc' in item:
                self.read_image(slug, item['darkSrc'])
        for item in fm.get('links', []):
            if not isinstance(item, dict) or not safe_link(item.get('href')):
                raise ContentError('项目链接须为站内路径或不含凭据的 HTTPS 地址')
            require_text(item.get('label'), '链接名称')
        preview = fm.get('preview', {})
        if not isinstance(preview, dict) or any(not isinstance(v, str) for v in preview.values()):
            raise ContentError('示意图文案必须是文字')
        if not isinstance(body, str):
            raise ContentError('项目正文必须是文字')
        prose = markdown_prose(body)
        for image, href in markdown_targets(prose):
            if image:
                self.read_image(slug, href)
            elif not safe_link(href) and not (href.startswith('#') and not re.search(r'[\\\x00-\x20]', href)):
                raise ContentError('正文链接须为站内路径或不含凭据的 HTTPS 地址')
        def check_autolink(match):
            if not safe_link(unescape(match[1])):
                raise ContentError('正文链接须为站内路径或不含凭据的 HTTPS 地址')
            return ''
        html = re.sub(r'<((?:[A-Za-z][A-Za-z0-9+.-]{1,31}:[^<>\s]*)|(?:[^<>\s@]+@[^<>\s@]+))>', check_autolink, prose)
        strike_depth = 0
        strike_tags = [tag for tag in re.finditer(r'</?del>', html)
                       if (len(html[:tag.start()]) - len(html[:tag.start()].rstrip('\\'))) % 2 == 0]
        for tag in strike_tags:
            strike_depth += -1 if tag.group() == '</del>' else 1
            if strike_depth < 0:
                raise ContentError('项目删除线标签未配对')
        if strike_depth:
            raise ContentError('项目删除线标签未配对')
        for tag in reversed(strike_tags):
            html = html[:tag.start()] + html[tag.end():]
        if re.search(r'</?[A-Za-z][A-Za-z0-9:-]*(?=[\s/>])|<![A-Za-z]|<\?', html):
            raise ContentError('项目正文请使用 Markdown 图片与文字，不接受活动 HTML 标签')

    @staticmethod
    def _revision(revision):
        if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{64}', revision):
            raise ContentError('缺少编辑版本，请先重新读取；当前内容尚未写入文件')

    def save_project(self, slug, fields, body, *, base_revision=None, create=False):
        path = self._inside(self.project_dir(slug) / 'index.md')
        if not isinstance(fields, dict):
            raise ContentError('项目字段无效')
        if create and path.exists():
            raise ContentConflict('同名项目已存在，没有覆盖，请换一个项目标识或重新读取')
        if create:
            old, newline, original = {}, '\n', '---\n{}\n---\n'
        else:
            self._revision(base_revision)
            self.content._check_revision(path, base_revision)
            original, newline = self.content._read(path)
            old = parse_fields(split_frontmatter(original)[0])
        for key in ('draft', 'updated', 'slug'):
            if key in fields and fields[key] != old.get(key):
                raise ContentError('项目不使用文章草稿、更新日期或可修改的标识字段')
        fm = {**old, **{key: value for key, value in fields.items() if key in PROJECT_FIELDS}}
        fm.setdefault('visual', 'image')
        fm.setdefault('effect', 'auto')
        fm.setdefault('tags', [])
        self._validate_project(slug, fm, body)
        rendered = patch_fields(original.replace('\r\n', '\n'), fm, PROJECT_FIELDS, body)
        raw = rendered.replace('\r\n', '\n').replace('\n', newline).encode('utf-8')
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('xb') as stream:
                    stream.write(raw)
            except FileExistsError as e:
                raise ContentConflict('同名项目已存在，没有覆盖') from e
        else:
            self.content._write(path, rendered, newline, base_revision)
        return self._value(raw, path, slug)

    def read_page(self):
        return self._read(self._page_path())

    def save_page(self, fields, *, base_revision=None):
        self._revision(base_revision)
        path = self._page_path()
        self.content._check_revision(path, base_revision)
        original, newline = self.content._read(path)
        old = parse_fields(split_frontmatter(original)[0])
        if not isinstance(fields, dict):
            raise ContentError('页面文案无效')
        fm = {**old, **{key: value for key, value in fields.items() if key in PAGE_FIELDS}}
        for key in PAGE_FIELDS:
            if key not in ('stages', 'notes') and key in fm:
                require_text(fm[key], '页面文案')
        stages = fm.get('stages')
        if not isinstance(stages, list) or len(stages) != 3:
            raise ContentError('滚动提示必须恰好三条')
        for stage in stages:
            require_text(stage, '滚动提示')
        if not isinstance(fm.get('notes'), list):
            raise ContentError('工作手记必须为列表')
        for item in fm['notes']:
            if not isinstance(item, dict):
                raise ContentError('请补全工作手记')
            require_text(item.get('title'), '手记标题')
            require_text(item.get('text'), '手记内容')
        rendered = patch_fields(original.replace('\r\n', '\n'), fm, PAGE_FIELDS)
        self.content._write(path, rendered, newline, base_revision)
        raw = rendered.replace('\r\n', '\n').replace('\n', newline).encode('utf-8')
        return self._value(raw, path)
