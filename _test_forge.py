"""造物台行为回归；夹具只写本次交付目录。"""
import copy
import importlib.util
import io
import tempfile
import unittest
import json
import subprocess
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pathlib import Path

from PIL import Image
from lib.content import ContentError, ContentConflict

ROOT = Path('D:/router/work/forge-writer-20261028/desktop-tests')
ROOT.mkdir(parents=True, exist_ok=True)
spec = importlib.util.find_spec('lib.forge')
if spec:
    from lib.forge import Forge
else:
    Forge = None


class ForgeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Forge, '桌面端尚不能读写造物台项目和页面文案')
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.forge = Forge(self.repo)

    def image(self, slug='demo', name='cover.png'):
        data = io.BytesIO()
        Image.new('RGB', (8, 8), 'green').save(data, 'PNG')
        return self.forge.upload(slug, name, data.getvalue(), 300)['rel']

    def fields(self):
        return dict(title='作品', summary='摘要', tagline='一句话', category='TOOL',
                    status='打磨中', order=0, tags=['工具'], visual='image', effect='auto',
                    images=[dict(src=self.image(), alt='绿色方块')], links=[])

    def test_create_list_and_save_without_article_metadata(self):
        saved = self.forge.save_project('demo', self.fields(), '## 过程\n', create=True)
        self.assertEqual(self.forge.list_projects()[0]['slug'], 'demo')
        self.assertEqual(saved['body'], '## 过程\n')
        self.assertNotIn('draft', saved['frontmatter'])
        self.assertNotIn('updated', saved['frontmatter'])
        with self.assertRaises(ContentConflict):
            self.forge.save_project('demo', self.fields(), '', create=True)

    def test_text_edit_keeps_model_preview_unknown_yaml_and_body(self):
        fields = self.fields()
        fields.update(visual='site', preview={'title': '原示意图'})
        p = self.forge.save_project('demo', fields, '保留正文\n', create=True)
        path = Path(p['dir']) / 'index.md'
        text = path.read_text(encoding='utf-8').replace('---\n', '---\n# 原注释\nunknown: {nested: [1, 2]}\n', 1)
        path.write_bytes(text.replace('\n', '\r\n').encode())
        p = self.forge.read_project('demo')
        changed = copy.deepcopy(p['frontmatter'])
        changed['summary'] = '新的摘要'
        out = self.forge.save_project('demo', changed, p['body'], base_revision=p['revision'])
        raw = path.read_bytes()
        self.assertIn(b'unknown: {nested: [1, 2]}\r\n', raw)
        self.assertIn('# 原注释'.encode(), raw)
        self.assertEqual(out['frontmatter']['preview'], {'title': '原示意图'})
        self.assertEqual(out['frontmatter']['visual'], 'site')
        self.assertEqual(out['body'], p['body'])

    def test_external_change_and_missing_revision_never_overwrite(self):
        p = self.forge.save_project('demo', self.fields(), '', create=True)
        path = Path(p['dir']) / 'index.md'
        path.write_bytes(path.read_bytes() + b'outside\n')
        old = path.read_bytes()
        for revision in (p['revision'], None):
            with self.assertRaises((ContentConflict, ContentError)):
                self.forge.save_project('demo', p['frontmatter'], 'lost', base_revision=revision)
            self.assertEqual(path.read_bytes(), old)

    def test_minimum_fields_gallery_effect_and_link_validation(self):
        fields = self.fields()
        for patch in ({'title': ''}, {'order': -1}, {'order': True}, {'effect': 'script'},
                      {'images': []}, {'images': fields['images'] * 7},
                      {'images': [dict(src='./cover.png', alt=' ')]},
                      {'links': [dict(label='坏链接', href='https://user:pass@example.com/')]},
                      {'links': [dict(label='坏链接', href='//example.com/')]},
                      {'visual': 'desk', 'draft': True}):
            with self.subTest(patch=patch), self.assertRaises(ContentError):
                self.forge.save_project('demo', {**fields, **patch}, '', create=True)
        self.assertFalse((self.repo / 'content/projects/demo/index.md').exists())

    def test_text_limits_match_site_utf16_counting(self):
        fields = self.fields()
        for key, value in (('title', '😀' * 31), ('tagline', '😀' * 31), ('summary', '😀' * 81)):
            with self.subTest(key=key), self.assertRaises(ContentError):
                self.forge.save_project('demo', {**fields, key: value}, '', create=True)
        self.assertFalse((self.repo / 'content/projects/demo/index.md').exists())

    def test_project_paths_assets_and_markdown_cannot_escape(self):
        fields = self.fields()
        for slug in ('../demo', 'demo/other', 'Demo', 'con', 'a' * 61):
            with self.subTest(slug=slug), self.assertRaises(ContentError):
                self.forge.read_project(slug)
        for src in ('../other/a.png', '/secret.png', 'https://example.com/a.png', './index.md', './nested/a.png'):
            with self.subTest(src=src), self.assertRaises(ContentError):
                self.forge.save_project('demo', {**fields, 'images': [dict(src=src, alt='图')]}, '', create=True)
        with self.assertRaises(ContentError):
            self.forge.save_project('demo', fields, '![](../other/a.png)', create=True)
        for href in ('javascript:alert', 'http://example.com/', 'https://u:p@example.com/'):
            with self.subTest(href=href), self.assertRaises(ContentError):
                self.forge.save_project('demo', fields, '[链接](' + href + ')', create=True)
        with self.assertRaises(ContentError):
            self.forge.upload('../escape', 'a.png', b'no', 300)

    def test_page_all_fields_stages_notes_preserve_unknown_and_conflict(self):
        path = self.repo / 'content/pages/forge.md'
        path.parent.mkdir(parents=True)
        path.write_text('---\ntitle: 造物台\ncustom: {keep: yes}\nstages: [一, 二, 三]\nnotes: []\n---\n原正文\n', encoding='utf-8')
        page = self.forge.read_page()
        fields = {**page['frontmatter'], 'title': '作品台', 'notes': [{'title': '手记', 'text': '两行\n内容'}]}
        out = self.forge.save_page(fields, base_revision=page['revision'])
        self.assertEqual(out['frontmatter']['notes'][0]['text'], '两行\n内容')
        self.assertEqual(out['body'], page['body'])
        self.assertIn('custom: {keep: yes}', path.read_text(encoding='utf-8'))
        with self.assertRaises(ContentConflict):
            self.forge.save_page(fields, base_revision=page['revision'])
        with self.assertRaises(ContentError):
            self.forge.save_page({**fields, 'stages': ['一']}, base_revision=out['revision'])

    def test_editing_notes_keeps_following_unknown_field_comment(self):
        path = self.repo / 'content/pages/forge.md'
        path.parent.mkdir(parents=True)
        path.write_bytes(('---\ntitle: 造物台\nstages: [一, 二, 三]\nnotes:\n'
                          '  - title: 原标题\n    text: 原内容\n'
                          '# 未知字段的说明\ncustom: {keep: yes}\n---\n原正文\n').encode())
        page = self.forge.read_page()
        fields = {**page['frontmatter'], 'notes': [{'title': '改后', 'text': '内容'}]}
        self.forge.save_page(fields, base_revision=page['revision'])
        self.assertIn('# 未知字段的说明\ncustom: {keep: yes}'.encode(), path.read_bytes())

    def test_http_create_upload_conflict_and_fixed_page_path(self):
        import server
        app = server.App(self.repo)
        handler = type('ForgeHandler', (server.Handler,), {'app': app})
        http = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        origin = 'http://127.0.0.1:' + str(http.server_port)

        def request(path, data=None):
            req = Request(origin + path, data=json.dumps(data).encode() if data else None,
                          headers={'Content-Type': 'application/json'})
            with urlopen(req) as response:
                return json.load(response)

        fields = self.fields()
        p = request('/api/project', dict(slug='demo', frontmatter=fields, body='正文', create=True))
        self.assertEqual(request('/api/projects')['items'][0]['title'], '作品')
        self.assertEqual(request('/api/project?slug=demo')['body'], '正文')
        with self.assertRaises(HTTPError) as conflict:
            request('/api/project', dict(slug='demo', frontmatter=fields, body='改写', base_revision='0' * 64))
        self.assertEqual(conflict.exception.code, 409)
        conflict.exception.close()
        with self.assertRaises(HTTPError) as bad:
            request('/api/project?slug=../demo')
        self.assertEqual(bad.exception.code, 400)
        bad.exception.close()
        with urlopen(origin + '/api/project/image?slug=demo&src=./cover.png') as image:
            self.assertEqual(image.headers['Content-Type'], 'image/png')
            self.assertTrue(image.read().startswith(b'\x89PNG'))
        self.assertEqual(p['frontmatter']['visual'], 'image')

    def test_broken_image_bytes_are_not_exposed_as_images(self):
        path = self.repo / 'content/projects/demo/fake.png'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'<script>unsafe</script>')
        with self.assertRaises(ContentError):
            self.forge.read_image('demo', './fake.png')

    def test_uploaded_common_filename_is_usable_in_gallery_and_markdown(self):
        fields = self.fields()
        src = self.image(name='my photo #1% (alt)..png')
        fields['images'] = [dict(src=src, alt='有空格和井号的原文件名')]
        saved = self.forge.save_project('demo', fields, '![配图](' + src + ')', create=True)
        self.assertEqual(saved['frontmatter']['images'][0]['src'], src)

    def test_existing_unicode_space_filename_saves_and_reads_in_same_project(self):
        fields = self.fields()
        root = self.repo / 'content/projects/demo'
        (root / 'cover.png').rename(root / '我的 作品.png')
        fields['images'] = [dict(src='./我的 作品.png', alt='带空格的旧配图')]
        p = self.forge.save_project('demo', fields, '![配图](<./我的 作品.png>)', create=True)
        self.assertEqual(p['frontmatter']['images'][0]['src'], './我的 作品.png')
        self.assertTrue(self.forge.read_image('demo', './我的 作品.png')[0].startswith(b'\x89PNG'))

    def test_slug_maximum_and_flat_image_scope_match_cloud_contract(self):
        self.assertEqual(self.forge.project_dir('a' * 60).name, 'a' * 60)
        with self.assertRaises(ContentError):
            self.forge.project_dir('a' * 61)
        self.image()
        root = self.repo / 'content/projects/demo'
        (root / 'nested').mkdir()
        (root / 'nested/cover.png').write_bytes((root / 'cover.png').read_bytes())
        with self.assertRaises(ContentError):
            self.forge.read_image('demo', './nested/cover.png')

    def test_yaml_alias_edge_case_cannot_write_broken_frontmatter(self):
        p = self.forge.save_project('demo', self.fields(), '', create=True)
        path = Path(p['dir']) / 'index.md'
        raw = path.read_bytes().replace('title: 作品'.encode(), 'title: &original 作品\ncustom: *original'.encode())
        path.write_bytes(raw)
        p = self.forge.read_project('demo')
        fields = {**p['frontmatter'], 'title': '新名称'}
        with self.assertRaises(ContentError):
            self.forge.save_project('demo', fields, p['body'], base_revision=p['revision'])
        self.assertEqual(path.read_bytes(), raw)

    def test_reference_style_assets_cannot_escape_but_code_examples_are_literal(self):
        p = self.forge.save_project('demo', self.fields(), '', create=True)
        path = Path(p['dir']) / 'index.md'
        raw = path.read_bytes()
        for body in ('![图][pic]\n\n[pic]: ../other/a.png',
                     '![pic]\n\n[pic]: https://example.com/a.png',
                     '[链接][bad]\n\n[bad]: javascript:alert',
                     '<img src="../other/a.png">'):
            with self.subTest(body=body):
                path.write_bytes(raw)
                with self.assertRaises(ContentError):
                    self.forge.save_project('demo', p['frontmatter'], body, base_revision=p['revision'])
                self.assertEqual(path.read_bytes(), raw)
        path.write_bytes(raw)
        body = '```md\n![示例](../sample.png)\n```\n\n`[示例](javascript:literal)`\n'
        saved = self.forge.save_project('demo', p['frontmatter'], body, base_revision=p['revision'])
        self.assertEqual(saved['body'], body)

    def test_image_and_link_titles_never_hide_unsafe_destinations(self):
        p = self.forge.save_project('demo', self.fields(), '', create=True)
        path = Path(p['dir']) / 'index.md'
        raw = path.read_bytes()
        for body in ("![alt](../other/a.png 'title')", '![alt](https://example.com/a.png (title))',
                     "![alt [nested]](../other/a.png 'title')", '[link](javascript:alert%281%29 "title")',
                     "[link](http://example.com/ 'title')", '[link](https://u:p@example.com/ (title))',
                     "![alt][pic]\n\n[pic]: ../other/a.png 'title'",
                     "![pic]\n\n[pic]: ../other/a.png\n[pic]: ./cover.png"):
            with self.subTest(body=body):
                path.write_bytes(raw)
                with self.assertRaises(ContentError):
                    self.forge.save_project('demo', p['frontmatter'], body, base_revision=p['revision'])
                self.assertEqual(path.read_bytes(), raw)
        path.write_bytes(raw)
        for title in ('"title"', "'title'", '(title)'):
            body = f'![封面](./cover.png {title}) [链接](https://example.com/ {title})'
            p = self.forge.save_project('demo', p['frontmatter'], body, base_revision=p['revision'])
            self.assertEqual(p['body'], body)

    def test_active_html_is_rejected_after_comments_without_touching_examples(self):
        p = self.forge.save_project('demo', self.fields(), '', create=True)
        path = Path(p['dir']) / 'index.md'
        raw = path.read_bytes()
        bodies = ('<!-- ` --><img src="../other/a.png"><!-- ` -->',
                  '<!-- ` --><image href="../other/a.png"><!-- ` -->',
                  '<!-- ` --><video poster="https://example.com/a.png"></video><!-- ` -->',
                  '<!--\n```\n-->\n<img src="../other/a.png">',
                  '<a href="http://example.com/">link</a>', '<a href="javascript:alert(1)">link</a>',
                  '<a href="https://example.com/">link</a>', '<meta http-equiv="refresh" content="0;url=https://example.com">',
                  '<div onclick="alert(1)">text</div>', '<svg:image href="./cover.png"/>',
                  '<http://example.com/>', '<javascript:alert(1)>', '<me@example.com>',
                  '<!--><img src="../other/a.png"><!-- -->',
                  '<!-- --!><img src="../other/a.png"><!-- -->')
        for body in bodies:
            with self.subTest(body=body):
                path.write_bytes(raw)
                with self.assertRaises(ContentError):
                    self.forge.save_project('demo', p['frontmatter'], body, base_revision=p['revision'])
                self.assertEqual(path.read_bytes(), raw)
        path.write_bytes(raw)
        body = ('<!-- <img src="../hidden.png"> ` -->\n'
                '```html\n<a href="javascript:example">example</a>\n<!--\n```\n'
                '`<img src="../example.png">` and `<!--`\n\n<https://example.com/>')
        saved = self.forge.save_project('demo', p['frontmatter'], body, base_revision=p['revision'])
        self.assertEqual(saved['body'], body)

    def test_collection_edit_keeps_indented_comment_but_literal_hash_stays_in_text(self):
        path = self.repo / 'content/pages/forge.md'
        path.parent.mkdir(parents=True)
        path.write_bytes(('---\ntitle: 造物台\nstages: [一, 二, 三]\nnotes:\n'
                          '  # keep explanation\n  - title: 原标题\n    text: |\n      # literal text\n'
                          '---\n原正文\n').encode())
        page = self.forge.read_page()
        fields = {**page['frontmatter'], 'notes': [{'title': '改后', 'text': '# literal text\n'}]}
        saved = self.forge.save_page(fields, base_revision=page['revision'])
        self.assertIn(b'  # keep explanation\n', path.read_bytes())
        self.assertEqual(saved['frontmatter']['notes'][0]['text'], '# literal text\n')

    def test_literal_markers_cannot_hide_active_targets_in_later_blocks(self):
        p = self.forge.save_project('demo', self.fields(), '', create=True)
        path = Path(p['dir']) / 'index.md'
        raw = path.read_bytes()
        for body in ('`example\n\n![图](../outside.png)\n\n`',
                     '\\` escaped ![图](../outside.png) `',
                     '> ```md\n> literal\n![图](../outside.png)\n> ```',
                     '- ```md\n  literal\n![图](../outside.png)\n  ```',
                     '- 项目\n    ![图](../outside.png)'):
            with self.subTest(body=body):
                path.write_bytes(raw)
                with self.assertRaises(ContentError):
                    self.forge.save_project('demo', p['frontmatter'], body, base_revision=p['revision'])
                self.assertEqual(path.read_bytes(), raw)

    def test_only_unescaped_comment_openers_hide_their_contents(self):
        p = self.forge.save_project('demo', self.fields(), '', create=True)
        path = Path(p['dir']) / 'index.md'
        raw = path.read_bytes()
        for tag in ('<a href="javascript:alert(1)">click</a>', '<img src="../other/a.png">'):
            for count in (1, 3):
                with self.subTest(tag=tag, backslashes=count):
                    path.write_bytes(raw)
                    body = '\\' * count + '<!-- ' + tag + ' -->'
                    with self.assertRaises(ContentError):
                        self.forge.save_project('demo', p['frontmatter'], body, base_revision=p['revision'])
                    self.assertEqual(path.read_bytes(), raw)
        path.write_bytes(raw)
        for count in (0, 2, 4):
            body = '\\' * count + '<!-- <a href="javascript:example">comment</a> -->'
            p = self.forge.save_project('demo', p['frontmatter'], body, base_revision=p['revision'])
            self.assertEqual(p['body'], body)

    def test_real_model_and_page_noop_save_are_byte_preserving(self):
        source = Path('D:/router/work/blog-cloud-writer')
        for path in (source / 'content/projects').glob('*/index.md'):
            target = self.repo / 'content/projects' / path.parent.name / 'index.md'
            target.parent.mkdir(parents=True)
            raw = path.read_bytes()
            target.write_bytes(raw)
            p = self.forge.read_project(path.parent.name)
            self.forge.save_project(p['slug'], p['frontmatter'], p['body'], base_revision=p['revision'])
            self.assertEqual(target.read_bytes(), raw)
        target = self.repo / 'content/pages/forge.md'
        target.parent.mkdir(parents=True)
        raw = (source / 'content/pages/forge.md').read_bytes()
        target.write_bytes(raw)
        p = self.forge.read_page()
        self.forge.save_page(p['frontmatter'], base_revision=p['revision'])
        self.assertEqual(target.read_bytes(), raw)

    def test_response_revision_is_own_write_even_if_external_write_follows(self):
        from unittest.mock import patch
        p = self.forge.save_project('demo', self.fields(), '', create=True)
        original_write = self.forge.content._write
        path = Path(p['dir']) / 'index.md'
        def write_then_external(*args):
            revision = original_write(*args)
            path.write_bytes(path.read_bytes() + b'external\n')
            return revision
        with patch.object(self.forge.content, '_write', side_effect=write_then_external):
            saved = self.forge.save_project('demo', p['frontmatter'], '我的正文', base_revision=p['revision'])
        self.assertNotEqual(saved['revision'], self.forge.content.revision(path))
        with self.assertRaises(ContentConflict):
            self.forge.save_project('demo', saved['frontmatter'], '下一次', base_revision=saved['revision'])

    def test_git_publish_scope_captures_project_page_and_binary_images(self):
        from lib import gitops
        def git(*args):
            result = subprocess.run(['git', *args], cwd=self.repo, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
            return result.stdout
        git('init', '-b', 'main')
        git('-c', 'user.name=Offline test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'fixture')
        self.forge.save_project('demo', self.fields(), '', create=True)
        page = self.repo / 'content/pages/forge.md'
        page.parent.mkdir(parents=True)
        page.write_bytes(b'---\ntitle: forge\n---\n')
        # 与发布入口使用同一真实 Git 路径检查和内容快照；不连接远端、不提交内容。
        self.assertTrue(gitops._content_changes(self.repo))
        snapshot = gitops.capture_publish_state(self.repo)
        paths = {item['path'] for item in snapshot['files']}
        self.assertEqual(paths, {'content/projects/demo/index.md', 'content/projects/demo/cover.png', 'content/pages/forge.md'})


if __name__ == '__main__':
    unittest.main()
