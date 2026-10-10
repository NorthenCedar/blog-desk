# -*- coding: utf-8 -*-
"""写作台 · 启动器。

用法（正常不用手打，双击桌面的「写作台.bat」就行）：
    python app.py [--browser] [--port N] [--repo <博客仓库路径>]

    --repo 省略时依次取：config.json 的 repo → 环境变量 BLOG_REPO → 空
    （空会明确提示去「设置」里填，不会拿一个猜的路径去动文件）

════════════════════════════════════════════════════════════════════════
这个文件存在的理由：本机 pywebview 起窗需要一个**手工前置步骤**
════════════════════════════════════════════════════════════════════════
实测（2026-10-09）：在 import webview 之前，必须用 `clr.AddReference` 手工加载
`Microsoft.Web.WebView2.Core.dll` 与 `Microsoft.Web.WebView2.WinForms.dll`
两个程序集；否则 `webview.start()` 会在内部等 20 秒然后抛
`WebViewException: Main window failed to start`（`BrowserProcessId` 为 None）。
⇒ 这两行必须在本文件**最顶部**，早于任何 webview 的导入。别挪，别删。

★ 回退链：真窗口起不来时（WebView2 被占 / 缺运行时 / 无桌面会话），
  自动退回**用系统浏览器打开同一个本地地址**。功能一个不少，只是少了三个窗口键。
  —— 宁可降级也不要黑屏，这是这个启动器的第一原则。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

try:
    HERE = Path(__file__).resolve().parent
except NameError:                    # 打包后 __file__ 不保证有，退到 exe 所在目录
    HERE = Path(sys.executable).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# 配置读写统一走 lib/config.py（唯一事实来源；见该文件头部说明）
from lib.config import load_config, save_config   # noqa: E402

def _webview_lib_dir():
    """webview 包里放 WebView2 那两个 DLL 的目录 —— **不导入 webview**。

    ★ 为什么绕这一下：那两个 DLL 必须在 `import webview` **之前**加载好（见文件头），
      所以不能 `import webview` 去问它自己在哪。用 importlib 只看包在哪、不执行它。
      好处是源码跑和打包成 exe 两种形态都对 —— 打包后包在临时解包目录里，
      路径与源码形态完全不同，写死 `sys.prefix/...` 在 exe 里必然找不到。
    """
    try:
        spec = importlib.util.find_spec("webview")
    except Exception:  # noqa: BLE001
        return None
    for loc in list(getattr(spec, "submodule_search_locations", None) or []):
        d = Path(loc) / "lib"
        if d.is_dir():
            return d
    d = Path(sys.prefix) / "Lib" / "site-packages" / "webview" / "lib"   # 兜底：老写法
    return d if d.is_dir() else None


# ── ★★ 这两行必须在 import webview 之前（见文件头）。缺了 = 永远起不来。 ──
_VIEW2_READY = False
try:
    import clr  # type: ignore

    _LIBDIR = _webview_lib_dir()
    if _LIBDIR:
        for _name in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll"):
            _dll = _LIBDIR / _name
            if _dll.is_file():
                clr.AddReference(str(_dll))
    _VIEW2_READY = True
except Exception:  # noqa: BLE001
    _VIEW2_READY = False
# ──────────────────────────────────────────────────────────────────────

# 窗口标题。**只有一处定义** —— `FindWindowW` 靠它找本窗口的原生句柄（见 `_find_hwnd`）。
WINDOW_TITLE = "写作台 · 北杉札记"


def _user32():
    """user32 的句柄（只在 Windows 上有；每次拿同一份，argtypes 只设一遍）。"""
    u = getattr(_user32, "_h", None)
    if u is None:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        u.FindWindowW.restype = wt.HWND
        u.IsZoomed.argtypes = [wt.HWND]
        u.IsZoomed.restype = wt.BOOL
        _user32._h = u
    return u


def _find_hwnd(title: str) -> int:
    """按标题找本窗口的**原生句柄**（纯 Win32，不碰 .NET）。找不到返回 0。

    ★ 为什么不走 `win.native.Handle`（pywebview 在 winforms 后端把那个 WinForms Form
      挂在 Window 的 `native` 上）：读控件的 `Handle` 是**跨线程访问控件** ——
      我们是从 JS 桥的线程调过来的，跟 UI 线程不是同一个。WinForms 对这类访问的
      行为取决于静态开关 `Control.CheckForIllegalCrossThreadCalls`
      （调试器挂着时它是 True ⇒ 直接抛异常）。**不该赌。**
      `FindWindowW` 是纯 Win32 调用，没有线程亲和，问什么答什么。
    """
    try:
        import ctypes.wintypes as wt
        return int(_user32().FindWindowW(None, title) or 0)
    except Exception:  # noqa: BLE001
        return 0


class DeskApi:
    """暴露给页面的 JS 桥（`window.pywebview.api.*`）。

    ★★ **窗口引用必须挂在 `_win`（下划线开头），绝不能叫 `win`** ——
      2026-10-09 站主报「刚启动的时候点它有几率卡死」，根因就在这里：

      pywebview 注入 JS 桥时，会在**一个新建的非 UI 线程**里遍历这个对象
      （`webview/util.py` 的 `inject_pywebview` → `get_functions`）：

          for name in dir(obj):
              if name.startswith('_'):            # ← 它自己跳过下划线开头的
                  continue
              attr = getattr(obj, name)
              ...
              elif inspect.isclass(attr) or (isinstance(attr, object)
                                             and not callable(attr)
                                             and hasattr(attr, '__module__')):
                  get_functions(attr, ...)        # ★ 递归下去

      而我们以前把 **pywebview 的 Window 对象**挂在 `self.win` 上 ⇒ 它被递归遍历
      ⇒ 一路摸到 .NET 的 WebView2 控件（`win.native.ActiveControl.CoreWebView2`、
      `win.native.AccessibilityObject.Bounds.Empty.Empty…`）⇒
      ① 非 UI 线程访问控件（日志里 69 次 `CoreWebView2 can only be accessed from
      the UI thread`）② 递归爆栈（16 次 `maximum recursion depth exceeded`）。

      ⇒ `_win` 的下划线不是命名风格，是**判据**：下划线开头的名字 pywebview
        那一行 `if name.startswith('_')` 会直接跳过，所以它摸不到我们的窗口，
        也就不会去摸 .NET 控件。**别"顺手"改回`win`。**
    """

    #: 靠这个标题找窗口句柄。测试会把它换成自己造的那个真窗口的标题。
    window_title = WINDOW_TITLE
    #: 拿句柄的方式。换成别的实现即可在测试/别的后端里替换（默认是 FindWindowW）。
    find_hwnd = staticmethod(_find_hwnd)

    def __init__(self, win=None, config=None):
        self._win = win            # ★ 见类文档：下划线是给 pywebview 的判据用的
        self.config = config or {}
        self._maxed = False          # 自己记最大化状态（不同 pywebview 版本不一定有 win.state）

    # ---- 三个窗口键 ----
    def _zoomed(self):
        """窗口现在是不是**最大化** —— 问操作系统。拿不到句柄时返回 None。

        ★★★ 这里曾经有个把「最大化」做成单向门的 bug（2026-10-09 站主报
          「全屏之后不能缩小」），根因值得原样记下来：

          pywebview 的 `Window.state` **不是窗口的几何状态**。它是一个
          `webview.state.State` 实例，而 `State` 是 **`dict` 的子类**，
          用途是把**页面级状态**同步给 JS（`window.pywebview.state`，还有个
          `webview.state.StateEventType` 配套）—— 跟窗口在哪、多大、是不是最大化
          **毫无关系**，winforms 后端从头到尾没往它里面写过东西。

          于是 `str(state)` 恒等于 `'{}'`，原来是这么写的：

              self._maxed = "max" in str(state).lower()      # ← 恒为 False

          每点一次都把 `_maxed` 重置成 False，接着必然走 `maximize()`：
          **能最大化，永远还原不了**，与站主描述逐字相符。
          而这个坑的形态很典型 —— **属性名对得上，语义完全不是一回事**。
          ⇒ 教训：**状态的真相要向产生它的那一层要**（这里是操作系统），
            别信一个"名字像是它"的属性。

        `IsZoomed` 是 OS 自己对"这个窗口是不是最大化"的定义：`Win+↑` 之类
        绕过我们按钮的路径它照样看得见，比我们自己记一个布尔更稳。
        """
        try:
            hwnd = int(self.find_hwnd(self.window_title) or 0)
        except Exception:  # noqa: BLE001
            hwnd = 0
        if not hwnd:
            return None                      # 非 winforms 后端 / 窗口还没建 ⇒ 退回本地记录
        try:
            import ctypes.wintypes as wt
            return bool(_user32().IsZoomed(wt.HWND(hwnd)))
        except Exception:  # noqa: BLE001
            return None

    def _is_maxed(self) -> bool:
        """当前是不是最大化：优先问 OS，问不到就用本地记录。"""
        z = self._zoomed()
        return self._maxed if z is None else z

    def win_min(self):
        try:
            self._win.minimize()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def win_max(self):
        """最大化 ⇄ 还原（点一下切换）。

        ★ 判据是 `_zoomed()`（问操作系统），不是 `win.state`（那不是窗口状态，见那里的说明）。
          判反了会变成单向门：只能最大化、永远缩不回来。
        """
        try:
            maxed = self._is_maxed()
            if maxed:
                self._win.restore()
            else:
                self._win.maximize()
            self._maxed = not maxed
            return {"ok": True, "maximized": self._maxed}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def win_close(self):
        try:
            self._win.destroy()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def win_pos(self):
        """窗口左上角现在在哪（**逻辑像素 / DIP**）+ 是不是最大化。拖动要用它当**绝对**起点。

        ★ 单位是**逻辑像素**，不是物理像素 —— 别按屏幕缩放比再换算一次。
          pywebview 在 `get_position()` 里做的就是 `int(i.Left / scale)`
          （`webview/platforms/winforms.py`），也就是已经把物理像素除回逻辑像素了；
          而写回去的 `Form.move()` 注释原文是 "Input x/y are in logical pixels"，
          内部再乘一次 scale。**两头都是逻辑像素，自洽**。
          页面那边 `MouseEvent.screenX` 是 CSS px，Windows 下 1 CSS px ≡ 1 DIP
          ⇒ 两边同一个单位，直接相减即可。
          （2026-10-09 站主报「拖动不跟鼠标，会比鼠标快一点」，真因就是上一版
            在这条注释的错误引导下多乘了一次 devicePixelRatio：本机 150% ⇒ 快 1.5 倍。）
        """
        try:
            return {"ok": True, "x": int(self._win.x), "y": int(self._win.y),
                    "unit": "dip",
                    "maximized": bool(self._is_maxed())}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "maximized": bool(self._is_maxed())}

    def win_moveto(self, x, y):
        """把窗口**绝对**移到 (x, y)。x/y 是**逻辑像素（DIP）**（同 win_pos，见那里的注释）。

        ★ 为什么不留老那套 `win_drag(dx, dy)` 的增量接口（2026-10-09 站主报"拖动时发抖"）：
          增量得先读一次 `win.x` 再相加，而那次读是异步的、会滞后；
          滞后一次的误差被叠加几十次，就是肉眼可见的漂移与抖动。
          绝对坐标没有这个问题 —— 每一帧都把"它该在哪"重新说一遍，
          丢一帧只是慢一拍，不会累积。页面那边也已改用 screenX/screenY 算，
          与这里配套（细节见 ui/app.js 里那段注释）。
        """
        try:
            if self._is_maxed():
                # 最大化时系统占着窗口，move() 不生效 —— 先还原再移，免得"拖不动"
                # 判据同样走 _is_maxed()：状态被 Win+↑ 之类改过时，本地布尔会是错的。
                self._win.restore()
                self._maxed = False
            self._win.move(int(x), int(y))
            return {"ok": True, "x": int(x), "y": int(y)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ---- 配置 ----
    def get_config(self):
        c = dict(self.config)
        c.pop("admin_token", None)          # 密钥不回传给页面
        c["has_token"] = bool(self.config.get("admin_token"))
        return c

    def set_config(self, patch: dict):
        self.config.update(patch or {})
        save_config(self.config)
        return {"ok": True}


def open_browser(url: str, delay: float = 0.8):
    def go():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception as e:  # noqa: BLE001
            print(f"[desk] 打开浏览器失败：{e}")
    threading.Thread(target=go, daemon=True).start()


class Fallback:
    """「桌面窗口起不来」的兜底：**只开一次**浏览器，并说清楚为什么。

    ★ 为什么不能只靠 try/except：
      实测 WebView2 不可用时 `webview.start()` 既不抛异常、也不返回 —— 它会**一直挂着**。
      只写 try/except 的话，用户看到的是：没有窗口、没有报错、什么都没有（静默卡死）。
      ⇒ 反过来看服务端有没有收到过请求：页面真起来了，就一定会来取 index.html / api。
    """

    def __init__(self, url: str, app, timeout: float = 25.0):
        self.url = url
        self.app = app
        self.timeout = timeout
        self._done = False
        self._lock = threading.Lock()

    def _fire(self, why: str):
        with self._lock:
            if self._done:
                return
            self._done = True
        print(f"[desk] {why}", flush=True)
        print(f"[desk] 改用浏览器打开：{self.url}", flush=True)
        open_browser(self.url)

    def from_exception(self, exc: BaseException):
        print("[desk] 桌面窗口起不来：", flush=True)
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        self._fire("桌面窗口创建失败。")

    def start_watchdog(self):
        def watch():
            t0 = time.time()
            while time.time() - t0 < self.timeout:
                if self.app.request_count > 0:
                    return          # 有请求 ⇒ 页面真的加载了，窗口是活的
                time.sleep(0.5)
            self._fire(f"{int(self.timeout)} 秒内没有收到任何页面请求，桌面窗口八成没起来。")
        threading.Thread(target=watch, daemon=True).start()


def redirect_output_if_headless():
    """打包成 exe 后是 `--windowed`（没有控制台），`print` 出去的东西**谁都看不到**。

    ⇒ 把 stdout/stderr 接到 exe 旁边的「运行日志.txt」。
      起不来的时候这个文件就是唯一的线索；文件名与开发时那份 .bat 写的是同一个，
      站主已经知道去哪儿看。
      只有在"确实没有控制台"时才接手 —— 用带控制台的调试启动器跑时别抢它的输出。
    """
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from lib.config import APP_DIR
        f = open(APP_DIR / "运行日志.txt", "a", encoding="utf-8", buffering=1)
        f.write("\n" + "═" * 60 + "\n")
        f.write(f"启动 {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        sys.stdout = f
        sys.stderr = f
    except Exception:  # noqa: BLE001
        pass          # 日志写不了也得让程序继续跑


def main():
    redirect_output_if_headless()
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true", help="不用桌面窗口，直接在浏览器里打开")
    ap.add_argument("--no-open", action="store_true",
                    help="只起服务、不打开任何窗口（自检/调试用，配合 --port）")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--repo", default=None)
    args = ap.parse_args()

    cfg = load_config()
    if args.repo:
        cfg["repo"] = args.repo

    # 空路径必须在这里挡掉，不能直接 `Path("")` —— 那会退化成 `Path(".")`，
    # 于是 `(repo / "content").is_dir()` 变成查 `./content`：
    # 在 blog-desk 自己目录下跑恰好没有 content，报错信息会指向一个**看起来像路径的 "."**，
    # 让人以为是仓库配错了，其实是没配。空就是空，说人话。
    if not str(cfg.get("repo") or "").strip():
        print("✘ 还没配博客仓库路径。")
        print("  两种办法任选其一：")
        print("   · 界面版：起一次窗口，在「设置」里填仓库路径（就是含 content/ 的那个目录）")
        print('   · 命令行：python app.py --repo "D:\\path\\to\\your\\blog-repo"')
        print("  也可以先复制 config.example.json 成 config.json 再填。")
        input("按回车退出…")
        return 2

    repo = Path(cfg["repo"])
    if not (repo / "content").is_dir():
        print(f"✘ 找不到博客仓库：{repo}")
        print("  请在 config.json 里把 repo 改成正确路径，或用 --repo 指定。")
        input("按回车退出…")
        return 2

    import server as desk_server   # 延后导入：先保证 WebView2 程序集加载完

    httpd, port, app = desk_server.serve(repo, cfg, port=args.port)
    url = f"http://127.0.0.1:{port}/"
    app.log(f"服务已起：{url}  （仓库 {repo}）")

    if args.no_open:
        # 自检用：只把服务立起来，不开任何窗口（打包之后没有控制台，
        # 这是唯一能用 curl 验「服务/界面/配置都对」的入口）
        print(f"[desk] 服务已起（未开窗口）：{url}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    if args.browser or not _VIEW2_READY:
        print(f"[desk] {'' if _VIEW2_READY else 'WebView2 程序集没就绪 ⇒ '}"
              f"用浏览器打开 {url}")
        open_browser(url)
        print("[desk] 按 Ctrl+C 结束。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    import webview

    # ★ 调试钩子：设了 DESK_CDP_PORT 才开 WebView2 的 CDP 端口（正常启动毫无影响）。
    #   为什么要它：这台机器上"看窗口"没法自动化验收，而 WebView2 支持
    #   `--remote-debugging-port` ⇒ 可以当普通网页连进去**量真实数值**。
    #   2026-10-09 拖动的单位换算（1 CSS px ≡ 1 DIP）就是用这个量出来的，
    #   不是靠读源码猜的 —— 见 docs/rhinelab-spec.md §6.69。
    #   ⚠ pywebview 会**整体覆盖** `AdditionalBrowserArguments`
    #   （platforms/edgechromium.py:82），所以设 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`
    #   这个环境变量是**没用**的，必须走它自己的 settings。
    _cdp = os.environ.get("DESK_CDP_PORT")
    if _cdp:
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(_cdp)
        print(f"[desk] CDP 端口已开：{_cdp}")

    api = DeskApi(config=cfg)
    w = cfg.get("window") or {}
    win = webview.create_window(
        WINDOW_TITLE,             # ← 与 _find_hwnd 找句柄用的是同一个常量，别写第二遍
        url=url,
        js_api=api,
        width=int(w.get("width", 1180)),
        height=int(w.get("height", 760)),
        min_size=(880, 560),
        frameless=True,          # 无边框 ⇒ 页面自己画标题栏与三个窗口键
        easy_drag=False,         # 拖动由页面调 win_pos/win_moveto 做（easy_drag 会吃掉点击）
        background_color="#0b100e",
        text_select=True,
    )
    api._win = win

    fb = Fallback(url, app)
    fb.start_watchdog()          # ★ 先挂看门狗：webview.start() 可能是"静默挂死"，不是抛错
    try:
        webview.start(gui="edgechromium" if _VIEW2_READY else None, debug=False)
        return 0
    except Exception as e:  # noqa: BLE001
        fb.from_exception(e)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
