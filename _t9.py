# -*- coding: utf-8 -*-
"""写作台 · **推送安全**：blob 的字节不许被文本层改坏（2026-10-09）。

站主这次报的是「传图片失败」，界面上的原话是
  「上传后的内容树与本地 HEAD 不一致 —— 为免留下半截状态，已中止（远端未改动）」。

根因不是网络、不是 API、也不是那道保险丝太严 —— **是我们在读文件的时候就把它改烂了**：
`git_bin()` 图省事走了 `_run()`，而 `_run` 带着
`text=True, encoding="utf-8", errors="replace"`。`errors="replace"` 会把
**每一个非法 UTF-8 字节换成 U+FFFD**，然后再 `.encode(..., "surrogateescape")` 编回去：

    同一张 PNG：104373 字节 → 读成 185779 字节，103664 处字节不同，
    首字节 0xff → 0xef；blob sha 由 86443864… 变成 24b7311a…。

上传的 blob 变了 ⇒ 构造出来的 tree 变了 ⇒ 保险丝跳闸。**保险丝是对的**，
它拦住的是"远端只收到一半内容"这种烂状态；错的是上游那条读法。

★ 为什么这之前一路全绿：**之前几轮推的全是 `.md`**。纯文本里没有非法字节，
  `errors="replace"` 一次都没触发过 —— **夹具里没有那种输入，就测不出那种 bug。**
  所以本文件的**第一件事**是先证明夹具真能咬住这个 bug（第二节的反向对照）。
  没有那一条，"修完通过"也可能只是夹具太温柔。

守四件事：
  ① `blob_bytes()` 交出的字节，git 认的 blob sha 必须 == `git rev-parse HEAD:<path>`
     —— 二进制要对，纯文本也要对（文本那条防止"修二进制把文本弄坏"）；
  ② ★ **离线复现 push 的那道保险丝**：拿这些字节本地组装 tree，
     必须 == `HEAD^{tree}`；再用**修复前**的读法算一遍，必须 !=
     —— 否则这道保险丝根本拦不住真问题；
  ③ 路径在 HEAD 里被删掉时返回 None（`push()` 据此发 `"sha": null`）；
  ④ ★ **"回读不到"不等于"没写上去"**：PATCH 成功之后紧接的那次 GET 可能还是旧值
     （GitHub 的写后读不是瞬时的，2026-10-09 真的踩到，把一次成功的推送报成了失败）
     ⇒ `_settle_ref()` 必须重试。夹具注入一个"前两次旧、第三次新"的读数序列。

全程在 `tempfile` 里造一个真仓库，**不碰 `_fixture/`、不碰站主的仓库、不出网**。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib import gitops, proc  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))
    print(("  ✓ " if ok else "  ✗ ") + name + (f"　{detail}" if detail else ""))


def blob_sha(data: bytes) -> str:
    """git 算 blob sha 的定义（不是我编的）：sha1("blob <len>\\0" + data)。"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def git(repo: Path, *args: str) -> str:
    p = proc.run(["git", *args], cwd=str(repo), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{p.stderr.decode('utf-8', 'replace')[:300]}")
    return p.stdout.decode("utf-8", "replace").strip()


# 0x00–0xff 各来 8 遍：里面必然有大量非法 UTF-8 序列 ⇒ 一挨文本解码就完蛋。
BIN = bytes(range(256)) * 8


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t9@example.invalid")
    git(repo, "config", "user.name", "t9")
    git(repo, "config", "core.autocrlf", "false")     # 别让换行转换掺进来干扰判据
    (repo / "blob.bin").write_bytes(BIN)
    (repo / "note.md").write_text("你好\n第二行\n", encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "初始")
    return repo


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="desk-t9-"))
    try:
        repo = make_repo(tmp)
        want_bin = git(repo, "rev-parse", "HEAD:blob.bin")
        want_md = git(repo, "rev-parse", "HEAD:note.md")

        # ═══════════ 一、blob_bytes 交出的字节对不对 ═══════════
        print("一、blob_bytes() 交出的字节，与 git 自己认的一致吗")
        print("-" * 66)
        got_bin = gitops.blob_bytes(repo, "blob.bin")
        got_md = gitops.blob_bytes(repo, "note.md")
        chk("① 二进制：字节数没变", (got_bin or b"") == BIN,
            f"拿到 {len(got_bin or b'')} 字节，原文件 {len(BIN)} 字节")
        chk("① 二进制：blob sha 与 git 一致", blob_sha(got_bin or b"") == want_bin,
            f"{blob_sha(got_bin or b'')[:12]} vs {want_bin[:12]}")
        chk("① 文本：blob sha 与 git 一致（防「修二进制把文本弄坏」）",
            blob_sha(got_md or b"") == want_md,
            f"{blob_sha(got_md or b'')[:12]} vs {want_md[:12]}")

        # ═══════════ 二、反向对照：夹具必须真能咬住旧 bug ═══════════
        print()
        print("二、反向对照：夹具能不能咬住修复前的那个 bug")
        print("-" * 66)
        bad = proc.run(["git", "cat-file", "blob", "HEAD:blob.bin"], cwd=str(repo),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace").stdout
        bad_bytes = bad.encode("utf-8", "surrogateescape")
        chk("② 用旧读法（text + errors=replace）读**这个**夹具，内容确实被改坏",
            blob_sha(bad_bytes) != want_bin,
            f"旧 {len(bad_bytes)} 字节 sha {blob_sha(bad_bytes)[:12]}"
            f"　vs　对 {len(BIN)} 字节 {want_bin[:12]}")
        chk("② 而且是**变长**的（一个 U+FFFD 用 3 字节顶替 1 个非法字节）",
            len(bad_bytes) > len(BIN), f"{len(bad_bytes)} > {len(BIN)}")

        # ═══════════ 三、离线复现 push 的那道保险丝 ═══════════
        print()
        print("三、离线复现 push 的保险丝（上传后的 tree  vs  本地 HEAD 的 tree）")
        print("-" * 66)
        want_tree = git(repo, "rev-parse", "HEAD^{tree}")
        names = sorted(p.name for p in repo.iterdir() if p.name != ".git")

        def mktree(sha_of) -> str:
            text = "\n".join(f"100644 blob {sha_of(n)}\t{n}" for n in names) + "\n"
            # --missing：允许条目指向仓库里没有的对象。
            #   「坏 sha」那个对象本来就不存在（它只存在于我们的想象里），
            #   而且 GitHub 的 tree API 也正是**直接采用你给它的 sha**，
            #   不会去核对对象在不在 —— 所以这里必须允许缺失，否则复现不了它那条路。
            p = proc.run(["git", "mktree", "--missing"], cwd=str(repo),
                         input=text.encode("utf-8"), capture_output=True)
            if p.returncode != 0:
                raise RuntimeError(p.stderr.decode("utf-8", "replace"))
            return p.stdout.decode().strip()

        def now_sha(n: str) -> str:
            return blob_sha(gitops.blob_bytes(repo, n) or b"")

        good = mktree(now_sha)
        old = mktree(lambda n: blob_sha(bad_bytes) if n == "blob.bin" else now_sha(n))
        chk("③ 用修复后的字节组装出来的 tree == 本地 HEAD 的 tree（保险丝放行）",
            good == want_tree, f"{good[:12]} vs {want_tree[:12]}")
        chk("③ 用修复前的字节组装出来的 tree != 本地 HEAD 的 tree（保险丝跳闸）",
            old != want_tree, f"{old[:12]} vs {want_tree[:12]}")

        # ═══════════ 四、被删掉的文件 ═══════════
        print()
        print("四、路径在 HEAD 里被删掉时（push 据此发 sha:null）")
        print("-" * 66)
        (repo / "blob.bin").unlink()
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "删掉那个二进制")
        chk("④ HEAD 里已经没有这个路径 ⇒ 返回 None", gitops.blob_bytes(repo, "blob.bin") is None)
        chk("④ 没动过的路径照旧拿得到", gitops.blob_bytes(repo, "note.md") is not None)
        chk("④ 从来就不存在的路径也是 None", gitops.blob_bytes(repo, "nope.txt") is None)

        # ═══════════ 五、远端 ref 的"写后读"（GitHub 不是瞬时的）═══════════
        print()
        print("五、回读要重试 —— 别把「还没读到」报成「没写上去」")
        print("-" * 66)
        WANT, OLD = "b" * 40, "a" * 40

        # 真实网络里的形状：PATCH 成功了，但紧接的一两次 GET 还是旧值。
        seq = [OLD, OLD, WANT]

        def flaky() -> str:
            return seq.pop(0) if seq else WANT

        got = gitops._settle_ref(flaky, WANT, tries=6, delay=0.0)
        chk("⑤ 前两次读到旧值也不放弃，第三次追上了就返回新值", got == WANT, f"拿到 {got[:12]}")

        # 对照：不重试（= 修复前的写法）必然停在旧值 —— 证明这个夹具咬得住。
        once = gitops._settle_ref(lambda: OLD, WANT, tries=1, delay=0.0)
        chk("⑤ 只读一次的话就停在旧值上（夹具确实咬得住旧写法）",
            once == OLD, f"拿到 {once[:12]}")

        # 真的没写上去：读多少次都是旧值 ⇒ 如实返回旧值，不许伪装成功。
        never = gitops._settle_ref(lambda: OLD, WANT, tries=3, delay=0.0)
        chk("⑤ 真没写上去时如实返回旧值（不把失败伪装成成功）", never == OLD, f"拿到 {never[:12]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad_names = [n for n, ok, _ in checks if not ok]
    print()
    print(f"合计 {len(checks)} 项，通过 {len(checks) - len(bad_names)}，失败 {len(bad_names)}")
    for n in bad_names:
        print(f"  失败：{n}")
    return 1 if bad_names else 0


if __name__ == "__main__":
    sys.exit(main())
