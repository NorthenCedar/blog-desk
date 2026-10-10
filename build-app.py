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
from make_shortcut import (APP_NAME, EXE, create_lnk, desktop, force_medium,  # noqa: E402
                           integrity_label, recycle, start_menu)
from make_readme import generate as gen_readme  # noqa: E402

DEST = EXE.parent
PROBE_PORT = 8791        # 自检用的端口，挑一个不常用的


def run(args: list[str]) -> int:
    print("$ " + " ".join(args))
    return proc.run(args, cwd=str(HERE)).returncode


def smoke_test(exe: Path, port: int = PROBE_PORT, timeout: float = 90.0):
    """装上之后**真跑一次**：起服务 → 打 `/api/health` → 收工。返回 (是否通过, 说明)。

    ★★ 这是本脚本唯一真正重要的检查，理由见文件头。

    用 `--no-open`：它会走到 `server.serve()` 之后、`import webview` **之前**就返回，
    也就是"只把 HTTP 服务立起来、不开任何窗口" —— 打包之后没有控制台的形态下，
    这是唯一能拿 curl / urllib 验「服务、界面、配置都对」的入口。
    （UI 自己那层还要靠站主双击目检；本机 pywebview 起窗会静默挂死，测不了。）
    """
    log = DEST / "_自检日志.txt"
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


def main() -> int:
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
        print(f"✘ 没有 {ico} —— 先在源码目录跑一次：python _icon.py")
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
        print(f"✘ PyInstaller 失败（退出码 {rc}）")
        return rc

    built = distpath / APP_NAME
    if not (built / f"{APP_NAME}.exe").is_file():
        print(f"✘ 没找到 {built / (APP_NAME + '.exe')}")
        return 1

    DEST.mkdir(parents=True, exist_ok=True)
    # dirs_exist_ok：只覆盖、不删除。升级时宁可留几个没用的旧文件，
    # 也不要在站主的盘上做递归删除 —— 这类操作出过一次事就够了。
    shutil.copytree(built, DEST, dirs_exist_ok=True)

    for extra in ("config.json",):
        src = HERE / extra
        if src.is_file():
            shutil.copy2(src, DEST / extra)
            print(f"  带上 {extra}")
        else:
            print("  ⚠ 没有 config.json（第一次装？）—— 启动后在「设置」里填一次就行")

    # 说明书：从 UTF-8 源现转成 GBK + CRLF（**不要**直接拷，否则源改了这里不会变）
    try:
        for note in gen_readme():
            print("  " + note)
    except Exception as e:                                    # noqa: BLE001
        print(f"  ⚠ 说明书没生成：{e}")

    # ① 完整性标签归位（正常情况这里没事 —— 新装的目录本来继承的就是 Medium，
    #    这一步是防"目标目录哪天又被打了 Low 标签"的兜底）
    for note in filter(None, (force_medium(DEST, recursive=True),
                              force_medium(EXE))):
        print(f"  ⚠ 完整性标签不正，已改回 Medium：{note}")
    label = integrity_label(EXE)
    print(f"  安装位置：{DEST}")
    print(f"  exe 完整性标签：{label}")

    # ② 快捷方式（桌面 + 开始菜单）
    for human, lnk in (("桌面", desktop() / f"{APP_NAME}.lnk"),
                       ("开始菜单", start_menu() / f"{APP_NAME}.lnk")):
        create_lnk(EXE, lnk, DEST, f"{APP_NAME} · 北杉札记")
        print(f"  快捷方式：{human}  {lnk}")

    # ③ 旧的 .bat 与旧安装位置：能请走就请走，请不动只提示（不影响用）
    for old in (desktop() / f"{APP_NAME}.bat", Path(r"D:\router") / APP_NAME):
        if old.exists():
            print(("  旧的已移入回收站：" if recycle(old)
                   else "  ⚠ 请不动（`D:\\router` 上有 DENY DELETE_CHILD），"
                        "想清掉请手动删：") + str(old))

    # ④ ★★ 门闩：真跑一次。不过就不许说"打好了"。
    print("  自检中（起服务 → /api/health）…")
    ok, why = smoke_test(EXE)
    print(("  ✔ 自检通过：" if ok else "  ✘ 自检没过：") + why)

    reclaim(distpath, workpath)

    size = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
    print()
    if not ok:
        print(f"✘ exe 打出来了但**起不来**，别用：{EXE}")
        print(f"  现场留在 {DEST / '_自检日志.txt'}；对照 说明.txt 里的「出问题时的自查顺序」。")
        return 1
    print(f"✔ 打好了并且自检跑通：{EXE}")
    print(f"  应用目录 {size / 1024 / 1024:.0f} MB（{DEST}）")
    print("  双击桌面上的「写作台」就能开 —— 没有脚本，没有命令行窗口。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
