# -*- coding: utf-8 -*-
"""写作台 · 图片入库。

════════════════════════════════════════════════════════════════════════
图片放哪里（内容模型 §2 定的规矩，别乱改）
════════════════════════════════════════════════════════════════════════
· **文章图** → `content/posts/<slug>/`，与正文同目录（一篇文章的全部素材一个文件夹，
  删除/备份/迁移一次拖拽；正文写 `![](./名字.png)`）。
· **说说图** → `src/assets/talks/`。★ 站上**唯一**允许放 src/ 的图片目录 ——
  因为说说图要参与构建期优化（转 WebP + 出 srcset），只有 src/ 里的资源能被 Astro 处理。
· **相册图** → `content/gallery/<相册名>/`，然后由 `scripts/gen-gallery-webp.mjs`
  在构建时生成两档 webp 到 public/gallery/；相册原图**不部署**。

════════════════════════════════════════════════════════════════════════
压缩
════════════════════════════════════════════════════════════════════════
站主原文（参考例图 + 口头）：「压到上限」。做法：
· 长边超过上限就等比缩到上限；
· 再按画质逐档降，直到文件 ≤ 上限；
· 已经是小的图**原样复制**（不重编码 —— 重编码只会掉画质不会更好）。
· ★ 不裁图（与全站「档案不是社交相册」一致）：只缩放，不改变构图。
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageSequence

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tiff", ".tif"}

# 站上对图片的要求：所有 <img> 必须有非空 alt（硬约束，测试守着）
# ⇒ 这里只负责"把文件放对地方"，alt 由界面收集。


class ImageError(Exception):
    pass


def _safe_name(name: str) -> str:
    """把用户给的文件名清成站上能用的样子。

    站上现成的两类命名都允许：
      · `20260928-1.jpg`（说说图：日期-序号）
      · `Image_1790268606244_504.jpg`（文章图：原始名，下划线分隔）
    所以这里**不强制改风格**，只做安全清理：去掉路径、去掉危险字符、保留中文。
    """
    stem = Path(name).name
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", stem)
    stem = stem.strip(". ")
    return stem or "image"


def timestamp_name(ext: str, prefix: str = "") -> str:
    """按站上现有风格给个新名：日期-时分秒。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}{ts}{ext.lower()}"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf, i = path.stem, path.suffix, 2
    while True:
        cand = path.with_name(f"{stem}-{i}{suf}")
        if not cand.exists():
            return cand
        i += 1


def compress_image(src: bytes, max_kb: int = 300, max_edge: int = 1600) -> tuple[bytes, dict]:
    """压到 max_kb 以内。返回 (bytes, 报告)。

    ★ 静态图与动图分开处理：GIF/webp 动图**一律原样返回**（动图没法"降画质"，
      PIL 存回去会只剩一帧 —— 那是把用户的图弄坏，不是压缩）。
    """
    report = {"original": len(src), "max_kb": max_kb}
    try:
        probe = Image.open(io.BytesIO(src))
        is_animated = getattr(probe, "is_animated", False)
        fmt = (probe.format or "").upper()
    except Exception as e:  # noqa: BLE001
        raise ImageError(f"这不是能识别的图片：{e}") from e

    if is_animated:
        report.update({"compressed": len(src), "note": "动图跳过压缩（直接入库）", "skipped": True})
        return src, report

    # 已经是 o 小的图：不重编码
    if len(src) <= max_kb * 1024 and max(probe.size) <= max_edge:
        report.update({"compressed": len(src), "note": "本来就够小，原样入库", "skipped": True})
        return src, report

    img = probe.convert("RGBA" if probe.mode in ("RGBA", "LA", "P") else "RGB")
    if max(img.size) > max_edge:
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)

    # 有透明通道 ⇒ 只能存 PNG/WebP；否则统一 JPEG（站上照片都是 jpg）
    has_alpha = img.mode == "RGBA" and img.getextrema()[3][0] < 255
    out_fmt = "PNG" if has_alpha else "JPEG"

    best = None
    for quality in (88, 82, 76, 70, 62, 55, 48, 40):
        buf = io.BytesIO()
        if out_fmt == "JPEG":
            img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True,
                                    progressive=True)
        else:
            img.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        best = data
        if len(data) <= max_kb * 1024:
            break

    report.update({
        "compressed": len(best),
        "size": list(img.size),
        "format": out_fmt,
        "ratio": round(len(best) / max(len(src), 1), 3),
        "note": f"{len(src) // 1024} KB → {len(best) // 1024} KB",
    })
    return best, report


def save_for_post(content, slug: str, filename: str, data: bytes,
                  max_kb: int = 300, compress: bool = True) -> dict:
    """存进 content/posts/<slug>/。"""
    d = content.posts_dir / slug
    if not d.is_dir():
        raise ImageError(f"文章目录不存在：content/posts/{slug}")
    name = _safe_name(filename)
    if Path(name).suffix.lower() not in IMAGE_EXT:
        raise ImageError(f"不支持的图片格式：{Path(name).suffix}")
    report = {}
    if compress:
        data, report = compress_image(data, max_kb)
    target = _unique(d / name)
    target.write_bytes(data)
    return {"ok": True, "name": target.name, "dir": "post", "path": str(target),
            "report": report, "rel": f"./{target.name}"}


def save_for_talk(content, filename: str, data: bytes,
                  max_kb: int = 300, compress: bool = True) -> dict:
    """存进 src/assets/talks/（说说图唯一允许的位置）。"""
    d = content.talks_assets
    d.mkdir(parents=True, exist_ok=True)
    name = _safe_name(filename)
    if Path(name).suffix.lower() not in IMAGE_EXT:
        raise ImageError(f"不支持的图片格式：{Path(name).suffix}")
    report = {}
    if compress:
        data, report = compress_image(data, max_kb)
    target = _unique(d / name)
    target.write_bytes(data)
    return {"ok": True, "name": target.name, "dir": "talks", "path": str(target),
            "report": report, "rel": target.name}


def save_for_gallery(content, album: str, filename: str, data: bytes,
                     max_kb: int = 900, compress: bool = True) -> dict:
    """存进 content/gallery/<相册>/。相册原图不部署，构建时另出 webp。"""
    d = content.gallery_dir / album
    if not d.is_dir():
        raise ImageError(f"相册不存在：content/gallery/{album}")
    name = _safe_name(filename)
    if Path(name).suffix.lower() not in IMAGE_EXT:
        raise ImageError(f"不支持的图片格式：{Path(name).suffix}")
    report = {}
    if compress:
        data, report = compress_image(data, max_kb, max_edge=2560)
    target = _unique(d / name)
    target.write_bytes(data)
    return {"ok": True, "name": target.name, "dir": "gallery", "path": str(target),
            "report": report, "rel": f"./{target.name}"}


def list_gallery_photos(content, album: str | None = None) -> list[dict]:
    """列出相册里的照片（原图），给界面上的照片墙用。"""
    out = []
    albums = [album] if album else [a["name"] for a in content.list_albums()]
    for a in albums:
        d = content.gallery_dir / a
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXT:
                st = p.stat()
                out.append({
                    "album": a,
                    "name": p.name,
                    "path": str(p),
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                    "rel": f"./{p.name}",
                })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def delete_image(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise ImageError(f"文件不存在：{path}")
    size = p.stat().st_size
    p.unlink()
    return {"ok": True, "removed": p.name, "freed": size}
