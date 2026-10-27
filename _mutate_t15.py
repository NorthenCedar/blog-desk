# -*- coding: utf-8 -*-
"""写作台 · `_t15.py` 的**变异验证**（YAML 标量序列化，2026-10-27）

纪律与 `_mutate_t12/13/14.py` 一致：按字节存取 + sha256 还原校验 + 信号兜底还原
（Windows 上没有 SIGHUP，要 getattr 取）；判据 = 失败 且 命中指定断言。

每条变异都往「悄悄退回事故形态、表面还像修好了」那个方向去：
  · 不转义换行   ⇒ 事故原样复现（引号被换行截断）
  · 不转义回车   ⇒ CRLF 文件上复现
  · 不转义其余控制字符 ⇒ 解析器崩
  · 不转义引号   ⇒ 引号提前闭合
  · 不翻倍反斜杠 ⇒ `\\` 被当成转义符，内容悄悄变
  · 放宽 needs_quote ⇒ 控制字符连引号都不加，直接写进去
  · 无条件全加引号 ⇒ diff 噪声（既有约定被破坏）

用法：python _mutate_t15.py
"""
from __future__ import annotations

import atexit
import hashlib
import signal
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "lib" / "content.py"

orig = TARGET.read_bytes()
sha0 = hashlib.sha256(orig).hexdigest()


def restore():
    if TARGET.read_bytes() != orig:
        TARGET.write_bytes(orig)


for sig in ("SIGINT", "SIGTERM", "SIGHUP"):
    s = getattr(signal, sig, None)
    if s is not None:
        signal.signal(s, lambda *_: (restore(), sys.exit(1)))
atexit.register(restore)

# (编号, 原文锚点, 替换成, 期望在失败输出里看到的断言名)
MUT = [
    (1, r'        .replace("\n", "\\n")' + "\n", "",
     "① 多行文本"),
    (2, r'        .replace("\r", "\\r")' + "\n", "",
     "② CRLF"),
    # ★ 变异 3（不转义制表符）**已删**：双引号里的裸 tab 在 YAML 里合法，
    #   转不转义都能原样读回 ⇒ 那是"改动不产生可观察差异"的假变异。
    #   `\t` 那行转义保留，但定性是**格式规范化**（与 TS 侧 JSON.stringify 对齐），
    #   不是正确性修复 —— 别拿它当门闩的证据。
    (4, r'    esc = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", '
        r'lambda m: "\\x%02x" % ord(m.group()), esc)' + "\n", "",
     "④ 其余 C0"),
    (5, r"""        .replace('"', '\\"')""" + "\n", "",
     "⑤ 文本自带双引号"),
    (6, r'        s.replace("\\", "\\\\")' + "\n",
     r'        s.replace("\\", "\\")' + "\n",
     "⑤ 文本自带反斜杠"),
    (7, "        or any(ord(c) < 0x20 or ord(c) == 0x7F for c in s)\n", "",
     "④ 其余 C0"),
    (8, "    if not needs_quote:\n        return s\n",
     "    if False:\n        return s\n",
     "⑦ 普通中文句子"),
]

caught = 0
failed_to_catch: list[int] = []

for n, old, new, hit in MUT:
    src = orig.decode("utf-8")
    if old not in src:
        print(f"  .. 变异 {n} 跳过：锚点没命中（先查源码是不是变了，别改门闩）")
        failed_to_catch.append(n)
        continue
    mutated = src.replace(old, new, 1)
    if mutated == src:
        print(f"  .. 变异 {n} 跳过：替换后没变")
        failed_to_catch.append(n)
        continue
    TARGET.write_bytes(mutated.encode("utf-8"))
    failed, out = False, ""
    try:
        r = subprocess.run([sys.executable, str(HERE / "_t15.py")], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=300,
                           cwd=str(HERE))
        out = r.stdout + r.stderr
        failed = r.returncode != 0
    except Exception as e:  # noqa: BLE001
        failed, out = True, f"变异脚本自己炸了：{e}"
    finally:
        restore()

    clean = hashlib.sha256(TARGET.read_bytes()).hexdigest() == sha0
    if failed and hit in out:
        caught += 1
        print(f"  OK  变异 {n} 咬住 —— 「{hit}」")
    elif failed:
        failed_to_catch.append(n)
        print(f"  ?? 变异 {n} 红了但没命中「{hit}」：")
        for line in out.splitlines():
            if "FAIL" in line:
                print("      " + line.strip())
    else:
        failed_to_catch.append(n)
        print(f"  ×× 变异 {n} **没咬住** —— 判据对「{hit}」不敏感")
    if not clean:
        print(f"  ！！ 变异 {n} 还原不干净，停止（后续结论一律不可信）")
        sys.exit(2)

print("\n" + "=" * 60)
print(f"结果：{caught}/{len(MUT)} 被咬住")
if failed_to_catch:
    print("未咬住/跳过：", ", ".join(str(x) for x in failed_to_catch))
    sys.exit(1)
sys.exit(0)
