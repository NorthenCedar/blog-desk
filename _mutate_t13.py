# -*- coding: utf-8 -*-
"""写作台 · `_t13.py` 的**变异验证**（2026-10-15）

纪律与 `_mutate_t12.py` 一致：按字节存取 + sha256 还原校验 + 信号兜底还原；
判据 = `failed && hit`（`failed` 真而 `hit` 假 ⇒ 标签写错了，不是门闩漏了）。

★ 这个门闩里有个特别的坑：**"构建依赖"这条门闩的第一版把自家模块 `server`
  当成了第三方依赖**（`app.py` 里有 `import server`，而 server.py 就在根目录）。
  ⇒ 专门有两条变异盯着"排除自家模块"，一条盯目录形式、一条盯 `lib/` 目录形式。

用法：python _mutate_t13.py
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


for sig in ("SIGINT", "SIGTERM", "SIGHUP"):          # Windows 上没有 SIGHUP
    s = getattr(signal, sig, None)
    if s is not None:
        signal.signal(s, lambda *_: (restore(), sys.exit(1)))
atexit.register(restore)

MUT = [
    # ① 自家根目录模块（server.py）没被排除
    (1, '            if (root / f"{top}.py").is_file() or (root / top).is_dir():\n'
        '                continue\n',
     '', '自家根目录模块不算依赖'),

    # ② 标准库没被排除
    (2, '            if top in ("__future__", "lib") or top in sys.stdlib_module_names:\n'
        '                continue\n',
     '            if top in ("__future__",):\n'
     '                continue\n', '标准库（re / json / subprocess）不许出现在依赖清单里'),

    # ③ 少扫一处：lib/ 目录下的 import 不看了
    (3, '    files += sorted((root / "lib").glob("*.py")) if (root / "lib").is_dir() else []\n',
     '    files += []\n', '子目录 lib/ 里的 import 也扫到了'),

    # ④ 退化成手写清单（不再扫源码）
    (4, '    root = root or HERE\n',
     '    return ["PIL", "yaml", "webview", "clr"]\n    root = root or HERE\n',
     '源码里 import 的新包被抓到了'),

    # ⑤ 真 import 退化成 find_spec —— 空壳目录会被当成"已装"（正是本轮那个坑）
    (5, '            importlib.import_module(name)\n',
     '            importlib.util.find_spec(name)\n', '与「逐个真的 import」结果一致'),

    # ⑥ stdout 的编码兜底被删了
    (6, '        _s.reconfigure(errors="replace")          # Python 3.7+\n',
     '        pass\n', 'GBK 控制台上打印成功路与失败路都没抛异常'),

    # ⑦ 自检那行退回带符号的 print（本轮真踩的那个坑）
    (7, '    say(f"  {OK if ok else BAD} " + ("自检通过：" if ok else "自检没过：") + why)\n',
     '    print(("  \u2714 自检通过：" if ok else "  \u2718 自检没过：") + why)\n',
     'GBK 控制台上打印成功路与失败路都没抛异常'),

    # ⑧ 依赖不全时的提前返回被删了 —— 门闩形同虚设
    (8, '    if missing:\n', '    if False:\n', '与「逐个真的 import」结果一致'),
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
        r = subprocess.run([sys.executable, str(HERE / "_t13.py")], capture_output=True,
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