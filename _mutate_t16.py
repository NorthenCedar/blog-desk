# -*- coding: utf-8 -*-
"""写作台 · `_t16.py` 的**变异验证**（updated 自动推进，2026-10-27）

纪律与 `_mutate_t12/13/14/15.py` 一致：按字节存取 + sha256 还原校验 + 信号兜底还原
（Windows 上没有 SIGHUP，要 getattr 取）；判据 = 失败 且 命中指定断言。

每条变异都往「看着像做完了、实际没生效或把不该动的动了」那个方向去：
  · 不推进            ⇒ 功能等于没写
  · 不尊重手动清空    ⇒ 用户藏不掉「更新于」
  · 去掉无变化判断    ⇒ 打开再保存就刷日期，把「更新于」变成噪声
  · 去掉不倒退判断    ⇒ 未来日期被拉回今天
  · 去掉滞后钳位      ⇒ 客户端还没同步就把日期顶回去（27→25 来回跳）
  · 去掉「没有就不加」⇒ 四篇本来不显示「更新于」的文章被动加字段
  · 只比正文不比字段  ⇒ 改标题/摘要/封面不刷新更新日
  · write_post 不接   ⇒ 纯函数对、线上不生效（最容易糊过去的一种）

用法：python _mutate_t16.py
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
    (1, '    out = dict(fm)\n    out["updated"] = target\n    return out\n',
     '    out = dict(fm)\n    return out\n',
     "① 正文改过"),
    (2, '    if not original or not current:\n        return fm\n',
     '    if not original:\n        return fm\n',
     "③ 手动清空"),
    (3, '            and _norm_body(body) == _norm_body(old_body)):\n',
     '            and False):\n',
     "④ 打开又保存"),
    (4, '    if current < original:\n',
     '    if False:\n',
     "⑨ 滞后客户端"),
    (5, '    if not original or not current:\n        return fm\n',
     '    if False:\n        return fm\n',
     "⑩ 没有 updated"),
    (6, '            == render_post_frontmatter({**fm, "updated": current})\n',
     '            is not None\n',
     "⑦ 只改字段"),
    (7, '    if old_text is None:\n        return fm\n',
     '    if old_text is None:\n        out = dict(fm)\n        out["updated"] = today\n        return out\n',
     "⑥ 新建文章不写"),
    (8, '        fm = sync_updated(fm, body, old_text, today or today_local())\n',
     '        fm = fm  # 变异 8：没有接上\n',
     "⑧ write_post 端到端"),
]

caught = 0
missed: list[int] = []

for n, old, new, hit in MUT:
    src = orig.decode("utf-8")
    if old not in src:
        print(f"  .. 变异 {n} 跳过：锚点没命中（先查源码是不是变了，别改门闩）")
        missed.append(n)
        continue
    mutated = src.replace(old, new, 1)
    if mutated == src:
        print(f"  .. 变异 {n} 跳过：替换后没变")
        missed.append(n)
        continue
    TARGET.write_bytes(mutated.encode("utf-8"))
    failed, out = False, ""
    try:
        r = subprocess.run([sys.executable, str(HERE / "_t16.py")], capture_output=True,
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
        missed.append(n)
        print(f"  ?? 变异 {n} 红了但没命中「{hit}」：")
        for line in out.splitlines():
            if "FAIL" in line:
                print("      " + line.strip())
    else:
        missed.append(n)
        print(f"  ×× 变异 {n} **没咬住** —— 判据对「{hit}」不敏感")
    if not clean:
        print(f"  ！！ 变异 {n} 还原不干净，停止（后续结论一律不可信）")
        sys.exit(2)

print("\n" + "=" * 60)
print(f"结果：{caught}/{len(MUT)} 被咬住")
if missed:
    print("未咬住/跳过：", ", ".join(str(x) for x in missed))
    sys.exit(1)
sys.exit(0)
