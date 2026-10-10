# -*- coding: utf-8 -*-
"""写作台 ↔ 站点留言管理接口的**契约测试**（2026-10-09）。

为什么这个接缝要单独测：它是**跨项目**的 ——
一端是部署在 Cloudflare 上的 `functions/api/admin/comments.ts`，
另一端是 `server.py` 的 `_comments_list` / `_comment_delete`。
两边各自"看起来对"不能说明任何事：真正会出事的是
「这边少传了一个 `kind`，那边就按顶层留言处理」——
`replies` 与 `comments` 是**两张表**、id 各自从 1 开始，撞号是常态。
这个 bug 在写这轮时真的存在过（写作台只发 `{"id": …}`），
所以第 ② 条就是这个 bug 的门闩。

做法：**不连网、不起服务** —— 把 `urllib.request.urlopen` 换成假的，
捕获真实构造出来的 Request（URL / 方法 / 头 / body），再对照接口那侧的约定断言。
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as S  # noqa: E402

# 这份测试**不连网、不起服务**，只捕获构造出来的 Request 再对照接口那侧的约定，
# 所以 REPO 纯粹是"仓库路径"这个参数的占位值 —— 目录不需要真的存在，
# 更不必钉死某个人的真路径（那等于开源时顺带公开本机目录布局）。
REPO = Path(os.environ.get("BLOG_REPO", "/tmp/any-blog-repo"))
TOK = "t" * 48
checks: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))
    print(("  ✓ " if ok else "  ✗ ") + name + (f"　{detail}" if detail else ""))


# ─────────────────────── 假的 urlopen ───────────────────────

class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


sent: list[dict] = []
next_result: dict = {}


def fake_urlopen(req, timeout=None):
    sent.append({
        "url": req.full_url,
        "method": req.get_method(),
        "headers": {k.lower(): v for k, v in req.header_items()},
        "body": json.loads(req.data.decode("utf-8")) if req.data else None,
    })
    boom = next_result.get("raise")
    if boom is not None:
        raise boom
    return FakeResp(next_result.get("ok", {"ok": True}))


urllib.request.urlopen = fake_urlopen  # 只影响本进程


def make_handler(cfg: dict):
    class Probe(S.Handler):
        def _json(self, obj, code: int = 200):
            self.last = (code, obj)

        def _send(self, *a, **k):
            raise AssertionError("契约测试里不该走到 socket")

    p = Probe.__new__(Probe)
    p.app = S.App(REPO, cfg)
    p.last = None
    return p


def cfg(token=TOK, site="https://northcedar.top"):
    return {"repo": str(REPO), "site": site, "admin_token": token}


print("一、删留言：写作台发出去的包，接口那侧认得吗")
print("-" * 66)

# ① 删顶层留言
sent.clear()
h = make_handler(cfg())
h._comment_delete({"id": 7, "kind": "comment"})
s = sent[-1]
chk("① 顶层留言", s["body"] == {"id": 7, "kind": "comment"}, json.dumps(s["body"], ensure_ascii=False))
chk("① 打在 /api/admin/comments 上", s["url"] == "https://northcedar.top/api/admin/comments", s["url"])
chk("① 用 DELETE", s["method"] == "DELETE", s["method"])
chk("① 带 Bearer 密钥", s["headers"].get("authorization") == f"Bearer {TOK}",
    (s["headers"].get("authorization") or "")[:20] + "…")
chk("① 声明了 JSON 体", s["headers"].get("content-type") == "application/json", s["headers"].get("content-type", ""))
# ★ 2026-10-09 真踩过：urllib 默认自报 `Python-urllib/3.x`，Cloudflare 直接拦成 403，
#   而同一个请求换 curl / python-requests 就 200 ⇒「命令行验通了、写作台里永远读不到」。
#   报的是 403 不是 401，极易误判成密钥错。所以必须钉死"不许用默认 UA"。
chk("① ★ 自报 UA（默认的 Python-urllib 会被 Cloudflare 拦成 403）",
    s["headers"].get("user-agent", "").startswith("NorthCedar-Desk"),
    s["headers"].get("user-agent", "(没带)"))
chk("① 界面拿到接口的原话", h.last == (200, {"ok": True}), str(h.last))

# ② ★ 删回复必须带 kind=reply —— 少传它，接口会按顶层留言处理、删掉**另一条**留言
sent.clear()
h = make_handler(cfg())
h._comment_delete({"id": 11, "kind": "reply"})
s = sent[-1]
chk("② ★ 回复带 kind=reply（不带就会删错另一张表里撞号的留言）",
    s["body"] == {"id": 11, "kind": "reply"}, json.dumps(s["body"], ensure_ascii=False))

# ③ 缺 kind 的老调用（界面上不该发生，但别把 undefined 当 reply 发出去）
sent.clear()
h = make_handler(cfg())
h._comment_delete({"id": 3})
chk("③ 缺 kind ⇒ 按 comment（不是丢字段、也不是当 reply）",
    sent[-1]["body"] == {"id": 3, "kind": "comment"}, json.dumps(sent[-1]["body"], ensure_ascii=False))

# ④ 不认识的 kind：拦在本机，别发出去让接口去猜
sent.clear()
h = make_handler(cfg())
try:
    h._comment_delete({"id": 3, "kind": "replys"})
    chk("④ 未知 kind 被拦住", False, "竟然没报错")
except S.ContentError as e:
    chk("④ 未知 kind 被拦住", "不认识的删除类型" in str(e), str(e))
chk("④ 未知 kind 时一个包都没发", sent == [], f"{len(sent)} 个")

# ⑤ 没配密钥：fail closed，且不发包
sent.clear()
h = make_handler(cfg(token=""))
try:
    h._comment_delete({"id": 3, "kind": "comment"})
    chk("⑤ 没配密钥就拒绝", False, "竟然发出去了")
except S.ContentError as e:
    chk("⑤ 没配密钥就拒绝，并说清要填什么", "ADMIN_TOKEN" in str(e) and "设置" in str(e), str(e))
chk("⑤ 没配密钥时一个包都没发", sent == [], f"{len(sent)} 个")

print()
print("二、接口回错时，界面看到的是什么")
print("-" * 66)

# ⑥ 404「这条已经不存在了」：要透出接口的原话，不是 "HTTP Error 404"
sent.clear()
next_result.clear()
next_result["raise"] = urllib.error.HTTPError(
    "https://x/api/admin/comments", 404, "Not Found", {},
    __import__("io").BytesIO(json.dumps({"error": "这条留言已经不存在了（可能刚被删过）"}).encode("utf-8")),
)
h = make_handler(cfg())
try:
    h._comment_delete({"id": 424242, "kind": "comment"})
    chk("⑥ 404 有转成能看懂的话", False, "竟然没报错")
except S.ContentError as e:
    chk("⑥ 404 转成接口的原话（不是 HTTP Error 404）", "已经不存在" in str(e), str(e))

# ⑦ 500「服务端没配 ADMIN_TOKEN」
next_result.clear()
next_result["raise"] = urllib.error.HTTPError(
    "https://x/api/admin/comments", 500, "err", {},
    __import__("io").BytesIO(json.dumps({"error": "服务端没有配置 ADMIN_TOKEN，管理接口已关闭"}).encode("utf-8")),
)
h = make_handler(cfg())
try:
    h._comment_delete({"id": 3, "kind": "comment"})
    chk("⑦ 500 有转成能看懂的话", False, "竟然没报错")
except S.ContentError as e:
    chk("⑦ 500 转成接口的原话", "ADMIN_TOKEN" in str(e), str(e))

# ⑧ HTTPError 的 body 不是 JSON 时不能连累报错本身
next_result.clear()
next_result["raise"] = urllib.error.HTTPError(
    "https://x", 502, "bad gateway", {}, __import__("io").BytesIO(b"<html>502</html>")
)
h = make_handler(cfg())
try:
    h._comment_delete({"id": 3, "kind": "comment"})
    chk("⑧ 非 JSON 错误体也能兜住", False, "竟然没报错")
except S.ContentError as e:
    chk("⑧ 非 JSON 错误体也能兜住（退化成 HTTP 502）", "502" in str(e), str(e))

print()
print("三、读留言列表")
print("-" * 66)

next_result.clear()
next_result["ok"] = {
    "items": [{"id": 1, "slug": "s", "name": "n", "body": "b", "createdAt": "2026-10-09T00:00:00.000Z",
               "replies": [{"id": 2, "parentId": 1, "replyTo": "", "name": "m", "body": "r",
                            "createdAt": "2026-10-09T00:00:01.000Z"}]}],
    "limit": 200,
}
sent.clear()
h = make_handler(cfg())
h._comments_list("")
chk("⑨ GET 打在同一个地址上", sent[-1]["url"].endswith("/api/admin/comments") and sent[-1]["method"] == "GET",
    sent[-1]["method"] + " " + sent[-1]["url"])
chk("⑨ 带 Bearer 密钥", sent[-1]["headers"].get("authorization") == f"Bearer {TOK}", "…")
chk("⑨ ★ 读列表这条路也要自报 UA（两条路都得过 Cloudflare 那道拦）",
    sent[-1]["headers"].get("user-agent", "").startswith("NorthCedar-Desk"),
    sent[-1]["headers"].get("user-agent", "(没带)"))
chk("⑨ items 原样透给界面", h.last[1]["items"][0]["id"] == 1 and h.last[1]["items"][0]["replies"][0]["id"] == 2,
    json.dumps(h.last[1]["items"][0], ensure_ascii=False)[:60] + "…")

# ⑩ 站点地址末尾多一个斜杠不能拼出双斜杠
sent.clear()
h = make_handler(cfg(site="https://northcedar.top/"))
h._comments_list("")
chk("⑩ 站点地址末尾带 / 也不会拼成 //", sent[-1]["url"] == "https://northcedar.top/api/admin/comments", sent[-1]["url"])

# ⑪ 401 也要能看懂
next_result.clear()
next_result["raise"] = urllib.error.HTTPError(
    "https://x", 401, "Unauthorized", {},
    __import__("io").BytesIO(json.dumps({"error": "管理密钥不对"}).encode("utf-8")),
)
h = make_handler(cfg())
try:
    h._comments_list("")
    chk("⑪ 401 转成接口的原话", False, "竟然没报错")
except S.ContentError as e:
    chk("⑪ 401 转成接口的原话", str(e) == "管理密钥不对", str(e))

print("-" * 66)
bad = [n for n, ok, _ in checks if not ok]
print(f"合计 {len(checks)} 项，通过 {len(checks) - len(bad)}，失败 {len(bad)}")
if bad:
    print("失败：" + "、".join(bad))
sys.exit(1 if bad else 0)
