# -*- coding: utf-8 -*-
"""写作台 · `updated` 自动推进（2026-10-27）

起因：`content/posts/my-python-memorandum/index.md` 当天加了 106 行，
`updated` 还停在 2026-10-25 ⇒ 页面印着「更新于 2026-10-25」，sitemap 的
lastmod 也滞后。`updated` 是**手填**字段，而改正文的人不会记得同步它。

口径（站主 2026-10-27 定，回答「文章日期更新」时选的甲）：
  **只推进 `updated`，不动 `date`** —— `date` 是首发日，归档排序、RSS、
  首页位置都按它；§6.60.2 那次「date 改今天」是一次性要求，不是常态。

守八件事：
  ① 正文改过 ⇒ `updated` 推进到今天
  ② 手填的 `updated` 不被覆盖（用户显式指定了日期）
  ③ 手动清空 `updated` 也不被覆盖（用户想藏掉「更新于」）
  ④ 没有实质变化（打开又保存）不动 `updated`
  ⑤ 原文 `updated` 比今天晚 ⇒ 不往回改
  ⑥ 新建文章不写 `updated`（`date` 就是今天，没有「更新」可言）
  ⑦ 只改字段、正文不动也算一次更新
  ⑧ `write_post` 真的接上了这套规则（临时仓库端到端，不是只测纯函数）

用法：python _t16.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib.content import Content, parse_frontmatter, split_frontmatter, sync_updated  # noqa: E402

TODAY = "2026-10-27"
OLD = (
    "---\n"
    "title: 题\n"
    "date: 2026-10-25\n"
    "updated: 2026-10-25\n"
    "summary: 简介\n"
    "tags: [随笔]\n"
    "draft: false\n"
    "---\n"
    "原正文\n"
)

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f"  {detail}" if detail else ""))


def old_fm(text: str = OLD) -> dict:
    """模拟「读进来再保存」：当前值就是原文的值。"""
    return parse_frontmatter(split_frontmatter(text)[0])


def old_body(text: str = OLD) -> str:
    return split_frontmatter(text)[1]


print("写作台 · updated 自动推进")

# ① 正文改过 ⇒ 推进
fm = old_fm()
fm["summary"] = fm["summary"]  # 不变
out = sync_updated(fm, "改过的正文\n", OLD, TODAY)
check("① 正文改过，updated 推进到今天", out.get("updated") == TODAY, f"→ {out.get('updated')!r}")

# ② 手填值不覆盖
fm = old_fm()
fm["updated"] = "2026-11-02"
out = sync_updated(fm, "改过的正文\n", OLD, TODAY)
check("② 手填的 updated 不被覆盖", out.get("updated") == "2026-11-02", f"→ {out.get('updated')!r}")

# ③ 手动清空不覆盖
fm = old_fm()
fm.pop("updated", None)
out = sync_updated(fm, "改过的正文\n", OLD, TODAY)
check("③ 手动清空 updated 不被覆盖", "updated" not in out, f"→ {out.get('updated')!r}")

# ④ 没有实质变化 ⇒ 不动
fm = old_fm()
out = sync_updated(fm, old_body(), OLD, TODAY)
check("④ 打开又保存不动 updated", out.get("updated") == "2026-10-25", f"→ {out.get('updated')!r}")

# ⑤ 不往回改
late = OLD.replace("updated: 2026-10-25", "updated: 2026-11-30")
fm = old_fm(late)
out = sync_updated(fm, "改过的正文\n", late, TODAY)
check("⑤ 原文日期更晚，不往回改", out.get("updated") == "2026-11-30", f"→ {out.get('updated')!r}")

# ⑥ 新建不写
fm = old_fm()
fm.pop("updated", None)
out = sync_updated(fm, "第一版正文\n", None, TODAY)
check("⑥ 新建文章不写 updated", "updated" not in out, f"→ {out.get('updated')!r}")

# ⑦ 只改字段
fm = old_fm()
fm["summary"] = "换掉的简介"
out = sync_updated(fm, old_body(), OLD, TODAY)
check("⑦ 只改字段也算更新", out.get("updated") == TODAY, f"→ {out.get('updated')!r}")

# ⑨ 客户端状态滞后（提交的还是上一次推进前的日期）⇒ 不能把日期顶回去
#    这是「27→25→27 来回跳」那个坑：编辑器的 working value 没跟上文件。
#    取一个"今天之后"的原文日期，才能把钳位的作用逼出来 —— 用今天做原文时
#    max(今天, 提交值) 恰好能兜住，判据会变成假阳性（变异验证当场发现的）。
adv = OLD.replace("updated: 2026-10-25", "updated: 2026-11-30")
fm = old_fm(adv)
fm["updated"] = "2026-10-25"
out = sync_updated(fm, "又改了一段\n", adv, TODAY)
check("⑨ 滞后客户端不会把日期顶回去", out.get("updated") == "2026-11-30",
      f"→ {out.get('updated')!r}（提交的是 2026-10-25）")

# ⑩ 原文没有 updated ⇒ 不主动添加（可选字段，加不加是站主的排版选择）
plain = OLD.replace("updated: 2026-10-25\n", "")
fm = old_fm(plain)
out = sync_updated(fm, "改过的正文\n", plain, TODAY)
check("⑩ 没有 updated 的文章不主动添加", "updated" not in out, f"→ {out.get('updated')!r}")

# ⑧ 端到端：write_post 真的调用了它
tmp = Path(tempfile.mkdtemp(prefix="writedesk-t16-"))
try:
    d = tmp / "content" / "posts" / "demo"
    d.mkdir(parents=True)
    (d / "index.md").write_bytes(OLD.encode("utf-8"))
    content = Content(tmp)
    saved = content.write_post("demo", old_fm(), "改过的正文\n", today=TODAY)
    reread = content.read_post("demo")
    check("⑧ write_post 端到端把 updated 落盘", reread["frontmatter"].get("updated") == TODAY,
          f"→ 返回 {saved.get('updated')!r}，文件里 {reread['frontmatter'].get('updated')!r}")
    check("⑧b 正文同时真的写进去了", reread["body"].strip() == "改过的正文", repr(reread["body"]))
    check("⑧c date 没有被改动（首发日语义）", reread["frontmatter"].get("date") == "2026-10-25",
          f"→ {reread['frontmatter'].get('date')!r}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n通过 {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
