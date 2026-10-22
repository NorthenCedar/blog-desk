# -*- coding: utf-8 -*-
"""写作台 · Git 与推送。

发布走普通、非强制 Git push，保留 AI 与写作台的真实提交历史。
fetch 只更新远端跟踪引用；远端领先或分叉就停下，由用户同步，绝不自动
stash、rebase 或 reset。提交只处理写作台内容路径，其他工作区或暂存区改动
会阻断发布。Windows 系统代理在每次网络命令前读取，只通过命令级 -c 使用。

旧版 API/blob 与历史对齐工具保留供旧测试使用，发布入口不再调用它们。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

from lib import proc

API = "https://api.github.com"
REPO = "NorthenCedar/personal"


class GitError(Exception):
    """消息直接给用户看 ⇒ 中文、说清楚怎么办。"""


# ────────────────────────────── git ──────────────────────────────

def _run(args: list[str], cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    """跑 git 并**按文本**取输出 —— 只许用于 `log` / `status` / `diff` 这类纯文本命令。

    ★★ 千万别拿它读 `cat-file blob`：`errors="replace"` 会把二进制里的非法 UTF-8
       字节换成 U+FFFD，静默改烂内容。要读 blob 用下面的 `git_bin()`。
    """
    # ★ 走 lib/proc.py：写作台是无控制台程序，直接 subprocess 调 git 会**闪黑窗**
    #   （站主报的"跳出来很多终端"）；那里统一加了 CREATE_NO_WINDOW。
    return proc.run(
        args, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def git(repo: Path, *args: str, check: bool = True, timeout: int = 180) -> str:
    p = _run(["git", *args], repo, timeout)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} 失败：{(p.stderr or p.stdout).strip()[:400]}")
    return (p.stdout or "").strip()


def git_bin(repo: Path, *args: str, timeout: int = 180) -> bytes:
    """跑 git 并取**原始字节**（`cat-file blob` 这类）。**完全不做文本解码。**

    ★★ 2026-10-09 修（站主报「传图片失败」）：原实现图省事走了上面的 `_run()`，
       而它带着 `text=True, encoding="utf-8", errors="replace"`。`errors="replace"`
       会把**每一个非法 UTF-8 字节换成 U+FFFD**（`ef bf bd`），再接
       `.encode("utf-8", "surrogateescape")` 编回去 —— 于是**静默把二进制改烂**，
       全程一声不响：

         实测同一张 PNG：104373 字节 → 被读成 **185779** 字节，
         103664 处字节不同，首字节 `0xff` → `0xef`；
         blob sha 从 `86443864…` 变成 `24b7311a…`。

       后果不是崩溃，是**推上去的内容和本地不一样**，于是被 `push()` 里那道
       「上传后的 tree == 本地 HEAD 的 tree」保险丝拦下。
       ★ 为什么以前一直没发现：之前几轮推的全是 `.md`，纯文本里没有非法字节，
         `errors="replace"` 一次都没触发 —— **又是"夹具里没有那种输入"型的假绿**。
         直到第一张图片才现形。

       ⇒ 读二进制必须彻底绕开文本层：`capture_output=True`，
         绝不给 `text` / `encoding` / `errors` 任何一个。
    """
    p = proc.run(["git", *args], cwd=str(repo), capture_output=True, timeout=timeout)
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} 失败：{err[:400]}")
    return p.stdout


def _system_proxy() -> str:
    """读取当前 Windows 系统代理；不写注册表，也不更改 Git/TLS 配置。"""
    if os.name != "nt":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            if not winreg.QueryValueEx(key, "ProxyEnable")[0]:
                return ""
            value = str(winreg.QueryValueEx(key, "ProxyServer")[0]).strip()
    except FileNotFoundError:
        return ""
    if "=" in value:
        choices = dict(part.strip().split("=", 1) for part in value.split(";") if "=" in part)
        value = choices.get("https", choices.get("http", "")).strip()
    return value if not value or "://" in value else "http://" + value


def _network_git(repo: Path, *args: str) -> str:
    proxy = _system_proxy()
    options = ["-c", "http.proxy=" + proxy] if proxy else []
    p = _run(["git", *options, *args], repo)
    if p.returncode != 0:
        raise GitError(f"git {args[0]} 失败：{(p.stderr or p.stdout).strip()[:600]}")
    return (p.stdout or "").strip()


def _fetch_main(repo: Path) -> dict:
    _network_git(repo, "fetch", "--no-tags", "origin",
                 "refs/heads/main:refs/remotes/origin/main")
    sha = git(repo, "rev-parse", "refs/remotes/origin/main")
    return {"sha": sha, "tree": git(repo, "rev-parse", f"{sha}^{{tree}}")}


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    p = _run(["git", "merge-base", "--is-ancestor", older, newer], repo)
    if p.returncode not in (0, 1):
        raise GitError("无法确认 Git 历史关系：" + (p.stderr or p.stdout).strip()[:300])
    return p.returncode == 0


def blob_bytes(repo: Path, path: str) -> bytes | None:
    """取 HEAD 里某个文件的**原始字节**；HEAD 里没有它（= 本次提交把它删了）返回 None。

    ★ 为什么单独拎出来一个函数：这里是「推上去的内容会不会变形」的唯一现场。
      `push()` 里"上传 blob"那一步要走网络，离线测不了；但 **blob 的 sha 是内容的
      函数** —— 只要这里交出去的字节与 git 自己算 blob sha 用的字节一致，
      上传之后构造出来的 tree 就必然与本地 HEAD 的一致。
      ⇒ 测试就钉这一条：`sha1("blob %d\\0" % len(b) + b)` 必须等于 `git rev-parse HEAD:<path>`。
    """
    if proc.run(["git", "cat-file", "-e", f"HEAD:{path}"],
                cwd=str(repo), capture_output=True).returncode != 0:
        return None                                  # 不在 HEAD 里 ⇒ 本次是「删除」
    return git_bin(repo, "cat-file", "blob", f"HEAD:{path}")


def _marker_path(repo: Path) -> Path:
    """本地记「上次推上去的远端 sha」的小文件（放在 .git/ 里，不进版本库）。"""
    gd = git(repo, "rev-parse", "--git-common-dir", check=False) or ".git"
    p = Path(gd)
    if not p.is_absolute():
        p = (repo / p).resolve()
    return p / "desk-remote-main"


def read_marker(repo: Path) -> str:
    p = _marker_path(repo)
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def write_marker(repo: Path, sha: str) -> None:
    try:
        _marker_path(repo).write_text(sha + "\n", encoding="utf-8")
    except OSError:
        pass


def remote_main(token: str | None = None) -> dict:
    """问 API：远端 main 的 commit sha 与它那棵 tree 的 sha。"""
    tok = token or get_token()
    sha = _gh("GET", f"/repos/{REPO}/git/ref/heads/main", tok)["object"]["sha"]
    tree = _gh("GET", f"/repos/{REPO}/git/commits/{sha}", tok)["tree"]["sha"]
    return {"sha": sha, "tree": tree}


def compare(repo: Path, token: str | None = None) -> dict:
    """fetch 后比较真实历史；保留界面使用的 synced/ahead/diverged 字段。

    token 参数仅为旧调用方兼容。历史不同但内容树相同也不能冒充已同步。
    """
    rem = _fetch_main(repo)
    head = git(repo, "rev-parse", "HEAD")
    head_tree = git(repo, "rev-parse", "HEAD^{tree}")
    dirty = _status_lines(repo)

    out = {
        "remote_sha": rem["sha"], "remote_tree": rem["tree"],
        "head": head, "head_tree": head_tree,
        "dirty": dirty, "dirty_count": len(dirty),
    }
    if rem["sha"] == head:
        out.update({"mode": "synced", "relation": "same", "pending": 0, "base": head})
        return out
    if _is_ancestor(repo, rem["sha"], head):
        n = int(git(repo, "rev-list", "--count", f"{rem['sha']}..{head}"))
        out.update({"mode": "ahead", "relation": "ahead", "pending": n, "base": rem["sha"]})
        return out
    relation = "behind" if _is_ancestor(repo, head, rem["sha"]) else "diverged"
    out.update({"mode": "diverged", "relation": relation, "pending": None, "base": None})
    return out


def status(repo: Path) -> dict:
    """给界面用的仓库状态。

    本地部分（分支/HEAD/改动）永远可用；远端部分要联网，失败时只填 remote_err，
    **不会**把失败伪装成"已同步"。
    """
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    head = git(repo, "rev-parse", "HEAD", check=False)
    subject = git(repo, "log", "-1", "--pretty=%s", check=False)
    dirty_raw = "\n".join(_status_lines(repo))
    dirty = [l for l in dirty_raw.split("\n") if l.strip()]

    out = {
        "branch": branch or "(空仓库)",
        "head": head,
        "head_short": head[:8],
        "subject": subject,
        "dirty": dirty,
        "dirty_count": len(dirty),
        "clean": not dirty,
        "remote_sha": "",
        "remote_short": "",
        "remote_err": "",
        "sync": None,          # synced / ahead / diverged；None = 没问到
        "pending": None,       # 本地待推的提交数（sync=ahead 时有值）
        "marker": read_marker(repo),   # 上次推送记下的远端 sha（离线时的参考）
    }
    try:
        c = compare(repo)
        out.update({
            "remote_sha": c["remote_sha"],
            "remote_short": c["remote_sha"][:8],
            "sync": c["mode"],
            "pending": c["pending"],
        })
    except Exception as e:  # noqa: BLE001
        out["remote_err"] = str(e)
    return out


def changed_files(repo: Path) -> list[str]:
    """工作区改动（含未跟踪）。"""
    return sorted({path for entry in _status_entries(repo)
                   for path in (entry["path"], entry.get("old_path")) if path})


def _status_lines(repo: Path) -> list[str]:
    """界面展示；解析统一走 NUL 分隔的 _status_entries，保留路径与状态空格。"""
    return [f"{e['xy']} " + (f"{e['old_path']} -> " if e.get("old_path") else "") + e["path"]
            for e in _status_entries(repo)]


def _status_entries(repo: Path) -> list[dict]:
    fields = git_bin(repo, "-c", "core.quotePath=false", "status", "--porcelain=v1",
                     "-z", "--untracked-files=all").decode("utf-8", "surrogateescape").split("\0")
    entries = []
    i = 0
    while i < len(fields) and fields[i]:
        field = fields[i]
        entry = {"xy": field[:2], "path": field[3:]}
        i += 1
        if "R" in entry["xy"] or "C" in entry["xy"]:
            entry["old_path"] = fields[i]
            i += 1
        entries.append(entry)
    return entries


def _content_changes(repo: Path) -> list[dict]:
    entries = _status_entries(repo)
    blocked = []
    for entry in entries:
        if "U" in entry["xy"] or entry["xy"] in ("AA", "DD"):
            raise GitError("仓库还有未解决的合并冲突，请先处理，写作台不会自动解决。")
        for path in (entry["path"], entry.get("old_path")):
            if path and not (path.startswith(("content/", "src/assets/talks/"))
                             or path in ("src/tag-registry.ts", "docs/content-model.md")):
                blocked.append(path)
    if blocked:
        raise GitError("发现写作台内容范围之外的工作区或暂存区改动，已停止发布："
                       + "、".join(sorted(set(blocked))[:12])
                       + "。请先完成或隔离这些改动，避免把 AI 源码一起提交。")
    return entries


def capture_publish_state(repo: Path) -> dict:
    """纯本地发布快照；调用方可用 == 检查校验期间的 HEAD、索引及文件变化。"""
    head = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    entries = _status_entries(repo)
    paths = sorted({p for e in entries for p in (e["path"], e.get("old_path")) if p})
    files = []
    for path in paths:
        p = repo / path
        if p.is_symlink():
            source = os.fsencode(os.readlink(p))
            data = b"symlink\0" + source
            hash_args = ['--no-filters', '--stdin']
        elif p.is_file():
            data = p.read_bytes()
            source = data
            hash_args = ['--path=' + path, '--stdin']
        elif p.exists():
            raise GitError(f"无法确认目录或子模块的内容归属，已停止发布：{path}")
        else:
            data = None
        blob = None
        if data is not None:
            hashed = proc.run(['git', 'hash-object', *hash_args], cwd=str(repo),
                              input=source, capture_output=True)
            if hashed.returncode:
                raise GitError('无法确认暂存后的文件摘要，已停止发布：' + path)
            blob = hashed.stdout.decode('ascii').strip()
        files.append({"path": path,
                      "worktree_sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                      "raw_blob_sha": hashlib.sha1(b'blob ' + str(len(source)).encode() + b'\0' + source).hexdigest() if data is not None else None,
                      "clean_blob_sha": blob})
    index = git_bin(repo, "ls-files", "--stage", "-z", "--", *paths) if paths else b""
    if git(repo, "rev-parse", "HEAD") != head:
        raise GitError("检查期间本地 HEAD 已变化，请等待 AI/Git 操作结束后再发布。")
    return {"head": head, "branch": branch, "status": entries, "files": files,
            "index_entries": index.decode("utf-8", "surrogateescape").split("\0")}


def _require_same_head(repo: Path, head: str) -> None:
    if (git(repo, "rev-parse", "HEAD") != head
            or git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False) != "main"):
        raise GitError("检查期间本地 HEAD 或分支已变化，请等待 AI/Git 操作结束后再发布。")


def preflight(repo: Path) -> dict:
    """构建前/提交前安全门；只 fetch，不修改工作区、索引或本地分支。"""
    repo = Path(repo)
    _content_changes(repo)
    before = capture_publish_state(repo)
    if before["branch"] != "main":
        raise GitError("写作台只能从本地 main 发布，请先切回 main 并完成同步。")
    origin = git(repo, "remote", "get-url", "origin", check=False)
    if REPO not in origin.replace("\\", "/"):
        raise GitError(f"这个仓库的 origin 不是 {REPO}，已拒绝发布；请检查仓库路径。")
    result = compare(repo)
    if capture_publish_state(repo) != before:
        raise GitError("检查期间工作区、暂存区或 HEAD 已变化，请等待 AI/Git 操作结束后再发布。")
    if result["mode"] == "diverged":
        relation = "远端 main 领先本地" if result["relation"] == "behind" else "本地与远端 main 已分叉"
        raise GitError(relation + "，请先完成同步再发布；写作台不会自动 stash、rebase 或 reset。")
    result.update({"ok": True, "branch": before["branch"], "files": changed_files(repo)})
    return result


def _pull_rebase(repo: Path, on_log=None) -> dict:
    """旧版兼容工具；当前 preflight/commit_all/push 均不调用。

    提交前先 `git pull --rebase origin main`：远端比你本地新时，别造分叉的兄弟提交。

    ★ 只做**读**+重放本地提交，不碰远端。
    ⚠ 拉不下来（冲突 / 没网 / 没有远端对象）⇒ **直接报错、不提交**：
      在一个已经分叉的仓库上再提交，只会让分叉更深。宁可让站主看见报错。
    """
    log = on_log or (lambda *_: None)
    head0 = git(repo, "rev-parse", "HEAD", check=False)
    p = _run(["git", "pull", "--rebase", "origin", "main"], repo)
    if p.returncode != 0:
        raise GitError(
            "提交前拉取远端失败，已停下、没有提交 —— "
            "在已经分叉的仓库上再提交只会让分叉更深。"
            f"请先处理这个再提交：{(p.stderr or p.stdout).strip()[:300]}"
        )
    head1 = git(repo, "rev-parse", "HEAD", check=False)
    if head0 != head1:
        log(f"已把本地提交重放到远端 main 之上（{head0[:8]} → {head1[:8]}）")
    return {"ok": True, "head": head1, "was": head0}


def _sync_with_remote(repo: Path, remote_sha: str, on_log=None) -> dict:
    """旧版兼容工具；当前发布入口禁止调用，也不再需要 API 后对齐。

    API 推成功之后，把本地 HEAD 对齐到远端那个提交。返回 {ok, gate, head, why, ...}。

    ★★ 为什么必须有这一步（2026-10-15 站主报「推成功了，终端却推不上去」）：
      本通道用 GitHub API 建提交（blob → tree → commit → PATCH ref），而那个提交的
      **父是"远端当时的 main"**，不是本地刚提交的那个 ⇒ 两边是**兄弟**不是父子：

          本地：dfd65b4 ── f289173        远端：dfd65b4 ── af4158a

      于是终端 `git push` 报 *Updates were rejected because the remote contains
      work that you do not have locally*。内容其实一模一样，只是提交身份不同。

    ⚠ `push()` 的 docstring 里那句「`git fetch` 在本机连不通」**已过期**：后来配了
      `http.sslBackend=openssl` + 自带 CA 包，fetch 现在是通的，`git cat-file -e <sha>`
      读回来是 `commit` ⇒ "对齐本地"这件事现在做得了。

    ★★★ **三道闸，一道都不能少** —— 这个函数的前身栽过：
      早先版本直接写 `git reset --soft origin/main`。fetch 静默失败时 `origin/main`
      停在**陈旧引用**上，那一句会把 HEAD **退回旧提交**（工作区内容不变，但历史被改写）。
      ⇒ ① fetch 退出码必须 0
        ② 远端那个对象必须**真的在本地**（`cat-file -e`）
        ③ 两边内容树必须相等（不等 ⇒ 本次推的内容与本地不是一回事）
      **任一条不过 ⇒ 原地停下、一个字节都不改**，只把原因报给用户。
    """
    log = on_log or (lambda *_: None)
    head0 = git(repo, "rev-parse", "HEAD", check=False)

    # ── 闸① ──
    p = _run(["git", "fetch", "origin", "main"], repo)
    if p.returncode != 0:
        return {"ok": False, "gate": "fetch", "head": head0,
                "why": (p.stderr or p.stdout).strip()[:200] or "git fetch 失败"}
    log("已 fetch 远端 main")

    # ── 闸② ──
    if _run(["git", "cat-file", "-e", f"{remote_sha}^{{commit}}"], repo).returncode != 0:
        return {"ok": False, "gate": "object", "head": head0,
                "why": f"远端提交 {remote_sha[:8]} 不在本地（fetch 没拿到它）"}
    log(f"远端提交 {remote_sha[:8]} 确实在本地")

    # ── 闸③ ──
    head_tree = git(repo, "rev-parse", "HEAD^{tree}", check=False)
    rem_tree = git(repo, "rev-parse", f"{remote_sha}^{{tree}}", check=False)
    if not rem_tree or rem_tree != head_tree:
        return {"ok": False, "gate": "tree", "head": head0,
                "why": f"两边内容树不同（本地 {head_tree[:10]} / 远端 {rem_tree[:10]}）"}
    log("两边内容树一致")

    # ── 三道都过，才动 HEAD。`--soft`：工作区与暂存区一个字节都不碰 ──
    git(repo, "reset", "--soft", remote_sha)
    head1 = git(repo, "rev-parse", "HEAD", check=False)
    return {"ok": True, "head": head1, "was": head0, "tree": head_tree}


def commit_all(repo: Path, message: str) -> dict:
    """检查历史与归属后只提交明确内容路径；不自动拉取重放或收录 AI 源码。"""
    repo = Path(repo)
    state = preflight(repo)
    if not state["files"]:
        return {"ok": True, "skipped": True, "reason": "工作区没有改动"}
    before = capture_publish_state(repo)
    _require_same_head(repo, state["head"])
    entries = _content_changes(repo)
    # 已暂存的删除/重命名不再 add 一个已从索引移除的旧路径。
    to_stage = sorted({e["path"] for e in entries if e["xy"] == "??" or e["xy"][1] != " "})
    if to_stage:
        git(repo, "add", "--", *to_stage)
    _content_changes(repo)
    after = capture_publish_state(repo)
    if any(before[key] != after[key] for key in ("head", "branch", "files")):
        raise GitError("暂存期间内容或 HEAD 已变化，已停止提交；请检查后重试。")
    _require_same_head(repo, state["head"])
    # ★ 绝不 --no-verify：站规（AGENTS §3）
    # 提交信息从 stdin 走（-F -），避免中文多行被命令行转义搞坏
    # --only 让另一进程此刻暂存的其他路径也不能被这一笔顺带收录；保留 hooks。
    p = proc.run(["git", "commit", "--only", "-F", "-", "--", *state["files"]], cwd=str(repo),
                 input=message, capture_output=True, text=True,
                 encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise GitError(f"提交失败：{(p.stderr or p.stdout).strip()[:400]}")
    sha = git(repo, "rev-parse", "HEAD", check=False)
    return {"ok": True, "sha": sha, "sha_short": sha[:8],
            "subject": git(repo, "log", "-1", "--pretty=%s", check=False),
            "files": state["files"]}


# ────────────────────────────── 凭据 ──────────────────────────────

class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR), ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def _read_cred(target: str) -> str | None:
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    p = ctypes.POINTER(_CREDENTIALW)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(p)):
        return None
    c = p.contents
    blob = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
    advapi.CredFree(p)
    return blob.decode("utf-16-le", "replace").strip()   # ★ UTF-16LE


_CRED_TARGETS = [
    "git:https://github.com",
    "git:https://api.github.com",
    "GitHub - https://api.github.com",
    f"git:https://github.com/{REPO}",
]


def get_token() -> str:
    tok = os.environ.get("GH_TOKEN", "").strip()
    if tok:
        return tok
    for t in _CRED_TARGETS:
        v = _read_cred(t)
        if v:
            return v
    raise GitError(
        "取不到 GitHub 凭据。请确认「凭据管理器」里有 github.com 的登录，"
        "或先设一个环境变量 GH_TOKEN。"
    )


# ────────────────────────────── GitHub API ──────────────────────────────

def _gh(method: str, path: str, token: str, body=None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "blog-desk")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise GitError(f"GitHub API {method} {path} → {e.code}：{detail}") from e
    except Exception as e:  # noqa: BLE001
        raise GitError(f"连不上 GitHub API：{type(e).__name__} {e}") from e


def _settle_ref(get_ref, want: str, tries: int = 6, delay: float = 1.0) -> str:
    """等远端 ref 追上 `want`，返回最后一次读到的 sha。**带重试**。

    ★★ 2026-10-09 实测踩到（站主推文档那一次）：PATCH 明明成功 —— 返回 200、
      返回体里的 ref 就是新 sha —— 可**紧接着**的 GET 读到的还是旧值。
      GitHub 的"写后读"不是瞬时的。旧写法只回读一次就拿它当判据，
      于是**把一次成功的推送报成了失败**（界面上写"回读不一致"），
      还把旧的 sha 记进了 `.git/desk-remote-main`。而内容其实早就上线了。
      ⇒ **"回读不到"和"没写成功"是两件事**，判据必须能区分它们；
        这里用重试把"还没读到"与"真的没写上去"分开。

    `get_ref` 是个无参函数（返回 sha），**注入进来是为了让这段重试能被离线测**——
    网络没法在测试里造延迟，但"前两次读到旧值、第三次才读到新值"可以。
    """
    got = ""
    for i in range(max(1, tries)):
        got = get_ref()
        if got == want:
            return got
        if i < tries - 1:
            time.sleep(delay)
    return got


def git_remote_head() -> str:
    """直接问 API 远端 main 指到哪（不依赖本地 remote-tracking 分支）。"""
    tok = get_token()
    return _gh("GET", f"/repos/{REPO}/git/ref/heads/main", tok)["object"]["sha"]


def push(repo: Path, on_log=None, expected_head: str | None = None) -> dict:
    """普通非强制 push；固定已检查的 SHA，保留原提交链与并发产生的本地提交。"""
    repo = Path(repo)
    log = on_log or (lambda *_: None)
    messages: list[str] = []

    def say(message):
        messages.append(message)
        log(message)

    state = preflight(repo)
    head = state["head"]
    if expected_head is not None and head != expected_head:
        raise GitError('校验后本地 HEAD 已变化，本次没有推送；请重新校验新提交')
    if state["dirty_count"]:
        raise GitError("工作区还有没提交的内容改动，请先提交；本通道只推已提交的内容。")
    say(f"本地 HEAD {head[:8]} ｜ 远端 main {state['remote_sha'][:8]}")
    _require_same_head(repo, head)
    if state["mode"] == "synced":
        say("本地与远端提交一致，已经同步")
        write_marker(repo, head)
        return {"ok": True, "up_to_date": True, "sha": head, "sha_short": head[:8],
                "remote": head, "remote_short": head[:8], "files": [],
                "messages": messages, "synced": True, "sync_gate": None,
                "head_changed": False}

    raw = git_bin(repo, "diff", "--name-only", "-z", state["remote_sha"], head)
    files = [p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p]
    say(f"保留 {state['pending']} 笔本地提交，普通推送到 main…")
    # 显式 SHA 固定本次发布快照。远端若并发前进，Git 的非快进检查会拒绝。
    # 不用 HEAD:main，避免另一进程在命令启动前新增的未校验提交被一起推走。
    _network_git(repo, "push", "--porcelain", "origin", f"{head}:refs/heads/main")
    write_marker(repo, head)
    current_head = git(repo, "rev-parse", "HEAD")
    changed = current_head != head
    say(f"远端 main 已推进到 {head[:8]}，原提交历史完整保留")
    if changed:
        say(f"发布期间本地新增了提交 {current_head[:8]}，已保留；这笔不在本次发布快照里")
    return {"ok": True, "sha": head, "sha_short": head[:8], "remote": head,
            "remote_short": head[:8], "files": files, "messages": messages,
            "synced": not changed, "sync_gate": None, "head_changed": changed}
