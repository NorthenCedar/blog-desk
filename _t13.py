# -*- coding: utf-8 -*-
"""写作台 · **打包脚本的两处硬门闩**（2026-10-15）

站主报的是：换了个 Python 环境重打包，**构建全绿、产物也有模有样**，双击却弹框：

    ImportError: cannot import name 'Image' from 'PIL' (unknown location)

`unknown location` 是关键：不是"没装 Pillow"，而是 `_internal/PIL` **这个目录在、
内容是空的** —— 因为 `copytree(dirs_exist_ok=True)` 只覆盖不删除，上一版安装留下的
`PIL` 目录把"没装 Pillow"这件事**遮住了**，直到双击才现形。

★★★ 本轮真正要修的是两件事（都出在这一轮）：

  ① **构建依赖没有前置检查**。`build-app.py` 只硬门闩了图标（缺图标直接失败），
     但 `pywebview` / `clr` / `Pillow` / `PyYAML` 缺了照样"构建成功"，
     只是产出一个双击就崩的 exe。README 的依赖清单也只写了两个（`pywebview`、
     `pythonnet`），`Pillow` 与 `PyYAML` 根本没列 —— 而 `lib/images.py` 与
     `lib/content.py` 顶层就 import 它们。
     ⇒ 新增 `check_build_deps()`：**从源码里扫顶层 import**（而不是手写一张清单），
        去掉标准库与自家模块，剩下的必须都能 import。扫源码的好处是下一个新依赖
        也会被自动抓到，手写清单则必然滞后。

  ② **自检的失败提示自己会崩**。`print(("  ✔ 自检通过：" if ok else "  ✘ 自检没过：") + why)`
     在 GBK 控制台上打印 `✘` 直接 `UnicodeEncodeError` ⇒ **自检失败时，屏幕上是一屏
     traceback，而不是"自检没通过 + 原因"**。本轮就被它误导过一回。
     ⇒ 关键输出改用 ASCII 记号，并在模块加载时把 stdout 兜成 `errors="replace"`，
       以后谁再写 `⚠` / `✔` 也不会把整段输出炸掉。

守四件事：
  ① `check_build_deps()` 在**当前这套构建环境**里报缺 `Pillow` 与 `PyYAML`
     （这是本轮的真实状态，不是夹具造出来的）；
  ② 它是从源码扫出来的 ⇒ 造一个"源码里 import 了不存在的包"的临时文件，
     那个包必须出现在清单里；
  ③ 标准库与自家模块（`lib`）**不许**被算成依赖；
  ④ `say()` 在一个**只能编码 GBK** 的 stdout 上不许抛异常，且失败路径上
     必须真的打出"没过"三个字。

全程不出网、不碰安装目录、不碰源码（临时文件用 `tempfile`）。
"""
from __future__ import annotations

import io
import re as _re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import importlib.util  # noqa: E402

# `build-app.py` 的文件名带连字符，没法 `import`。按文件路径加载成
# `build_app_probe` 这个名字；它有 `if __name__ == "__main__"` 保护，导入不会跑构建。
_spec = importlib.util.spec_from_file_location("build_app_probe", HERE / "build-app.py")
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)

checks: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))
    print(("  OK  " if ok else "  FAIL") + name + (f"  {detail}" if detail else ""))


print("=== ① 当前这套构建环境缺什么 ===")
missing = bp.check_build_deps()
print(f"  依赖清单：{', '.join(bp.build_deps())}")
print(f"  缺：{missing or '（无）'}")
chk("清单里认得出 Pillow / PyYAML / webview / clr",
    all(m in bp.build_deps() for m in ("PIL", "yaml", "webview", "clr")),
    str(bp.build_deps()))
chk("★ 自家根目录模块不算依赖（app.py 里的 `import server`）",
    "server" not in bp.build_deps() and "app" not in bp.build_deps(),
    str(bp.build_deps()))

