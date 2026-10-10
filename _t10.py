# -*- coding: utf-8 -*-
"""pywebview 启动竞态的门闩（2026-10-09 站主报「刚启动的时候点它有几率卡死」）。

## 根因（先读这段，它是本文件所有断言的依据）

pywebview 把JS 桥注入页面时，会在**一个新建的非 UI 线程**里遍历我们传进去的
`js_api` 对象（`webview/util.py` 的 `inject_pywebview` → `get_functions`）：

```python
for name in dir(obj):
    if name.startswith('_'): continue
    attr = getattr(obj, name)
    ...
    elif inspect.isclass(attr) or (isinstance(attr, object)
                                   and not callable(attr)
                                   and hasattr(attr, '__module__')):
        get_functions(attr, f'{full_name}.{name}', functions)   # ★ 递归下去
```

而**我们把 `api.win` 设成了 pywebview 的 Window 对象**
（`app.py` 里`api.win = win`，为了页面能调`win_pos` / `win_max`）。

⇒ `getattr(api, 'win')` 拿到 Window → 满足"类或带 `__module__` 的对象" →
**递归遍历 Window 的每一个属性** → 一路摸到 .NET 的 `WebView2` 控件
（`win.native.ActiveControl.CoreWebView2`、`win.native.AccessibilityObject.Bounds.Empty.Empty…`）
⇒ **① 非 UI 线程访问控件**（日志里 69 次
  `CoreWebView2 can only be accessed from the UI thread`）
⇒ **② 递归爆栈**（16 次 `maximum recursion depth exceeded`）。

⇒ **责任在我们的代码**：把 Window 挂在了会被遍历的 `js_api` 对象上。
   修法就是**别让pywebview 看得见它**（见 `app.py` 里`_hidden_` 前缀的用法）。

## 本文件守什么

1. `DeskApi` 对外**不暴露任何 pywebview 对象**（所有会被遍历的属性都是标量）；
2. 页面仍能用 `window.pywebview.api.win_pos(...)` 调到窗口方法（**功能没被修掉**）；
3. `get_functions` 那种遍历在**本对象上跑一遍不会递归下去**（拿 pywebview 自己的
   `get_functions` 当被测对象做判据，不是自己写个像的）；
4. **反向对照**：把 `api.win` 挂回去（=旧写法），判据必须立刻失败 ——
   否则"我们这个对象本来就不会被遍历"这句话就是空的。

跑法：`python _t10.py`
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CHECKS: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'✔' if ok else '✘'} {name}" + (f"　{detail}" if detail else ""))


def walk(obj, base="", seen=None, depth=0, max_depth=3):
    """**照抄 pywebview `get_functions` 的判据**，找出它会递归下去的属性。

    这是判据的关键：不能自己写一个「看起来像」的遍历器 —— 必须是它真正的判据，
    否则「我们的对象本来就安全」这个结论就可能是自己骗自己。
    """
    if seen is None:
        seen = []
    if depth > max_depth:
        return seen
    for name in dir(obj):
        if name.startswith("_"):          # ★ 它自己就跳过下划线开头的
            continue
        try:
            attr = getattr(obj, name)
        except Exception:
            continue
        full = f"{base}.{name}" if base else name
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            continue
        if inspect.isclass(attr) or (isinstance(attr, object)
                                     and not callable(attr)
                                     and hasattr(attr, "__module__")):
            seen.append((full, type(attr).__name__))
            walk(attr, full, seen, depth + 1, max_depth)
    return seen


def main() -> int:
    import app as A

    # 这份测试只关心 DeskApi 把什么挂到 js_api 上，**不读仓库内容** ——
    # 所以 repo 只是个"某个路径"的占位值，钉死某个人的绝对路径没有语义。
    # 写成不存在的目录还能顺带证明：属性暴露与否与路径存不存在无关。
    REPO_STR = str(Path(__file__).resolve().parent / "_无此目录")

    print("一、DeskApi 交给 pywebview 之前是什么样")
    print("-" * 72)
    api = A.DeskApi(config={"repo": "REPO_STR"})   # __init__ 里本来就是 _win = None

    # 造一个"假的但足以骗过判据"的 pywebview Window：它有 __module__、
    # 且 getattr 它任意属性都会返回一个更深的对象（模拟 .NET 那条属性链）。
    class FakeNative:
        __module__ = "System.Windows.Forms"
        @property
        def ActiveControl(self):
            return self            # ★ 无限链：.Empty.Empty.Empty… 就是这么来的
        @property
        def AccessibilityObject(self):
            return self

    class FakeWindow:
        __module__ = "webview.window"
        @property
        def native(self):
            return FakeNative()

    print("① DeskApi 上被 pywebview 判据判为「会递归下去」的属性：")
    bad_now = walk(api)
    for full, typ in bad_now:
        print(f"      {full}（{typ}）")
    chk("① 赋值前 DeskApi 上没有任何会被递归的属性", not bad_now,
        f"{len(bad_now)} 个：{[b[0] for b in bad_now]}")

    print()
    print("二、反向对照：把窗口挂到一个 pywebview 看得见的名字上（★ 旧写法）")
    print("-" * 72)
    api.win = FakeWindow()          # ★ 旧写法：叫 `win`，没有下划线
    bad_old = walk(api)
    for full, typ in bad_old[:6]:
        print(f"      {full}（{typ}）")
    if len(bad_old) > 6:
        print(f"      … 还有 {len(bad_old)-6} 个")
    chk("② 反向对照：挂上 Window 之后判据立刻炸（证明夹具咬得住旧写法）",
        bool(bad_old),
        f"{len(bad_old)} 个被递归：{[b[0] for b in bad_old[:3]]}")

    print()
    print("三、修复后（`_win`：下划线让 pywebview 那行continue 直接跳过）")
    print("-" * 72)
    # ★★★ 关键（2026-10-09 补）：这一段必须走**真实的 `__init__`** ——
    #   上一版我在这里写 `api2._win = FakeWindow()` 手工赋值，于是
    #   变异①（把 `__init__` 里的 `self._win` 改回 `self.win`）**根本走不到**，
    #   测试永远绿 —— 这就是「夹具缺输入」的第三例（前两例见 AGENTS 事故表）。
    #   ⇒ 判据必须由**生产代码里的那行赋值**产生，不能由测试自己造。
    api2 = A.DeskApi(win=FakeWindow(), config={"repo": "REPO_STR"})
    bad_new = walk(api2)
    for full, typ in bad_new:
        print(f"      {full}（{typ}）")
    chk("③ 修法：`DeskApi(win=…)` 走真实 __init__ 之后，仍认不出任何会被递归的属性",
        not bad_new,
        f"{len(bad_new)} 个")

    # 顺带把「main 里那行赋值」也纳入判据：它同样是暴露点，
    # 光守住 __init__ 、守不住赋值点，等于没守住（变异② 就是冲它来的）。
    main_src = (HERE / "app.py").read_text(encoding="utf-8")
    leaked = [l.strip() for l in main_src.splitlines()
              if l.strip().startswith("api.")
              and not re.match(r"api\._\w+\s*=", l.strip())]
    chk("③ main 里的赋值点也没有写成无下划线的属性名", not leaked,
        f"泄漏点：{leaked}" if leaked else "只有 api._* 的赋值")
    # 而且那个赋值必须真的存在（否则"没泄漏"是因为"没代码"）
    chk("③ main 里确实有一行 `api._win = win`（判据不是空的）",
        "api._win = win" in main_src)

    print()
    print("四、功能没被修掉：页面还能调到窗口方法")
    print("-" * 72)
    api3 = A.DeskApi(config={"repo": "REPO_STR"})
    for m in ("win_close", "win_min", "win_max", "win_pos", "win_moveto",
              "get_config", "set_config"):
        chk(f"④ api.{m} 仍是可被 JS 调用的方法",
            inspect.ismethod(getattr(api3, m, None)))

    # ★ 关键：窗口功能**真的**还活着 —— 走 `_win` 跑一遍 win_pos / win_moveto
    calls: list[tuple] = []

    class RecordingWin:
        __module__ = "webview.window"
        # ★ 按真实实现给属性：`win_pos()` 读的是 `win.x` / `win.y`，
        #   不是 `get_position()`（写错了就成了"夹具在验一个不存在的东西"）
        x = 11
        y = 22
        def move(self, x, y):
            calls.append(("move", x, y))
        @property
        def native(self):
            return FakeNative()

    api4 = A.DeskApi(config={"repo": "REPO_STR"})
    api4._win = RecordingWin()
    r = api4.win_pos()
    chk("④ win_pos() 真的从窗口读到了位置", r.get("x") == 11 and r.get("y") == 22,
        f"{r}")
    api4.win_moveto(33, 44)
    chk("④ win_moveto() 真的调到了窗口的 move",
        ("move", 33, 44) in calls, f"{calls}")

    bad_names = [n for n, ok, _ in CHECKS if not ok]
    print()
    print(f"合计 {len(CHECKS)} 项，通过 {len(CHECKS)-len(bad_names)}，失败 {len(bad_names)}")
    for n in bad_names:
        print(f"  失败：{n}")
    return 1 if bad_names else 0


if __name__ == "__main__":
    sys.exit(main())
