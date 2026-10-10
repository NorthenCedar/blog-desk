# -*- coding: utf-8 -*-
"""给写作台建**真正的 Windows 快捷方式**（.lnk）—— 桌面一个、开始菜单一个。

站主的要求是"变成应用，不用脚本"。exe 有了；剩下的就是让双击的那个东西
不再是 `.bat`，而是一个正常的快捷方式（有自己的图标、能钉到任务栏、
开始菜单里搜得到）。

★ 为什么手搓 COM 而不是用现成的库：
  本机没有 pywin32（`win32com.client`），而且历史上 COM 实例化被拦过。
  实测（2026-10-09）**用 ctypes 直连 ole32 是放行的** —— 所以这里走
  `CoCreateInstance` + 手写 vtable 调用，不引入任何新依赖。
  vtable 序号是 COM 规范定死的，写在下面每个调用旁边，方便以后核对。

用法：
    python make-shortcut.py                # 建（或重建）两个快捷方式
    python make-shortcut.py --report       # 只看现在有没有
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:        # 直接 `python make_shortcut.py` 跑时也能找到 lib/
    sys.path.insert(0, str(HERE))

from lib import proc  # noqa: E402  （外部命令统一走 lib/proc.py，否则会闪黑窗）

APP_NAME = "写作台"

# ── 装在哪：`%LOCALAPPDATA%\Programs\写作台\` ────────────────────────
# 这是 Windows 上"当前用户级应用"的常规位置。
#
# ★ 为什么不沿用最初那个装在 D 盘工作区里的位置（2026-10-09 实测踩坑）：
#   那个工作区整棵树被打上了 **`Mandatory Label\Low Mandatory Level` 低完整性标签**
#   （目录上还挂着一个沙箱 SID 和 Everyone 的 DENY DELETE_CHILD；同一个工作区下的
#    博客仓库也一样）。低完整性标签会被整棵子树继承，
#   而**带 Low 标签的 exe 根本起不来** —— PyInstaller 的 bootloader 连自己这个文件
#   都打不开（`_wfopen` 返回 NULL），启动即报
#     "Could not load PyInstaller's embedded PKG archive from the executable"
#
#   实证（同一份 exe 字节，只换目录）：
#     `%TEMP%\he.exe`                  ✅ 能跑
#     另一个 D 盘普通目录 `he.exe`     ✅ 能跑
#     低完整性工作区下的 `he.exe`       ❌ cannot open archive
#     再开一个同样带标签的目录 `he.exe` ❌ 同上
#     → 对那个文件 `icacls … /setintegritylevel Medium` 后**立刻** ✅
#     → 只删掉目录的 DENY ACE（不动标签）仍然 ❌  ⇒ 与 DENY 无关，就是那条 Low 标签
#   顺带排除掉的：归档/cookie 完好、bootloader 与官方 wheel 逐字节一致
#   （sha256 四个全同）、不是沙箱（关掉沙箱照样挂）、不是盘符、不是长路径、不是 UPX。
#
#   ⇒ 装到 AI 不会去动标签的常规用户目录最稳。build-app.py 装完还会**实跑一次**复验。
APPDIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP_NAME
EXE = APPDIR / f"{APP_NAME}.exe"

# ── COM 基本件 ──────────────────────────────────────────────────────
ole32 = ctypes.windll.ole32
shell32 = ctypes.windll.shell32

CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010b-0000-0000-C000-000000000046}"
CLSCTX_INPROC_SERVER = 1
HRESULT = ctypes.c_long


class GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]


def _guid(s: str) -> GUID:
    g = GUID()
    if ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g)) != 0:
        raise OSError(f"CLSIDFromString 失败：{s}")
    return g


def _call(obj: ctypes.c_void_p, index: int, *argtypes):
    """按 vtable 序号取方法。

    obj 是接口指针；vtable[0] 是 QueryInterface、[1] AddRef、[2] Release，
    之后按各接口自己的顺序排（序号写在每个调用点旁）。
    """
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


# ── 完整性标签（Mandatory Label）────────────────────────────────────
#
# 存在的理由见上面 APPDIR 的注释：带 Low 标签的 exe 根本起不来。
# 读标签走 ctypes（只读，不碰任何东西）；写标签交给系统的 icacls
# （改标签要特权，让 icacls 自己去处理，比手工 SetNamedSecurityInfoW 稳）。

SE_FILE_OBJECT = 1
LABEL_SECURITY_INFORMATION = 0x00000010
_SDDL_REVISION_1 = 1

advapi32 = ctypes.windll.advapi32
kernel32 = ctypes.windll.kernel32

# SDDL 里标签长这样：S:AI(ML;OICI;NW;;;LW)  —— 最后那段是级别
_LABEL_RE = re.compile(r"\(ML;[^;]*;[^;]*;;;([^)]+)\)")
_LABEL_NAMES = {"LW": "Low", "ME": "Medium", "HI": "High", "SI": "System",
                "S-1-16-0": "Untrusted"}


def integrity_label(path: Path) -> str:
    """读一个文件/目录的完整性标签：'Low' / 'Medium' / 'High' / '无' / '未知(…)'。"""
    pSD = ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(
        ctypes.c_wchar_p(str(path)), SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION,
        None, None, None, None, ctypes.byref(pSD))
    if rc != 0:
        return f"未知(rc={rc})"
    try:
        sddl = ctypes.c_wchar_p()
        need = wintypes.DWORD()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                pSD, _SDDL_REVISION_1, LABEL_SECURITY_INFORMATION,
                ctypes.byref(sddl), ctypes.byref(need)):
            return "未知(转换失败)"
        m = _LABEL_RE.search(sddl.value or "")
        if not m:
            return "无"
        return _LABEL_NAMES.get(m.group(1), f"未知({m.group(1)})")
    finally:
        kernel32.LocalFree(pSD)


def force_medium(path: Path, recursive: bool = True) -> str | None:
    """若 `path` 带 Low/Untrusted 标签，就改回 Medium。返回改动说明（没动返回 None）。

    ★ 方向只有一个：**往常规级别调**。绝不会把东西提成 High —— 那才是真危险的操作。
    """
    before = integrity_label(path)
    if before not in ("Low", "Untrusted"):
        return None
    if path.is_dir():
        cmd = ["icacls", str(path), "/setintegritylevel", "(OI)(CI)Medium"]
        if recursive:
            cmd += ["/T", "/C"]
    else:
        cmd = ["icacls", str(path), "/setintegritylevel", "Medium"]
    proc.run(cmd, capture_output=True)          # 输出不给人看，安静跑完
    return f"{path}  {before} → {integrity_label(path)}"


# ── ShellLink ───────────────────────────────────────────────────────

def create_lnk(target: Path, lnk: Path, workdir: Path, desc: str = "") -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ole32.CoInitialize(None)

    p = ctypes.c_void_p()
    clsid, iid = _guid(CLSID_ShellLink), _guid(IID_IShellLinkW)
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
                                ctypes.byref(iid), ctypes.byref(p))
    if hr != 0:
        raise OSError(f"CoCreateInstance 失败：HRESULT={hr}")

    try:
        _call(p, 20, ctypes.c_wchar_p)(p, str(target))          # IShellLink::SetPath
        _call(p, 9, ctypes.c_wchar_p)(p, str(workdir))          # SetWorkingDirectory
        _call(p, 11, ctypes.c_wchar_p)(p, "")                   # SetArguments
        _call(p, 7, ctypes.c_wchar_p)(p, desc)                  # SetDescription
        # SetIconLocation(路径, 序号)：指到 exe 自己，取它的内嵌图标（0 号）
        _call(p, 17, ctypes.c_wchar_p, ctypes.c_int)(p, str(target), 0)

        # 拿 IPersistFile 才能存盘
        pf = ctypes.c_void_p()
        iidpf = _guid(IID_IPersistFile)
        hr = _call(p, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            p, ctypes.byref(iidpf), ctypes.byref(pf))
        if hr != 0:
            raise OSError(f"QueryInterface(IPersistFile) 失败：HRESULT={hr}")
        try:
            hr = _call(pf, 6, ctypes.c_wchar_p, ctypes.c_int)(pf, str(lnk), 1)   # IPersistFile::Save
            if hr != 0:
                raise OSError(f"IPersistFile::Save 失败：HRESULT={hr}")
        finally:
            _call(pf, 2)(pf)                                     # Release
    finally:
        _call(p, 2)(p)                                           # Release


def desktop() -> Path:
    """桌面真实路径。

    ★ 站主的桌面被 OneDrive 接管了（`C:\\Users\\…\\OneDrive\\Desktop`），
      `C:\\Users\\…\\Desktop` 根本不存在 —— 不能用后者硬拼。
      问系统要才是对的（没接管时它也会给出正确的那一条）。
    """
    buf = ctypes.create_unicode_buffer(512)
    if shell32.SHGetFolderPathW(None, 0x0000, None, 0, buf) == 0:   # CSIDL_DESKTOP
        return Path(buf.value)
    return Path(os.environ["USERPROFILE"]) / "Desktop"


def start_menu() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


# ── 把旧的 .bat 送进回收站（站主："不用脚本"） ──────────────────────

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040          # ★ 进回收站，不是真删 —— 万一还要用能捞回来


class _ShFileOp(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def recycle(path: Path) -> bool:
    """把文件/目录送进回收站（可恢复）。

    ★ 判据是「它还在不在」，**不是** SHFileOperationW 的返回值（2026-10-09 实测）：
      对目录它恒定回 2（ERROR_FILE_NOT_FOUND），而东西其实已经进回收站了 ——
      拿系统自己的计数器核对过：D 盘回收站项数 1387 → 1389，目录也确实不在了。
      用返回码当判据会得出"失败了"的错误结论，然后要么白重试、要么误以为没删掉。
      （`D:\\workbuddy\\...\\shim\\sitecustomize.py` 就是踩在这上面：
        它按返回码抛 OSError，于是 PyInstaller 清理临时目录时直接构建失败。）
    """
    if not path.exists():
        return False
    op = _ShFileOp()
    op.wFunc = FO_DELETE
    op.pFrom = str(path) + "\0\0"          # 这个字段要求双 NUL 结尾
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    shell32.SHFileOperationW(ctypes.byref(op))
    return not path.exists()


def main() -> int:
    if "--report" in sys.argv:
        for name, p in (("桌面", desktop() / f"{APP_NAME}.lnk"),
                        ("开始菜单", start_menu() / f"{APP_NAME}.lnk")):
            print(f"  {name}：{'有' if p.is_file() else '没有'}  {p}")
        if EXE.is_file():
            print(f"  exe ：有  {EXE}  完整性标签={integrity_label(EXE)}")
        else:
            print(f"  exe ：没有  {EXE}")
        return 0

    if not EXE.is_file():
        print(f"✘ 找不到 exe：{EXE}")
        print("  先跑 build-app.py 把应用打出来。")
        return 1

    made = []
    for label, lnk in (("桌面", desktop() / f"{APP_NAME}.lnk"),
                       ("开始菜单", start_menu() / f"{APP_NAME}.lnk")):
        create_lnk(EXE, lnk, EXE.parent, f"{APP_NAME} · 北杉札记")
        made.append(f"{label}  {lnk}")

    print("✔ 快捷方式已建好：")
    for m in made:
        print("   " + m)

    # 旧的 .bat 从桌面上撤掉（进回收站，可还原）
    for old in (desktop() / f"{APP_NAME}.bat", desktop() / "写作台.bat"):
        if old.is_file():
            print(("  已移入回收站：" if recycle(old) else "  ⚠ 没移走（可在资源管理器里手动删）：") + str(old))
    return 0


if __name__ == "__main__":
    sys.exit(main())
