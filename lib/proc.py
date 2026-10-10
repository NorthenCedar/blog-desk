# -*- coding: utf-8 -*-
"""写作台 · 跑外部命令的**唯一出口**（git / npm / node 都从这里走）。

★ 为什么必须统一走这里（2026-10-09，站主报"打开桌面那个 bat 跳出来很多终端"）：

  写作台是个**没有控制台**的程序（`pythonw.exe`，打包后是 `--noconsole` 的 exe）。
  Windows 的规矩是：**控制台子进程若父进程没有控制台，就给它新分配一个**
  —— 也就是弹一个黑色的终端窗口出来。于是每一次 `git` / `npm` 调用都闪一下，
  启动那几秒里（读仓库状态、比对远端）能连闪十几个，看着像"中毒了"。

  解法：给每个子进程加 `CREATE_NO_WINDOW`，再用 `STARTF_USESHOWWINDOW + SW_HIDE`
  兜一层（有些情况光靠 flag 不够）。

★ 第二个理由（同样重要）：**留一个改的地方**。
  这台机器上 Node 的 `child_process` 全线 EBUSY（见 AGENTS 里的记录），
  所以凡是"要跑外部命令"的活儿都得从 Python 的 `subprocess` 走。
  哪天要加超时、要加环境变量白名单、要把调用记进日志，
  改这一处就够了 —— 散在四个文件里迟早漏掉一个，漏掉的那个就是闪窗的那个。

用法：把 `subprocess.run(...)` 换成 `proc.run(...)`，参数一模一样。
"""

from __future__ import annotations

import subprocess
import sys

# Windows: 不给子进程分配控制台（0x08000000）
CREATE_NO_WINDOW = 0x08000000

_FLAGS = 0
_STARTUPINFO = None

if sys.platform == "win32":
    _FLAGS = CREATE_NO_WINDOW
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = subprocess.SW_HIDE


def run(args, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` 的替身：参数原样透传，只加上"别弹窗口"。

    允许调用方自己覆盖 `creationflags` / `startupinfo`（`setdefault`），
    这样将来真要开一个可见窗口时不必绕开这个函数。
    """
    kwargs.setdefault("creationflags", _FLAGS)
    if _STARTUPINFO is not None:
        kwargs.setdefault("startupinfo", _STARTUPINFO)
    return subprocess.run(args, **kwargs)
