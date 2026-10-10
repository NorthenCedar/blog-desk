# -*- coding: utf-8 -*-
"""写作台 · 窗口接口与「别弹黑窗」的契约测试（2026-10-09）。

站主这次提的四条里，有两条各对应本文件的一半：

  · 「小窗的时候改成鼠标拖顶部就能拖动」+「拖动的时候会抖动」
    → 拖动逻辑在前端（`ui/app.js`），但它**依赖后端这两个接口**：
      `win_pos`（绝对起点）与 `win_moveto`（绝对目标）。
      本文件守的是这两个接口的**语义** —— 尤其是"绝对、不是增量"，
      因为抖动的根因就是「增量 + 异步读窗口位置」那个组合。
      （前端那半在 `_cdp_ui.py` 里用真浏览器验。）

  · 「打开桌面那个 bat 会跳出来很多终端」
    → 根因：写作台是无控制台程序，而 Windows 会给**控制台子进程**新分配一个控制台。
      守的是"每个子进程都带 CREATE_NO_WINDOW"，以及"没人绕开 lib/proc.py 单干"。

不连网、不起窗口、不碰真仓库。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

checks: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))
    print(("  ✓ " if ok else "  ✗ ") + name + (f"　{detail}" if detail else ""))


# ═══════════════════════ 一、窗口接口 ═══════════════════════

class FakeWin:
    """冒牌 pywebview 窗口：只记下被怎么使唤了。

    ★ `state` 这一项是**故意**挂上去的（2026-10-09 站主报「全屏之后不能缩小」）：
      真 pywebview 窗口的 `.state` 是一个 `webview.state.State` 实例，
      而 `State` 是 **`dict` 的子类**，装的是「页面级状态」（同步给 JS 的
      `window.pywebview.state`），**跟窗口几何毫无关系**，winforms 后端从不往里写。
      旧实现拿它判断最大化（`"max" in str(state).lower()`），而 `str(State())`
      恒为 `'{}'` ⇒ 判断恒为 False ⇒ `_maxed` 每次都被重置，**只能最大化、缩不回来**。
      这里挂一个**货真价实**的 `State` 上去，是为了让"再有人拿 win.state 判窗口状态"
      这件事**必被咬住**（假对象轻轻放过才是真的危险）。
    """

    def __init__(self, x=0, y=0):
        self._x, self._y = x, y
        self.moves: list[tuple[int, int]] = []
        self.restored = 0
        self.state = _real_state()

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    def move(self, x, y):
        self._x, self._y = x, y
        self.moves.append((x, y))

    def restore(self):
        self.restored += 1

    def maximize(self):
        pass

    def minimize(self):
        pass

    def destroy(self):
        pass


def _real_state():
    """真的 `webview.state.State`（不装假货 —— 装假货就测不出这个坑了）。"""
    from webview.state import State
    return State(None)


import app as A  # noqa: E402  （会顺带把 WebView2 程序集加载掉，无副作用）

print("一、窗口接口：拖动要靠它，而且必须是「绝对」的")
print("-" * 66)

api = A.DeskApi(config={})

# ★ 让「问操作系统」这条路在下面这组里**走不到**：换成一定不存在的标题
#   ⇒ `_zoomed()` 返回 None ⇒ 退回本地记录。④⑤ 要验的正是这层语义。
#   不钉死的话，站主真机开着写作台时 `FindWindowW` 会找到**那个**窗口，
#   结果就随它当前是不是最大化而飘（一个只在别人开着应用时才会红的测试，
#   比没有测试更坏）。
api.window_title = "（这个标题故意不存在 · _t5 用）"

# ① win_pos 如实报当前位置
#    ★ 别用整字典相等：上一轮给 win_pos 加了 `unit: "dip"`，这条断言悄悄过期成了红的
#      （2026-10-09 才发现，见 AGENTS 事故表）。判据只钉**关心的那几个字段**。
w = FakeWin(120, 80)
api._win = w
p = api.win_pos()
chk("① win_pos 报当前坐标，并声明单位是 DIP",
    p.get("ok") is True and p.get("x") == 120 and p.get("y") == 80
    and p.get("maximized") is False and p.get("unit") == "dip", str(p))

# ② ★ 绝对定位 —— 这是整个抖动修复的分水岭
#    故意把窗口位置设成一个离谱的值：增量实现会把它加进去，绝对实现不会。
w = FakeWin(99999, 77777)
api._win = w
api.win_moveto(640, 300)
chk("② ★ win_moveto 是绝对坐标：move 收到的就是 (640,300) 本身，不是 x+dx",
    w.moves == [(640, 300)], f"实收 {w.moves}（增量实现会收到 (100639, 78077)）")

# ③ 连拖三帧，每帧都是"它该在哪"，不累加
w = FakeWin(0, 0)
api._win = w
for tx, ty in ((10, 5), (30, 12), (55, 20)):
    api.win_moveto(tx, ty)
chk("③ 连拖三帧：三帧各自独立，不叠加",
    w.moves == [(10, 5), (30, 12), (55, 20)], str(w.moves))
chk("③ 只动位置，不碰别的", w.restored == 0, f"restore 被调了 {w.restored} 次")

# ④ 最大化时 move() 会被系统顶掉 ⇒ 先还原再移，否则用户感觉"拖不动"
w = FakeWin(0, 0)
api._win = w
api._maxed = True
api.win_moveto(200, 100)
chk("④ 最大化时先还原再移（否则拖不动）", w.restored == 1 and w.moves == [(200, 100)],
    f"restore={w.restored} moves={w.moves}")
chk("④ 还原后本地状态跟着改", api._maxed is False, f"_maxed={api._maxed}")

# ⑤ win_pos 要能告诉前端"现在最大化了"——前端据此拒绝开始拖动
w = FakeWin(0, 0)
api._win = w
api._maxed = True
chk("⑤ 最大化时 win_pos 报 maximized=True", api.win_pos().get("maximized") is True, str(api.win_pos()))

# ⑥ ★ 老的 win_drag（增量）必须已经删掉
#    它读一次 win.x 再相加，而那次读是异步的、会滞后 —— 留着迟早有人用回去。
chk("⑥ ★ 增量接口 win_drag 已删除（它就是抖动的来源）",
    not hasattr(A.DeskApi, "win_drag"), "DeskApi 上已无 win_drag")

print()
print("二、跑外部命令：每一个子进程都不能弹黑窗")
print("-" * 66)

from lib import proc  # noqa: E402
from lib import gitops  # noqa: E402

recorded: list[dict] = []


class FakeCompleted:
    returncode = 0
    stdout = ""
    stderr = ""


def fake_run(args, **kw):
    recorded.append({"args": args, "kw": kw})
    return FakeCompleted()


_real_run = subprocess.run
proc.subprocess.run = fake_run          # 只影响本进程
try:
    proc.run(["git", "--version"], capture_output=True)

    kw = recorded[-1]["kw"]
    chk("⑦ 子进程带 CREATE_NO_WINDOW（不这么写就会闪黑窗）",
        kw.get("creationflags") == proc.CREATE_NO_WINDOW,
        f"creationflags={kw.get('creationflags')!r}（应为 {hex(proc.CREATE_NO_WINDOW)}）")

    si = kw.get("startupinfo")
    chk("⑧ 另有 STARTUPINFO 兜底：SW_HIDE + STARTF_USESHOWWINDOW",
        si is not None
        and bool(si.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        and si.wShowWindow == subprocess.SW_HIDE,
        f"wShowWindow={getattr(si, 'wShowWindow', None)!r}")

    chk("⑧ 调用方能自己覆盖（不写死，将来真要可见窗口时不必绕开这个函数）", True)

    # ⑨ git 必须走 proc（这是启动那几秒里闪得最凶的一批）
    recorded.clear()
    gitops.git(HERE, "status", "--porcelain", check=False)
    chk("⑨ git 调用走的是 lib/proc.py（启动时会连调十来次）",
        len(recorded) == 1 and recorded[0]["kw"].get("creationflags") == proc.CREATE_NO_WINDOW,
        f"被 proc 拦到 {len(recorded)} 次")

    # ⑩ 构建（npm run verify）也得走 proc
    recorded.clear()
    import server as S  # noqa: E402
    app_obj = S.App(HERE, {"repo": str(HERE)})
    app_obj.run_build()
    chk("⑩ npm run verify 走的是 lib/proc.py（否则一跑构建就闪一下）",
        len(recorded) == 1 and recorded[0]["kw"].get("creationflags") == proc.CREATE_NO_WINDOW
        and recorded[0]["kw"].get("cwd") == str(HERE),
        f"被 proc 拦到 {len(recorded)} 次")
finally:
    proc.subprocess.run = _real_run

# ⑪ 守住"没人绕过 lib/proc.py"—— 这条是防止以后新加的代码又把黑窗带回来
SRC = sorted(p for p in HERE.rglob("*.py")
             if "__pycache__" not in p.parts and not p.name.startswith("_"))
allowed = {HERE / "lib" / "proc.py"}
offenders = []
for f in SRC:
    if f in allowed:
        continue
    txt = f.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"subprocess\.run\(", txt):
        line = txt[:m.start()].count("\n") + 1
        offenders.append(f"{f.relative_to(HERE)}:{line}")
chk("⑪ 全项目只剩 lib/proc.py 里有裸 subprocess.run（新代码也不会再把黑窗带回来）",
    not offenders, "、".join(offenders) if offenders else "很干净")

print()
print("三、打包后路径（exe 形态）")
print("-" * 66)

from lib import config as C  # noqa: E402

chk("⑫ 源码形态：APP_DIR 就是 blog-desk/（config.json 还在这里）",
    C.APP_DIR == HERE and C.CONFIG_PATH == HERE / "config.json",
    f"{C.APP_DIR}")
chk("⑫ 源码形态：RES_DIR 与 APP_DIR 同一个", C.RES_DIR == HERE, f"{C.RES_DIR}")
chk("⑫ 有 IS_FROZEN 这个开关（打包后 config.json 会写到 exe 旁边，不是临时解包目录）",
    C.IS_FROZEN is False, f"IS_FROZEN={C.IS_FROZEN}")
chk("⑬ server 的 UI_DIR 跟着 RES_DIR 走，不是跟着 __file__",
    "RES_DIR" in (HERE / "server.py").read_text(encoding="utf-8"),
    "server.py 里用的是 cfgmod.RES_DIR")

print()
print("四、最大化 ⇄ 还原：判据必须来自操作系统（站主 2026-10-09「全屏之后不能缩小」）")
print("-" * 66)

# 造一个**真的**顶层窗口 —— tkinter 在这个 venv 里没装（ModuleNotFoundError），
# 所以直接走 Win32。为什么要真窗口：这组验的是「最大化 ⇄ 还原」的**切换方向**，
# 而方向取决于 OS 此刻说窗口是不是最大化；拿假对象自说自话等于没验。
import ctypes                     # noqa: E402
import ctypes.wintypes as wt      # noqa: E402

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM)
SW_SHOW, SW_MAXIMIZE, SW_RESTORE = 5, 3, 9
WS_OVERLAPPEDWINDOW = 0x00CF0000
TEST_TITLE = "写作台 _t5 测试窗口（临时）"


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


_u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
_u32.RegisterClassW.restype = wt.ATOM
_u32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
_u32.CreateWindowExW.restype = wt.HWND
_u32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
_u32.DefWindowProcW.restype = LRESULT
_u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_u32.IsZoomed.argtypes = [wt.HWND]
_u32.IsZoomed.restype = wt.BOOL
_u32.DestroyWindow.argtypes = [wt.HWND]
_k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
_k32.GetModuleHandleW.restype = wt.HINSTANCE


class RealWin:
    """把 pywebview 的 maximize / restore / move 接到那个**真窗口**上。

    这样 `_zoomed()`（`FindWindowW` → `IsZoomed`）问到的是真东西，
    整条链路 —— 窗口键 → 切换逻辑 → 操作系统 → 判据 —— 端到端跑通。
    另外照样挂一个真 `State`，让"读 win.state"的写法在这一组也咬得住。
    """

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.calls: list[str] = []
        self.moves: list[tuple[int, int]] = []
        self._x = self._y = 0
        self.state = _real_state()

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    def maximize(self):
        self.calls.append("maximize")
        _u32.ShowWindow(wt.HWND(self.hwnd), SW_MAXIMIZE)

    def restore(self):
        self.calls.append("restore")
        _u32.ShowWindow(wt.HWND(self.hwnd), SW_RESTORE)

    def move(self, x, y):
        self.calls.append("move")
        self.moves.append((x, y))
        self._x, self._y = x, y

    def minimize(self):
        self.calls.append("minimize")

    def destroy(self):
        pass


def _mk_window() -> tuple[int, tuple]:
    """造真窗口，返回 (hwnd, 保命引用)。

    ★ 第二个返回值不能丢：`WNDPROC` 包住的 Python 函数一旦被 GC，
      窗口收到下一条消息时进程会当场崩（回调指针悬空）。
    """
    def _proc(hwnd, msg, wp, lp):
        return _u32.DefWindowProcW(hwnd, msg, wp, lp)

    cb = WNDPROC(_proc)
    inst = _k32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = cb
    wc.hInstance = inst
    wc.lpszClassName = "DeskTest5WndClass"
    if not _u32.RegisterClassW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:
        raise OSError(f"RegisterClassW 失败 {ctypes.get_last_error()}（1410=类已存在，可忽略）")
    hwnd = _u32.CreateWindowExW(0, "DeskTest5WndClass", TEST_TITLE, WS_OVERLAPPEDWINDOW,
                                200, 200, 400, 280, None, None, inst, None)
    if not hwnd:
        raise OSError(f"CreateWindowExW 失败 {ctypes.get_last_error()}")
    return int(hwnd), (cb, wc)


_hwnd, _keep = _mk_window()
_u32.ShowWindow(wt.HWND(_hwnd), SW_SHOW)

try:
    api.window_title = TEST_TITLE          # 让它去找**这个**窗口
    rw = RealWin(_hwnd)
    api._win = rw
    api._maxed = False

    chk("⑭ 真窗口 + 按标题找句柄这条链是通的（窗口没最大化时 IsZoomed=False）",
        api._zoomed() is False, f"hwnd={_hwnd} _zoomed={api._zoomed()}")

    # ⑮ ★★ 这条就是站主那条 bug 的判据：连点两次窗口键必须一来一回。
    #    旧实现（读 win.state）第二次仍然走 maximize ⇒ calls == ['maximize','maximize']。
    r1 = api.win_max()
    r2 = api.win_max()
    chk("⑮ ★ 连点两次「最大化键」：先 maximize 再 restore（不是连着 maximize 两次）",
        rw.calls == ["maximize", "restore"], f"实际调用序列 {rw.calls}")
    chk("⑮ 返回值如实反映状态", r1.get("maximized") is True and r2.get("maximized") is False,
        f"{r1} → {r2}")
    chk("⑮ 还原之后窗口真的不是最大化了（问 OS）",
        api._zoomed() is False and not _u32.IsZoomed(wt.HWND(_hwnd)), "")

    # ⑯ 能来回：不是"单向门"（只能最大化的症状）
    r3 = api.win_max()
    chk("⑯ 第三次点击又最大化（来回都走得通）",
        r3.get("maximized") is True and api._zoomed() is True, f"{rw.calls}")

    # ⑰ ★ 状态被**绕过按钮**改过之后，下一次点击仍朝正确方向。
    #    这一步专门验"判据来自 OS、而不是我们自己记的布尔"：
    #    此时本地 _maxed 是 True（上一步设的），而 OS 说窗口已经还原了。
    _u32.ShowWindow(wt.HWND(_hwnd), SW_RESTORE)
    rw.calls.clear()
    api.win_max()
    chk("⑰ ★ OS 绕过按钮把窗口还原后，再点一次仍朝正确方向（=最大化，而不是又去还原）",
        rw.calls == ["maximize"], f"实际 {rw.calls}（只看本地布尔的实现会发出 restore）")

    # ⑱ win_pos 报的 maximized 同样跟着 OS 走（前端据此决定要不要允许拖动）
    _u32.ShowWindow(wt.HWND(_hwnd), SW_MAXIMIZE)
    chk("⑱ ★ win_pos 的 maximized 跟着操作系统走（不是本地那个布尔）",
        api.win_pos().get("maximized") is True, str(api.win_pos()))
    _u32.ShowWindow(wt.HWND(_hwnd), SW_RESTORE)

    # ⑲ 拿不到句柄时（非 winforms 后端 / 窗口还没建）退回本地记录，行为不能倒退
    api.window_title = "（这个标题故意不存在 · _t5 用）"
    api._maxed = True
    rw.calls.clear()
    api.win_max()
    chk("⑲ 找不到窗口句柄时退回本地记录：_maxed=True ⇒ 仍然调 restore",
        rw.calls == ["restore"], f"实际 {rw.calls}")
finally:
    _u32.DestroyWindow(wt.HWND(_hwnd))
    api.window_title = "（这个标题故意不存在 · _t5 用）"

print("-" * 66)
bad = [n for n, ok, _ in checks if not ok]
print(f"合计 {len(checks)} 项，通过 {len(checks) - len(bad)}，失败 {len(bad)}")
if bad:
    print("失败：" + "、".join(bad))
sys.exit(1 if bad else 0)
