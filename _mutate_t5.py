# -*- coding: utf-8 -*-
"""`_t5.py`（窗口接口契约）的**变异验证**（2026-10-09 第 4 组）。

把 `app.py` 里「最大化 ⇄ 还原」那几处逐条改坏，每改一处跑一次 `_t5.py`，
**要求它转红**；跑完按字节还原并比对 sha256。

四条都对应站主报过的那个症状（「全屏之后不能缩小」）的不同坏法：

  ① `_is_maxed` 回去读 `win.state`
     —— 这就是**当初的真 bug**：`win.state` 是 `webview.state.State`（`dict` 子类），
        装的是页面级状态，`str(State())` 恒为 `'{}'` ⇒ 判断恒为 False ⇒
        `_maxed` 每次被重置 ⇒ 只能最大化、永远还原不了。
  ② 拿不到窗口句柄时返回 False（而不是 None）
     —— "问不到"被伪装成"没最大化"，于是反复 maximize。
  ③ `IsZoomed` 的结果取反 —— 判据反了，最大化时以为没最大化。
  ④ `win_max` 变成单向门（永远走 maximize）—— 最直白的那个症状。

★ 两条纪律（与 `_mutate_t6/7/8.py` 一致）：
  · `run_tests()` **必须带超时**，且**超时不算咬住**（单独报，别把"测试挂了"记成"抓住了"）
  · 锚点必须**恰好出现 1 次**，否则脚本自己报错退出，不许瞎改
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "app.py"
TEST = "_t5.py"
PY = sys.executable

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "①",
        "        z = self._zoomed()\n"
        "        return self._maxed if z is None else z",
        '        st = getattr(self._win, "state", None)\n'
        "        if st is not None:\n"
        '            self._maxed = "max" in str(st).lower()\n'
        "        return self._maxed",
        "★ 回去读 win.state（= 当初那个真 bug：State 是 dict，str 恒为 '{}'）⇒ 单向门",
    ),
    (
        "②",
        "        if not hwnd:\n"
        "            return None                      # 非 winforms 后端 / 窗口还没建 ⇒ 退回本地记录",
        "        if not hwnd:\n"
        "            return False",
        "★ 把「问不到」伪装成「没最大化」⇒ 找不到窗口时反复 maximize",
    ),
    (
        "③",
        "            return bool(_user32().IsZoomed(wt.HWND(hwnd)))",
        "            return not bool(_user32().IsZoomed(wt.HWND(hwnd)))",
        "★ 判据取反 ⇒ 最大化时以为没最大化，方向全反",
    ),
    (
        "④",
        "            maxed = self._is_maxed()\n"
        "            if maxed:\n"
        "                self._win.restore()",
        "            maxed = self._is_maxed()\n"
        "            if False:\n"
        "                self._win.restore()",
        "★ 变成单向门：永远 maximize ⇒ 点了缩不回来（站主原话的症状）",
    ),
]

# ★ 一条**故意不写**的变异，记下来免得以后有人"补上"：
#   「把 win_moveto 里的 `self._is_maxed()` 换回 `self._maxed`」。
#   它是**假变异** —— `_t5.py` 的第 ④ 条是直接设 `api._maxed = True` 再拖的，
#   两条路在那个场景下行为完全一样，改了也观察不到差异。
#   真变异的标准：**必须能构造出输入，让改动前后的可观察行为不同**。
#   （真正会暴露它的场景是"OS 说还原了、本地布尔还写着 True"时去拖动 ——
#    目前没有那条用例，属于已知空白，不是这里能糊过去的。）


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_tests() -> tuple[int, str]:
    """跑一次 `_t5.py`，**带超时**（见文件头）。超时返回 99，不算咬住。"""
    env = dict(os.environ, CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD="20000")
    try:
        proc = subprocess.run([PY, str(HERE / TEST)], cwd=HERE, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        return 99, "超时（300 秒）"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    original = TARGET.read_bytes()
    before = sha(TARGET)

    # ★★★ 2026-10-09 修一个**一直存在的洞**：本脚本把 str锚点 encode 成 utf-8 之后，
    #   拿去在**字节**上做 `.count()`。而 `app.py` 存的是 **CRLF** 行尾，
    #   锚点里的 `\n` 是 LF ⇒ 凡是**跨行**的锚点都命中 0 次 ⇒ 那几条变异
    #   会被静默跳过（报"锚点出现 0 次"还算好，最坏是有人把它当"咬住了"）。
    #   之前它报 4/4 是历史遗留 —— 当时那几条锚点恰好是单行、且不含中文。
    #   ⇒ **锚点必须按目标文件的行尾归一化**。判据：先看文件用哪种行尾。
    nl = b"\r\n" if b"\r\n" in original else b"\n"
    print(f"[变异] 目标文件行尾：{'CRLF' if nl == b'\r\n' else 'LF'}"
          f"（锚点会按它归一化）")

    print("[变异] 基线（未改动）应全绿 ……", flush=True)
    code, out = run_tests()
    if code != 0:
        print("[变异] ✗ 基线就是红的，先修基线")
        print(out[-1500:])
        return 2
    total = [l for l in out.splitlines() if l.startswith("合计")]
    print("[变异] 基线通过：" + (total[0] if total else "（没读到合计行）"))

    caught, missed, left = [], [], []
    try:
        for num, old, new, why in MUTATIONS:
            # 归一化到目标文件的行尾（见上面那段注释）
            o = old.replace("\r\n", "\n").replace("\n", nl.decode()).encode("utf-8")
            n = new.replace("\r\n", "\n").replace("\n", nl.decode()).encode("utf-8")
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
            if not code:
                print("      " + "\n      ".join(
                    l for l in out.splitlines() if l.startswith("失败："))[:800])
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
