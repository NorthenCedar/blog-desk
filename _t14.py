# -*- coding: utf-8 -*-
"""写作台 · **安装策略：从「只覆盖」改成「装到新目录 → 自检 → 切换」**（2026-10-15）

起因：量了一次才发现 `copytree(dirs_exist_ok=True)` 的实际后果 ——
安装目录是「**新构建 ∪ 旧残留**」：

    新构建 123 个文件   安装目录 177 个文件
    ★ 旧残留 54 个 / 52.75 MB —— 超过安装目录（91 MB）的一半
       _internal/numpy/**.pyd   ← **cp313**，而当前构建是 cp314 ⇒ 永远加载不了
       _internal/libcrypto-3-x64.dll / libssl-3-x64.dll
       另有 5 个 **0 字节**文件；其中一个 `_internal/PIL` 冒充成"装好了"，
       把「没装 Pillow」这件事遮住，直到双击才报 `unknown location`（本轮真踩了）。

★ **预检门闩管不到这一类**：它看的是**构建环境**，而遮蔽发生在**安装目录**。
  ⇒ 「只覆盖不删除」是这一类问题里最后一个没堵上的口子。

守五件事（全程在 `tempfile` 里造目录，不碰真安装目录、不跑 PyInstaller）：
  ① 装完的目录与构建产物**逐文件一一对应**：不多、不少、不改内容。
     （这一条就是整个改动的意义 —— 多一个文件就是遮蔽的温床。）
  ② `config.json` 与运行日志要从**旧安装**带过来（新建目录会丢站主的设置）。
  ③ 切换用**目录改名**，旧目录留下可回退，**不删**。
  ④ 第二步失败 ⇒ **自动回退**（旧目录改回原位），不许把站主留在"没有应用"的状态。
  ⑤ 收尾回收旧目录**失败也不算错**（那个目录上可能有 DENY ACE），顶多占点地方。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_spec = importlib.util.spec_from_file_location("build_app_probe", HERE / "build-app.py")
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)

checks: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))
    print(("  OK  " if ok else "  FAIL") + name + (f"  {detail}" if detail else ""))


def attempt(label: str, fn, *a, **kw):
    """跑一步；抛了就打印出来并返回 None，**不中断整场**。

    ★ 为什么要这个：本轮两条变异（①清残留、⑦清上一轮的 `-旧`）会让
      `copytree` / `rename` 直接抛 `FileExistsError` —— 脚本**崩**在断言之前，
      门闩虽然红了（退出码 1），但"红在哪"读不出来，变异驱动器只好判
      「标签写错了」。崩掉的红只等于"退出码非零"，比断言弱得多。
    """
    try:
        return fn(*a, **kw)
    except Exception as e:  # noqa: BLE001
        print(f"  ~~ {label} 抛了：{type(e).__name__}: {e}")
        return None


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def make_build(base: Path) -> Path:
    b = base / "built"
    (b / "_internal" / "webview").mkdir(parents=True)
    (b / "写作台.exe").write_bytes(b"EXE-NEW")
    (b / "_internal" / "webview" / "dom.py").write_text("new webview\n", encoding="utf-8")
    (b / "_internal" / "python314.dll").write_bytes(b"\x00" * 16)
    return b


print("=== ① 装完的目录必须与构建产物逐文件一一对应 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    built = make_build(base)
    dest = base / "写作台"
    # 旧安装：故意塞一堆残留（正是本轮的现场）
    (dest / "_internal" / "numpy" / "core").mkdir(parents=True)
    (dest / "_internal" / "numpy" / "core" / "_umath.cp313-win_amd64.pyd").write_bytes(b"\x00" * 8)
    (dest / "_internal" / "PIL").mkdir(parents=True)          # ★ 0 字节的空壳目录
    (dest / "_internal" / "libcrypto-3-x64.dll").write_bytes(b"\x00" * 4)
    (dest / "写作台.exe").write_bytes(b"EXE-OLD")
    (dest / "config.json").write_text('{"repo": "D:/router/blog"}', encoding="utf-8")
    (dest / "运行日志.txt").write_text("以前的日志\n", encoding="utf-8")

    staged = base / "写作台-新"
    # ★ 上一轮失败会留下一个 `写作台-新`；它必须被清干净，否则"全新目录"名不副实
    #   （又变回「新旧合并」，正是本轮要消灭的那件事）。
    staged.mkdir()
    (staged / "上一轮留下的垃圾.dll").write_bytes(b"\x00" * 4)
    attempt("stage_install", bp.stage_install, built, staged, carry_from=dest, source=base)
    chk("★ 上一轮留下的 `写作台-新` 被清干净了（真的是全新目录）",
        not (staged / "上一轮留下的垃圾.dll").exists())
    got, want = snapshot(staged), snapshot(built)
    # ⚠ 有意带过来的两个文件（config.json / 运行日志）不算"多出来" ——
    #   它们是站主的设置，新目录天生没有，正是这个函数要补的。
    carried = {"config.json", "运行日志.txt"}
    chk("★ 不多：安装里没有构建没产生的文件", set(got) - carried == set(want),
        f"多了 {sorted(set(got) - carried - set(want))[:4]} 少了 {sorted(set(want) - set(got))[:4]}")
    chk("★ 少的也只是那两个有意带过来的",
        not (set(want) - set(got)), str(set(want) - set(got)))
    core = {k: v for k, v in got.items() if k in want}
    chk("★ 内容一字不差", core == want, str([k for k in want if core.get(k) != want[k]][:3]))
    chk("★ 旧的 numpy 残留没被带进来",
        not any("numpy" in k for k in got), str([k for k in got if "numpy" in k][:3]))
    chk("★ 旧的空壳 PIL 没被带进来",
        not any(k.startswith("_internal/PIL/") for k in got))
    chk("★ exe 是新的那个", (staged / "写作台.exe").read_bytes() == b"EXE-NEW")

print("\n=== ② config.json / 运行日志 要从旧安装带过来 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    built = make_build(base)
    dest = base / "写作台"
    dest.mkdir(parents=True)
    (dest / "config.json").write_text('{"repo": "D:/router/blog"}', encoding="utf-8")
    (dest / "运行日志.txt").write_text("以前的日志\n", encoding="utf-8")
    staged = base / "写作台-新"
    attempt("stage_install", bp.stage_install, built, staged, carry_from=dest, source=base)
    # ⚠ 一律先 `.is_file()` 再读：直接 `read_text()` 会在文件不存在时抛
    #   FileNotFoundError，脚本**崩掉**而不是报一条 FAIL —— 那样门闩虽然红了，
    #   但"红在哪"读不出来（本轮变异 1/4 就栽在这，看起来像"标签写错了"）。
    chk("★ config.json 带过来了（站主的仓库路径与密钥没丢）",
        (staged / "config.json").is_file()
        and (staged / "config.json").read_text(encoding="utf-8") == '{"repo": "D:/router/blog"}')
    chk("★ 运行日志带过来了", (staged / "运行日志.txt").is_file())
    # 源码里有 config.json 时以源码为准
    (base / "config.json").write_text('{"repo": "D:/other"}', encoding="utf-8")
    staged2 = base / "写作台-新2"
    attempt("stage_install2", bp.stage_install, built, staged2, carry_from=dest, source=base)
    chk("★ 源码里有 config.json 时以源码为准",
        (staged2 / "config.json").is_file()
        and '"D:/other"' in (staged2 / "config.json").read_text(encoding="utf-8"))

print("\n=== ③ 切换用改名：旧的留下可回退，不删 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    built = make_build(base)
    dest = base / "写作台"
    dest.mkdir()
    (dest / "写作台.exe").write_bytes(b"EXE-OLD")
    staged = base / "写作台-新"
    attempt("stage_install", bp.stage_install, built, staged, carry_from=dest, source=base)

    old_dir = attempt("swap_install", bp.swap_install, staged, dest)
    chk("★ 切换成功",
        (dest / "写作台.exe").is_file() and (dest / "写作台.exe").read_bytes() == b"EXE-NEW")
    chk("★ 新目录被搬空了（它就是新安装）", not staged.exists())
    chk("★ 旧目录被保留成可回退的一份", old_dir and old_dir.exists(), str(old_dir))
    chk("★ 旧目录里还是旧 exe（能拿它回退）",
        old_dir is not None and (old_dir / "写作台.exe").is_file()
        and (old_dir / "写作台.exe").read_bytes() == b"EXE-OLD")

print("\n=== ④ 第二步失败 ⇒ 自动回退，不把站主留在没有应用的状态 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    dest = base / "写作台"
    dest.mkdir()
    (dest / "写作台.exe").write_bytes(b"EXE-OLD")
    staged = base / "写作台-新"
    staged.mkdir()
    (staged / "写作台.exe").write_bytes(b"EXE-NEW")

    real = bp._rename
    calls = {"n": 0}

    def flaky(a, b):
        # ★ 只让**第 2 次**（staged → dest）失败。第 3 次是回退（old → dest），
        #   必须成功 —— 否则测的就变成"回退也失败"，不是"回退成功"。
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(5, "Access is denied")
        return real(a, b)

    bp._rename = flaky
    try:
        raised = None
        try:
            bp.swap_install(staged, dest)
        except OSError as e:
            raised = e
    finally:
        bp._rename = real
    chk("★ 失败被抛出来了（没被吞掉）", raised is not None, str(raised))
    chk("★ **旧安装回到原位**", dest.exists() and (dest / "写作台.exe").read_bytes() == b"EXE-OLD")
    chk("★ 新目录还在原地（没被搬走半截）", staged.exists())

print("\n=== ⑤ 收尾回收失败不算错 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    dest = base / "写作台"
    dest.mkdir()
    (dest / "x").write_text("x", encoding="utf-8")
    old = base / "写作台-旧"
    old.mkdir()
    (old / "y").write_text("y", encoding="utf-8")
    real_recycle = bp.recycle
    bp.recycle = lambda p: False          # 假装请不动（那个目录上可能有 DENY ACE）
    try:
        bp.reclaim(old)                    # 不该抛
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
        print("   抛了：", e)
    finally:
        bp.recycle = real_recycle
    chk("★ 回收失败不抛（顶多占点地方）", ok)

print("\n=== ⑥ 回收不掉的时候：宁可停下，也不许悄悄合并 ===")
# ★ 这是上一条换来的**真实场景**：上一轮失败留下 `写作台-新`，而它请不动
#   （那个目录上可能有 DENY ACE —— 站内踩过一次）。此时若退回 `dirs_exist_ok`，
#   旧文件就会混进新安装 —— 那正是本轮要消灭的那件事。
#   ⇒ 正确行为是**抛出来**（构建中止、现有安装原封不动），而不是合并。
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    built = make_build(base)
    staged = base / "写作台-新"
    staged.mkdir()
    (staged / "垃圾.dll").write_bytes(b"\x00" * 4)
    real_recycle = bp.recycle
    bp.recycle = lambda p: False
    try:
        raised = None
        try:
            bp.stage_install(built, staged, carry_from=None, source=base)
        except Exception as e:  # noqa: BLE001
            raised = e
    finally:
        bp.recycle = real_recycle
    chk("★ 请不动旧目录时**抛出来**（构建中止），而不是合并",
        raised is not None, type(raised).__name__ if raised else "居然没抛")
    chk("★ 旧安装没被动过（staged 之外没有任何地方被写）",
        set(built.iterdir()) == {built / "写作台.exe", built / "_internal"},
        str(sorted(p.name for p in built.iterdir())))

print("\n=== ⑦ 切换前，上一轮没清干净的 `写作台-旧` 要先请走 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    dest = base / "写作台"
    dest.mkdir()
    (dest / "写作台.exe").write_bytes(b"EXE-OLD")
    (base / "写作台-旧").mkdir()                 # 上一轮残留
    (base / "写作台-旧" / "上一轮").write_text("x", encoding="utf-8")
    staged = base / "写作台-新"
    staged.mkdir()
    (staged / "写作台.exe").write_bytes(b"EXE-NEW")
    old_dir = attempt("swap_install", bp.swap_install, staged, dest)
    chk("★ 切换成功",
        (dest / "写作台.exe").is_file() and (dest / "写作台.exe").read_bytes() == b"EXE-NEW")
    chk("★ 旧的 `写作台-旧` 是**这次**的那一份，不是上一轮的残留",
        old_dir is not None and not (old_dir / "上一轮").exists(), str(old_dir))
    chk("★ 上一轮的残留被请走了（`写作台-旧` 里只剩这一份）",
        not (base / "上一轮").exists()
        and all(p.name != "上一轮" for p in (base / "写作台-旧").rglob("*"))
        if (base / "写作台-旧").exists() else False,
        str(sorted(p.name for p in (base / "写作台-旧").rglob("*")))[:120])

print("\n=== ⑧ 自检日志必须跟着被测的 exe 走（提示指一处、现场在另一处最难查）===")
# ★ 这条是本轮真踩出来的：日志路径原来写死 `DEST`，旧流程里正好等于 exe 所在目录，
#   换成"装到新目录"之后自检跑的是 `写作台-新/写作台.exe`，日志却落在**旧安装**里，
#   于是失败提示指向 staged、现场却在旧目录 —— 看哪边都像合理的。
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    dest_exe = base / "写作台" / "写作台.exe"
    staged_exe = base / "写作台-新" / "写作台.exe"
    chk("★ 自检 staged 的 exe 时，日志落在 staged 旁边（不是旧安装）",
        bp._log_path(staged_exe).parent == staged_exe.parent,
        str(bp._log_path(staged_exe)))
    chk("★ 旧流程里（exe 就在安装目录）行为不变，日志仍在 exe 旁边",
        bp._log_path(dest_exe).parent == dest_exe.parent)
    chk("★ 显式指定时听调用方的",
        bp._log_path(staged_exe, log_dir=base / "别处").parent == base / "别处")

    # ★ 上面只守了 `_log_path` 这个**函数**，没守它真被 `smoke_test` **用上** ——
    #   而"接线与实现是两回事"正是本轮踩过的坑（日志路径写死 DEST 时，
    #   `_log_path` 根本不存在，测试自然全绿）。
    #   这里真起一次进程：拿一份系统里的 whoami.exe 当替身 ——
    #   它碰到 `--no-open` 这种非法参数会**立刻退出**（秒级，不挂），
    #   于是 `smoke_test` 必然返回"没过"，正好用来验日志落在哪。
    stub = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"
    if not stub.is_file():                        # 没有就明说，别装作验过了
        print("  ~~ 跳过接线那一小段：这台机器上没有 whoami.exe 当替身")
    else:
        with tempfile.TemporaryDirectory() as td2:
            fake_exe = Path(td2) / "写作台.exe"    # ★ 换名 ⇒ 所在目录不是 APPDIR
            shutil.copy2(stub, fake_exe)
            ok2, why2 = bp.smoke_test(fake_exe, port=8937, timeout=25)
            chk("★ 替身确实被判定为没过（否则这一小段是恒真的）", not ok2, why2[:60])
            chk("★ smoke_test 真把日志写在**被测 exe 旁边**，不是写死 APPDIR",
                (Path(td2) / "_自检日志.txt").is_file(),
                str(sorted(p.name for p in Path(td2).iterdir())))
            chk("★ 那次自检没往现有安装目录里写东西",
                not (bp.DEST / "_自检日志.txt").exists()
                or bp._log_path(fake_exe) != bp.DEST / "_自检日志.txt")

bad = [c for c in checks if not c[1]]
print(f"\n通过 {len(checks) - len(bad)} / {len(checks)}")
sys.exit(1 if bad else 0)

bad = [c for c in checks if not c[1]]
print(f"\n通过 {len(checks) - len(bad)} / {len(checks)}")
sys.exit(1 if bad else 0)