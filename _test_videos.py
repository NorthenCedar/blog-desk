# -*- coding: utf-8 -*-
"""视频入库与过期编辑保护：只在临时目录运行，不触碰站主内容。"""
import hashlib
import base64
import json
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
from pathlib import Path

from lib.content import Content, ContentConflict
from lib import videos

MP4 = b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp42'
WEBM = b'\x1a\x45\xdf\xa3\x87\x42\x82\x84webm'


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='desk-video-')
        self.repo = Path(self.tmp.name)
        (self.repo / 'content/posts/test').mkdir(parents=True)
        (self.repo / 'content/posts/test/index.md').write_text('---\ntitle: 原题\ndraft: true\n---\n原文', encoding='utf-8')
        (self.repo / 'content/pages').mkdir(parents=True)
        (self.repo / 'content/pages/talks.md').write_text('---\n# 保留说明\ntitle: 说说\nitems:\n  - date: 2026-10-20\n    text: 旧说说\n---\n尾文', encoding='utf-8')
        (self.repo / 'content/pages/log.md').write_text('---\nitems:\n  - date: 2026-10-20\n    version: v1.0\n    changes: [旧记录]\n---\n', encoding='utf-8')
        self.content = Content(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def test_original_bytes_and_content_address_dedup(self):
        a = videos.save_video(self.content, '片段.mp4', MP4, 'post', 'test')
        b = videos.save_video(self.content, '另一名称.mp4', MP4, 'talks')
        self.assertEqual(a['src'], b['src'])
        self.assertEqual(a['name'], hashlib.sha256(MP4).hexdigest() + '.mp4')
        self.assertEqual(Path(a['path']).read_bytes(), MP4)
        self.assertTrue(b['deduplicated'])
        self.assertEqual(len(list((self.repo / 'content/videos').iterdir())), 1)

    def test_format_size_and_target_rejected_before_writing(self):
        for name, data, where, slug in [('fake.mp4', b'hello', 'talks', ''), ('a.mov', MP4, 'talks', ''), ('a.webm', MP4, 'talks', ''), ('a.mp4', MP4, 'post', '../test'), ('a.mp4', MP4, 'gallery', '')]:
            with self.subTest(name=name, where=where, slug=slug), self.assertRaises(videos.VideoError):
                videos.save_video(self.content, name, data, where, slug)
        with self.assertRaises(videos.VideoError):
            videos.save_video(self.content, 'large.mp4', MP4 + bytes(videos.MAX_VIDEO_BYTES), 'talks')
        self.assertFalse((self.repo / 'content/videos').exists())
        self.assertTrue(videos.save_video(self.content, 'a.webm', WEBM, 'talks')['src'].endswith('.webm'))

    def test_stale_post_talks_log_and_delete_preserve_external_changes(self):
        post = self.content.read_post('test')
        md = self.repo / 'content/posts/test/index.md'
        external = md.read_bytes().replace('原文'.encode(), 'AI 新文'.encode())
        md.write_bytes(external)
        with self.assertRaises(ContentConflict):
            self.content.write_post('test', post['frontmatter'], '旧编辑', base_revision=post['revision'])
        with self.assertRaises(ContentConflict):
            self.content.delete_post('test', base_revision=post['revision'])
        self.assertEqual(md.read_bytes(), external)
        for kind in ['talks', 'log']:
            snapshot = getattr(self.content, 'read_' + kind)()
            path = self.repo / f'content/pages/{kind}.md'
            path.write_bytes(path.read_bytes() + b'\nAI edit')
            before = path.read_bytes()
            with self.assertRaises(ContentConflict):
                getattr(self.content, 'write_' + kind)(snapshot['items'], base_revision=snapshot['revision'])
            self.assertEqual(path.read_bytes(), before)

    def test_talk_roundtrip_keeps_legacy_comment_identity_and_videos(self):
        upload = videos.save_video(self.content, 'a.mp4', MP4, 'talks')
        snapshot = self.content.read_talks()
        self.assertEqual(snapshot['items'][0]['id'], 'talk-20261020')
        item = snapshot['items'][0] | {'videos': [{'src': upload['src'], 'alt': '测试片段'}]}
        self.content.write_talks([{'id':'talk-new', 'date':'2026-10-20', 'text':'新说说'}, item], base_revision=snapshot['revision'])
        result = self.content.read_talks()
        self.assertEqual(result['items'][1]['id'], 'talk-20261020')
        self.assertEqual(result['items'][1]['videos'], item['videos'])
        text = (self.repo / 'content/pages/talks.md').read_text(encoding='utf-8')
        self.assertIn('# 保留说明', text)
        self.assertTrue(text.endswith('尾文'))
        self.assertNotEqual(result['revision'], snapshot['revision'])

    def test_edit_during_video_validation_is_not_overwritten(self):
        upload = videos.save_video(self.content, 'a.mp4', MP4, 'talks')
        snapshot = self.content.read_talks()
        snapshot['items'][0]['videos'] = [{'src': upload['src'], 'alt': '测试'}]
        path = self.repo / 'content/pages/talks.md'
        external = path.read_bytes() + b'\nAI changed during validation'
        validate = videos.validate_reference

        def edit_after_validation(*args):
            validate(*args)
            path.write_bytes(external)

        with patch('lib.videos.validate_reference', side_effect=edit_after_validation):
            with self.assertRaises(ContentConflict):
                self.content.write_talks(snapshot['items'], base_revision=snapshot['revision'])
        self.assertEqual(path.read_bytes(), external)
        self.assertEqual(list(path.parent.glob('.talks.md.*.tmp')), [])

    def test_edit_during_temporary_write_is_not_overwritten(self):
        snapshot = self.content.read_post('test')
        path = self.repo / 'content/posts/test/index.md'
        external = path.read_bytes() + b'\nAI changed before replacement'
        import os
        original = os.fsync

        def edit_after_fsync(fd):
            original(fd)
            path.write_bytes(external)

        with patch('lib.content.os.fsync', side_effect=edit_after_fsync):
            with self.assertRaises(ContentConflict):
                self.content.write_post('test', snapshot['frontmatter'], '写作台修改', snapshot['revision'])
        self.assertEqual(path.read_bytes(), external)
        self.assertEqual(list(path.parent.glob('.index.md.*.tmp')), [])

    def test_http_upload_ranges_and_stale_save_conflict(self):
        from server import serve
        httpd, port, _ = serve(self.repo, {})
        root = f'http://127.0.0.1:{port}'

        def post(path, body):
            request = urllib.request.Request(root + path, data=json.dumps(body).encode(), headers={'Content-Type':'application/json'}, method='POST')
            return urllib.request.urlopen(request, timeout=5)

        try:
            with post('/api/video/upload', {'data':base64.b64encode(MP4).decode(), 'name':'test.mp4', 'where':'talks'}) as reply:
                video = json.load(reply)
            url = root + '/api/video?name=' + video['name']
            with urllib.request.urlopen(urllib.request.Request(url, headers={'Range':'bytes=4-7'})) as reply:
                self.assertEqual(reply.status, 206)
                self.assertEqual(reply.read(), b'ftyp')
                self.assertEqual(reply.headers['Content-Range'], f'bytes 4-7/{len(MP4)}')
            with urllib.request.urlopen(urllib.request.Request(url, method='HEAD')) as reply:
                self.assertEqual(reply.headers['Content-Type'], 'video/mp4')
                self.assertEqual(int(reply.headers['Content-Length']), len(MP4))
                self.assertEqual(reply.read(), b'')
            with self.assertRaises(urllib.error.HTTPError) as invalid:
                urllib.request.urlopen(urllib.request.Request(url, headers={'Range':'bytes=100-'}))
            self.assertEqual(invalid.exception.code, 416)
            invalid.exception.close()
            snapshot = self.content.read_post('test')
            path = self.repo / 'content/posts/test/index.md'
            path.write_bytes(path.read_bytes() + b'\nexternal')
            with self.assertRaises(urllib.error.HTTPError) as conflict:
                post('/api/post', {'slug':'test', 'frontmatter':snapshot['frontmatter'], 'body':'旧编辑', 'base_revision':snapshot['revision']})
            self.assertEqual(conflict.exception.code, 409)
            conflict.exception.close()
            self.assertIn(b'external', path.read_bytes())
            with self.assertRaises(urllib.error.HTTPError) as missing:
                post('/api/post', {'slug':'test', 'frontmatter':snapshot['frontmatter'], 'body':'旧编辑'})
            self.assertEqual(missing.exception.code, 400)
            missing.exception.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == '__main__':
    unittest.main()
