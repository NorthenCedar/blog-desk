# -*- coding: utf-8 -*-
"""`_t10.py`（启动竞态：别把 pywebview 对象挂到 js_api 上）的**变异验证**（2026-10-09）。

把`app.py` 里那条修法逐条改坏，每改一处跑一次 `_t10.py`，**要求它转红**；
跑完按字节还原并比对 sha256。

四条各自咬住什么：
  ① `DeskApi.__init__` 里 `_win` 改回`win`（★ **就是那个真 bug 的原形**）
     ⇒ pywebview 又能递归摸到窗口 ⇒ ③ 必须红
  ② main 里 `api._win = win` 改回 `api.win = win` ⇒ 同上，另一处赋值点
  ③ 拿掉一处 `self._win.xxx` 的调用（让 `win_pos` 读不到位置）⇒ ④ 必须红
  ④ 把 `_win` 换成另一个**没有下划线**的名字（`the_window`）⇒ ③ 必须红
     —— 这一条是给「下划线本身就是判据」这句话钉的：换个有下划线的名字行，
        换个没下划线的就不行。

★ 与 `_mutate_t6/7/8/9` 同样的两条纪律：
  · `run_tests()` **必须带超时**（超时记 99，不算咬住 —— 别把"测试挂了"记成"抓住了"）
  · 锚点必须**恰好出现 1 次**，否则脚本自己先报错退出，不许瞎改
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "app.py"
TEST = "_t10.py"
PY = sys.executable

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "①",
        "        self._win = win            # ★ 见类文档：下划线是给 pywebview 的判据用的",
        "        self.win = win            # ★ 变异：改回没有下划线 ⇒ pywebview 又能摸到窗口",
        "★ __init__ 里挂上无下划线的名字 ⇒ 整条递归路径回来",
    ),
    (
        "②",
        "    api._win = win",
        "    api.win = win",
        "★ main 里的赋值点改回去 ⇒ 同样炸",
    ),
    (
        "③",
        '            return {"ok": True, "x": int(self._win.x), "y": int(self._win.y),',
        '            return {"ok": True, "x": 0, "y": 0,',
        "★ win_pos 不读窗口了 ⇒ ④「真的读到位置」必须红（证明功能那条守得住）",
    ),
    (
        "④",
        "        self._win = win",
        "        self.the_window = win",
        "★ 换成另一个**没有下划线**的名字 ⇒ 下划线才是判据这条被咬住",
    ),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_tests() -> tuple[int, str]:
    """跑一次 `_t10.py`，**带超时**。超时返回 99，不算咬住。"""
    env = dict(os.environ, CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD="20000")
    try:
        p = subprocess.run([PY, str(HERE / TEST)], cwd=HERE, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return 99, "超时（120 秒）"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    original = TARGET.read_bytes()
    before = sha(TARGET)

    # ★ 与 `_mutate_t5.py` 同一个洞（那边2026-10-09 先撞上）：`app.py` 是 **CRLF**，
    #   锚点里的 `\n` 是 LF ⇒ 跨行锚点在字节层命中 0 次 ⇒ 那条变异被静默跳过。
    #   （`_mutate_t7/8/9` 的目标是 `lib/content.py` / `ui/app.js`，那两个都是 LF，所以没事。
    #   **别以为"别的脚本没报错"就是安全 —— 目标文件一行尾换了就会中招。**）
    nl = b"\r\n" if b"\r\n" in original else b"\n"
    print(f"[变异] 目标文件行尾：{'CRLF' if nl == b'\r\n' else 'LF'}（锚点按它归一化）")

    print("[变异] 基线（未改动）应全绿……")
    code, out = run_tests()
    if code != 0:
        print("[变异] ✗ 基线就是红的，先修基线")
        print(out[-1500:])
        return 2
    print("[变异] 基线通过：" + [l for l in out.splitlines() if l.startswith("合计")][0])

    caught, missed, left = [], [], []
    try:
        for num, old, new, why in MUTATIONS:
            # 归一化到目标文件的行尾（见main() 里那段注释）
            o = old.replace("\r\n", "\n").replace("\n", nl.decode()).encode("utf-8")
            n = new.replace("\r\n", "\n").replace("\n", nl.decode()).encode("utf-8")
            cnt = original.count(o)
            if cnt != 1:
                print(f"[变异] {num} ✗ 锚点出现 {cnt} 次（要恰好 1 次），脚本要改")
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
            if not code:
                print("      " + "\n      ".join(
                    l for l in out.splitlines() if l.startswith("  失败："))[:800])
            (caught if code else missed).append(num)
    finally:
        TARGET.write_bytes(original)
        if sha(TARGET) != before:
            left.append("app.py")

    print()
    print(f"[变异] 咬住 {len(caught)}/{len(MUTATIONS)}：{' '.join(caught)}")
    if missed:
        print(f"[变异] 未咬住：{' '.join(missed)}")
    print(f"[变异] 源文件逐字节还原：{'是' if not left else '★ 否 ' + str(left)}")
    return 0 if (not missed and not left) else 1


if __name__ == "__main__":
    sys.exit(main())
