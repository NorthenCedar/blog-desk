# -*- coding: utf-8 -*-
"""把 `说明.source.txt`（UTF-8，好读好改）转成 `说明.txt`（**GBK + CRLF**）并送到两处。

★ 为什么要分两层：`说明.txt` 是给**站主用记事本/双击打开**的，而 cmd 和老版记事本
  会按 **GBK** 解释字节 ⇒ 交付出去的那份必须是 GBK + CRLF，否则一打开全是乱码。
  但 GBK 的源文件又没法在现代编辑器里好好改 ⇒ 于是：**源是 UTF-8，产物是 GBK**。

★ 转换前**先全量预检**：GBK 编不进去的字符（例如 `⇒` U+21D2）会在写到一半时抛异常，
  在盘上留下一个半截文件。所以先把整篇试编一遍，有问题就**当场报出是哪个字符**、一个字节都不写。
  （这条预检第一次跑就救了自己 —— 正文里真用了 `⇒`。）

用法：`python make_readme.py`
      `build-app.py` 打包前也会自动调一次 `generate()`，保证发出去的那份和源一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from make_shortcut import APPDIR  # noqa: E402

SOURCE = HERE / "说明.source.txt"
TARGETS = (HERE / "说明.txt", APPDIR / "说明.txt")


def generate() -> list[str]:
    """转一次。返回人话描述；有不可编码的字符就抛 ValueError。"""
    if not SOURCE.is_file():
        raise FileNotFoundError(f"找不到源文件：{SOURCE}")

    text = SOURCE.read_text(encoding="utf-8")

    bad = []
    for ch in sorted(set(text)):
        try:
            ch.encode("gbk")
        except UnicodeEncodeError:
            bad.append("U+%04X %r" % (ord(ch), ch))
    if bad:
        raise ValueError(
            "这些字符编不进 GBK，先换掉再转（否则会写出半截文件）：" + "、".join(bad))

    norm = text.replace("\r\n", "\n").replace("\n", "\r\n")
    blob = norm.encode("gbk")

    notes = []
    for dest in TARGETS:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        back = dest.read_bytes()
        crlf_ok = back.count(b"\r") == back.count(b"\n")
        notes.append(f"{dest}　{len(back)} 字节　CRLF 统一：{crlf_ok}")
    return notes


def main() -> int:
    try:
        notes = generate()
    except (FileNotFoundError, ValueError) as e:
        print(f"✘ {e}")
        return 1
    print("✔ 说明书已生成（GBK + CRLF）：")
    for n in notes:
        print("   " + n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
