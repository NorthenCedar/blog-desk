# -*- coding: utf-8 -*-
"""复验：**exe 里内嵌的图标，是不是我们画的那张**（2026-10-09）。

为什么不能只看"spec 里写了 icon= 就算数"：
  这与本站那条「线上产物核对」是同一条纪律 —— **构建输入写对了，不等于产物里真是它**。
  PyInstaller 可能因为路径、编码、尺寸表等原因换一张、或者干脆用默认图标，
  而这一切在**源码里一个字符都看不出来**。

做法（全程只读，不改任何文件）：
  ① `PrivateExtractIconsW` 从 exe 里按指定边长把 HICON 抠出来
     （Vista+ 才有的未文档化 API，但它能按 256 这种大尺寸取；
       退路是 `ExtractIconExW`，只能拿到 32/16）；
  ② HICON → `GetIconInfo` → `hbmColor` → `GetDIBits`（32bpp、top-down）→ BGRA 像素；
  ③ 与 `writedesk.ico` 里同尺寸那一条逐像素比，报 **平均误差 / 最大误差 /
     明显不同的像素占比**。

判据（带余量，不写零容差）：平均误差 ≤ 6/255 且"差得明显"（>32）的像素 < 2%。
  —— Windows 有可能自己做一次缩放而不是直接用我们那条位图，
     所以不能要求逐字节相同；但"另一张图"必然远远超过这个量级。

★ ctypes 全部声明 `argtypes`：不声明时 64 位句柄会被当 32 位 `c_int` 传进去，
  `GetIconInfo` / `GetDIBits` 会**静默失败**（本轮在窗口枚举上刚踩过这个坑）。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image  # noqa: E402

from make_shortcut import EXE  # noqa: E402

ICO = Path(__file__).resolve().parent / "writedesk.ico"
SIZES = [256, 48, 32, 16]

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)   # ExtractIconExW 在这里，不在 user32


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.PrivateExtractIconsW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
    wintypes.UINT, wintypes.UINT]
user32.PrivateExtractIconsW.restype = wintypes.UINT
shell32.ExtractIconExW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.HICON),
    ctypes.POINTER(wintypes.HICON), wintypes.UINT]
shell32.ExtractIconExW.restype = wintypes.UINT
user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL


def hicon_to_image(hicon) -> Image.Image:
    """HICON → PIL RGBA。

    ★ 两个必须踩对的点（第一版就栽在这两条上，报 `GetDIBits 失败：87`（参数无效））：
      ① `GetDIBits` 的第一参**要给一个真 DC**。`GetIconInfo` 给的 `hbmColor`
         常常是设备相关位图（DDB），传 NULL 当 DC 会 87。
         （`CreateCompatibleDC(NULL)` 就是"与屏幕兼容"的那个。）
      ② 查询那一调用（`lpvBits=NULL, cLines=0`）回来要把宽高读出来，
         读到 0 说明这次查询没成 —— 不能拿 0 去发第二次调用，那也是 87。
    """
    info = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        raise OSError(f"GetIconInfo 失败：{ctypes.get_last_error()}")

    hdc = gdi32.CreateCompatibleDC(None)
    try:
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        # 第一次：只问尺寸与格式（lpvBits=NULL）
        gdi32.GetDIBits(hdc, info.hbmColor, 0, 0, None, ctypes.byref(bi), 0)
        w, h = bi.bmiHeader.biWidth, abs(bi.bmiHeader.biHeight)
        if not w or not h:
            raise OSError(f"问不到位图尺寸（w={w} h={h}）")

        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h      # 负 = top-down，省得自己翻行
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0  # BI_RGB
        bi.bmiHeader.biSizeImage = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        n = gdi32.GetDIBits(hdc, info.hbmColor, 0, h, buf, ctypes.byref(bi), 0)
        if n == 0:
            raise OSError(f"GetDIBits 失败：{ctypes.get_last_error()}")
    finally:
        gdi32.DeleteDC(hdc)
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)

    raw = bytearray(buf.raw)
    for i in range(0, len(raw), 4):      # BGRA → RGBA
        raw[i], raw[i + 2] = raw[i + 2], raw[i]
    return Image.frombytes("RGBA", (w, h), bytes(raw))


def extract(exe: Path, size: int) -> Image.Image | None:
    """从 exe 里按边长取图标。PrivateExtractIconsW 拿不到就退到 ExtractIconExW。"""
    hicon = wintypes.HICON()
    n = user32.PrivateExtractIconsW(str(exe), 0, size, size,
                                    ctypes.byref(hicon), None, 1, 0)
    if n and hicon.value:
        try:
            return hicon_to_image(hicon)
        finally:
            user32.DestroyIcon(hicon)
    if size <= 48:
        large, small = wintypes.HICON(), wintypes.HICON()
        if shell32.ExtractIconExW(str(exe), 0, ctypes.byref(large),
                                  ctypes.byref(small), 1):
            h = large if size > 16 else small
            if h.value:
                try:
                    return hicon_to_image(h)
                finally:
                    user32.DestroyIcon(h)
    return None


def ico_entry(path: Path, size: int) -> Image.Image:
    im = Image.open(path)
    im.size = (size, size)
    return im.convert("RGBA")


def diff(a: Image.Image, b: Image.Image) -> tuple[float, int, float]:
    """返回 (平均误差, 最大误差, 明显不同像素的占比)。尺寸不一致先缩到同一尺寸。"""
    if a.size != b.size:
        b = b.resize(a.size, Image.LANCZOS)
    pa, pb = a.tobytes(), b.tobytes()
    tot = n = 0
    mx = 0
    big = 0
    for i in range(0, len(pa), 4):
        d = max(abs(pa[i] - pb[i]), abs(pa[i + 1] - pb[i + 1]),
                abs(pa[i + 2] - pb[i + 2]), abs(pa[i + 3] - pb[i + 3]))
        tot += d
        n += 1
        mx = max(mx, d)
        if d > 32:
            big += 1
    return tot / n, mx, big / n


def main() -> int:
    if not EXE.is_file():
        print(f"✘ 没有 {EXE}（先跑 build-app.py）")
        return 1
    if not ICO.is_file():
        print(f"✘ 没有 {ICO}（先跑 _icon.py）")
        return 1

    print(f"exe：{EXE}")
    print(f"ico：{ICO}")
    print("-" * 66)
    bad = []
    for s in SIZES:
        got = extract(EXE, s)
        if got is None:
            print(f"  ✘ {s:>3}px  从 exe 里取不到这个尺寸")
            bad.append(s)
            continue
        want = ico_entry(ICO, s)
        avg, mx, frac = diff(want, got)
        ok = avg <= 6 and frac < 0.02
        print(f"  {'✔' if ok else '✘'} {s:>3}px  取到 {got.size}  "
              f"平均误差 {avg:.2f}/255  最大 {mx}  明显不同 {frac:.1%}")
        if not ok:
            bad.append(s)

    print("-" * 66)
    if bad:
        print(f"✘ 这些尺寸对不上：{bad} —— exe 里的图标不是 {ICO.name}")
        print("  （把取到的图存到 _icon_exe_<尺寸>.png 给你看）")
        for s in bad:
            got = extract(EXE, s)
            if got:
                got.save(Path(__file__).resolve().parent / f"_icon_exe_{s}.png")
        return 1
    print("✔ exe 内嵌图标与 writedesk.ico 一致（逐像素比过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