# ★ "缺哪些"是**相对于当前这套解释器**的 —— 测试可能跑在系统 Python 上（那里确实没有
#   webview/clr/PIL），也可能跑在构建用的 venv 上（全都有）。所以不能写死期望值，
#   只能验它与"逐个真的 import 一遍"的结果一致 —— 那才是判据本身。
import importlib  # noqa: E402
expect_missing = []
for _m in bp.build_deps():
    try:
        importlib.import_module(_m)
    except Exception:  # noqa: BLE001
        expect_missing.append(_m)
chk("★ check_build_deps 与「逐个真的 import」结果一致（不是拿 find_spec 冒充）",
    missing == expect_missing, f"函数报 {missing} / 实测 {expect_missing}")
chk("★ 报出来的每个都确实在依赖清单里",
    set(missing) <= set(bp.build_deps()), str(missing))
chk("标准库（re / json / subprocess）不许出现在依赖清单里",
    not ({"re", "json", "subprocess", "pathlib"} & set(bp.build_deps())),
    str(bp.build_deps()))
chk("自家模块 lib 不许出现在依赖清单里", "lib" not in bp.build_deps())

print("\n=== ② 它是从源码扫出来的，不是手写清单 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    (base / "app.py").write_text(
        "import os\nimport yaml\nfrom PIL import Image\nimport lib.gitops\n", encoding="utf-8")
    (base / "server.py").write_text("import webbrowser\n", encoding="utf-8")
    (base / "lib").mkdir()
    (base / "lib" / "thing.py").write_text("import definitely_not_installed_xyz\n", encoding="utf-8")
    deps = bp.build_deps(base)
    chk("★ 源码里 import 的新包被抓到了", "definitely_not_installed_xyz" in deps, str(deps))
    chk("子目录 lib/ 里的 import 也扫到了", "PIL" in deps and "yaml" in deps, str(deps))
    chk("深层模块只取顶层名（PIL.Image → PIL）", "PIL.Image" not in deps and "PIL" in deps)
    chk("标准库被排除", "os" not in deps and "webbrowser" not in deps, str(deps))
    chk("自家 lib 被排除", "lib" not in deps and "lib.gitops" not in deps)
    chk("★ 同目录下的自家模块（server.py）被排除", "server" not in deps, str(deps))

print("\n=== ③ say() 在 GBK 控制台上不许崩 ===")
# 一个"只能编码 GBK"的 stdout —— 这正是站主双击 .bat 之后的那个控制台
buf = io.BytesIO()
gbk = io.TextIOWrapper(buf, encoding="gbk", errors="strict", newline="")
old = sys.stdout
try:
    sys.stdout = gbk
    bp.say("  OK 自检通过：/api/health -> 200")
    bp.say("  !! 自检没过：ImportError: cannot import name 'Image' from 'PIL'")
    bp.say("  !! 提醒：构建依赖不全，见上面那行")
finally:
    sys.stdout = old
    gbk.flush()
out = buf.getvalue().decode("gbk", "replace")
print("  实际输出：", out.replace("\n", " | "))
chk("★ GBK 控制台上打印成功路与失败路都没抛异常", True)
chk("★ 失败路径上真的打出了「没过」", "没过" in out, out[:80])
chk("★ ASCII 记号原样保留（OK / !!）", "OK" in out and "!!" in out)
chk("★ 关键信息（ImportError 原文）没丢", "cannot import name 'Image'" in out)

print("\n=== ④ 反向对照 ===")
buf2 = io.BytesIO()
gbk2 = io.TextIOWrapper(buf2, encoding="gbk", errors="strict", newline="")
old = sys.stdout
crashed = False
try:
    sys.stdout = gbk2
    print("  ✔ 自检通过：/api/health -> 200")     # ← 改之前的写法
except UnicodeEncodeError:
    crashed = True
finally:
    sys.stdout = old
chk("★ 反向对照：改之前的 print 在 GBK 控制台上**确实会崩**（证明上面不是恒真）",
    crashed, "crashed" if crashed else "居然没崩")

