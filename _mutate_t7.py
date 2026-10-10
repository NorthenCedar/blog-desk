# -*- coding: utf-8 -*-
"""`_t7.py`（标签注册表 增/删 的契约测试）的**变异验证**（2026-10-09）。

把 `lib/content.py` 里标签那几处逐条改坏，每改一处跑一次 `_t7.py`，**要求它转红**；
跑完按字节还原并比对 sha256。

为什么这五条值得各自咬一次 —— 它们全是**会真的写坏站上源码**的形态：
  ① 不校验长度   ⇒ 空名/1 字/6 字会被写进 `src/tag-registry.ts`
  ② 不校验重名   ⇒ 同一标签出现两次（大小写不同也算），站点校验直接红
  ③ 去掉"在用不许删" ⇒ 删掉正在被文章用的标签 ⇒ 那几篇文章**构建失败**
  ④ 多行数组被压平 ⇒ 一次"加个标签"把整个数组挤成一行，diff 没法看
  ⑤ 不剥首尾空白 ⇒ 写出 `' 读书 '` 这种带空格的标签

★ 与 `_mutate_t6.py` 同样的两条纪律：
  · `run_tests()` **必须带超时**（超时不算咬住，单独报，别把"测试挂了"记成"抓住了"）
  · 锚点必须**恰好出现 1 次**，否则脚本自己先报错退出，不许瞎改
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "lib" / "content.py"
TEST = "_t7.py"
PY = sys.executable

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "①",
        "        if not (self.TAG_MIN <= len(n) <= self.TAG_MAX):",
        "        if False:",
        "★ 去掉长度校验 ⇒ 1 字 / 6 字的标签会被写进 src/tag-registry.ts",
    ),
    (
        "②",
        "            if t.lower() == low:",
        "            if False:",
        "★ 去掉重名检查 ⇒ 同名（含只差大小写）标签可以重复登记",
    ),
    (
        "③",
        "        if used:\n"
        "            head = \"、\".join(used[:5]) + (\"…\" if len(used) > 5 else \"\")\n"
        "            raise ContentError(",
        "        if False:\n"
        "            head = \"、\".join(used[:5]) + (\"…\" if len(used) > 5 else \"\")\n"
        "            raise ContentError(",
        "★ 去掉「正在被文章用就不许删」⇒ 删完那几篇文章构建失败（本轮最重要的一条）",
    ),
    (
        "④",
        "        if \"\\n\" not in old_inner:\n"
        "            return \", \".join(f\"'{t}'\" for t in tags)",
        "        if True:\n"
        "            return \", \".join(f\"'{t}'\" for t in tags)",
        "★ 多行数组永远被压成一行 ⇒ 一次「加标签」把整个数组挤平，diff 没法看",
    ),
    (
        "⑤",
        "        self._write(p, text[:m.start(2)] + inner + text[m.end(2):], nl)",
        "        self._write(p, \"export const TAGS = [\" + inner + \"] as const;\\n\", nl)",
        "★ 不再定点替换、改成整文件重写 ⇒ 注释与 `as const` 被吃掉（这是最贵的一种坏法）",
    ),
    (
        "⑥",
        "            raise ContentError(\n"
        "                f\"{self.TAGS_FILE} 的形状变了（找不到 export const TAGS = [...] as const）\"\n"
        "                f\"—— 没有动它。请手工改，改完再回来用这个面板。\")",
        "            return",
        "★ 形状不认识时不再报错收手、静默返回 ⇒ 面板会显示「成功」但其实什么都没做",
    ),
    (
        "⑦",
        "        if _LOOKS_NON_STR.match(n):",
        "        if False:",
        "★ 不查「会被 YAML 读成数字/布尔」⇒ 纯数字或 true 的标签写进去，站点校验直接拒",
    ),
    # ── 以下四条是 2026-10-09 新增的「文档镜像自动补行」（`docs/content-model.md` §4）──
    (
        "⑧",
        "        tags = set(self.read_tags())\n"
        "        return [t for t in self._doc_names(*got) if t not in tags]",
        "        tags = set(self.read_tags())\n"
        "        _all = re.findall(r\"^\\|\\s*`([^`]+)`\\s*\\|\", text.replace(\"\\r\\n\", \"\\n\"), re.M)\n"
        "        return [t for t in _all if t not in tags]",
        "★ 定位退回「整份文件抓 `| `名` |`」⇒ §1 字段表里的 title/date/summary 全被当成标签",
    ),
    (
        "⑨",
        "        if name in self._doc_names(*got):\n"
        "            return False, f\"表里已经有「{name}」了，没重复加\"",
        "        if False:\n"
        "            return False, f\"表里已经有「{name}」了，没重复加\"",
        "★ 去掉「表里已有同名行」的保护 ⇒ 加/删/加一轮，表里就叠出两行同名",
    ),
    (
        "⑩",
        "        lines.insert(last, f\"| `{name}` | {self.DOC_STUB} | — |\")",
        "        lines.insert(2, f\"| `{name}` | {self.DOC_STUB} | — |\")",
        "★ 插错位置（不是表尾）⇒ 表头被挤走、行落在表外，表格结构被破坏",
    ),
    (
        "⑪",
        "        lines.insert(last, f\"| `{name}` | {self.DOC_STUB} | — |\")\n"
        "        self._write(p, \"\\n\".join(lines), nl)",
        "        lines.insert(last, f\"| `{name}` | {self.DOC_STUB} | — |\")",
        "★ 只改内存不落盘 ⇒ 面板报「已补一行」，打开文档却什么都没有（假成功）",
    ),
]

# ★ 曾经还有一条，被删掉了 —— 记在这里免得以后有人再加回来：
#   「去掉 `n = (name or '').strip()`」。我当初以为它能咬住，跑下来是**漏**的。
#   原因：`strip` 只是多余的一道，后面 `any(c.isspace() for c in n)` 对
#   `" 读书 "` / `"　读书"`（全角空格 isspace() 也为真）**照样会拒**，
#   去掉 strip 观察不到任何行为差异。
#   ⇒ 一个「改了行为也不变」的变异是**假变异**，它漏掉不代表测试有洞。
#   真变异的标准是：**必须能构造出输入，让改动前后的可观察行为不同**。


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_tests() -> tuple[int, str]:
    """跑一次 `_t7.py`，**带超时**（见文件头）。超时返回 99，不算咬住。"""
    env = dict(os.environ, CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD="20000")
    try:
        proc = subprocess.run([PY, str(HERE / TEST)], cwd=HERE, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return 99, "超时（120 秒）"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


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
            left.append("lib/content.py")

    print()
    print(f"[变异] 咬住 {len(caught)}/{len(MUTATIONS)}：{' '.join(caught)}")
    if missed:
        print(f"[变异] 未咬住：{' '.join(missed)}")
    print(f"[变异] 源文件逐字节还原：{'是' if not left else '★ 否 ' + str(left)}")
    return 0 if (not missed and not left) else 1


if __name__ == "__main__":
    sys.exit(main())
