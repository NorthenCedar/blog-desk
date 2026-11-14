"""Windows 原生窗口的磨砂背景和最大化边界。"""

import ctypes as ct
from ctypes import wintypes as wt
import sys
import winreg


class MonitorInfo(ct.Structure):
    _fields_ = [
        ("size", wt.DWORD), ("monitor", wt.RECT), ("work", wt.RECT), ("flags", wt.DWORD),
    ]


class MinMaxInfo(ct.Structure):
    _fields_ = [(name, wt.POINT) for name in ("reserved", "size", "position", "minimum", "maximum")]


def respect_work_area(hwnd):
    """保持 Windows 的最大化和还原语义，只修正无边框窗口的范围。"""
    user = ct.WinDLL("user32", use_last_error=True)
    controls = ct.WinDLL("comctl32", use_last_error=True)
    user.MonitorFromWindow.argtypes = (wt.HWND, wt.DWORD)
    user.MonitorFromWindow.restype = wt.HMONITOR
    user.GetMonitorInfoW.argtypes = (wt.HMONITOR, ct.POINTER(MonitorInfo))
    user.GetMonitorInfoW.restype = wt.BOOL
    subclass = ct.WINFUNCTYPE(ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, ct.c_size_t, ct.c_size_t)
    controls.SetWindowSubclass.argtypes = (wt.HWND, subclass, ct.c_size_t, ct.c_size_t)
    controls.SetWindowSubclass.restype = wt.BOOL
    controls.RemoveWindowSubclass.argtypes = (wt.HWND, subclass, ct.c_size_t)
    controls.RemoveWindowSubclass.restype = wt.BOOL
    controls.DefSubclassProc.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    controls.DefSubclassProc.restype = ct.c_ssize_t

    @subclass
    def on_message(handle, message, wparam, lparam, identifier, _):
        if message == 0x0082:  # WM_NCDESTROY
            controls.RemoveWindowSubclass(handle, on_message, identifier)
        result = controls.DefSubclassProc(handle, message, wparam, lparam)
        if message == 0x0024 and lparam:  # WM_GETMINMAXINFO
            monitor = user.MonitorFromWindow(handle, 2)
            info = MonitorInfo(size=ct.sizeof(MonitorInfo))
            if user.GetMonitorInfoW(monitor, ct.byref(info)):
                bounds = MinMaxInfo.from_address(lparam)
                bounds.position = wt.POINT(info.work.left - info.monitor.left, info.work.top - info.monitor.top)
                bounds.size = wt.POINT(info.work.right - info.work.left, info.work.bottom - info.work.top)
        return result

    if not controls.SetWindowSubclass(hwnd, on_message, 1, 0):
        raise ct.WinError(ct.get_last_error())
    return on_message  # 窗口销毁前须保留函数指针。


def enable_acrylic(form):
    from webview.platforms.winforms import WinForms, Color

    if sys.getwindowsversion().build < 22621 or WinForms.SystemInformation.HighContrast:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            enabled, _ = winreg.QueryValueEx(key, "EnableTransparency")
    except FileNotFoundError:
        enabled = True
    if not enabled:
        return False

    dwm = ct.WinDLL("dwmapi")
    dwm.DwmSetWindowAttribute.argtypes = (wt.HWND, wt.DWORD, ct.c_void_p, wt.DWORD)
    dwm.DwmSetWindowAttribute.restype = ct.c_long

    class Margins(ct.Structure):
        _fields_ = [(side, ct.c_int) for side in ("left", "right", "top", "bottom")]

    dwm.DwmExtendFrameIntoClientArea.argtypes = (wt.HWND, ct.POINTER(Margins))
    dwm.DwmExtendFrameIntoClientArea.restype = ct.c_long
    hwnd = form.Handle.ToInt64()
    acrylic = ct.c_int(3)  # DWMSBT_TRANSIENTWINDOW，系统桌面亚克力
    if dwm.DwmSetWindowAttribute(hwnd, 38, ct.byref(acrylic), ct.sizeof(acrylic)) != 0:
        return False
    if dwm.DwmExtendFrameIntoClientArea(hwnd, ct.byref(Margins(-1, -1, -1, -1))) != 0:
        return False
    # 黑色 WinForms 底面留给 DWM 合成，WebView2 只绘制网页本身。
    form.BackColor = Color.Black
    form.webview.DefaultBackgroundColor = Color.Transparent
    return True
