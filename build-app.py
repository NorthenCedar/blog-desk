# -*- coding: utf-8 -*-
"""把写作台打包成 Windows 应用（`写作台.exe`）。用法：`python build-app.py`

做五件事：
  1. 用 PyInstaller 打一个**没有控制台**的 exe（`--windowed`）；
  2. 把产物搬到 `%LOCALAPPDATA%\\Programs\\写作台\\` —— 与源码目录分开，
     站主看到的是「一个应用」，而不是 `dist/写作台/_internal/…` 那一堆构建产物；
  3. 把 `config.json` 带过去（**一定要带** —— 仓库路径和管理密钥都在里面，
     不带过去新装的这份会"忘了"密钥），并从 `说明.source.txt` **现转**一份
     GBK + CRLF 的 `说明.txt` 进安装目录（见 make_readme.py 的文件头）；
  4. **复验完整性标签 + 实跑一次**（下面 ★★ 两条）；
  5. 建/刷新桌面与开始菜单的快捷方式。

★★ 为什么装到 `%LOCALAPPDATA%\\Programs\\`（而不是原来的 `D:\\router\\写作台`）：
  `D:\\router` 整棵树被打了 `Mandatory Label\\Low Mandatory Level` 低完整性标签，
  而**带 Low 标签的 exe 起不来**。完整实证见 make_shortcut.py 里 APPDIR 的注释。
  一句话：PyInstaller 的 bootloader 会去 `_wfopen` 自己那个 exe 文件，
  Low 标签下这个打开会失败，于是启动即报
  "Could not load PyInstaller's embedded PKG archive from the executable"，
  双击毫无反应 —— 而文件、归档、cookie、bootloader 全都是好的。

★★ 为什么"装完必须真跑一次"（`smoke_test`）：
  "exe 文件存在"和"exe 起得来"是两件事。上面那个坑里，
  PyInstaller 退出码 0、文件大小正常、`pyi-archive_viewer` 能读、
  bootloader 与官方 wheel 逐字节一致 —— 所有静态检查全绿，**唯独跑不起来**。
  所以门闩必须是"起服务 → 打 `/api/health` → 200"，不是"文件在不在"。
  这就是本站"线上产物核对"那条纪律在打包场景下的形态。

★ 为什么每轮都换一个带时间戳的 dist / build 目录（而不是固定用 `dist/写作台`）：
  PyInstaller 在 COLLECT 那一步会先把 `dist/<名字>` **删掉**再重建。
  本机的开发环境里有一层"删除要走回收站"的保护，它按 SHFileOperationW 的返回值
  判成败、而那个返回值对目录**恒定报错**（见 make_shortcut.recycle 的注释），
  于是它会抛异常、整个构建就此失败。
  绕开它的办法不是把那层保护关掉（那是保护站主文件用的），
  而是**让构建过程根本不需要删东西**：每轮开一个新目录，
  收工之后再让**我们自己**那套（判据正确）把临时目录收回回收站。

★ 为什么必须是 `--windowed`（而不是图省事用默认的控制台版）：
  配合 `lib/proc.py` 里的 `CREATE_NO_WINDOW`，两个一起才能保证
  git / npm 子进程**不闪黑窗**（站主报过"跳出来很多终端"）。详见 lib/proc.py 文件头。

★ 为什么 `--collect-all webview`：WebView2 的两个 DLL 藏在 webview 包的 lib/ 里，
  不是普通的 Python 模块，PyInstaller 的静态分析看不见它们。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib import proc  # noqa: E402

# ★★ 2026-10-15：把 stdout 兜成"编不出来的字符不抛异常"。
#   起因：本脚本第 224 行那句 `print(("  ✔ 自检通过：" if ok else "  ✘ 自检没过：") + why)`
#   在 **GBK 控制台**上打印 `✔`/`✘` 会直接 `UnicodeEncodeError`
#   ⇒ **自检失败时，屏幕上是一屏 traceback，而不是"自检没通过 + 原因"**。
#   本轮就被它误导过一回（以为自检本身炸了）。
#   `errors="replace"` 只是兜底；关键输出另用 ASCII 记号（见 say()），保证任何编码都读得懂。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")          # Python 3.7+
    except (AttributeError, ValueError):
        pass
from make_shortcut import (APP_NAME, EXE, create_lnk, desktop, force_medium,  # noqa: E402
                           integrity_label, recycle, start_menu)
from make_readme import generate as gen_readme  # noqa: E402

DEST = EXE.parent
PROBE_PORT = 8791        # 自检用的端口，挑一个不常用的


def run(args: list[str]) -> int:
    print("$ " + " ".join(args))
    return proc.run(args, cwd=str(HERE)).returncode


def _log_path(exe: Path, log_dir: Path | None = None) -> Path:
    """自检日志放哪。默认**放在被测 exe 旁边**。

    ★ 原来这里写死 `DEST`，那在旧流程里正好等于 exe 所在目录，没毛病。
      改成"装到新目录"之后自检跑的是 `写作台-新/写作台.exe`，
      日志却落在**旧安装**里 —— 于是失败提示指向 staged，现场却不在那儿。
      （"提示指一处、现场在另一处"这种错最难查：看哪边都是合理的。）
    """
    return (log_dir or exe.parent) / "_自检日志.txt"


def smoke_test(exe: Path, port: int = PROBE_PORT, timeout: float = 90.0):
    """装上之后**真跑一次**：起服务 → 打 `/api/health` → 收工。返回 (是否通过, 说明)。

    ★★ 这是本脚本唯一真正重要的检查，理由见文件头。

    用 `--no-open`：它会走到 `server.serve()` 之后、`import webview` **之前**就返回，
    也就是"只把 HTTP 服务立起来、不开任何窗口" —— 打包之后没有控制台的形态下，
    这是唯一能拿 curl / urllib 验「服务、界面、配置都对」的入口。
    （UI 自己那层还要靠站主双击目检；本机 pywebview 起窗会静默挂死，测不了。）
    """
    log = _log_path(exe)
    with open(log, "wb") as out:
        p = subprocess.Popen(
            [str(exe), "--no-open", "--port", str(port)],
            cwd=str(exe.parent), stdout=out, stderr=subprocess.STDOUT,
            creationflags=proc.CREATE_NO_WINDOW,
        )
    try:
        deadline = time.time() + timeout
        last = "（还没试）"
        while time.time() < deadline:
            if p.poll() is not None:
                last = f"进程提前退出，退出码 {p.returncode}"
                break
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                    if r.status == 200:
                        body = r.read(200).decode("utf-8", "replace").strip()
                        return True, f"/api/health → 200  {body}"
            except (urllib.error.URLError, OSError) as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(0.5)
        return False, f"等了 {timeout:.0f} 秒没起来；最后一次：{last}"
    finally:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def reclaim(*paths: Path) -> None:
    """把临时构建目录收回回收站（可恢复）。失败不算错 —— 顶多占点地方。"""
    for p in paths:
        if p.exists() and recycle(p):
            print(f"  已把临时目录收回回收站：{p}")


def say(msg: str = "") -> None:
    """打印一行。**关键输出一律用它**，因为它的记号是纯 ASCII。

    为什么不用 `✔` / `✘`：默认控制台编码是 GBK，编不出这两个字符 ⇒ 抛
    UnicodeEncodeError ⇒ 恰恰在"构建失败"这种最需要看到信息的时候，把信息本身炸没了。
    """
    print(msg)


# ★ 成功 / 失败 / 提醒 —— 纯 ASCII，任何编码都打得出来
OK, BAD, WARN = "OK", "!!", "warn"


def build_deps(root: Path | None = None) -> list[str]:
    """**从源码里扫**出运行期必须能 import 的第三方顶层模块。

    ★★ 为什么扫源码而不是手写一张清单（2026-10-15）：
       换了一套 Python 重打包，`Pillow` 与 `PyYAML` 都没装 —— 而 `lib/images.py`
       顶层 `from PIL import Image`、`lib/content.py` 顶层 `import yaml`。
       PyInstaller 照样**构建成功**，只是产出一个双击就崩的 exe。
       手写清单必然滞后（这次 README 就只列了 `pywebview` 与 `pythonnet`）；
       扫源码则下一个新依赖也会被自动抓到。

    扫描范围：`app.py` / `server.py` / `lib/*.py` 的**顶层** import。
    排除：标准库（`sys.stdlib_module_names`）、自家包（`lib`）、`__future__`。
    只取顶层名（`PIL.Image` → `PIL`）。
    """
    root = root or HERE
    files = [root / "app.py", root / "server.py"]
    files += sorted((root / "lib").glob("*.py")) if (root / "lib").is_dir() else []

    found: set[str] = set()
    for f in files:
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(?:import\s+([A-Za-z_][\w.]*)|from\s+([A-Za-z_][\w.]*)\s+import\b)",
                         line)
            if not m:
                continue
            top = (m.group(1) or m.group(2)).split(".")[0]
            if top in ("__future__", "lib") or top in sys.stdlib_module_names:
                continue
            # ★ 自家模块也要排除：`app.py` 里有 `import server`，而 server.py 就在根目录 ——
            #   只排除 `lib` 会把 `server` 当成第三方依赖（第一版就漏在这里）。
            if (root / f"{top}.py").is_file() or (root / top).is_dir():
                continue
            found.add(top)
    return sorted(found)


def check_build_deps(root: Path | None = None) -> list[str]:
    """返回**当前这套构建环境里 import 不了**的那些依赖（空 = 全齐）。

    ★ `importlib.util.find_spec` 只查"能不能找到"，不真 import ——
       `find_spec` 对一个**空壳目录**（例如上一版安装留下的 `_internal/PIL`）
       也可能"找到"，而真 import 会报 `cannot import name 'Image' (unknown location)`。
       所以这里**真的 import 一次**：那才是"双击会不会崩"的判据。
    """
    import importlib

    missing: list[str] = []
    for name in build_deps(root):
        try:
            importlib.import_module(name)
        except Exception:                                  # noqa: BLE001
            missing.append(name)
    return missing


def _rename(a: Path, b: Path) -> None:
    """目录改名。单独抽出来是为了能被测试替换掉（回退那一步要模拟失败）。"""
    a.rename(b)


def stage_install(built: Path, staged: Path, carry_from: Path | None = None,
                  source: Path | None = None) -> list[str]:
    """把构建产物装进一个**全新**目录。返回给人看的几行。

    ★★ 为什么不再直接 copytree 到安装目录（2026-10-15 改）：
       `copytree(dirs_exist_ok=True)` 只覆盖不删除 ⇒ 安装目录变成「**新构建 ∪ 旧残留**」。
       实测：残留 54 个文件 / **52.75 MB**，占安装目录（91 MB）的一半以上，其中包含
       **cp313** 的 numpy 二进制（当前构建是 cp314）—— 永远加载不了，纯死重；
       另有 5 个 **0 字节**空壳文件，其中 `_internal/PIL` 冒充成"装好了"，
       把「没装 Pillow」这件事遮住直到双击才报 `unknown location`（本轮真踩了）。
       ⇒ 新建目录装 = 安装目录与构建产物**逐文件一一对应**，遮蔽没有生长的余地。

    ★ 站主的设置不能丢：`config.json` 与 `运行日志.txt` 从**旧安装**带过来
      （新建目录天然没有它们）。源码里有 `config.json` 时以源码为准。
    """
    notes: list[str] = []
    staged.parent.mkdir(parents=True, exist_ok=True)
    if staged.exists():                       # 上一轮失败的残留
        recycle(staged)
    shutil.copytree(built, staged)            # ★ 不用 dirs_exist_ok：目标是全新的
    notes.append(f"  装进全新目录（零残留）：{staged}")

    src_root = source or HERE
    for extra in ("config.json", "运行日志.txt"):
        if (staged / extra).exists():
            continue
        src = src_root / extra
        if not src.is_file() and carry_from:
            src = carry_from / extra           # ★ 旧安装里的那份（站主的设置）
        if src.is_file():
            shutil.copy2(src, staged / extra)
            notes.append(f"  带上 {extra}（来自 {src.parent}）")
    return notes


def swap_install(staged: Path, dest: Path) -> Path | None:
    """把已自检通过的 `staged` 换到 `dest` 上。返回**旧目录**（改名后，可回退）。

    ★ 用**目录改名**而不是删除：改名是秒级、可回退的；真正的清理由调用方走回收站。
    ★ 回退：第二步（staged → dest）失败就把 `dest-旧` 改回 `dest` ——
      **绝不能把站主留在"没有应用"的状态**（这正是升级中途失败最糟的那种结局）。
    """
    old = dest.with_name(dest.name + "-旧")
    if old.exists():                           # 上一轮没清干净的
        recycle(old)
    if dest.exists():
        _rename(dest, old)
    try:
        _rename(staged, dest)
    except Exception:
        if old.exists() and not dest.exists():
            _rename(old, dest)                 # ★ 回退
        raise
    return old if old.exists() else None


def preflight() -> bool:
    """**构建前的硬门闩**：依赖齐全返回 True，不齐就打印清单并返回 False。

    ★ 单独抽成函数而不是写在 main() 里：这样它能被单独测。
      第一版把它 inline 在 main() 开头，于是"把 `if missing:` 改掉"这种变异
      根本没测试会红 —— 门闩摆在那儿，却没人走过那条路。
    """
    deps = build_deps()
    missing = check_build_deps()
    if missing:
        say(f"  {BAD} 构建依赖不全，**先停下**（否则会打出一个双击就崩的 exe）：")
        for m in missing:
            say(f"      缺 {m}")
        say(f"  装上（README 的清单曾经漏了 Pillow 与 PyYAML，2026-10-15 实测）：")
        say(f"      {Path(sys.executable)} -m pip install " + " ".join(deps))
        say(f"  {WARN} 还要注意：构建用的 venv **不要建在 D:\\router 里** —— 那棵树挂着")
        say(f"     低完整性标签，venv 里的 python 会继承，写不进 %LOCALAPPDATA%\\Programs\\。")
        return False
    say(f"  {OK} 构建依赖齐全（{len(deps)} 个：{', '.join(deps)}）")
    return True


def main() -> int:
    if not preflight():
        return 1
    # 老版本留下的固定目录：顺手请走，免得一直占着（请不动也不影响构建）
    reclaim(HERE / "dist" / APP_NAME, HERE / "build" / APP_NAME)

    ts = time.strftime("%Y%m%d-%H%M%S")
    distpath = HERE / "dist" / f"_{ts}"
    workpath = HERE / "build" / f"_{ts}"

    # ★ 图标必须**先有**才许打包（2026-10-09，站主第 3 条）。
    #   为什么写成硬门闩：这里刚发生过一次**静默失效** ——
    #   我把 `icon=` 写进了 `写作台.spec`，但本脚本根本不读 spec，
    #   它是**直接用命令行调 PyInstaller** 的，于是那行改动一点作用都没有，
    #   产物照旧是 PyInstaller 的默认图标（蓝软盘 + Python），而"构建成功"照样打绿。
    #   是 `_verify_icon.py`（从 exe 里把图标抠出来逐像素比）把它抓出来的 ——
    #   又一次印证：**构建输入写对了 ≠ 产物里真是它**。
    #   所以这里不但要传 `--icon`，缺图标时还要**直接失败**，
    #   不许悄悄退回默认图标（那只会让站主看到一个"莫名其妙"的应用图标）。
    ico = HERE / "writedesk.ico"
    if not ico.is_file():
        say(f"{BAD} 没有 {ico} —— 先在源码目录跑一次：python _icon.py")
        print("  （图标是交付物的一部分，缺了不该悄悄用默认图标顶上）")
        return 1

    rc = run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--windowed",
        "--name", APP_NAME,
        "--icon", str(ico),
        "--distpath", str(distpath),
        "--workpath", str(workpath),
        "--paths", ".",
        "--collect-all", "webview",
        "--hidden-import", "clr",
        "--hidden-import", "server",          # app.py 里是延后导入，静态分析看不到
        "--add-data", "ui;ui",
        "app.py",
    ])
    if rc != 0:
        say(f"{BAD} PyInstaller 失败（退出码 {rc}）")
        return rc

    built = distpath / APP_NAME
    if not (built / f"{APP_NAME}.exe").is_file():
        say(f"{BAD} 没找到 {built / (APP_NAME + '.exe')}")
        return 1

    # ── 装进**全新**目录，不碰现有安装（2026-10-15 改，见 stage_install 的注释）──
    staged = DEST.with_name(DEST.name + "-新")
    for note in stage_install(built, staged, carry_from=DEST, source=HERE):
        say(note)
    staged_exe = staged / f"{APP_NAME}.exe"

    # 说明书：从 UTF-8 源现转成 GBK + CRLF（**不要**直接拷，否则源改了这里不会变）
    # ⚠ `gen_readme()` 的 TARGETS 里写死了 APPDIR（= 现有安装目录），所以这里
    #   **临时**把第二个目标指到 staged —— 否则说明书会写进**旧**安装里，
    #   切换完就没了。
    import make_readme
    _saved_targets = make_readme.TARGETS
    try:
        make_readme.TARGETS = (HERE / "说明.txt", staged / "说明.txt")
        for note in gen_readme():
            say("  " + note)
    except Exception as e:                                    # noqa: BLE001
        say(f"  warn 说明书没生成：{e}")
    finally:
        make_readme.TARGETS = _saved_targets

    # ① ★★ 门闩：对**新目录**真跑一次。不过就一步都不动现有安装。
    #   （自检日志落在 staged 旁边，见 `_log_path` 的注释）
    say("  自检中（起服务 → /api/health）…")
    ok, why = smoke_test(staged_exe)
    say(f"  {OK if ok else BAD} " + ("自检通过：" if ok else "自检没过：") + why)
    if not ok:
        say(f"{BAD} 新版本没跑起来 —— **现有安装原封不动**，这个新目录收回回收站：{staged}")
        # ★ 提示指的地方必须真的有现场：`reclaim(staged)` 会把日志一起带走，
        #   所以先把它抄到**用户已经在看的那个目录**（= 现有安装），
        #   否则上面那句"对照自查顺序"就指了个空地方。
        _log = staged / "_自检日志.txt"
        if _log.is_file():
            shutil.copy2(_log, DEST / "_自检日志.txt")
            say(f"  现场已存到 {DEST / '_自检日志.txt'}（对照 说明.txt 里的「出问题时的自查顺序」）。")
        reclaim(staged)
        return 1

    # ② 切换：改名，秒级可回退。旧目录最后才回收。
    try:
        old_dir = swap_install(staged, DEST)
    except Exception as e:                                    # noqa: BLE001
        say(f"{BAD} 切换失败，已自动回退，现有安装没动：{e}")
        reclaim(staged)
        return 1

    # ③ 完整性标签归位（新目录继承的可能是 Low —— 目录整棵带标签）
    for note in filter(None, (force_medium(DEST, recursive=True),
                              force_medium(EXE))):
        say(f"  warn 完整性标签不正，已改回 Medium：{note}")
    label = integrity_label(EXE)
    say(f"  安装位置：{DEST}")
    say(f"  exe 完整性标签：{label}")

    # ④ 旧的进回收站（★ 排在自检与切换**之后**；请不动只提示）
    if old_dir:
        say(("  旧版本已移入回收站：" if recycle(old_dir)
            else f"  warn 旧版本请不动（那个目录上可能有 DENY ACE），想清掉请手动删：{old_dir}"))

    # ⑤ 快捷方式（桌面 + 开始菜单）
    for human, lnk in (("桌面", desktop() / f"{APP_NAME}.lnk"),
                       ("开始菜单", start_menu() / f"{APP_NAME}.lnk")):
        create_lnk(EXE, lnk, DEST, f"{APP_NAME} · 北杉札记")
        print(f"  快捷方式：{human}  {lnk}")

    # ⑥ 旧的 .bat 与旧安装位置：能请走就请走，请不动只提示（不影响用）
    #    ★ 这里原来写死了站主本机的旧安装位置（D 盘某工作区），公开仓库里不该有 ——
    #    而且对别人毫无意义（他机器上没有那个目录）。改为可配置：
    #    设 OLD_INSTALL_DIR 环境变量指向你曾经的安装位置即可顺带清理；
    #    不给就只清桌面那个 .bat。历史上那个目录带过 Low 完整性标签
    #    （有 DENY DELETE_CHILD），所以删除失败是预期内的，只提示不报错。
    _old_dirs = [desktop() / f"{APP_NAME}.bat"]
    _old_dir = os.environ.get("OLD_INSTALL_DIR", "").strip()
    if _old_dir:
        _old_dirs.append(Path(_old_dir) / APP_NAME)
    for old in _old_dirs:
        if old.exists():
            say(("  旧的已移入回收站：" if recycle(old)
                   else "  warn 请不动（那个目录上可能有 DENY ACE），"
                        "想清掉请手动删：") + str(old))

    reclaim(distpath, workpath)

    size = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
    say()
    say(f"{OK} 打好了并且自检跑通：{EXE}")
    print(f"  应用目录 {size / 1024 / 1024:.0f} MB（{DEST}）")
    print("  双击桌面上的「写作台」就能开 —— 没有脚本，没有命令行窗口。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
