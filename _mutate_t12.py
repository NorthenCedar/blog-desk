# -*- coding: utf-8 -*-
"""写作台 · `_t12.py` 的**变异验证**（2026-10-15）

纪律（照站内与本项目既有变异脚本那套）：
  ① **按字节存取**，跑完比对 sha256 —— 文本模式会把整个文件的行尾改写，
     "还原了但文件已变形"最难发现；
  ② 每条变异**先验证锚点命中且内容真的变了**，否则"没咬住"分不清是门闩漏了
     还是变异根本没生效；
  ③ 挂 SIGINT/SIGTERM/SIGHUP/exit 兜底还原 —— 进程被掐掉时变异会留在源码里，
     而 `lib/gitops.py` 是**被跟踪**的文件，混进下一次提交就麻烦了；
  ④ 判据 = `failed && hit`：`failed` 真而 `hit` 假 ⇒ 标签写错了，不是门闩漏了。

★ 为什么这三条闸必须各自被独立验证：它们的功能**完全一样**（都是"停下别动"），
  只测"停下"的话，去掉任意一条另外两条照样兜住 ⇒ 门闩看起来在守，其实没守。

用法：python _mutate_t12.py
"""
from __future__ import annotations

import hashlib
import signal
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "lib" / "gitops.py"

orig = TARGET.read_bytes()
sha0 = hashlib.sha256(orig).hexdigest()


def restore():
    if TARGET.read_bytes() != orig:
        TARGET.write_bytes(orig)


# ⚠ Windows 上**没有 SIGHUP**（那是 POSIX 的）。第一版无条件 getattr 三个信号，
#   在这台机器上直接 AttributeError 崩掉 —— 变异脚本自己先炸了，
#   "没咬住"与"脚本没跑起来"会长得一模一样。
for sig in ("SIGINT", "SIGTERM", "SIGHUP"):
    s = getattr(signal, sig, None)
    if s is not None:
        signal.signal(s, lambda *_: (restore(), sys.exit(1)))
import atexit  # noqa: E402
atexit.register(restore)

MUT = [
    (1, '    p = _run(["git", "fetch", "origin", "main"], repo)\n'
        '    if p.returncode != 0:\n'
        '        return {"ok": False, "gate": "fetch", "head": head0,\n'
        '                "why": (p.stderr or p.stdout).strip()[:200] or "git fetch 失败"}\n',
     '    _run(["git", "fetch", "origin", "main"], repo)\n',
     '闸①fetch 失败'),

    (2, '    if _run(["git", "cat-file", "-e", f"{remote_sha}^{{commit}}"], repo).returncode != 0:\n'
        '        return {"ok": False, "gate": "object", "head": head0,\n'
        '                "why": f"远端提交 {remote_sha[:8]} 不在本地（fetch 没拿到它）"}\n',
     '',
     '闸②远端对象不在本地'),

    (3, '    if not rem_tree or rem_tree != head_tree:\n'
        '        return {"ok": False, "gate": "tree", "head": head0,\n'
        '                "why": f"两边内容树不同（本地 {head_tree[:10]} / 远端 {rem_tree[:10]}）"}\n',
     '',
     '闸③两边内容树不等'),

    (4, '    git(repo, "reset", "--soft", remote_sha)\n',
     '    git(repo, "reset", "--hard", remote_sha)\n',
     '工作区一个字节都没动'),

    (5, '    _pull_rebase(repo)\n    git(repo, "add", "-A")\n',
     '    git(repo, "add", "-A")\n',
     '新提交的父是**远端**那个提交'),

    (6, '    if p.returncode != 0:\n'
        '        raise GitError(\n'
        '            "提交前拉取远端失败，已停下、没有提交 —— "\n'
        '            "在已经分叉的仓库上再提交只会让分叉更深。"\n'
        '            f"请先处理这个再提交：{(p.stderr or p.stdout).strip()[:300]}"\n'
        '        )\n',
     '',
     '报错了（没默默提交）'),

    (7, '    sync = _sync_with_remote(repo, commit["sha"], say)\n',
     '    sync = {"ok": True}\n',
     '对齐成功'),

    # ── core.quotePath（中文文件名）这一组 ──
    (8, '    raw = git(repo, "-c", "core.quotePath=false", "status", "--porcelain", check=False)\n',
     '    raw = git(repo, "status", "--porcelain", check=False)\n',
     'changed_files 里有中文名原样'),

    # ★ 这一条咬的是"散着写就会漏"：只把**一个**调用点退回裸写法。
    #   统一出口的价值就在这儿 —— 以后谁新加一处忘了带 -c，立刻被抓住。
    (9, '    dirty_raw = "\\n".join(_status_lines(repo))\n',
     '    dirty_raw = git(repo, "status", "--porcelain", check=False)\n',
     'status() 的 dirty 也是原样的'),
]

caught = 0
for n, old, new, hit in MUT:
    src = orig.decode("utf-8")
    if old not in src:
        print(f"  ⚠ 变异 {n} 跳过：锚点没命中（先查源码是不是已变，别改门闩）")
        continue
    mutated = src.replace(old, new, 1)
    if mutated == src:
        print(f"  ⚠ 变异 {n} 跳过：替换后没变")
        continue
    TARGET.write_bytes(mutated.encode("utf-8"))
    failed = False
    out = ""
    try:
        env = {"PYTHONIOENCODING": "utf-8", "PATH": __import__("os").environ.get("PATH", "")}
        r = subprocess.run([sys.executable, str(HERE / "_t12.py")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900, cwd=str(HERE), env=env)
        out = r.stdout + r.stderr
        failed = r.returncode != 0
    except Exception as e:  # noqa: BLE001
        failed, out = True, f"变异脚本自己炸了：{e}"
    finally:
        restore()

    ok = hashlib.sha256(TARGET.read_bytes()).hexdigest() == sha0
    if failed and hit in out:
        caught += 1
        print(f"  ✓ 变异 {n} 咬住 —— 「{hit}」")
    elif failed:
        line = [l for l in out.splitlines() if "✗" in l]
        print(f"  ✗ 变异 {n} 红了但没命中期望 ⇒ 标签写错了；实际红在：{line[0][:90] if line else '(没抓到 ✗ 行)'}")
    else:
        print(f"  ✗ 变异 {n} 没咬住 ⇒ 门闩漏了它")
    if not ok:
        print(f"     ⚠ 还原校验失败！{TARGET.name}")

print(f"\n咬住 {caught} / {len(MUT)}")
sys.exit(0 if caught == len(MUT) else 1)