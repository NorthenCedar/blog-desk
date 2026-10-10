# -*- coding: utf-8 -*-
"""生成写作台的启动器与说明（.bat 必须 GBK + CRLF，否则 cmd 里中文乱码）。

★ 三条硬要求（踩过的坑，见站主的工作笔记）：
  ① 中文一律 GBK 编码（chcp 936）—— UTF-8 在 cmd 里是乱码；
  ② 外部命令写绝对路径 —— 只写 `find` 会被 Git Bash 的 GNU find 顶掉；
     这里只用到 cmd 内建的 `start`/`chcp`/`cd`，以及绝对路径的 python，安全；
  ③ 行尾 CRLF。
"""
from pathlib import Path
import os
import sys

# 路径一律现算，不写死某个人的机器布局：
#   APPDIR   —— 脚本自身所在目录（也就是仓库根）
#   DESKTOP  —— 走环境变量，OneDrive 重定向的机器上 `%USERPROFILE%\Desktop` 不存在
#   PYDIR    —— 找当前解释器的 Scripts 目录，跟跑这个脚本的 Python 保持一致
APPDIR = Path(__file__).resolve().parent
# 桌面目录不能假设是 %USERPROFILE%\Desktop：这台机器的桌面被 OneDrive 重定向过
# （真实位置在 OneDrive 下），写死前者会直接跳过桌面快捷方式的生成。
# 依次问系统环境变量、再退到 OneDrive 常见目录，最后才是传统位置。
def _find_desktop() -> Path:
    candidates = []
    if os.environ.get("OneDrive"):
        candidates.append(Path(os.environ["OneDrive"]) / "Desktop")
    candidates.append(Path(os.environ.get("USERPROFILE", str(APPDIR))) / "Desktop")
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[-1]

DESKTOP = _find_desktop()
PYDIR = Path(sys.executable).resolve().parent
PY = Path(sys.executable).resolve()
PYW = PYDIR / "pythonw.exe"

HEAD = "@echo off\nchcp 936 >nul\n"

LAUNCH_MAIN = HEAD + f"""rem ══════════════════════════════════════════════════════════════
rem 写作台 · 启动器（不带控制台窗口，像桌面应用那样）
rem 出错了看不到提示 —— 所以输出都写进「运行日志.txt」，起不来先看那个。
rem ══════════════════════════════════════════════════════════════
setlocal
set "APPDIR=%~dp0"
set "PYW={PYW}"
set "PY={PY}"
if not exist "%APPDIR%app.py" goto :noapp
if not exist "%PYW%" set "PYW=%PY%"
if not exist "%PYW%" goto :nopy
start "写作台" "%PYW%" "%APPDIR%app.py" 1>>"%APPDIR%运行日志.txt" 2>&1
exit /b 0

:noapp
echo 找不到 app.py —— 这个文件要放在写作台目录里（现在在 %APPDIR%）。
echo.
pause
exit /b 1

:nopy
echo 找不到 Python：{PYW}
echo 写作台用的那个 Python 环境没有了（或换了路径），要重新装一遍。
echo.
pause
exit /b 1
"""

LAUNCH_DEBUG = HEAD + """rem ══════════════════════════════════════════════════════════════
rem 写作台 · 带控制台的启动器（调试用）
rem 窗口起不来、或者想看到服务端的实时输出时，用这个。
rem ══════════════════════════════════════════════════════════════
setlocal
set "APPDIR=%~dp0"
set "PY=""" + str(PY) + """
cd /d "%APPDIR%"
echo 写作台 · 正在启动……
echo.
echo （关掉这个窗口就是退出写作台）
echo.
"%PY%" app.py
set "RC=%ERRORLEVEL%"
echo.
echo —— 已退出（代码 %RC%）——
if not "%RC%"=="0" echo 上面就是报错内容，把它发给帮你搭这个应用的人。
echo.
pause
"""

