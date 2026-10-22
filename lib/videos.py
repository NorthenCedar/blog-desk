# -*- coding: utf-8 -*-
"""站主视频原件：按内容去重，构建时只导出公开页面实际引用的文件。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

MAX_VIDEO_BYTES = 25 * 1024 * 1024
VIDEO_NAME = re.compile(r'^[0-9a-f]{64}\.(mp4|webm)$')


class VideoError(Exception):
    pass


def validate_video(data: bytes, extension: str) -> str:
    if not data or len(data) > MAX_VIDEO_BYTES:
        raise VideoError('视频不能为空，单个视频最多 25 MiB；请先压缩较大的视频')
    if extension == '.mp4' and len(data) >= 12 and data[4:8] == b'ftyp':
        return 'video/mp4'
    if extension == '.webm' and data.startswith(b'\x1a\x45\xdf\xa3') and b'webm' in data[:4096]:
        return 'video/webm'
    raise VideoError('只接受真实的 MP4 / WebM 视频；MP4 建议使用 H.264 + AAC 编码')


def video_dir(repo: Path) -> Path:
    root = Path(repo).resolve()
    folder = (root / 'content/videos').resolve()
    if not folder.is_relative_to(root):
        raise VideoError('视频目录必须位于博客仓库内')
    return folder


def read_video(repo: Path, name: str) -> tuple[bytes, str]:
    if not VIDEO_NAME.fullmatch(name):
        raise VideoError('视频文件名无效，请重新上传')
    folder = video_dir(repo)
    path = (folder / name).resolve()
    if not path.is_relative_to(folder) or not path.is_file():
        raise VideoError('找不到视频原件，请重新上传')
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise VideoError('视频超过 25 MiB')
    data = path.read_bytes()
    mime = validate_video(data, path.suffix)
    if hashlib.sha256(data).hexdigest() != path.stem:
        raise VideoError('视频内容与文件名不一致，请重新上传')
    return data, mime


def validate_reference(repo: Path, src: str, alt: str) -> None:
    if not isinstance(src, str) or not src.startswith('/videos/'):
        raise VideoError('视频引用必须来自写作台上传的视频')
    if not isinstance(alt, str) or not alt.strip():
        raise VideoError('请填写视频说明，供读屏和无法播放时使用')
    read_video(repo, src[len('/videos/'):])


def save_video(content, filename: str, data: bytes, where: str, slug: str = '') -> dict:
    if where not in ('post', 'talks'):
        raise VideoError('视频只能插入文章或说说')
    if where == 'post':
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug) or not (content.posts_dir / slug / 'index.md').is_file():
            raise VideoError('请先保存并打开一篇有效文章，再上传视频')
    extension = Path(filename).suffix.lower()
    mime = validate_video(data, extension)
    name = hashlib.sha256(data).hexdigest() + extension
    folder = video_dir(content.repo)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    exists = path.exists()
    if exists:
        read_video(content.repo, name)
    else:
        # 独占创建，同一个视频从两处上传也不会覆盖其他文件。
        try:
            with path.open('xb') as out:
                out.write(data)
        except FileExistsError:
            read_video(content.repo, name)
            exists = True
    return {'ok': True, 'name': name, 'path': str(path), 'src': '/videos/' + name,
            'mime': mime, 'size': len(data), 'deduplicated': exists}
