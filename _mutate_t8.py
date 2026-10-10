# -*- coding: utf-8 -*-
"""`_t8.py`（标签页端到端，界面层）的**变异验证**（2026-10-09）。

`_mutate_t7.py` 咬的是**后端契约**（`lib/content.py`）。这里咬的是**前端接线**
（`ui/app.js`）—— 两者是两套完全不同的坏法，必须各咬一次：

  ① 删除按钮忘了禁用    ⇒ 站主在界面上就能删掉**正在被文章用**的标签 ⇒ 那几篇文章构建失败
  ② 孤儿不再标 orphan   ⇒ 「登记了却没人用」这件事在界面上看不见（而它会直接让构建红）
  ③ POST 字段名写错     ⇒ 界面一直报"名字不合法"，但**没有一个字写进文件**
  ④ 首屏忘了存 tagInfo  ⇒ 标签页永远空（但加点什么又活了，最难查的一种）
  ⑤ 「表格里多出」不再渲染 ⇒ 加过又删掉的标签会在 §4 表里留下幽灵行，界面上看不见

★ 用 `DESK_T8_SRC=1`（`_serve.py` 源码起服务）而不是打好的 exe：
  变异要跑四轮，每轮重打包一次不现实。`ui/app.js` 从磁盘读 ⇒ 改完立刻生效。
  打包那条链路由 `_t8.py` 的**默认**跑法（打 exe）覆盖，两者是不同的问题。

★ 与 `_mutate_t6.py` / `_mutate_t7.py` 同两条纪律：
  · `run_tests()` 必须带超时；**超时记 99、不算咬住**（那多半是测试自己坏了）
  · 锚点必须**恰好出现 1 次**，否则脚本自己报错退出，不许瞎改
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "ui" / "app.js"
TEST = "_t8.py"
PY = sys.executable

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "①",
        "        del.disabled = used.length > 0;",
        "        del.disabled = false;",
        "★ 删除按钮不再禁用 ⇒ 站主在界面上就能删掉**正在被文章用**的标签，"
        "那几篇文章随即构建失败（最贵的一条）",
    ),
    (
        "②",
        "        row.className = 'tagrow' + (used.length ? '' : ' orphan');",
        "        row.className = 'tagrow';",
        "★ 孤儿标签不再标出来 ⇒ 「登记了却没人用」在界面上看不见，"
        "而它正是让 `npm run verify` 变红的那件事",
    ),
    (
        "③",
        "    const r = await POST('/api/tags', { name });",
        "    const r = await POST('/api/tags', { tag: name });",
        "★ POST 的字段名写错 ⇒ 界面永远报「名字不合法」，文件却一个字节都没写",
    ),
    (
        "④",
        "    S.tagInfo = r;          // 管理页要的：usage / unused / doc 漂移",
        "    S.tagInfo = null;       // 变异：忘了存",
        "★ 首屏忘了存 tagInfo ⇒ 标签页永远空；但因为增删之后会重新拉一次，"
        "「点一下就活了」，是最难查的一种",
    ),
    (
        "⑤",
        "    if (extra.length) {",
        "    if (false) {",
        "★ 不再渲染「表格里多出」⇒ 加过又删掉的标签在 §4 表里留下一行幽灵，"
        "界面上一句话都不说（文档就在安静地撒谎）",
    ),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_tests() -> tuple[int, str]:
    """跑一次 `_t8.py`（源码模式），**带超时**。超时返回 99，不算咬住。"""
    env = dict(os.environ, CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD="20000",
               DESK_T8_SRC="1")
    try:
        proc = subprocess.run([PY, str(HERE / TEST)], cwd=HERE, env=env,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=240)
    except subprocess.TimeoutExpired:
        return 99, "超时（240 秒）"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    original = TARGET.read_bytes()
    before = sha(TARGET)
    # ★ 行尾归一化（2026-10-09，`_mutate_t5.py` 先撞上）：目标文件若哪天改成 CRLF，
    #   锚点里的 `\\n`（LF）就会在字节层命中 0 次 ⇒ 那条变异被**静默跳过**。
    #   当时 `_mutate_t5.py` 报 4/4 是历史遗留，实际有三条从未真正执行过。
    nl = b"\r\n" if b"\r\n" in original else b"\n"

    print("[变异] 基线（未改动）应全绿 ……", flush=True)
    code, out = run_tests()
    if code != 0:
        print("[变异] ✗ 基线就是红的，先修基线")
        print(out[-1500:])
        return 2
    print("[变异] 基线：" + [l for l in out.splitlines() if l.startswith("通过")][-1], flush=True)

    caught, missed, left = [], [], []
    try:
        for num, old, new, why in MUTATIONS:
            o, n = old.replace("\r\n", "\n").replace("\n", nl.decode()).encode("utf-8"), new.replace("\r\n", "\n").replace("\n", nl.decode()).encode("utf-8")
            if original.count(o) != 1:
                print(f"[变异] {num} ✗ 锚点出现 {original.count(o)} 次（要恰好 1 次），脚本要改",
                      flush=True)
                missed.append(f"{num} 锚点")
                continue
            TARGET.write_bytes(original.replace(o, n, 1))
            code, out = run_tests()
            TARGET.write_bytes(original)
            if code == 99:
                print(f"[变异] {num} ★ 超时（既不是绿也不是红，要查）—— {why}", flush=True)
                missed.append(num)
                continue
            print(f"[变异] {num} {'咬住' if code else '★ 漏了'} —— {why}", flush=True)
            if not code:
                print("      " + "\n      ".join(
                    l for l in out.splitlines() if l.strip().startswith("✘"))[:800])
            (caught if code else missed).append(num)
    finally:
        TARGET.write_bytes(original)
        if sha(TARGET) != before:
            left.append("ui/app.js")

    print(flush=True)
    print(f"[变异] 咬住 {len(caught)}/{len(MUTATIONS)}：{' '.join(caught)}", flush=True)
    if missed:
        print(f"[变异] 未咬住：{' '.join(missed)}", flush=True)
    print(f"[变异] 源文件逐字节还原：{'是' if not left else '★ 否 ' + str(left)}", flush=True)
    return 0 if (not missed and not left) else 1


if __name__ == "__main__":
    sys.exit(main())