LAUNCH_DESKTOP = HEAD + f"""rem 桌面快捷方式：直接起写作台（真正干活的那个 bat 在写作台目录里）
set "APPDIR={APPDIR}\\"
set "PYW={PYW}"
set "PY={PY}"
if not exist "%APPDIR%app.py" goto :noapp
if not exist "%PYW%" set "PYW=%PY%"
start "写作台" "%PYW%" "%APPDIR%app.py" 1>>"%APPDIR%运行日志.txt" 2>&1
exit /b 0

:noapp
echo 找不到写作台：{APPDIR}
echo 应用目录被移走或改名了。
echo.
pause
exit /b 1
"""

README = """写作台 · 使用说明
════════════════════════════════════════════════════════════════════

【怎么启动】
  双击桌面上的「写作台」，或者双击本目录里的「写作台.bat」。
  起不来 / 想看到实时输出，就用「写作台（调试）.bat」（带控制台）。
  出错的线索都在本目录的「运行日志.txt」里。

【它是什么】
  一个给「北杉札记」用的本地编辑器。写完点「发布 → 构建 + 提交 + 推送」，
  推到 main 就等于发布（Cloudflare 会在 1–3 分钟内自动部署）。

【能干什么】
  · 写作   新建/修改/删除文章，选标签，插图片，Markdown 预览
           （文章存在 content/posts/<slug>/，图片和正文放同一个目录）
  · 说说   content/pages/talks.md；配图存进 src/assets/talks/
  · 日志   content/pages/log.md
  · 相册   看图、右键复制引用（大图 1280 / 缩略 800）、上传新图到相册
  · 留言   读 / 删线上的留言（需要先在「设置」里配管理密钥）
  · 发布   跑 npm run verify（构建 + 测试），过了才提交、才推

【几个必须知道的事】
  1. 推送走的是 GitHub API，不是 git push
     —— 本机到 github.com 的常规通道不通。
     后果：远端那个提交的 sha 和本地不一样，**内容完全相同**。
     「发布」页会明确写出这一点，不用疑心。
  2. 删留言要在「设置」里填 ADMIN_TOKEN，值和站点环境变量必须是同一个。
     密钥只存在本机的 config.json 里，不会回传给界面。
  3. 「设置」里的仓库路径改了要重开一次应用。
  4. 界面配色和字体是从站上的 src/styles/tokens.css 镜像过来的。
     博客那边改了 token，这个应用的 ui/style.css 也要跟着改，否则两边会走形。
  5. 相册新加的图，**还要把文件名写进该相册的 index.md**（含 alt），
     不然不会出现在页面上 —— 这是站上的内容模型决定的，不是 bug。

【出问题时的自查顺序】
  ① 看「运行日志.txt」有没有报错；
  ② 「设置」里仓库路径对不对（要指向你那个含 content\\ 的博客仓库目录）；
  ③ 界面右下角/侧栏的状态：显示「远端没问到」说明网络或凭据有问题；
  ④ 用「写作台（调试）.bat」启动，看控制台输出。
"""


def write_gbk(path: Path, text: str):
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk"))


def main():
    targets = [
        (APPDIR / "写作台.bat", LAUNCH_MAIN, "启动器（无控制台）"),
        (APPDIR / "写作台（调试）.bat", LAUNCH_DEBUG, "启动器（带控制台）"),
        (APPDIR / "说明.txt", README, "使用说明"),
    ]
    for p, text, label in targets:
        write_gbk(p, text)
        print(f"  写好 {p}  ({label}, {p.stat().st_size} 字节)")

    if DESKTOP.is_dir():
        p = DESKTOP / "写作台.bat"
        write_gbk(p, LAUNCH_DESKTOP)
        print(f"  写好 {p}  (桌面快捷方式, {p.stat().st_size} 字节)")
    else:
        print(f"  ✘ 桌面目录不存在，跳过：{DESKTOP}")


if __name__ == "__main__":
    main()
