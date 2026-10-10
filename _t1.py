# -*- coding: utf-8 -*-
"""内容层只读冒烟测试：不改任何文件。"""
import sys, json
from pathlib import Path
sys.path.insert(0, r"D:/router/blog-desk")
from lib.content import Content

c = Content(Path(r"D:/router/blog"))
posts = c.list_posts()
print(f"文章 {len(posts)} 篇:")
for p in posts:
    print(f"  {p['slug']:42s} {p['date']}  草稿={p['draft']}  图{len(p['images'])}张  {p['title'][:22]}")

print("\n标签注册表:", c.read_tags())

t = c.read_talks()
print(f"\n说说 {len(t['items'])} 条 | head 长度 {len(t['head'])} 字符（应含注释）")
print("  head 前 80:", repr(t['head'][:80]))
print("  ★ 注释保住了吗:", "# 说说页内容" in t['head'])

lg = c.read_log()
print(f"\n日志 {len(lg['items'])} 条 | 下一版本 {c.next_version()}")
print("  ★ 注释保住了吗:", "# 日志页内容" in lg['head'])

albums = c.list_albums()
print(f"\n相册 {len(albums)} 本:")
for a in albums:
    print(f"  {a['name']:22s} {a['count']} 张  {a['date']}")

rd = c.read_post(posts[0]['slug'])
print(f"\n读单篇 OK：{rd['slug']} | frontmatter 键 {list(rd['frontmatter'].keys())}")
print(f"  正文 {len(rd['body'])} 字符 | 行尾 {'CRLF' if rd['newline']==chr(13)+chr(10) else 'LF'}")
