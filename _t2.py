# -*- coding: utf-8 -*-
"""写回往返测试：在**临时副本**上做，验证
  ① 说说/日志写回后，注释与已知内容一字不差
  ② 文章 frontmatter 写回的字段顺序与站上一致
  ③ 危险标量（冒号+空格）会被正确加引号
"""
import shutil, sys, tempfile, subprocess
from pathlib import Path
sys.path.insert(0, r"D:/router/blog-desk")
from lib.content import Content, yaml_scalar

SRC = Path(r"D:/router/blog")
tmp = Path(tempfile.mkdtemp(prefix="desk-test-"))
# 只复制内容层与 src/（够用）
for sub in ("content", "src"):
    shutil.copytree(SRC / sub, tmp / sub)
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
slug = "two-ai-built-this-blog"
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
