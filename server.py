# -*- coding: utf-8 -*-
"""写作台 · 本地服务（HTTP + JSON API）。

界面是纯静态 HTML/CSS/JS，跑在 `127.0.0.1` 的一个随机端口上；
所有文件读写、git、构建、推送都在这个 Python 进程里做。
**只监听回环地址** —— 不是一个对外服务。

路由：
  GET  /                     → UI
  GET  /api/state            → 仓库状态（分支 / 干净否 / 领先落后）
  GET  /api/posts            → 文章列表
  GET  /api/post?slug=       → 读一篇文章
  POST /api/post             → 保存 / 新建
  POST /api/post/delete      → 删文章
  GET  /api/talks            → 说说
  POST /api/talks            → 存说说
  GET  /api/log              → 日志（含下一个版本号）
  POST /api/log              → 存日志
  GET  /api/gallery          → 相册列表
  GET  /api/photos           → 相册照片（照片墙）
  POST /api/upload           → 上传图片（base64）
  POST /api/image/delete     → 删图片
  GET  /api/tags             → 标签注册表（含每个标签的用法、文档镜像漂移）
  POST /api/tags             → 加一个标签（写 src/tag-registry.ts）
  POST /api/tags/delete      → 删一个标签（正被文章用的会被拒）
  GET  /api/tree             → content/ 目录树（快捷入口用）
  POST /api/commit           → 只提交不推
  POST /api/push             → 一键推送（构建 + 校验 + 提交 + 推）
  GET  /api/comments         → 线上留言（需管理密钥）
  POST /api/comment/delete   → 删一条线上留言
  GET  /api/config           → 本机配置（不含密钥）
  POST /api/config           → 存配置
  POST /api/reveal           → 在资源管理器里打开某个目录
  POST /api/copy-to-talks    → 把一张现有图片转存进 src/assets/talks/
  GET  /api/health           → 探活
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import subprocess
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lib import config as cfgmod
from lib import gitops, images, proc, videos
from lib.content import Content, ContentError, ContentConflict
from lib.videos import VideoError
from lib.images import ImageError
from lib.gitops import GitError

# ★ 界面资源从 RES_DIR 读，不是从 __file__ 旁边：打包成 exe 后代码在临时解包目录里
#   （见 lib/config.py 头部那段）。config.json 走 APP_DIR，两件事别混。
UI_DIR = cfgmod.RES_DIR / "ui"
MAX_BODY = 64 * 1024 * 1024      # 64 MB（一张原图的 base64 上限）


class App:
    """把所有状态收在一处，方便测试时替换。"""

    def __init__(self, repo: Path, config: dict | None = None):
        self.repo = Path(repo)
        self.content = Content(self.repo)
        self.config = config or {}
        self.push_lock = threading.RLock()
        self.last_push: dict | None = None
        self.log_lines: list[str] = []
        # ★ 收到过几次请求。启动器靠它判断「桌面窗口到底开起来了没」：
        #   WebView2 起不来时 webview.start() 是**一直挂着**（不抛错），
        #   只能反过来看"页面有没有真的来取过东西"。
        self.request_count = 0

    # ---- 通用 ----

    def log(self, msg: str):
        print(f"[desk] {msg}", flush=True)
        self.log_lines.append(msg)
        if len(self.log_lines) > 400:
            del self.log_lines[:200]

    # ---- 构建（推送前的门） ----

    def run_build(self) -> dict:
        """跑 `npm run verify`（= build + vitest）。站规：Cloudflare 只构建不测试，
        能抓到的问题会静默上线 ⇒ 推送前必须本地过一遍。

        ★ 本机注意：Node 的 child_process 全线 EBUSY，但那是 **Node 内部**的问题；
          由 Python 的 subprocess 调 npm/node 是**正常**的（已实测）。
        """
        self.log("跑 npm run verify（build + 测试）…")
        env = dict(os.environ)
        env["CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD"] = "20000"
        # ★ 走 lib/proc.py：不这么走的话，无控制台的写作台每跑一次构建就闪一个黑窗
        p = proc.run(
            ["npm", "run", "verify"], cwd=str(self.repo), shell=True,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=1800,
        )
        out = (p.stdout or "") + (p.stderr or "")

        pages = None
        m = re.search(r"(\d+)\s+page\(s\)\s+built", out)
        if m:
            pages = int(m.group(1))
        tests = None
        m = re.search(r"Tests\s+(.+?)\n", out)
        if m:
            tests = m.group(1).strip()

        # ★ 唯一允许红的：tests/tokens.test.ts 的 spawnSync EBUSY（本机在册环境坑）
        env_fail = "spawnSync" in out and "EBUSY" in out
        ok = p.returncode == 0 or (env_fail and "Tests" in out and "failed" in out
                                   and out.count("failed") == 1)
        self.log(f"verify 结束：returncode={p.returncode} 页数={pages} 测试={tests}")
        return {"ok": ok, "returncode": p.returncode, "pages": pages, "tests": tests,
                "env_fail": env_fail, "tail": out[-4000:]}

    # ---- 一键推送 ----

    def _check_verified_commit(self, snapshot: dict, sha: str) -> None:
        """提交必须仍然只包含构建前的文件快照；不通过时只停下，不重写历史。"""
        if gitops.git(self.repo, 'show', '-s', '--format=%P', sha) != snapshot['head']:
            raise GitError('提交期间历史发生变化，本次没有推送；请刷新状态重新校验')
        raw = gitops.git_bin(self.repo, 'diff', '--name-only', '--no-renames', '-z', snapshot['head'], sha)
        changed = [p for p in raw.decode('utf-8', 'surrogateescape').split('\0') if p]
        expected = {f['path']: f['clean_blob_sha'] for f in snapshot['files']}
        # Git 对已有 CRLF 索引可能保留原字节；二者都必须来自校验前同一份内容。
        originals = {f['path']: f.get('raw_blob_sha', f['clean_blob_sha']) for f in snapshot['files']}
        if any(path not in expected for path in changed):
            raise GitError('提交包含构建快照之外的文件，本次没有推送；请检查并发改动')
        actual = {}
        if expected:
            tree = gitops.git_bin(self.repo, 'ls-tree', '-r', '-z', sha, '--', *expected)
            for row in tree.decode('utf-8', 'surrogateescape').split('\0'):
                if row:
                    metadata, path = row.split('\t', 1)
                    actual[path] = metadata.split()[2]
        if any(actual.get(path) not in (blob, originals[path]) for path, blob in expected.items()):
            raise GitError('提交内容与构建前快照不同，本次没有推送；请重新校验')

    def push(self, message: str, skip_verify: bool = False) -> dict:
        with self.push_lock:
            result = {"steps": [], "ok": False}

            try:
                gitops.preflight(self.repo)
                snapshot = gitops.capture_publish_state(self.repo)
            except GitError as e:
                result['error'] = str(e)
                result['steps'].append({'name': '发布前检查', 'ok': False, 'detail': str(e)})
                return result

            if not skip_verify:
                b = self.run_build()
                result["build"] = b
                result["steps"].append({"name": "构建与校验", "ok": b["ok"],
                                        "detail": f"{b['pages']} 页 · {b['tests']}"})
                if not b["ok"]:
                    result["error"] = "构建/测试没过 —— 没有推送。看下面的输出。"
                    return result
            else:
                result["steps"].append({"name": "构建与校验", "ok": True, "detail": "已跳过（你选的）"})

            if gitops.capture_publish_state(self.repo) != snapshot:
                result['error'] = '校验期间文件、暂存区或提交历史发生变化，本次没有发布。请刷新状态后重新校验'
                return result

            files = gitops.changed_files(self.repo)
            approved_sha = snapshot['head']
            if not files:
                result["steps"].append({"name": "提交", "ok": True, "detail": "工作区没有改动"})
            else:
                c = gitops.commit_all(self.repo, message)
                result["steps"].append({"name": "提交", "ok": c.get("ok", False),
                                        "detail": c.get("sha_short", "") + " " + c.get("subject", "")})
                result["commit"] = c
                if not c.get('skipped'):
                    approved_sha = c['sha']
                    try:
                        self._check_verified_commit(snapshot, approved_sha)
                    except GitError as e:
                        result['error'] = str(e)
                        return result

            try:
                pr = gitops.push(self.repo, on_log=self.log, expected_head=approved_sha)
                result["push"] = pr
                result["steps"].append({"name": "推送", "ok": pr["ok"],
                                        "detail": "远端 " + (pr.get("remote_short") or pr.get("sha_short", ""))})
                result["ok"] = pr["ok"]
            except GitError as e:
                result["steps"].append({"name": "推送", "ok": False, "detail": str(e)})
                result["error"] = str(e)

            self.last_push = result
            return result


# ────────────────────────────── HTTP ──────────────────────────────

class Handler(BaseHTTPRequestHandler):
    app: App = None            # 由 serve() 注入
    server_version = "BlogDesk/1.0"

    # 安静一点（默认会把每条请求打到 stderr）
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    # ---- 基础设施 ----

    def _send(self, code: int, body: bytes, ctype: str):
        self.app.request_count += 1        # 计数：启动器的看门狗靠它判断窗口开没开
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 本地工具，别缓存 —— 改完立刻要看到
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ContentError("请求太大（一次最多 64 MB）")
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise ContentError(f"请求体不是合法 JSON：{e}") from e

    def _static(self, rel: str):
        # ★ 路径穿越防护（虽然只监听回环，但没必要留这个口子）
        target = (UI_DIR / rel.lstrip("/")).resolve()
        if not str(target).startswith(str(UI_DIR.resolve())):
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # ---- 路由 ----

    def _video(self, q, head=False):
        data, mime = videos.read_video(self.app.repo, q.get('name', [''])[0])
        size = len(data)
        start, end, code = 0, size - 1, 200
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if not match or not any(match.groups()):
                match = None
            if match:
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                elif int(right) > 0:
                    start = max(0, size - int(right))
                else:
                    start = size
            if not match or start >= size or start > end:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Cache-Control', 'no-store')
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if not head:
            self.wfile.write(data[start:end + 1])

    def do_HEAD(self):  # noqa: N802
        target = urlparse(self.path)
        if target.path != '/api/video':
            self.send_error(404)
            return
        try:
            self._video(parse_qs(target.query), head=True)
        except VideoError:
            self.send_error(404)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == '/api/video':
                return self._video(q)
            if u.path == "/api/health":
                return self._json({"ok": True, "repo": str(self.app.repo)})
            if u.path == "/api/state":
                st = gitops.status(self.app.repo)
                st["last_push"] = self.app.last_push
                return self._json(st)
            if u.path == "/api/posts":
                return self._json({"items": self.app.content.list_posts()})
            if u.path == "/api/post":
                return self._json(self.app.content.read_post(q.get("slug", [""])[0]))
            if u.path == "/api/talks":
                return self._json(self.app.content.read_talks())
            if u.path == "/api/log":
                d = self.app.content.read_log()
                d["next_version"] = self.app.content.next_version()
                return self._json(d)
            if u.path == "/api/gallery":
                return self._json({"items": self.app.content.list_albums()})
            if u.path == "/api/photos":
                album = q.get("album", [None])[0]
                return self._json({"items": images.list_gallery_photos(self.app.content, album)})
            if u.path == "/api/tags":
                # 面板要的都在 tags_overview 里：items / usage / unused / doc 漂移
                return self._json(self.app.content.tags_overview())
            if u.path == "/api/file":
                # 给界面预览图片用（只允许仓库内的文件）
                p = Path(q.get("path", [""])[0]).resolve()
                if not str(p).startswith(str(self.app.repo.resolve())) or not p.is_file():
                    return self._json({"error": "无权访问这个文件"}, 403)
                ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
                return self._send(200, p.read_bytes(), ctype)
            if u.path == "/api/tree":
                return self._json({"cwd": str(self.app.repo),
                                   "posts": self.app.content.list_posts(),
                                   "albums": self.app.content.list_albums()})
            if u.path == "/api/comments":
                return self._comments_list(q)
            if u.path == "/api/config":
                return self._json(cfgmod.public_config(self.app.config))
            # 静态
            return self._static("index.html" if u.path == "/" else u.path)
        except (ContentError, ImageError, VideoError) as e:
            return self._json({"error": str(e)}, 400)
        except GitError as e:
            return self._json({"error": str(e)}, 502)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):  # noqa: N802
        # 同一个应用内的保存、上传、提交与发布互斥；外部 AI 更新另由 revision / Git 检查处理。
        with self.app.push_lock:
            self._post()

    @staticmethod
    def _base_revision(body):
        revision = body.get('base_revision')
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{64}', revision):
            raise ContentError('缺少编辑版本，请重新读取后再保存；当前编辑内容尚未写入文件')
        return revision

    def _post(self):
        u = urlparse(self.path)
        try:
            body = self._body()
            c = self.app.content

            if u.path == "/api/post":
                slug = body.get("slug", "").strip()
                fm, text = body.get("frontmatter") or {}, body.get("body", "")
                if body.get("create"):
                    return self._json(c.create_post(slug, fm))
                return self._json(c.write_post(slug, fm, text, base_revision=self._base_revision(body)))

            if u.path == "/api/post/delete":
                return self._json(c.delete_post(body.get("slug", ""), base_revision=self._base_revision(body)))

            if u.path == "/api/tags":
                # 加一个标签（写进 src/tag-registry.ts）
                return self._json(c.add_tag(body.get("name", "")))

            if u.path == "/api/tags/delete":
                # 删一个标签。正在被文章用的会被拒（否则那些文章构建失败）
                return self._json(c.remove_tag(body.get("name", "")))

            if u.path == "/api/talks":
                return self._json(c.write_talks(body.get("items") or [], base_revision=self._base_revision(body)))

            if u.path == "/api/log":
                return self._json(c.write_log(body.get("items") or [], base_revision=self._base_revision(body)))

            if u.path == '/api/video/upload':
                try:
                    data = base64.b64decode((body.get('data') or '').split(',')[-1], validate=True)
                except (ValueError, TypeError) as e:
                    raise VideoError('视频数据无效，请重新选择文件') from e
                return self._json(videos.save_video(c, body.get('name', ''), data, body.get('where', ''), body.get('slug', '')))

            if u.path == "/api/upload":
                data = base64.b64decode((body.get("data") or "").split(",")[-1])
                where = body.get("where")
                name = body.get("name") or images.timestamp_name(".jpg")
                mk = int(body.get("max_kb") or 300)
                if where == "post":
                    return self._json(images.save_for_post(c, body["slug"], name, data, mk))
                if where == "talks":
                    return self._json(images.save_for_talk(c, name, data, mk))
                if where == "gallery":
                    return self._json(images.save_for_gallery(c, body["album"], name, data, mk))
                raise ContentError("不知道这张图该放哪（where 必须是 post / talks / gallery）")

            if u.path == "/api/image/delete":
                return self._json(images.delete_image(body.get("path", "")))

            if u.path == "/api/commit":
                return self._json(gitops.commit_all(self.app.repo, body.get("message", "写作台提交")))

            if u.path == "/api/push":
                return self._json(self.app.push(
                    body.get("message") or "写作台：更新内容",
                    bool(body.get("skip_verify")),
                ))

            if u.path == "/api/comment/delete":
                return self._json(self._comment_delete(body))

            if u.path == "/api/config":
                return self._json(self._set_config(body))

            if u.path == "/api/reveal":
                return self._json(self._reveal(body))

            if u.path == "/api/copy-to-talks":
                src = Path(body.get("path", ""))
                if not src.is_file():
                    raise ContentError(f"找不到这张图：{src}")
                mk = int(body.get("max_kb") or self.app.config.get("max_kb") or 300)
                return self._json(images.save_for_talk(
                    c, body.get("name") or src.name, src.read_bytes(), mk))

            return self._json({"error": "没有这个接口"}, 404)

        except ContentConflict as e:
            return self._json({'error': str(e), 'conflict': True}, 409)
        except (ContentError, ImageError, VideoError) as e:
            return self._json({"error": str(e)}, 400)
        except GitError as e:
            return self._json({"error": str(e)}, 502)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # ---- 本机配置 ----

    def _set_config(self, body: dict) -> dict:
        """只接受白名单里的键。`admin_token` 允许被显式清空（传空字符串）。"""
        patch = body.get("patch") if isinstance(body.get("patch"), dict) else body
        clean = {}
        for k, v in (patch or {}).items():
            if k not in cfgmod.WRITABLE:
                continue
            if k == "max_kb":
                try:
                    v = max(30, min(4000, int(v)))
                except (TypeError, ValueError):
                    continue
            if k == "repo" and not Path(str(v)).is_dir():
                raise ContentError(f"这个目录不存在：{v}")
            clean[k] = v
        self.app.config.update(clean)
        cfgmod.save_config(self.app.config)
        self.app.log(f"配置已存：{', '.join(clean) or '（无改动）'}")
        return {"ok": True, "config": cfgmod.public_config(self.app.config)}

    def _reveal(self, body: dict) -> dict:
        """在资源管理器里打开目录（只认几个固定名字，或仓库内的绝对路径）。"""
        named = {
            "repo": self.app.repo,
            "content": self.app.repo / "content",
            "posts": self.app.repo / "content" / "posts",
            "pages": self.app.repo / "content" / "pages",
            "gallery": self.app.repo / "content" / "gallery",
            "talks": self.app.repo / "src" / "assets" / "talks",
            "assets": self.app.repo / "src" / "assets",
            "public": self.app.repo / "public",
            "dist": self.app.repo / "dist",
        }
        p = None
        if body.get("path"):
            cand = Path(body["path"]).resolve()
            root = self.app.repo.resolve()
            if not str(cand).startswith(str(root)):
                raise ContentError("只能打开仓库内的目录")
            p = cand
        else:
            p = named.get(body.get("target", ""))
        if p is None:
            raise ContentError(f"不认识这个入口：{body.get('target') or body.get('path')}")
        if not p.exists():
            raise ContentError(f"这个目录还不存在：{p}")
        if not hasattr(os, "startfile"):          # 非 Windows
            raise ContentError("这个功能只在 Windows 上可用")
        if p.is_file():
            os.startfile(str(p.parent))
        else:
            os.startfile(str(p))
        return {"ok": True, "path": str(p)}

    # ---- 线上留言（管理接口，需要密钥）----

    # ★ 必须自报一个 UA（2026-10-09 实测踩到）：Python 的默认头是 `Python-urllib/3.x`，
    #   **Cloudflare 会直接拦成 403** —— 而同一个请求换 curl / python-requests 就 200。
    #   症状是「命令行验通了、写作台里永远读不到」，且报的是 403 不是 401，很容易
    #   误判成密钥错。写死一个自己的名字即可，不要依赖默认值。
    UA = "NorthCedar-Desk/1.0"

    def _admin(self):
        """读配置里的管理密钥。没有就明确报错 —— **fail closed**。"""
        tok = (self.app.config.get("admin_token") or "").strip()
        if not tok:
            raise ContentError(
                "还没有配置管理密钥，所以删不了线上留言。"
                "请在「设置」里填入与站点环境变量 ADMIN_TOKEN 相同的值。"
            )
        return tok

    def _site(self):
        return (self.app.config.get("site") or "https://northcedar.top").rstrip("/")

    def _comments_list(self, q):
        import urllib.request, urllib.error  # noqa: E401
        tok = self._admin()
        url = f"{self._site()}/api/admin/comments"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {tok}", "User-Agent": self.UA}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return self._json(json.loads(r.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            # 接口的错误消息比 "HTTP 401" 有用得多（会说清是"密钥不对"还是"没配 ADMIN_TOKEN"），
            # 原样转给界面。**不回显密钥** —— 接口那边也不回显。
            detail = ""
            try:
                detail = (json.loads(e.read().decode("utf-8")) or {}).get("error", "")
            except Exception:  # noqa: BLE001
                pass
            raise ContentError(detail or f"读线上留言失败：HTTP {e.code}") from e
        except Exception as e:  # noqa: BLE001
            raise ContentError(f"读线上留言失败：{e}") from e

    def _comment_delete(self, body):
        import urllib.request, urllib.error  # noqa: E401
        tok = self._admin()
        url = f"{self._site()}/api/admin/comments"
        # ★ 必须把 kind 带上去（2026-10-09 修）：
        #   接口在 kind 缺省时按"顶层留言"处理，而 replies 与 comments 是**两张表**
        #   ⇒ 两条表的 id 会各从 1 开始，撞号是常态而不是例外。
        #   少传一个 kind，删一条回复就变成了"删掉 id 相同的另一条顶层留言，连带它的回复"。
        kind = body.get("kind") or "comment"
        if kind not in ("comment", "reply"):
            raise ContentError(f"不认识的删除类型：{kind!r}")
        payload = json.dumps({"id": body.get("id"), "kind": kind}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="DELETE",
                                     headers={"Authorization": f"Bearer {tok}",
                                              "User-Agent": self.UA,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return self._json(json.loads(r.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            # 接口把"这条已经不在了"回成 404：这是**能看懂**的业务答复，不该报成"删留言失败"。
            detail = ""
            try:
                detail = (json.loads(e.read().decode("utf-8")) or {}).get("error", "")
            except Exception:  # noqa: BLE001
                pass
            raise ContentError(detail or f"删留言失败：HTTP {e.code}") from e
        except Exception as e:  # noqa: BLE001
            raise ContentError(f"删留言失败：{e}") from e


def serve(repo: Path, config: dict, port: int = 0, host: str = "127.0.0.1"):
    """起服务。port=0 表示让系统挑一个空闲端口。返回 (httpd, port, app)。"""
    cfg = dict(config or {})
    # ★ 让「界面上看到的仓库路径」永远等于「真正在用的那个」：
    #   启动器可以用 --repo 覆盖配置，若不纠正，设置页会显示 config.json 里的旧路径，
    #   用户会以为在改一个仓库，实际改的是另一个。
    cfg["repo"] = str(Path(repo))
    app = App(repo, cfg)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    real_port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, real_port, app
