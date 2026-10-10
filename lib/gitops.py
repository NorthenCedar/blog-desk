# -*- coding: utf-8 -*-
"""写作台 · Git 与推送。

════════════════════════════════════════════════════════════════════════
为什么推送**不走 `git push`**
════════════════════════════════════════════════════════════════════════
本机实测（2026-10-06 起，反复确认）：`github.com:443` 被重置 / 超时；
`ssh git@github.com:22` TCP 通但没有公钥；git 协议 9418 阻塞；
几个常见加速域返回 401。
**唯一可用通路是 GitHub REST API**（blobs → trees → commits → refs）。
仓库里已有一个同构脚本 `_probe/_push-via-api.py`，本模块是它的**库化版本**
（同一套坑，一条都没省）：

  ① 必须证明「远端内容 == 本地历史的某一点」才能拿它当 parent
     （API 是「基于某个 parent 建 commit」，不是「推一串历史」）。
  ② 读文件内容必须用 **HEAD 的 blob**，不能用工作区文件
     （本机 core.autocrlf=true ⇒ 工作区 CRLF、仓库 LF，而 git status 看不出差异）。
  ③ 「远端是不是本地祖先」会假失败 —— API 建的 commit 本地没有那个对象，
     `merge-base --is-ancestor` 必然失败。改用 **tree sha** 判内容等价
     （见 `compare()`；`status()` 也走它，绝不拿 rev-list 计数冒充同步状态）。
  ④ ★ `git fetch` / `git pull` 在本机**不可能成功**（443 被重置，实测 SSL EOF）
     ⇒ 别写 "推完顺便对齐本地" 的代码。远端 sha 只记进 .git/desk-remote-main。
  ⑤ 凭据：优先环境变量 GH_TOKEN，否则 ctypes CredReadW 读
     `git:https://github.com`（**blob 是 UTF-16LE**），绕开经常 EBUSY 的
     git-credential-manager。

★ 为什么整个文件用 Python 而不是 Node：本机 Node 的 child_process **全线 EBUSY**，
  连 spawn 自己都失败（沙箱豁免后依旧）⇒ 凡是"脚本要跑外部命令"的地方都必须 Python。
"""

from __future__ import annotations

import base64
import ctypes
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
    """**本地 HEAD 与远端 main 的内容关系**（这是判断"要不要推"的唯一正确依据）。

    ★ 为什么不用 ahead/behind 计数：
      本通道用 GitHub API 建提交 ⇒ **远端的 commit sha 本地没有那个对象**，
      `git rev-list --left-right --count <远端sha>...HEAD` 会直接报错。
      更糟的是它**不抛异常只返回空**，解析出来就是 0/0 ——
      界面于是显示"已同步"，而实际本地多着东西。这是个会说谎的状态，必须避免。
      ⇒ 改用 **tree sha** 判内容（内容是内容的函数，与谁建的提交无关）。

    返回 mode：
      synced   —— 两边内容一字不差
      ahead    —— 远端内容是本地历史里的某一点，本地多出 pending 个提交
      diverged —— 远端内容不在本地最近 200 个提交里 ⇒ 远端有你没有的东西，别覆盖
    """
    tok = token or get_token()
    rem = remote_main(tok)
    head = git(repo, "rev-parse", "HEAD")
    head_tree = git(repo, "rev-parse", "HEAD^{tree}")
    dirty = [l for l in git(repo, "status", "--porcelain", check=False).split("\n") if l.strip()]

    out = {
        "remote_sha": rem["sha"], "remote_tree": rem["tree"],
        "head": head, "head_tree": head_tree,
        "dirty": dirty, "dirty_count": len(dirty),
    }
    if rem["tree"] == head_tree:
        out.update({"mode": "synced", "pending": 0, "base": head})
        return out

    base = None
    for c in git(repo, "rev-list", "--max-count=200", "HEAD", check=False).split("\n"):
        if c and git(repo, "rev-parse", f"{c}^{{tree}}", check=False) == rem["tree"]:
            base = c
            break
    if base:
        n = len([x for x in git(repo, "rev-list", f"{base}..HEAD", check=False).split("\n") if x.strip()])
        out.update({"mode": "ahead", "pending": n, "base": base})
        return out

    out.update({"mode": "diverged", "pending": None, "base": None})
    return out


