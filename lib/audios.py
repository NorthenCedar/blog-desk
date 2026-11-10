# -*- coding: utf-8 -*-
"""站主音频原件（mp3 / wav）：按内容去重，构建时只导出公开页面实际引用的文件。

与 videos.py 同一套纪律：文件名 = sha256，内容是唯一的真相。
站点侧（blog/src/lib/audio-assets.mjs）用同一套规则校验，两边不一致就当场拒。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

MAX_AUDIO_BYTES = 24 * 1024 * 1024
AUDIO_NAME = re.compile(r'^[0-9a-f]{64}\.(mp3|wav)$')


class AudioError(Exception):
    pass


def validate_audio(data: bytes, extension: str) -> str:
    if not data:
        raise AudioError('音频是空的，请重新选择文件')
    if len(data) > MAX_AUDIO_BYTES:
        raise AudioError('单个音频最多 24 MiB；太长的请先压缩成 128 kbps 的 mp3')
    if extension == '.wav':
        if len(data) > 12 and data[0:4] == b'RIFF' and data[8:12] == b'WAVE':
            return 'audio/wav'
        raise AudioError('这不是真正的 WAV 文件')
    if extension == '.mp3':
        if data.startswith(b'ID3') or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
            return 'audio/mpeg'
        raise AudioError('这不是真正的 MP3 文件')
    raise AudioError('只接受 .mp3 和 .wav')


def audio_dir(repo: Path) -> Path:
    root = Path(repo).resolve()
    folder = (root / 'content/audio').resolve()
    if not folder.is_relative_to(root):
        raise AudioError('音频目录必须位于博客仓库内')
    return folder


def read_audio(repo: Path, name: str) -> tuple[bytes, str]:
    if not AUDIO_NAME.fullmatch(name):
        raise AudioError('音频文件名无效，请重新上传')
    folder = audio_dir(repo)
    path = (folder / name).resolve()
    if not path.is_relative_to(folder) or not path.is_file():
        raise AudioError('找不到音频原件，请重新上传')
    if path.stat().st_size > MAX_AUDIO_BYTES:
        raise AudioError('音频超过 24 MiB')
    data = path.read_bytes()
    mime = validate_audio(data, path.suffix)
    if hashlib.sha256(data).hexdigest() != path.stem:
        raise AudioError('音频内容与文件名不一致，请重新上传')
    return data, mime


def save_audio(content, filename: str, data: bytes, where: str, slug: str = '') -> dict:
    """`where` = post / talks：音频是「为某个页面准备的」，这个判断与图片、视频一致。"""
    if where not in ('post', 'talks'):
        raise AudioError('音频只能插进文章或说说')
    if where == 'post':
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug) or not (content.posts_dir / slug / 'index.md').is_file():
            raise AudioError('请先保存并打开一篇有效文章，再上传音频')
    extension = Path(filename).suffix.lower()
    mime = validate_audio(data, extension)
    name = hashlib.sha256(data).hexdigest() + extension
    folder = audio_dir(content.repo)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    exists = path.exists()
    if exists:
        read_audio(content.repo, name)
    else:
        # 独占创建：同一段音频从两处上传也不会覆盖别的文件。
        try:
            with path.open('xb') as out:
                out.write(data)
        except FileExistsError:
            read_audio(content.repo, name)
            exists = True
    return {'ok': True, 'name': name, 'path': str(path), 'src': '/audio/' + name,
            'mime': mime, 'size': len(data), 'deduplicated': exists}
def validate_audio_reference(repo: Path, src: str, label: str) -> None:
    """说说 frontmatter 里的一条语音引用。label 可以留空（播放条上就不显示文字），
    但 src 必须是写作台自己存的、且内容与文件名一致的那一条。"""
    if not isinstance(src, str) or not src.startswith('/audio/'):
        raise AudioError('语音引用必须来自写作台上传的音频')
    if label is not None and (not isinstance(label, str) or len(label) > 120):
        raise AudioError('语音说明最多 120 字')
    read_audio(repo, src[len('/audio/'):])
