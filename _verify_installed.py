# -*- coding: utf-8 -*-
"""独立复验：切换**之后**的成品 exe 自己能不能起来（2026-10-15，一次性）。

为什么不在 build-app.py 里跑：它自检的是**切换前**的 staged 目录。
本轮新策略的关键恰恰是"切换这一步会不会把东西弄坏"（改名、回退、回收），
所以必须**切换之后**再起一次成品 —— 这是两件事。

用法：python _verify_installed.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APPDIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "写作台"
EXE = APPDIR / "写作台.exe"
PORT = 8931
NO_WINDOW = 0x08000000

checks: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))
    print(("  OK  " if ok else "  FAIL") + name + (f"  {detail}" if detail else ""))


print(f"复验目标：{EXE}")
chk("★ exe 在（切换后的正式路径）", EXE.is_file(), str(EXE))
chk("★ 切换不留 -新 / -旧",
    not (APPDIR.with_name("写作台-新")).exists()
    and not (APPDIR.with_name("写作台-旧")).exists())
chk("★ 没有跨 ABI 的 cp313 残留（那是永远加载不了的死重）",
    not any("cp313" in p.name for p in APPDIR.rglob("*")))
chk("★ 说明书在（应指向切换后的目录，不是旧安装）", (APPDIR / "说明.txt").is_file())
chk("★ config.json 在（站主的设置没丢）", (APPDIR / "config.json").is_file())

log = APPDIR / "_复验日志.txt"
with open(log, "wb") as out:
    p = subprocess.Popen([str(EXE), "--no-open", "--port", str(PORT)], cwd=str(APPDIR),
                         stdout=out, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
ok, body = False, ""
try:
    deadline = time.time() + 90
    while time.time() < deadline:
        if p.poll() is not None:
            body = f"进程提前退出，退出码 {p.returncode}"
            break
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2) as r:
                if r.status == 200:
                    ok, body = True, r.read(300).decode("utf-8", "replace").strip()
                    break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
finally:
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:  # noqa: BLE001
        p.kill()

chk("★ 切换后的 exe 真能起来并答 /api/health", ok, body)
if ok:
    try:
        j = json.loads(body)
        chk("★ 报出来的仓库路径就是站主那个", bool(j.get("repo", "").lower().endswith("blog")), str(j))
    except Exception:  # noqa: BLE001
        chk("★ 回包是 JSON", False, body[:80])

bad = [c for c in checks if not c[1]]
print(f"\n通过 {len(checks) - len(bad)} / {len(checks)}")
if bad:
    print("日志：", log)
sys.exit(1 if bad else 0)