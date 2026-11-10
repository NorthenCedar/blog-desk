# -*- coding: utf-8 -*-
"""语音入库：只在临时目录运行，不触碰站主内容。

守四件事，每件都对应一条真实会踩的坑：
  ① 文件头说了算 —— 扩展名是客户端说的，头是文件自己的；
  ② 内容寻址 —— 同一段声音传两次只落一个文件（路径即 sha256）；
  ③ 路径必须能回读 —— read_audio 要能把名字和内容对上，否则构建期会拷进一个假货；
  ④ 前端的引用形状 —— `!audio[...](/audio/<64hex>.mp3)`，写歪了产物里就是一条哑链接。
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from lib import audios
from lib.content import Content

WAV = b'RIFF\x24\x00\x00\x00WAVEfmt ' + b'\x00' * 20
MP3 = b'ID3\x03\x00\x00\x00\x00\x00\x00' + b'\x00' * 20


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='desk-audio-')
        self.repo = Path(self.tmp.name)
        (self.repo / 'content/posts/test').mkdir(parents=True)
        (self.repo / 'content/posts/test/index.md').write_text('---\ntitle: 原题\ndraft: true\n---\n原文', encoding='utf-8')
        (self.repo / 'content/pages').mkdir(parents=True)
        (self.repo / 'content/pages/talks.md').write_text('---\ntitle: 说说\nitems:\n  - date: 2026-10-20\n    text: 旧说说\n---\n尾文', encoding='utf-8')
        self.content = Content(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def test_headers_decide_format(self):
        ok_wav = audios.validate_audio(WAV, '.wav')
        ok_mp3 = audios.validate_audio(MP3, '.mp3')
        self.assertEqual(ok_wav, 'audio/wav')
        self.assertEqual(ok_mp3, 'audio/mpeg')
        # 名字说 mp3、内容是 wav：不许混过去
        for data, ext in [(WAV, '.mp3'), (MP3, '.wav'), (b'hello', '.wav'), (b'', '.mp3')]:
            with self.subTest(ext=ext, data=data[:4]), self.assertRaises(audios.AudioError):
                audios.validate_audio(data, ext)

    def test_content_address_and_dedupe(self):
        a = audios.save_audio(self.content, '一段.wav', WAV, 'post', 'test')
        b = audios.save_audio(self.content, '换个名字.wav', WAV, 'talks', '')
        self.assertEqual(a['src'], b['src'])
        self.assertEqual(a['name'], hashlib.sha256(WAV).hexdigest() + '.wav')
        self.assertEqual(a['src'], '/audio/' + a['name'])
        self.assertEqual(Path(a['path']).read_bytes(), WAV)
        self.assertTrue(b['deduplicated'])
        self.assertEqual(len(list((self.repo / 'content/audio').iterdir())), 1)

    def test_rejects_bad_names_and_targets(self):
        for name, where, slug in [('x.m4a', 'talks', ''), ('x.wav', 'gallery', ''), ('x.wav', 'post', '../test'), ('x.wav', 'post', 'a/b')]:
            with self.subTest(where=where, slug=slug), self.assertRaises(audios.AudioError):
                audios.save_audio(self.content, name, WAV, where, slug)

    def test_read_audio_matches_content(self):
        saved = audios.save_audio(self.content, 'a.mp3', MP3, 'talks', '')
        data, mime = audios.read_audio(self.repo, saved['name'])
        self.assertEqual(data, MP3)
        self.assertEqual(mime, 'audio/mpeg')
        # 内容被换掉（同目录下同名不同内容）⇒ 读的时候要发现
        Path(saved['path']).write_bytes(WAV)
        with self.assertRaises(audios.AudioError):
            audios.read_audio(self.repo, saved['name'])
        with self.assertRaises(audios.AudioError):
            audios.read_audio(self.repo, '../server.py')

    def test_audio_reference_shape(self):
        name = audios.save_audio(self.content, '开场.wav', WAV, 'talks', '')['name']
        self.assertIsNone(audios.validate_audio_reference(self.repo, '/audio/' + name, '开场'))
        for src in ['/audio/x.wav', '/videos/' + name, '/audio/' + hashlib.sha256(WAV).hexdigest() + '.mp3']:
            with self.subTest(src=src), self.assertRaises(audios.AudioError):
                audios.validate_audio_reference(self.repo, src, '')
        with self.assertRaises(audios.AudioError):
            audios.validate_audio_reference(self.repo, '/audio/' + name, 'x' * 121)

    def test_talks_write_keeps_audio_block(self):
        name = audios.save_audio(self.content, '开场.mp3', MP3, 'talks', '')['name']
        # 说说的 id 由 read_talks 生成（talk-<日期>），写回时必须原样带着
        base = self.content.read_talks()['items'][0]
        items = [{**base, 'audio': [{'src': '/audio/' + name, 'label': '开场'}]}]
        self.content.write_talks(items)
        text = (self.repo / 'content/pages/talks.md').read_text(encoding='utf-8')
        self.assertIn('audio:', text)
        self.assertIn('  - src: /audio/' + name, text)
        self.assertIn('label: 开场', text)
        back = self.content.read_talks()['items']
        self.assertEqual(back[0]['audio'][0]['src'], '/audio/' + name)

    def test_talks_write_rejects_bad_reference(self):
        items = [{**self.content.read_talks()['items'][0], 'audio': [{'src': '/audio/nonsense.mp3'}]}]
        with self.assertRaises(audios.AudioError):
            self.content.write_talks(items)


if __name__ == '__main__':
    unittest.main(verbosity=2)