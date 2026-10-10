# -*- coding: utf-8 -*-
"""`_t6.py`（拖动窗口的真机测试）的**变异验证**（2026-10-09）。

把 `ui/app.js` 里拖动那几行逐条改坏，每改一处跑一次 `_t6.py`，**要求它转红**；
跑完按字节还原并比对 sha256。

第 ① 条是这个文件存在的理由：站主报的那句「拖动不跟鼠标，会比鼠标快一点」
就是它 —— 指针位移乘了一次 `devicePixelRatio`。上一版 `_t6.py` 把同一个错误
假设抄进了期望值里，于是 dpr=1 时它永远绿。现在期望值与屏幕缩放解耦（⑦ 那条），
必须看到它真的被咬住。

第 ② 条是当初抖动（站主第 4 条）的根因：用 client 坐标算位移 = 正反馈。
`_t6.py` 里的 `fire()` 现在会让 client 坐标跟着窗口移动一起变，就是为了让这条可验。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "ui" / "app.js"
TEST = "_t6.py"
PY = sys.executable

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "①",
        "x: Math.round(drag.wx + (e.screenX - drag.sx)),\n"
        "        y: Math.round(drag.wy + (e.screenY - drag.sy)),",
        "x: Math.round(drag.wx + (e.screenX - drag.sx) * window.devicePixelRatio),\n"
        "        y: Math.round(drag.wy + (e.screenY - drag.sy) * window.devicePixelRatio),",
        "★ 把 devicePixelRatio 乘回去 ⇒ 窗口跑出鼠标 1.5 倍速（站主这次报的原文）",
    ),
    (
        "②",
        "x: Math.round(drag.wx + (e.screenX - drag.sx)),\n"
        "        y: Math.round(drag.wy + (e.screenY - drag.sy)),",
        "x: Math.round(drag.wx + (e.clientX - drag.sx)),\n"
        "        y: Math.round(drag.wy + (e.clientY - drag.sy)),",
        "★ 用 client 坐标算位移 ⇒ 窗口一动坐标就变（正反馈）= 当初的抖动",
    ),
    (
        "③",
        "x: Math.round(drag.wx + (e.screenX - drag.sx)),\n"
        "        y: Math.round(drag.wy + (e.screenY - drag.sy)),",
        "x: Math.round(e.screenX - drag.sx),\n"
        "        y: Math.round(e.screenY - drag.sy),",
        "★ 退化成「发位移量」而不是「发目标位置」⇒ 窗口会跳到屏幕原点附近",
    ),
    (
        "④",
        "      if (e.target.closest && e.target.closest(INTERACTIVE)) return;",
        "      if (false) return;",
        "★ 去掉可交互元素的排除 ⇒ 点三个窗口键/输入框也会把窗口拖走",
    ),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_tests() -> tuple[int, str]:
    """跑一次 `_t6.py`。**必须带超时**：没有超时的话一挂就是十几分钟，
    而外层用 `timeout` 杀进程时 finally 不执行 ⇒ 变异版会被留在磁盘上没人还原。

    ★ 超时**不算「咬住」**（它既不是绿也不是红，多半是测试自己坏了）——
      单独报出来，别把「测试挂掉」误记成「bug 被抓住了」。
    """
    env = dict(os.environ, CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD="20000")
    try:
        proc = subprocess.run([PY, str(HERE / TEST)], cwd=HERE, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=240)
    except subprocess.TimeoutExpired:
        return 99, "超时（240 秒）"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    original = TARGET.read_bytes()
    before = sha(TARGET)

    print("[变异] 基线（未改动）应全绿 ……")
    code, out = run_tests()
    if code != 0:
        print("[变异] ✗ 基线就是红的，先修基线")
        print(out[-1500:])
        return 2
    print("[变异] 基线通过：" + [l for l in out.splitlines() if l.startswith("合计")][0])

    caught, missed, left = [], [], []
    try:
        for num, old, new, why in MUTATIONS:
            o, n = old.encode("utf-8"), new.encode("utf-8")
            if original.count(o) != 1:
                print(f"[变异] {num} ✗ 锚点出现 {original.count(o)} 次（要恰好 1 次），脚本要改")
                missed.append(f"{num} 锚点")
                continue
            TARGET.write_bytes(original.replace(o, n, 1))
            code, out = run_tests()
            TARGET.write_bytes(original)
            if code == 99:
                print(f"[变异] {num} ★ 测试超时（既不是绿也不是红，要查）—— {why}", flush=True)
                missed.append(num)
                continue
            print(f"[变异] {num} {'咬住' if code else '★ 漏了'} —— {why}", flush=True)
            (caught if code else missed).append(num)
    finally:
        TARGET.write_bytes(original)
        if sha(TARGET) != before:
            left.append("ui/app.js")

    print()
    print(f"[变异] 咬住 {len(caught)}/{len(MUTATIONS)}：{' '.join(caught)}")
    if missed:
        print(f"[变异] 未咬住：{' '.join(missed)}")
    print(f"[变异] 源文件逐字节还原：{'是' if not left else '★ 否 ' + str(left)}")
    return 0 if (not missed and not left) else 1


if __name__ == "__main__":
    sys.exit(main())
