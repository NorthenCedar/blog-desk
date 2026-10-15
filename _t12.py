# -*- coding: utf-8 -*-
"""写作台 · **推完之后把本地 HEAD 对齐到远端**（2026-10-15）

站主这次报的是：写作台明明显示"推成功了"，可回到终端 `git push` 却被拒：

    Updates were rejected because the remote contains work that you do not have locally.

**病根不是网络、不是 API，也不是凭据** —— 是本通道的推送方式：
它用 GitHub API 建提交（blob → tree → commit → PATCH ref），而那个提交的
**父是"远端当时的 main"**，不是你本地刚提交的那个。于是：

    本地：dfd65b4 ── f289173          （父 = dfd65b4）
    远端：dfd65b4 ── af4158a          （父 = dfd65b4，也是 dfd65b4）

两边是**兄弟**，不是父子 ⇒ 终端 `git push` 判定为分叉、拒绝。

⚠ 顺带查清一件事：`push()` 的 docstring 里那句「`git fetch` 在本机连不通
（github.com:443 SSL 被重置，已实测）」**已经过期** —— 后来配了
`http.sslBackend=openssl` + 自带 CA 包，`git fetch` 现在是通的，
`git cat-file -e <远端sha>` 读回来是 `commit`。所以"对齐本地"这件事
**现在做得了**，当年做不了只是因为通道坏了。

★★★ 本文件第一件事是**先证明夹具真能咬住这个 bug**（第二节反向对照）：
先把对齐那一步摘掉，看断言会不会红 —— 不然"修完通过"可能只是夹具太温柔。

守四件事：
  ① **三道闸缺一不可**。这个函数的前身栽过：`git reset --soft origin/main`
     在 fetch 静默失败时会**把 HEAD 退回旧提交**（工作区内容不变，历史被改写）。
     ⇒ 闸①fetch 退出码 0 · 闸②远端对象真的在本地 · 闸③两边内容树相等。
        任一条不过 ⇒ **原地停下、一个字节都不改**。
  ② 对齐用 `--soft`：工作区与暂存区一个字节都不许动（逐字节验）。
  ③ 成功之后 `git status` 必须干净 —— 不干净说明我的推论错了。
  ④ `commit_all()` 提交前先 `git pull --rebase`（治"远端比你本地新"那种情况），
     **拉不下来就不许提交** —— 宁可报错也别制造一个分叉的兄弟提交。

全程在 `tempfile` 里造**真仓库 + 本地裸库当 origin**：真跑 git、但不出网。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib import gitops  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))
    print(("  ✔ " if ok else "  ✗ ") + name + (f"　{detail}" if detail else ""))


def run(args, cwd, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=e)


def pinned(offset_seconds: int) -> dict[str, str]:
    """把提交时间**钉死**，让"两个内容相同、提交不同的兄弟"这件事不再看运气。

    ★ 为什么非钉不可（实测踩出来的）：git 提交对象里带作者/提交者时间戳，
      精度只有**秒**。第①节那两个兄弟提交作者、邮箱、消息、内容、父提交
      **全都一样**，唯一的差别就是时间戳 ——
      两次提交一旦落在同一秒，SHA 就**完全相同**，"兄弟"变成了"同一个"，
      下面那条前提直接红。实测约每 10 次里飘中 1 次。
      正解是把时间钉死，不是"跑慢一点碰运气"。
    """
    return {"GIT_AUTHOR_DATE": f"2026-01-01T00:00:0{offset_seconds}+00:00",
            "GIT_COMMITTER_DATE": f"2026-01-01T00:00:0{offset_seconds}+00:00"}


def mkrepo(base: Path, name: str) -> Path:
    """一个真仓库。origin 指向**本地裸库** ⇒ fetch/pull 都能真跑，但不出网。"""
    origin = base / f"{name}-origin.git"
    work = base / name
    run(["git", "init", "--bare", "-b", "main", str(origin)], base)
    run(["git", "clone", str(origin), str(work)], base)
    run(["git", "config", "user.email", "t@t"], work)
    run(["git", "config", "user.name", "t"], work)
    # ⚠ 这里**故意不**设 `core.quotePath false`：夹具要是替生产代码把这件事做了，
    #   第⑧节的反向对照就永远看不到转义、整节变成恒真。
    #   正解是生产代码在**每次 status 调用上带 `-c`**，不依赖仓库里有什么设置。
    (work / "a.md").write_text("一\n", encoding="utf-8")
    run(["git", "add", "-A"], work)
    run(["git", "commit", "-m", "初版"], work)
    run(["git", "push", "origin", "main"], work)
    return work


def head(work: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], work).stdout.strip()


def tree(work: Path, ref: str = "HEAD") -> str:
    return run(["git", "rev-parse", f"{ref}^{{tree}}"], work).stdout.strip()


def status(work: Path) -> str:
    return run(["git", "status", "--porcelain"], work).stdout.strip()


def snapshot(d: Path) -> dict[str, bytes]:
    """工作区逐字节快照 —— `--soft` 的本质就是"这里一个字节都不许动"。"""
    return {str(p.relative_to(d)): p.read_bytes()
            for p in sorted(d.rglob("*")) if p.is_file() and ".git" not in p.parts}


print("=== ① 正常路径：推完之后 HEAD 对齐到远端那个提交 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    w = mkrepo(base, "a")
    # 模拟"API 建提交"：远端 main 上多一个**兄弟**提交（父 = 本地 HEAD，内容相同）
    before_head = head(w)
    (w / "a.md").write_text("二\n", encoding="utf-8")
    run(["git", "add", "-A"], w)
    run(["git", "commit", "-m", "写作台：内容更新，写说说"], w, env=pinned(0))
    local_commit = head(w)
    snap = snapshot(w)

    # 另起一个"远端侧"的克隆，做出那个 API 提交，再把 origin 推上去
    other = base / "other"
    run(["git", "clone", str(base / "a-origin.git"), str(other)], base)
    run(["git", "config", "user.email", "t@t"], other)
    run(["git", "config", "user.name", "t"], other)
    (other / "a.md").write_text("二\n", encoding="utf-8")
    run(["git", "add", "-A"], other)
    # ★ 时间钉在第 1 秒（本地那个钉在第 0 秒）⇒ 两个 SHA 必然不同，与跑多快无关
    run(["git", "commit", "-m", "写作台：内容更新，写说说"], other, env=pinned(1))
    remote_commit = head(other)
    run(["git", "push", "origin", "main"], other)
    chk("前提：本地与远端已经是兄弟（内容相同、提交不同）",
        local_commit != remote_commit and tree(w) == tree(other),
        f"本地 {local_commit[:8]} / 远端 {remote_commit[:8]}")

    r = gitops._sync_with_remote(w, remote_commit)
    chk("对齐成功", r.get("ok") is True, str(r.get("why", "")))
    chk("HEAD 现在指向远端那个提交", head(w) == remote_commit, head(w)[:8])
    chk("★ 工作区一个字节都没动", snapshot(w) == snap)
    chk("★ git status 干净", status(w) == "", repr(status(w)))
    chk("★ 内容树没变（对齐不是改内容）", tree(w) == tree(other))
    _a_before = before_head

print("\n=== ② 三道闸：任一条不过 ⇒ 原地停下、一个字节都不改 ===")
# ★ `_run` 替身**必须在循环外取一次**并逐轮还原。第一版在循环里取，
#   于是第二轮的 `orig_run` 取到的已经是第一轮的替身 ⇒ 后面两闸全被短路成"闸①"，
#   连③的 commit_all 也跟着短路成"工作区没有改动"。替身漏一轮，后面的读数全是假的。
REAL_RUN = gitops._run
cases = [
    ("闸①fetch 失败", "fetch", "远端提交 abcdef12 不在本地"),
    ("闸②远端对象不在本地", "object", "远端提交 0000000 不在本地"),
    ("闸③两边内容树不等", "tree", "内容树不同"),
]
for label, want_gate, why_hint in cases:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        w = mkrepo(base, "b")
        (w / "a.md").write_text("二\n", encoding="utf-8")
        run(["git", "add", "-A"], w)
        run(["git", "commit", "-m", "本地那次"], w)
        h0, t0, s0 = head(w), tree(w), snapshot(w)

        if want_gate == "fetch":
            gitops._run = lambda *a, **k: subprocess.CompletedProcess(
                a[0] if a else [], 128, "", "fatal: unable to access 'https://…': Connection reset")
            r = gitops._sync_with_remote(w, "0" * 40)
        elif want_gate == "object":
            # fetch 真跑（会成功），但给的这个 sha 本地确实没有 ⇒ 该由闸②拦住
            gitops._run = REAL_RUN
            r = gitops._sync_with_remote(w, "0" * 40)
        else:
            gitops._run = REAL_RUN
            other = base / "c"
            run(["git", "clone", str(base / "b-origin.git"), str(other)], base)
            run(["git", "config", "user.email", "t@t"], other)
            run(["git", "config", "user.name", "t"], other)
            (other / "a.md").write_text("完全不同的内容\n", encoding="utf-8")
            run(["git", "add", "-A"], other)
            run(["git", "commit", "-m", "远端那次"], other)
            sha = head(other)
            # ★ 必须真的推上去：不然 fetch 拿不到这个对象，闸②会先拦下来 ——
            #   那道闸拦得没错（对象确实不在本地），只是这一节要考的是闸③。
            run(["git", "push", "origin", "main"], other)
            run(["git", "fetch", "origin", "main"], w)
            chk("闸③前提：对象已在本地，但两边内容不同",
                run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], w).returncode == 0
                and tree(w) != tree(w, sha))
            r = gitops._sync_with_remote(w, sha)
        gitops._run = REAL_RUN          # 逐轮还原，绝不留给下一轮

        chk(f"{label}：判为不成功", r.get("ok") is not True, f"gate={r.get('gate')}")
        chk(f"{label}：★ 停在**自己那一道闸**上", r.get("gate") == want_gate, str(r.get("gate")))
        chk(f"{label}：HEAD 一个字节都没动", head(w) == h0, head(w)[:8])
        chk(f"{label}：内容树没动", tree(w) == t0)
        chk(f"{label}：工作区没动", snapshot(w) == s0)
        if want_gate != "tree":
            chk(f"{label}：说清了为什么停下", bool(r.get("why")), str(r.get("why"))[:60])

print("\n=== ③ commit_all 提交前先拉：远端比你本地新时，不许造出兄弟提交 ===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    w = mkrepo(base, "d")
    # 远端先走一步（模拟"你在终端推了，然后写作台又提交"）
    other = base / "e"
    run(["git", "clone", str(base / "d-origin.git"), str(other)], base)
    run(["git", "config", "user.email", "t@t"], other)
    run(["git", "config", "user.name", "t"], other)
    (other / "b.md").write_text("远端先有的\n", encoding="utf-8")
    run(["git", "add", "-A"], other)
    run(["git", "commit", "-m", "远端那次"], other)
    run(["git", "push", "origin", "main"], other)
    remote_head = head(other)
    local_head = head(w)
    chk("前提：远端领先本地", remote_head != local_head, f"{local_head[:8]} → {remote_head[:8]}")

    (w / "c.md").write_text("本地这次\n", encoding="utf-8")
    r = gitops.commit_all(w, "写作台：本地这次")
    chk("commit_all 成功", r.get("ok") is True, str(r)[:80])
    new_head = head(w)
    parent = run(["git", "rev-parse", "HEAD^"], w).stdout.strip()
    chk("★ 新提交的父是**远端**那个提交（不再是兄弟）", parent == remote_head,
        f"父 {parent[:8]} / 远端 {remote_head[:8]}")
    chk("★ 工作区仍干净", status(w) == "")

print("\n=== ④ 反向对照：不 pre-pull 直接提交，父就是旧的本地 HEAD（= 分叉的兄弟）===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    w = mkrepo(base, "f")
    other = base / "g"
    run(["git", "clone", str(base / "f-origin.git"), str(other)], base)
    run(["git", "config", "user.email", "t@t"], other)
    run(["git", "config", "user.name", "t"], other)
    (other / "b.md").write_text("远端先有的\n", encoding="utf-8")
    run(["git", "add", "-A"], other)
    run(["git", "commit", "-m", "远端那次"], other)
    run(["git", "push", "origin", "main"], other)
    remote_head = head(other)
    (w / "c.md").write_text("本地这次\n", encoding="utf-8")

    # 手工做一遍「没有 pre-pull」的提交（= 改之前的 commit_all 行为）
    run(["git", "add", "-A"], w)
    run(["git", "commit", "-m", "写作台：本地这次"], w)
    parent = run(["git", "rev-parse", "HEAD^"], w).stdout.strip()
    chk("★ 没有 pre-pull 时，父是旧的本地 HEAD（= 分叉的兄弟）",
        parent != remote_head, f"父 {parent[:8]} ≠ 远端 {remote_head[:8]}")
    chk("★ 这也解释了终端为什么被拒：本地多出的那个提交远端没有",
        parent == run(["git", "rev-parse", f"{remote_head}^"], other).stdout.strip())

print("\n=== ⑤ 拉取失败 ⇒ 报错停下、且不许产生提交（别在已分叉的仓库上再提交）===")
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    w = mkrepo(base, "h")
    h0 = head(w)
    (w / "d.md").write_text("本地这次\n", encoding="utf-8")

    def _pull_fails(*a, **k):
        args = a[0] if a else []
        if args[:3] == ["git", "pull", "--rebase"]:
            return subprocess.CompletedProcess(args, 1, "", "error: could not apply … CONFLICT")
        return REAL_RUN(*a, **k)

    gitops._run = _pull_fails
    try:
        gitops.commit_all(w, "写作台：本地这次")
        raised = None
    except gitops.GitError as e:
        raised = str(e)
    finally:
        gitops._run = REAL_RUN
    chk("★ 报错了（没默默提交）", raised is not None, (raised or "")[:70])
    chk("★ HEAD 没动（一个提交都没产生）", head(w) == h0, head(w)[:8])
    chk("★ 工作区仍保留未提交的改动", status(w) != "")

print("\n=== ⑥ 接线门闩：push() 真的调了对齐那一步（删掉那行调用必须被抓住）===")
#★ 为什么单列一节：前面所有节都是**直接调 `_sync_with_remote`**，
#   测的是"这个函数对不对"。可要是 `push()` 里忘了调它（或者调错了参数），
#   前面全绿、实际上一行都不会执行 ⇒ **接线与实现是两回事，两边都得守**。
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    origin = base / "NorthenCedar" / "personal.git"
    origin.parent.mkdir(parents=True)
    run(["git", "init", "--bare", "-b", "main", str(origin)], base)
    w = base / "w"
    run(["git", "clone", str(origin), str(w)], base)
    run(["git", "config", "user.email", "t@t"], w)
    run(["git", "config", "user.name", "t"], w)
    (w / "a.md").write_text("一\n", encoding="utf-8")
    run(["git", "add", "-A"], w)
    run(["git", "commit", "-m", "初版"], w)
    run(["git", "push", "origin", "main"], w)
    # ★ push() 开头那道安全闸比对的是 `NorthenCedar/personal.git`（**正斜杠**）——
    #   本地路径在 Windows 上是反斜杠，天然匹配不上。生产环境是 https URL 所以没事，
    #   这里照生产形态把 remote URL 设成 https，省得把闸改松（那道闸是挡"推错仓库"的）。
    run(["git", "remote", "set-url", "origin", "https://github.com/NorthenCedar/personal.git"], w)
    (w / "a.md").write_text("二\n", encoding="utf-8")
    run(["git", "add", "-A"], w)
    run(["git", "commit", "-m", "写作台：内容更新，写说说"], w)

    seen: dict[str, str] = {}
    made = {"sha": ""}          # push() 造出来的那个"远端提交"
    real_sync, real_gh, real_tok, real_remote = (
        gitops._sync_with_remote, gitops._gh, gitops.get_token, gitops.remote_main)

    def fake_sync(repo, sha, on_log=None):
        seen["sha"] = sha                      # ★ 接线证据：push 到底拿哪个 sha 来对齐
        return {"ok": True, "head": sha}

    def fake_remote_main(tok=None):
        # compare() 用它判断"远端落后"：此时 origin/main 已被 update-ref 指回初版
        sha = run(["git", "rev-parse", "origin/main"], w).stdout.strip()
        tree = run(["git", "rev-parse", f"{sha}^{{tree}}"], w).stdout.strip()
        return {"sha": sha, "tree": tree}

    def fake_gh(method, path, token, body=None):
        if method == "POST" and path.endswith("/git/commits"):
            made["sha"] = "b" * 40             # 远端提交的身份（本地当然没有这个对象）
            return {"sha": made["sha"], "tree": {"sha": "t" * 40}}
        if method == "PATCH" and path.endswith("/git/refs/heads/main"):
            return {"object": {"sha": made["sha"]}}
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": made["sha"] or "c" * 40}}
        if "/git/trees" in path:
            return {"sha": run(["git", "rev-parse", "HEAD^{tree}"], w).stdout.strip()}
        if "/git/blobs" in path:
            return {"sha": "0" * 40}
        raise AssertionError(f"没预料到的 API 调用：{method} {path}")

    gitops._sync_with_remote = fake_sync
    gitops._gh = fake_gh
    gitops.get_token = lambda *a, **k: "t"
    gitops.remote_main = fake_remote_main
    # 让 compare() 看到"远端落后"：origin/main 指回初版（本地多一个提交）
    first = run(["git", "rev-list", "--max-parents=0", "HEAD"], w).stdout.strip()
    run(["git", "update-ref", "refs/remotes/origin/main", first], w)
    try:
        r = gitops.push(w)
    except Exception as e:  # noqa: BLE001
        r = {"ok": False, "err": f"{type(e).__name__}: {e}"}
    finally:
        gitops._sync_with_remote, gitops._gh = real_sync, real_gh
        gitops.get_token, gitops.remote_main = real_tok, real_remote

    chk("★ push() 跑通了（没有在中途报错）", r.get("ok") is True, str(r)[:100])
    chk("★ push() 调了对齐那一步", bool(seen.get("sha")), str(seen)[:60])
    chk("★ 对齐用的是**本次造出来的那个远端提交**",
        seen.get("sha") == made.get("sha") and bool(made.get("sha")),
        f"对齐 {seen.get('sha','-')[:8]} / 造的 {made.get('sha','-')[:8]}")
    chk("★ 返回值里带 synced（界面能据此显示「本地已对齐」）",
        r.get("synced") is True, str(r.get("synced")))

print("\n=== ⑦ 工作区有未提交改动时，对齐不许把它抹掉（soft 与 hard 在这里才有差别）===")
# ★ 为什么单列：闸③ 已经保证两边内容树相等 ⇒ 在**干净**的工作区上，
#   `reset --soft` 与 `reset --hard` 的结果一模一样，那条变异天然咬不住
#   （"改动不产生可观察差异"就不该硬凑一条测试）。
#   真正能分开两者的是**工作区有未提交改动**的时候 —— `--hard` 会把它抹掉，
#   而那正是站主还没保存的东西。soft 的意义就在这里。
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    w = mkrepo(base, "j")
    (w / "a.md").write_text("二\n", encoding="utf-8")
    run(["git", "add", "-A"], w)
    run(["git", "commit", "-m", "本地那次"], w)
    other = base / "k"
    run(["git", "clone", str(base / "j-origin.git"), str(other)], base)
    run(["git", "config", "user.email", "t@t"], other)
    run(["git", "config", "user.name", "t"], other)
    (other / "a.md").write_text("二\n", encoding="utf-8")
    run(["git", "add", "-A"], other)
    run(["git", "commit", "-m", "写作台：内容更新，写说说"], other)
    sha = head(other)
    run(["git", "push", "origin", "main"], other)
    run(["git", "fetch", "origin", "main"], w)

    # ★ 站主还没保存的一笔。⚠ 必须是**已跟踪文件**的未提交修改：
    #   新建（未跟踪）文件的话 `reset --hard` 根本不会删它 ⇒ soft 与 hard 结果一样、
    #   那条变异就咬不住（"改动不产生可观察差异"就别硬凑测试，见这节的题头）。
    (w / "a.md").write_text("二\n站主正在改，还没保存\n", encoding="utf-8")
    before = snapshot(w)
    r = gitops._sync_with_remote(w, sha)
    chk("对齐成功", r.get("ok") is True, str(r.get("why", "")))
    chk("★ HEAD 对齐到远端那个提交", head(w) == sha, head(w)[:8])
    chk("★ **未提交的那笔还在**（soft 的意义就在这一条）",
        (w / "a.md").read_text(encoding="utf-8") == "二\n站主正在改，还没保存\n",
        repr((w / "a.md").read_text(encoding="utf-8")[:30]))
    chk("★ 工作区逐字节没变", snapshot(w) == before)
    chk("★ status 仍显示那处未提交的修改", "a.md" in status(w), repr(status(w)[:60]))

print("\n=== ⑧ 中文文件名必须原样出现（git 默认转义成八进制）===")
# ★ 站主会写中文文件名（`风景.md`、`说说.md` 那种）。git 的 `status --porcelain`
#   **默认把非 ASCII 路径转义成八进制** ⇒ 写作台"改了哪些文件"那个列表里，
#   他看到的是 `?? "\351\243\216\346\231\257.md"` —— 一串谁也认不出的东西。
#   ⇒ 所有 status 调用都要带 `-c core.quotePath=false`。
#   ⚠ 用 `-c`（单次生效）而不是 `git config core.quotePath false`（改用户仓库配置）：
#     后者是在**站主的仓库**里留设置，本产品不该替用户改仓库配置。
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    w = mkrepo(base, "m")
    (w / "风景.md").write_text("新写的一篇\n", encoding="utf-8")
    (w / "q.md").write_text("普通\n", encoding="utf-8")

    files = gitops.changed_files(w)
    chk("★ changed_files 里有中文名原样", "风景.md" in files, str(files))
    chk("★ 没有八进制转义", not any("\\3" in f for f in files), str(files))
    st = gitops.status(w)
    dirty = " ".join(st.get("dirty") or [])
    chk("★ status() 的 dirty 也是原样的", "风景.md" in dirty and "\\3" not in dirty,
        dirty[:80])
    chk("★ 普通文件名也不受影响", "q.md" in files, str(files))
    # 反向对照：证明夹具真能咬住 —— 不加 -c 时读到的就是转义的
    import subprocess as _sp
    raw = _sp.run(["git", "status", "--porcelain"], cwd=str(w), capture_output=True,
                  text=True, encoding="utf-8", errors="replace").stdout
    chk("★ 反向对照：不加 -c 时 git 给的确实是转义的（证明上面两条不是恒真）",
        "\\3" in raw, raw.strip()[:60])

bad = [c for c in checks if not c[1]]
print(f"\n通过 {len(checks) - len(bad)} / {len(checks)}")
sys.exit(1 if bad else 0)