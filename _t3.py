# -*- coding: utf-8 -*-
"""接口冒烟测试：起服务，逐个打新老接口，只看形状不看内容。

只读为主；唯一会写的是 /api/config 往返（写到临时 config 后立刻还原）。
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as S           # noqa: E402
from lib import config as C  # noqa: E402

# 被当作"博客仓库"替身的目录。
# ★ 这份测试会真的往里写图（upload / copy-to-talks），所以替身必须**临时生成**：
#   既不能钉死某个人的绝对路径（那是本机目录布局，开源时不该带出去），
#   也不能指向真博客仓库（它会往站主的 content/ 里塞 _smoke_test.jpg 再删，
#   万一中途失败就留下垃圾）。
#   ⇒ 自己造一个最小仓库骨架：content/gallery（放一张图，供 copy-to-talks 取用）
#     与 src/assets/talks（copy 的落点）—— 后者不存在的话接口会直接报错，
#     copy-to-talks 那条会假失败。
#   ⇒ 测完整个删掉。想指到真仓库跑，设 BLOG_REPO 环境变量即可。
_FIXTURE = Path(__file__).resolve().parent / "_fixture"


def _make_sandbox() -> Path:
    """造一个最小的"博客仓库"骨架，够上面那批接口跑通。

    哪些文件是**必须**的（少一个就假失败）：
      · src/tag-registry.ts     —— /api/tags 的唯一数据源，缺了返回空数组
      · content/posts/*/index.md —— /api/posts 要至少一篇
      · content/pages/log.md     —— /api/log 要至少一条版本记录
      · content/gallery/demo/    —— /api/gallery 要至少一个专辑
      · src/assets/talks/        —— copy-to-talks 的落点，目录不存在会直接报错
    """
    if os.environ.get("BLOG_REPO"):
        return Path(os.environ["BLOG_REPO"])
    sb = Path(tempfile.mkdtemp(prefix="writedesk-t3-"))
    for rel in ("content/gallery/demo", "content/pages", "content/posts/hello-world",
                "src/assets/talks", "public"):
        (sb / rel).mkdir(parents=True, exist_ok=True)

    (sb / "src" / "tag-registry.ts").write_text(
        "// 标签注册表 —— 校验的唯一事实来源。\n"
        "// ★ 测试沙箱自带的最小版本，与真仓库同形即可。\n"
        "export const TAGS = ['随笔', '技术'] as const;\n"
        "export type Tag = (typeof TAGS)[number];\n",
        encoding="utf-8")

    (sb / "content" / "posts" / "hello-world" / "index.md").write_text(
        "---\n"
        "title: 测试文章一\n"
        "date: 2026-10-01\n"
        "summary: \"改过的摘要: 带冒号\"\n"
        "tags: [随笔, 技术]\n"
        "---\n\n"
        "正文第一段。这里有一句带冒号的内容 举例：像这样。\n",
        encoding="utf-8")

    (sb / "content" / "pages" / "log.md").write_text(
        "---\n"
        "title: 日志\n"
        "items:\n"
        "  - date: 2026-10-01\n"
        "    version: v1.0\n"
        "    changes:\n"
        "      - 沙箱里的第一条\n"
        "---\n\n"
        "（沙箱占位，不需要真的内容。）\n",
        encoding="utf-8")

    (sb / "content" / "pages" / "talks.md").write_text(
        "---\n"
        "title: 说说\n"
        "intro: 短句。\n"
        "items:\n"
        "  - date: 2026-10-01\n"
        "    text: 沙箱里的第一条说说\n"
        "---\n\n"
        "（沙箱占位，不需要真的内容。）\n",
        encoding="utf-8")

    (sb / "content" / "gallery" / "demo" / "index.md").write_text(
        "---\ntitle: 沙箱相册\n---\n\n沙箱占位。\n", encoding="utf-8")

    # 一张 1×1 的 PNG：copy-to-talks 需要一张真图当源
    (sb / "content" / "gallery" / "demo" / "a.png").write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000d49444154789c6360000002000100ffff0300000600"
        "0557bfabd40000000049454e44ae426082"))
    return sb


REPO = _make_sandbox()
orig = C.load_config()
cfg = dict(orig)


def hit(path, body=None, method=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


httpd, PORT, app = S.serve(REPO, cfg)
print(f"服务起在 {PORT}\n" + "-" * 66)

checks = []


def chk(name, cond, extra=""):
    checks.append((name, bool(cond), extra))
    print(f"{'✔' if cond else '✘'} {name}  {extra}")


# ---- 只读接口 ----
st, j = hit("/api/health")
chk("health", st == 200 and j.get("ok"), f"repo={Path(j.get('repo','')).name}")

st, j = hit("/api/state")
chk("state", st == 200 and "branch" in j,
    f"branch={j.get('branch')} sync={j.get('sync')} pending={j.get('pending')} dirty={j.get('dirty_count')} 远端={j.get('remote_short')}")

st, j = hit("/api/posts")
chk("posts", st == 200 and isinstance(j.get("items"), list), f"{len(j.get('items', []))} 篇")

st, j = hit("/api/log")
chk("log", st == 200 and "items" in j, f"{len(j.get('items', []))} 条 · 下一个 {j.get('next_version')}")

st, j = hit("/api/talks")
chk("talks", st == 200 and "items" in j, f"{len(j.get('items', []))} 条 · head {len(j.get('head',''))} 字")

st, j = hit("/api/gallery")
chk("gallery", st == 200, f"{len(j.get('items', []))} 个相册")

st, j = hit("/api/photos")
chk("photos(all)", st == 200, f"{len(j.get('items', []))} 张")

st, j = hit("/api/tags")
chk("tags", st == 200 and j.get("items"), ", ".join(j.get("items", [])))

st, j = hit("/api/config")
chk("config(GET)", st == 200 and "admin_token" not in j, f"has_token={j.get('has_token')} site={j.get('site')}")
chk("config 报告的是「真正在用的仓库」", j.get("repo") == str(REPO), j.get("repo"))

# ---- 写接口：配置往返（存回原值） ----
st, j = hit("/api/config", {"max_kb": 333})
chk("config(POST 收下)", st == 200 and j.get("config", {}).get("max_kb") == 333)
st, j = hit("/api/config", {"max_kb": orig.get("max_kb", 300)})
chk("config(还原)", st == 200 and j.get("config", {}).get("max_kb") == orig.get("max_kb", 300))

# 白名单：不许塞别的键
st, j = hit("/api/config", {"__evil__": 1, "site": orig["site"]})
after = C.load_config()
chk("config 白名单", "__evil__" not in after and after["site"] == orig["site"])

# ---- 上传/转存：走真实文件但立刻删掉 ----
import base64, io
from PIL import Image
buf = io.BytesIO()
Image.new("RGB", (2400, 1600), (40, 120, 70)).save(buf, "JPEG", quality=90)
raw = buf.getvalue()
data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()

st, j = hit("/api/upload", {"data": data_url, "where": "talks", "name": "_smoke_test.jpg", "max_kb": 120})
ok = st == 200 and j.get("ok")
chk("upload→talks", ok, f"{j.get('name')} {j.get('report', {}).get('note', '')}")

made = []
if ok:
    made.append(Path(j["path"]))

# 相册原图 → 转存进 talks（copy-to-talks）
album_imgs = [p for p in (REPO / "content" / "gallery").rglob("*")
              if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
if album_imgs:
    st, j = hit("/api/copy-to-talks", {"path": str(album_imgs[0]), "max_kb": 120})
    ok2 = st == 200 and j.get("ok")
    chk("copy-to-talks", ok2, f"源 {album_imgs[0].name} → {j.get('name')} {j.get('report', {}).get('note','')}")
    if ok2:
        made.append(Path(j["path"]))

# reveal：真开资源管理器太吵，只验证非法输入被拦
st, j = hit("/api/reveal", {"target": "不存在的入口"})
chk("reveal 拒绝未知入口", st == 400 and "error" in j, j.get("error", "")[:40])
st, j = hit("/api/reveal", {"path": r"C:\Windows"})
chk("reveal 拒绝仓库外路径", st == 400, j.get("error", "")[:40])

# ---- 清理：删掉刚才造出来的图 ----
for p in made:
    if p.is_file():
        p.unlink()
        print(f"  （已清理 {p.name}）")
left = [p.name for p in (REPO / "src" / "assets" / "talks").glob("_smoke_test*")]
chk("清理干净", not left, str(left))

print("-" * 66)
bad = [n for n, okk, _ in checks if not okk]
print(f"合计 {len(checks)} 项，通过 {len(checks)-len(bad)}，失败 {len(bad)}")
if bad:
    print("失败：" + "、".join(bad))
httpd.shutdown()
# 只删自己造的那个沙箱；指向真仓库跑时（BLOG_REPO）绝不能删别人的目录
if not os.environ.get("BLOG_REPO"):
    shutil.rmtree(REPO, ignore_errors=True)
    print(f"（沙箱已清理 {REPO}）")
sys.exit(1 if bad else 0)