print("\n=== ⑤ 门闩本身（preflight）===")
# ★ 门闩必须能**单独调**。第一版把它 inline 在 main() 开头，
#   于是「把 `if missing:` 改成 `if False:`」这种变异压根没有测试会红 ——
#   门闩摆在那儿，却没人走过那条路。
_real_bd, _real_cd = bp.build_deps, bp.check_build_deps
try:
    bp.build_deps = lambda *a, **k: ["PIL", "yaml"]
    bp.check_build_deps = lambda *a, **k: ["PIL"]          # 假装缺一个
    import io as _io
    _b = _io.StringIO(); _o = sys.stdout; sys.stdout = _b
    try:
        okpf = bp.preflight()
    finally:
        sys.stdout = _o
    _out = _b.getvalue()
    chk("★ 依赖不全 ⇒ preflight 返回 False（不许让它去打）", okpf is False, str(okpf))
    chk("★ 并且把缺哪一个说清楚了", "PIL" in _out, _out[:90])
    chk("★ 给了可直接复制的安装命令", "pip install" in _out, _out[:90])

    bp.check_build_deps = lambda *a, **k: []               # 假装全齐
    _b2 = _io.StringIO(); _o = sys.stdout; sys.stdout = _b2
    try:
        okpf2 = bp.preflight()
    finally:
        sys.stdout = _o
    chk("依赖齐全 ⇒ preflight 返回 True", okpf2 is True, str(okpf2))
    chk("★ 齐全时也报一句（站主要知道门闩真的跑过了）", "齐全" in _b2.getvalue(), _b2.getvalue()[:60])
finally:
    bp.build_deps, bp.check_build_deps = _real_bd, _real_cd

print("\n=== ⑥ 模块加载时对 stdout 做了编码兜底（GBK 控制台）===")
# ★ 必须在**导入之前**把 stdout 换成 GBK 的替身：兜底是对模块加载那一刻的
#   sys.stdout 做的，测试中途换掉就绕过它了 ⇒ 那条变异本来咬不住。
_probe = (
    "import io, sys, importlib.util\n"
    "sys.stdout.flush()\n"
    "buf = io.BytesIO()\n"
    "sys.stdout = io.TextIOWrapper(buf, encoding='gbk', errors='strict', newline='')\n"
    "spec = importlib.util.spec_from_file_location('bp2', r'%s')\n"
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
    "m.say('  OK 依赖齐全 · 中文也要打得出来 ✔')\n"
    "sys.stdout.flush()\n"
    "sys.__stdout__.write('SURVIVED\\n')\n"
    "sys.__stdout__.write(buf.getvalue().decode('gbk', 'replace'))\n"
) % str(HERE / "build-app.py")
_r = subprocess.run([sys.executable, "-c", _probe], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=300)
combined = _r.stdout + _r.stderr
print("  子进程 stdout:", repr(_r.stdout[:200]))
print("  子进程 stderr:", repr(_r.stderr[:200]))
print("  子进程 returncode:", _r.returncode)
chk("★ 模块加载后，打印中文与符号在 GBK 控制台上不抛异常",
    "SURVIVED" in combined, combined.strip()[:80])
chk("★ 中文内容真的打出来了（没被 errors=replace 吞成空）",
    "依赖齐全" in combined, combined.strip()[:80])

print("\n=== ⑦ 关键结果行不许再带 ✔/✘/⚠ ===")
# 剥掉注释再做**负向**断言 —— 否则注释里引用了那些字符，断言会永远红。
# 这条守的是本轮真踩的那个坑：自检失败那行用 `✘`，在 GBK 控制台上把"失败原因"本身炸没了。
_src = (HERE / "build-app.py").read_text(encoding="utf-8")
_nc = _re.sub(r"#.*", "", _src)
_bad_lines = [l.strip() for l in _nc.splitlines()
              if l.lstrip().startswith(("print(", "say(")) and _re.search(r"[✔✘⚠]", l)]
chk("★ 没有任何一行输出里还带 ✔/✘/⚠", not _bad_lines, str(_bad_lines[:2]))
chk("★ 反向对照：剥注释前确实有这些字符（证明不是恒真）",
    bool(_re.search(r"[✔✘⚠]", _src)) and bool(_re.search(r"[✔✘⚠]", _nc)),
    "源码里有 / 剥完还有")

bad = [c for c in checks if not c[1]]
print(f"\n通过 {len(checks) - len(bad)} / {len(checks)}")
sys.exit(1 if bad else 0)