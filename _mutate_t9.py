# -*- coding: utf-8 -*-
"""`_t9.py`（推送安全：blob 字节不许被文本层改坏 + 回读要重试）的**变异验证**（2026-10-09）。

把 `lib/gitops.py` 里那四处逐条改坏，每改一处跑一次 `_t9.py`，**要求它转红**；
跑完按字节还原并比对 sha256。

四条各自咬住什么：
  ① `git_bin` 退回 `_run`（就是**那个真 bug 的原形**）⇒ 二进制被 errors="replace" 改烂，
     blob sha 变、组装出的 tree 变 ⇒ ①③ 必须红
  ② `blob_bytes` 去掉「在不在 HEAD 里」的判断 ⇒ 删掉的文件会抛异常而不是返回 None，
     `push()` 也就发不出 `"sha": null` ⇒ ④ 必须红
  ③ 把那个判断**反过来**（`!= 0` → `== 0`）⇒ 存在说成不存在、不存在说成存在 ⇒ ④ 必须红
  ④ `_settle_ref` 去掉重试（= 修复前写法）⇒ 只读一次，PATCH 成功也会被报成失败 ⇒ ⑤ 必须红

★ 与 `_mutate_t6/t7/t8` 同样的两条纪律：
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
TARGET = HERE / "lib" / "gitops.py"
TEST = "_t9.py"
PY = sys.executable

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "①",
        "    p = proc.run([\"git\", *args], cwd=str(repo), capture_output=True, timeout=timeout)\n"
        "    if p.returncode != 0:\n"
        "        err = (p.stderr or b\"\").decode(\"utf-8\", \"replace\").strip()\n"
        "        raise GitError(f\"git {' '.join(args)} 失败：{err[:400]}\")\n"
        "    return p.stdout",
        "    return _run([\"git\", *args], repo, timeout).stdout.encode(\"utf-8\", \"surrogateescape\")",
        "★ git_bin 退回文本读（errors=replace）⇒ 二进制被改烂，tree 保险丝必跳",
    ),
    (
        "②",
        "    if proc.run([\"git\", \"cat-file\", \"-e\", f\"HEAD:{path}\"],\n"
        "                cwd=str(repo), capture_output=True).returncode != 0:\n"
        "        return None                                  # 不在 HEAD 里 ⇒ 本次是「删除」\n"
        "    return git_bin(repo, \"cat-file\", \"blob\", f\"HEAD:{path}\")",
        "    return git_bin(repo, \"cat-file\", \"blob\", f\"HEAD:{path}\")",
        "★ 不判断「在不在 HEAD 里」⇒ 删掉的文件抛异常，push 发不出 sha:null",
    ),
    (
        "③",
        "    if proc.run([\"git\", \"cat-file\", \"-e\", f\"HEAD:{path}\"],\n"
        "                cwd=str(repo), capture_output=True).returncode != 0:",
        "    if proc.run([\"git\", \"cat-file\", \"-e\", f\"HEAD:{path}\"],\n"
        "                cwd=str(repo), capture_output=True).returncode == 0:",
        "★ 把存在性判断反过来 ⇒ 存在的说成不存在、不存在的说成存在",
    ),
    (
        "④",
        "    got = \"\"\n"
        "    for i in range(max(1, tries)):\n"
        "        got = get_ref()\n"
        "        if got == want:\n"
        "            return got\n"
        "        if i < tries - 1:\n"
        "            time.sleep(delay)\n"
        "    return got",
        "    return get_ref()",
        "★ 回读不重试（= 修复前写法）⇒ 把一次成功的推送报成「回读不一致」",
    ),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_tests() -> tuple[int, str]:
    """跑一次 `_t9.py`，**带超时**。超时返回 99，不算咬住。"""
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
    # ★ 行尾归一化（2026-10-09，`_mutate_t5.py` 先撞上）：目标文件若哪天改成 CRLF，
    #   锚点里的 `\\n`（LF）就会在字节层命中 0 次 ⇒ 那条变异被**静默跳过**。
    #   当时 `_mutate_t5.py` 报 4/4 是历史遗留，实际有三条从未真正执行过。
    nl = b"\r\n" if b"\r\n" in original else b"\n"

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
            o, n = old.replace("\r\n", "\n").replace("\n", nl.decode()).encode("utf-8"), new.replace("\r\n", "\n").replace("\n", nl.decode()).encode("utf-8")
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
                    l for l in out.splitlines() if l.startswith("  失败："))[:800])
            (caught if code else missed).append(num)
    finally:
        TARGET.write_bytes(original)
        if sha(TARGET) != before:
            left.append("lib/gitops.py")

    print()
    print(f"[变异] 咬住 {len(caught)}/{len(MUTATIONS)}：{' '.join(caught)}")
    if missed:
        print(f"[变异] 未咬住：{' '.join(missed)}")
    print(f"[变异] 源文件逐字节还原：{'是' if not left else '★ 否 ' + str(left)}")
    return 0 if (not missed and not left) else 1


if __name__ == "__main__":
    sys.exit(main())
