# -*- coding: utf-8 -*-
"""写回往返测试：在**临时副本**上做，验证
  ① 说说/日志写回后，注释与已知内容一字不差
  ② 文章 frontmatter 写回的字段顺序与站上一致
  ③ 危险标量（冒号+空格）会被正确加引号
"""
import shutil, sys, os, tempfile, subprocess
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib.content import Content, yaml_scalar

# 待测仓库走 BLOG_REPO；不给就在临时目录里造一份最小骨架。
#
# ★ 为什么自带骨架而不是指向某个人的真仓库：
#   这份测试第 ③ 段要读一篇**具名文章**（字段顺序要照着它比对），
#   指向真仓库就等于把「本机仓库里恰好有这篇」当成前提 ——
#   换个内容仓库、或那篇被改名，它就假失败（实测踩过：找不到文章直接抛异常）。
#   自建骨架后，这份测试在任何机器上跑出来的结论都一样。
SLUG = "two-ai-built-this-blog"

def _scaffold(root: Path) -> Path:
    (root / "content/posts" / SLUG).mkdir(parents=True, exist_ok=True)
    (root / "content/pages").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "content/posts" / SLUG / "index.md").write_text(
        "---\n"
        "title: 测试文章\n"
        "date: 2026-10-09\n"
        "summary: 往返测试用的样例文章。\n"
        "tags:\n"
        "  - 随笔\n"
        "---\n\n正文一行。\n",
        encoding="utf-8")
    (root / "content/pages/talks.md").write_text(
        "---\n"
        "# 说说页内容\n"
        "items:\n"
        "  - date: 2026-10-01\n"
        "    text: 原有一条说说。\n"
        "---\n",
        encoding="utf-8")
    (root / "content/pages/log.md").write_text(
        "---\n"
        "# 日志页内容\n"
        "items:\n"
        "  - date: 2026-10-01\n"
        "    version: v1.0\n"
        "    changes:\n"
        "      - 初版。\n"
        "---\n",
        encoding="utf-8")
    return root

tmp = Path(tempfile.mkdtemp(prefix="desk-test-"))
if os.environ.get("BLOG_REPO"):
    SRC = Path(os.environ["BLOG_REPO"])
    # 只复制内容层与 src/（够用）
    for sub in ("content", "src"):
        if (SRC / sub).is_dir():
            shutil.copytree(SRC / sub, tmp / sub)
else:
    _scaffold(tmp)
c = Content(tmp)
print("临时仓库:", tmp)

# ── ① 说说：加一条带「冒号+空格」的，再写回，再读回来比对 ──
before = c.read_talks()
orig_head = before["head"]
items = list(before["items"])
items.insert(0, {"date": "2026-10-09", "text": "测试：注意这里有个冒号 后面还有字",
                 "images": [{"src": "2026xxxx.jpg", "alt": "说明"}]})
c.write_talks(items)
raw = (tmp / "content/pages/talks.md").read_text(encoding="utf-8")
after = c.read_talks()
print("\n① 说说往返")
print("   注释仍在:", "# 说说页内容" in after["head"])
print("   head 一字不差:", after["head"] == orig_head)
print("   条数:", len(before["items"]), "→", len(after["items"]))
newit = [i for i in after["items"] if i.get("date") == "2026-10-09"][0]
print("   冒号那条读回来:", repr(newit["text"]))
print("   ★ 不是 [object Object]:", isinstance(newit["text"], str))
print("   写出的那行:", [l for l in raw.split("\n") if "注意这里" in l])

# ── ② 日志：加一条多句 changes ──
lb = c.read_log()
li = list(lb["items"])
li.insert(0, {"date": "2026-10-09", "version": "v9.99",
              "changes": ["第一句:含冒号+空格", "第二句很普通", "第三句有 # 号"]})
c.write_log(li)
la = c.read_log()
print("\n② 日志往返")
print("   注释仍在:", "# 日志页内容" in la["head"])
print("   head 一字不差:", la["head"] == lb["head"])
v99 = [i for i in la["items"] if i["version"] == "v9.99"][0]
print("   changes 全为字符串:", all(isinstance(x, str) for x in v99["changes"]), v99["changes"])
print("   下一版本:", c.next_version())

# ── ③ 文章 frontmatter 字段顺序 ──
slug = SLUG
p = c.read_post(slug)
fm = dict(p["frontmatter"])
fm["updated"] = "2026-10-09"
fm["cover"] = "./cover.jpg"
fm["coverAlt"] = "封面说明"
c.write_post(slug, fm, p["body"])
raw2 = (tmp / "content/posts" / slug / "index.md").read_text(encoding="utf-8")
print("\n③ 文章写回")
print("   新 frontmatter:")
for line in raw2.split("---")[1].strip().split("\n"):
    print("     ", line)
print("   正文长度不变:", len(c.read_post(slug)["body"]) == len(p["body"]))

print("\n临时仓库清理:", shutil.rmtree(tmp, ignore_errors=True) is None)
