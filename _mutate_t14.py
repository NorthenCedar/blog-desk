# -*- coding: utf-8 -*-
"""写作台 · `_t14.py` 的**变异验证**（安装策略：全新目录 → 自检 → 切换，2026-10-15）

纪律与 `_mutate_t12.py` / `_mutate_t13.py` 一致：按字节存取 + sha256 还原校验 +
信号兜底还原（Windows 上没有 SIGHUP，要 getattr 取）；判据 = `failed && hit`。

★ 这个门闩守的是**"绝不把站主留在没有应用的状态"**那条，所以每条变异都往
  "悄悄少做一步、看起来还是绿的"那个方向去：
    · 不清上一轮留下的 `写作台-新`  ⇒ 又变回新旧合并
    · 去掉回退                      ⇒ 切换中途失败 = 站主没有应用了
    · 不带 config.json              ⇒ 站主的仓库路径与密钥悄悄丢了
    · 带过来时以旧安装优先            ⇒ 源码里的新配置被旧的老配置盖掉
    · 不返回旧目录                  ⇒ 旧目录没人回收，磁盘上永远堆着

用法：python _mutate_t14.py
"""
from __future__ import annotations

import atexit
import hashlib
import signal
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "build-app.py"

orig = TARGET.read_bytes()
sha0 = hashlib.sha256(orig).hexdigest()


def restore():
    if TARGET.read_bytes() != orig:
        TARGET.write_bytes(orig)


for sig in ("SIGINT", "SIGTERM", "SIGHUP"):
    s = getattr(signal, sig, None)
    if s is not None:
        signal.signal(s, lambda *_: (restore(), sys.exit(1)))
atexit.register(restore)

MUT = [
    # ① 不清上一轮留下的 staged ⇒ "全新目录"名不副实
    (1, '    if staged.exists():                       # 上一轮失败的残留\n'
        '        recycle(staged)\n',
     '', '上一轮留下的 `写作台-新` 被清干净了'),

    # ② 回到 dirs_exist_ok（"只覆盖不删除"）—— 这一条正是本轮要消灭的做法。
    #   ⚠ 第一版它**咬不住**：`recycle(staged)` 已经保证目录全新，这条路上两者没差别
    #   ⇒ 那是"改动不产生可观察差异"的假变异。
    #   补了第⑥节（回收失败的那条真实路径）之后它才真的有意义：那时 staged 还在，
    #   dirs_exist_ok 会**悄悄合并**，而不带它会抛出来。
    (2, '    shutil.copytree(built, staged)            # ★ 不用 dirs_exist_ok：目标是全新的\n',
     '    shutil.copytree(built, staged, dirs_exist_ok=True)\n',
     '请不动旧目录时**抛出来**'),

    # ③ 去掉回退 ⇒ 切换中途失败就把站主留在"没有应用"的状态
    (3, '        if old.exists() and not dest.exists():\n'
        '            _rename(old, dest)                 # ★ 回退\n',
     '', '**旧安装回到原位**'),

    # ④ 不带 config.json ⇒ 站主的仓库路径与密钥悄悄丢了
    (4, '    for extra in ("config.json", "运行日志.txt"):\n',
     '    for extra in ("运行日志.txt",):\n', 'config.json 带过来了'),

    # ⑤ 带过来时旧安装优先 ⇒ 源码里的新配置被旧的老配置盖掉
    (5, '        if not src.is_file() and carry_from:\n'
        '            src = carry_from / extra           # ★ 旧安装里的那份（站主的设置）\n',
     '        if carry_from and (carry_from / extra).is_file():\n'
     '            src = carry_from / extra\n', '源码里有 config.json 时以源码为准'),

    # ⑥ 不返回旧目录 ⇒ 旧目录没人回收，磁盘上永远堆着
    (6, '    return old if old.exists() else None\n',
     '    return None\n', '旧目录被保留成可回退的一份'),

    # ⑦ 切换前不请走上一轮残留的 `写作台-旧` ⇒ 改名会撞上已存在的目录
    (7, '    if old.exists():                           # 上一轮没清干净的\n        recycle(old)\n',
     '', '旧的 `写作台-旧` 是**这次**的那一份'),
# ⑧ 日志路径写死 DEST ⇒ 提示指 staged、现场却在旧安装
    (8, '    return (log_dir or exe.parent) / "_自检日志.txt"\n',
     '    return (log_dir or DEST) / "_自检日志.txt"\n',
     '日志落在 staged 旁边'),

    # ⑨ `_log_path` 还在，但 `smoke_test` 又绕开它写死 DEST ——
    #   **接线与实现是两回事**：函数改对了，调用点没跟上，一样全绿。
    (9, '    log = _log_path(exe)\n',
     '    log = DEST / "_自检日志.txt"\n',
     'smoke_test 真把日志写在**被测 exe 旁边**'),
]

caught = 0
for n, old, new, hit in MUT:
    src = orig.decode("utf-8")
    if old not in src:
        print(f"  .. 变异 {n} 跳过：锚点没命中（先查源码是不是已变，别改门闩）")
        continue
    mutated = src.replace(old, new, 1)
    if mutated == src:
        print(f"  .. 变异 {n} 跳过：替换后没变")
        continue
    TARGET.write_bytes(mutated.encode("utf-8"))
    failed, out = False, ""
    try:
        r = subprocess.run([sys.executable, str(HERE / "_t14.py")], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600,
                           cwd=str(HERE))
        out = r.stdout + r.stderr
        failed = r.returncode != 0
    except Exception as e:  # noqa: BLE001
        failed, out = True, f"变异脚本自己炸了：{e}"
    finally:
        restore()

    ok = hashlib.sha256(TARGET.read_bytes()).hexdigest() == sha0
    if failed and hit in out:
        caught += 1
        print(f"  OK  变异 {n} 咬住 —— 「{hit}」")
    elif failed:
        line = [l for l in out.splitlines() if "FAIL" in l]
        print(f"  !!  变异 {n} 红了但没命中期望 ⇒ 标签写错了；实际红在：{line[0][:90] if line else '(没抓到 FAIL 行)'}")
    else:
        print(f"  !!  变异 {n} 没咬住 ⇒ 门闩漏了它")
    if not ok:
        print(f"      还原校验失败！{TARGET.name}")

print(f"\n咬住 {caught} / {len(MUT)}")
sys.exit(0 if caught == len(MUT) else 1)