def status(repo: Path) -> dict:
    """给界面用的仓库状态。

    本地部分（分支/HEAD/改动）永远可用；远端部分要联网，失败时只填 remote_err，
    **不会**把失败伪装成"已同步"。
    """
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    head = git(repo, "rev-parse", "HEAD", check=False)
    subject = git(repo, "log", "-1", "--pretty=%s", check=False)
    dirty_raw = git(repo, "status", "--porcelain", check=False)
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
    raw = git(repo, "status", "--porcelain", check=False)
    files = []
    for line in raw.split("\n"):
        if len(line) > 3:
            files.append(line[3:].strip().strip('"'))
    return files


def commit_all(repo: Path, message: str) -> dict:
    """`git add -A` + commit。没有改动时返回 skipped。"""
    if not git(repo, "status", "--porcelain", check=False):
        return {"ok": True, "skipped": True, "reason": "工作区没有改动"}
    git(repo, "add", "-A")
    # ★ 绝不 --no-verify：站规（AGENTS §3）
    # 提交信息从 stdin 走（-F -），避免中文多行被命令行转义搞坏
    p = proc.run(["git", "commit", "-F", "-"], cwd=str(repo),
                 input=message, capture_output=True, text=True,
                 encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise GitError(f"提交失败：{(p.stderr or p.stdout).strip()[:400]}")
    sha = git(repo, "rev-parse", "HEAD", check=False)
    return {"ok": True, "sha": sha, "sha_short": sha[:8],
            "subject": git(repo, "log", "-1", "--pretty=%s", check=False)}


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


def push(repo: Path, on_log=None) -> dict:
    """把本地 HEAD 推上去。返回 {ok, sha, sha_short, files, messages}。

    ★ 对齐本地与远端这件事，本通道**做不到也不该硬做**：
      `git fetch` 在本机连不通（github.com:443 SSL 被重置，已实测），
      而远端 sha 是 API 建的提交、本地根本没有那个对象。
      早先版本在这里写 `git reset --soft origin/main` —— 因为 fetch 静默失败，
      `origin/main` 停在陈旧引用上，这句会把 HEAD **退回旧提交**（工作区内容不变，
      但历史被改写）。现已删除，改为：只把远端 sha 记进 .git/desk-remote-main。
      内容是否一致一律由 `compare()` 用 tree sha 判定 —— 不依赖提交 sha 相等。
    """
    log = on_log or (lambda *_: None)
    messages: list[str] = []

    def say(m):
        messages.append(m)
        log(m)

    token = get_token()
    local_sha = git(repo, "rev-parse", "HEAD")

    # ★ 安全闸：核对这个仓库确实是要推的那个。
    #   为什么必须有：推送用的是**硬编码的** REPO 常量，而"本地树"取自传入的 repo。
    #   要是哪天指向了别的仓库/测试夹具，这一行是唯一挡住「把别处的内容推到站上」的东西。
    url = git(repo, "remote", "get-url", "origin", check=False)
    if f"{REPO}.git" not in url and REPO not in url:
        raise GitError(
            f"这个仓库的 origin 不是 {REPO}（现在是「{url or '没有 origin'}」）—— 拒绝推送。"
            "请把「设置」里的仓库路径改对。"
        )

    if git(repo, "status", "--porcelain", check=False):
        raise GitError("工作区还有没提交的改动 —— 本通道只推已提交的内容。请先在「发布」里提交。")

    cmp = compare(repo, token)
    remote_sha, remote_tree = cmp["remote_sha"], cmp["remote_tree"]
    say(f"本地 HEAD {local_sha[:8]} ｜ 远端 main {remote_sha[:8]}")

    if cmp["mode"] == "synced":
        say("内容一致 —— 远端已经是最新，没什么要推的")
        write_marker(repo, remote_sha)
        return {"ok": True, "up_to_date": True, "messages": messages, "files": [],
                "remote": remote_sha, "remote_short": remote_sha[:8]}
    if cmp["mode"] == "diverged":
        raise GitError(
            "远端有你本机没有的改动（远端内容不在本地最近 200 个提交里）⇒ 不能覆盖。"
            "请先在别的机器/网页上处理好，或在本机用 GitHub API 把远端的内容取下来再推。"
        )

    base = cmp["base"]
    say(f"远端内容是本地 {base[:8]} 的内容 ⇒ 本地多出 {cmp['pending']} 个提交，按这批推")

    files = [f for f in git(repo, "diff", "--name-only", base, check=False).split("\n") if f.strip()]
    if not files:
        say("改动文件列表为空（内容树却不同）—— 这不该发生，已停下")
        raise GitError("内部不一致：tree 不同但没有差异文件。请把这条日志发给开发者。")
    say(f"要推 {len(files)} 个文件：{', '.join(files[:8])}{' …' if len(files) > 8 else ''}")

    entries = []
    for f in files:
        # ★ 必须用 HEAD 的 blob（坑②），且必须走二进制安全的那条路（见 blob_bytes 的注释）。
        content = blob_bytes(repo, f)
        if content is None:
            # 这个路径在 HEAD 里已经没有了 ⇒ 本次提交删掉了它。
            # GitHub 的 tree API 用 `"sha": null` 表示「从这棵树里移除该路径」，
            # 不是「把这个路径指向一个空 blob」—— 后者会留下一串 0 号空 blob。
            entries.append({"path": f, "mode": "100644", "type": "blob", "sha": None})
            say(f"  − 删除 {f}")
            continue
        blob = _gh("POST", f"/repos/{REPO}/git/blobs", token,
                   {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
        entries.append({"path": f, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = _gh("POST", f"/repos/{REPO}/git/trees", token,
               {"base_tree": remote_tree, "tree": entries})

    if tree["sha"] != cmp["head_tree"]:
        raise GitError(
            "上传后的内容树与本地 HEAD 不一致 —— 为免留下半截状态，已中止（远端未改动）。"
            f"远端树 {tree['sha'][:10]} vs 本地 {cmp['head_tree'][:10]}"
        )

    message = git(repo, "log", "-1", "--pretty=%s") + "\n\n" + git(repo, "log", "-1", "--pretty=%b")
    commit = _gh("POST", f"/repos/{REPO}/git/commits", token,
                 {"message": message, "tree": tree["sha"], "parents": [remote_sha]})
    patched = _gh("PATCH", f"/repos/{REPO}/git/refs/heads/main", token,
                  {"sha": commit["sha"], "force": False})

    # ★★ 判据取三处、优先级从高到低（原因见 _settle_ref 的注释）：
    #   ① PATCH 的**返回体** —— 它自己就把更新后的 ref 给了出来，最可信；
    #   ② 带重试的回读 —— 分开"还没读到"与"真的没写"；
    #   ③ 实在读不到就用本次构造的 sha（PATCH 已经返回 2xx，写是成功了）。
    after = ((patched or {}).get("object") or {}).get("sha") or ""

    def _read() -> str:
        return _gh("GET", f"/repos/{REPO}/git/ref/heads/main", token)["object"]["sha"]

    if after != commit["sha"]:
        after = _settle_ref(_read, commit["sha"]) or after or commit["sha"]

    write_marker(repo, after)
    ok = after == commit["sha"]
    if ok:
        say(f"✔ 远端 main 已推进到 {after[:8]}")
    else:
        say(f"⚠ 远端 main 现在读到的是 {after[:8]}，本次构造的是 {commit['sha'][:8]} —— "
            f"内容已上传，但没读到 ref 指向它；请到网页上确认一次")
    say("★ 注意：远端这个提交是本通道用 GitHub API 建的，sha 与本地不同，但**内容完全相同**。")
    return {"ok": ok, "sha": commit["sha"], "sha_short": commit["sha"][:8],
            "remote": after, "remote_short": after[:8], "files": files, "messages": messages}
