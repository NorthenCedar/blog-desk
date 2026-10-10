# -*- coding: utf-8 -*-
"""`_t4.py`（写作台 ↔ 站点管理接口的契约测试）的**变异验证**（2026-10-09）。

把 `server.py` 里与留言管理有关的那几行逐条改坏，每改一处跑一次 `_t4.py`，
**要求它转红**；跑完按字节还原并比对 sha256。

第 ① 条就是这个文件存在的理由：`payload` 里少一个 `kind`，
是这轮真的踩到过的 bug（两条表 id 撞号 ⇒ 删 A 删成了 B），
它必须被测试咬住，否则以后再删一次也没人知道。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
TEST = "_t4.py"
PY = sys.executable

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "①",
        'payload = json.dumps({"id": body.get("id"), "kind": kind}).encode("utf-8")',
        'payload = json.dumps({"id": body.get("id")}).encode("utf-8")',
        "★ 不发 kind ⇒ 删回复会删掉另一张表里撞号的顶层留言（本轮真踩过的 bug）",
    ),
    (
        "②",
        'if kind not in ("comment", "reply"):',
        'if kind not in ("comment", "reply", "replys"):',
        "放行不认识的 kind，让接口去猜",
    ),
    (
        "③",
        'raise ContentError(detail or f"删留言失败：HTTP {e.code}") from e',
        'raise ContentError(f"删留言失败：HTTP {e.code}") from e',
        "丢掉接口的原话 ⇒ 界面只看到 HTTP 404/500",
    ),
    (
        "④",
        "        if not tok:",
        "        if False:",
        "没配密钥不 fail closed ⇒ 不带头就往外发",
    ),
    (
        "⑤",
        'raise ContentError(detail or f"读线上留言失败：HTTP {e.code}") from e',
        'raise ContentError(f"读线上留言失败：HTTP {e.code}") from e',
        "读列表时丢掉接口的原话（401 会说成『密钥不对』，现在只会说 HTTP 401）",
    ),
    (
        "⑥",
        'return (self.app.config.get("site") or "https://northcedar.top").rstrip("/")',
        'return self.app.config.get("site") or "https://northcedar.top"',
        "站点地址不 trim 尾斜杠 ⇒ 拼出 //api/…",
    ),
    (
        "⑦",
        'UA = "NorthCedar-Desk/1.0"',
        'UA = "Python-urllib/3.13"',
        "★ 把 UA 换回 urllib 的默认值 ⇒ Cloudflare 一律 403（这轮真踩到的坑）",
    ),
    (
        "⑧",
        ', "User-Agent": self.UA}',
        "}",
        "★ 读列表那条路少带 UA（只有删除那条带了）",
    ),
    (
        "⑨",
        '"User-Agent": self.UA,',
        "",
        "★ 删除那条路少带 UA（只有读列表那条带了）",
    ),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_tests() -> tuple[int, str]:
    proc = subprocess.run(
        [PY, str(HERE / TEST)], cwd=HERE, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    original = SERVER.read_bytes()          # ★ 字节级：文本模式会顺手把 LF 改成 CRLF（吃过一次亏）
    before = sha(SERVER)

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
            SERVER.write_bytes(original.replace(o, n, 1))
            code, out = run_tests()
            SERVER.write_bytes(original)
            print(f"[变异] {num} {'咬住' if code else '★ 漏了'} —— {why}")
            (caught if code else missed).append(num)
    finally:
        SERVER.write_bytes(original)
        if sha(SERVER) != before:
            left.append("server.py")

    print()
    print(f"[变异] 咬住 {len(caught)}/{len(MUTATIONS)}：{' '.join(caught)}")
    if missed:
        print(f"[变异] 未咬住：{' '.join(missed)}")
    print(f"[变异] 源文件逐字节还原：{'是' if not left else '★ 否 ' + str(left)}")
    return 0 if (not missed and not left) else 1


if __name__ == "__main__":
    sys.exit(main())
