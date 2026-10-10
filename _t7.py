# -*- coding: utf-8 -*-
"""写作台 · **标签注册表**（增 / 删）的契约测试（2026-10-09）。

站主这次的第 2 条：「加一个管理标签的功能（增加/删除）」。

⚠ 这件事的风险不在"点一下加个标签"，而在**改的是站上的源码**：
  `src/tag-registry.ts` 是标签的**唯一事实来源**，`src/lib/schema.ts` 拿它校验
  每篇文章的 `tags`。这个文件是**手写的 TypeScript**（有注释、有 `as const`），
  所以「写坏」的形态有很多种：吃掉注释、把行尾从 CRLF 换成 LF、
  整个文件被重排、写一半失败留下半成品……

本文件守的就是这些：
  ① 读：标签列表 / 每个标签被哪些文章用 / 文档镜像缺哪些
  ② 写：**只有那一行变**（逐行 diff 恰好 1 增 1 删，其余字节不动），行尾不变
  ③ 校验：不合格的名字一律拒（空 / 太短 / 太长 / 空格 / 逗号 / 引号 / 纯数字 / 布尔 / 重名）
     —— 而且**被拒时文件必须一个字节都没变**（失败不许留痕）
  ④ 红线：**正在被文章用的标签不许删**（删了那些文章构建失败），文件同样不变
  ⑤ 形状不认识（被改成多行 / 去掉 as const）⇒ 报错收手，绝不半改
  ⑥ 面板的口径与站上的规则一致：孤儿标签要标出来（npm run verify 会红）
  ⑦ 文档镜像：`docs/content-model.md` §4 那张表在**新增**时自动补一行
     （2026-10-09 站主授权），补的行只插在表尾、别的字节不动；
     表里已有同名行时**不重复加**；表找不到时降级成"如实报没补上"而不是报错回滚

★ 全程只动 `_fixture/`（假仓库），跑完按字节还原并比对哈希。
"""
from __future__ import annotations

import difflib
import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib.content import Content, ContentError  # noqa: E402

FIX = HERE / "_fixture"
SRC = FIX / "src" / "tag-registry.ts"
DOC = FIX / "docs" / "content-model.md"

checks: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))
    print(("  ✓ " if ok else "  ✗ ") + name + (f"　{detail}" if detail else ""))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p)
            for p in root.rglob("*") if p.is_file()}


def inner(text: str) -> str:
    """把 TAGS 数组里的内容原样取出来（测试自己也要能独立读到它）。"""
    m = re.search(r"export\s+const\s+TAGS\s*=\s*\[(.*?)\]\s*as\s+const", text, re.S)
    return m.group(1) if m else "<没找到>"


def changed_lines(a: str, b: str) -> list[str]:
    return [l for l in difflib.ndiff(a.splitlines(keepends=True),
                                     b.splitlines(keepends=True)) if l[0] in "+-"]


C = Content(FIX)
snapshot = tree(FIX)
SRC_BEFORE = SRC.read_bytes()
DOC_BEFORE = DOC.read_bytes()          # ★ 文档那侧也要能逐字节还原（add_tag 会写它）
failed: list[str] = []


def refuses(fn, label: str, why: str) -> None:
    """要求 fn() 抛 ContentError，**并且两个文件都一个字节都没变**。

    ★ 2026-10-09 起 `add_tag` 会**同时**写 `docs/content-model.md`（自动补行），
      所以"被拒时不许留痕"必须两个文件一起看 —— 只盯注册表会漏掉
      「标签没加进去、文档表却多了一行」这种半脏状态。
    """
    before = (sha(SRC), sha(DOC))
    try:
        fn()
    except ContentError as e:
        ok = (sha(SRC), sha(DOC)) == before
        chk(label, ok, f"{why}　→　{str(e)[:56]}…" if ok else "★ 被拒了，但文件被改动了！")
        if not ok:
            failed.append(label)
        return
    except Exception as e:  # noqa: BLE001
        chk(label, False, f"抛的不是 ContentError 而是 {type(e).__name__}: {e}")
        failed.append(label)
        return
    chk(label, False, "★ 居然被放行了")
    failed.append(label)


try:
    # ═══════════════════ 一、读 ═══════════════════
    print("\n一、读注册表")
    print("-" * 66)
    chk("① 读出夹具里登记的 4 个标签（注释与 as const 都不干扰）",
        C.read_tags() == ["AI", "随笔", "教程", "工具"], f"{C.read_tags()}")

    usage = C.tag_usage()
    chk("① 用法按 frontmatter 统计", usage == {"AI": ["hello-world"],
                                              "随笔": ["hello-world"]}, f"{usage}")

    ov = C.tags_overview()
    chk("① 孤儿标签（登记了没文章用）单独列出 —— 面板据此标红", ov["unused"] == ["教程", "工具"],
        f"{ov['unused']}")
    chk("① 文档镜像漂移：报告 registry 里、docs/content-model.md §4 表里没有的",
        ov["doc"]["missing"] == ["工具"], f"{ov['doc']}")
    chk("① 把长度约束一并给前端（面板要显示 2–5 字）",
        (ov["limit"]["min"], ov["limit"]["max"]) == (2, 5), f"{ov['limit']}")
    # ★ 夹具里**必须**另有一张 `| \`名字\` |` 形状的表（§1 字段表）。
    #   没有它，"整份文件乱抓标签名"那种错法在夹具上**毫无差别** —— 会漏。
    #   这条既守夹具、也守判据：extra 只该看 §4，不该被 §1 的字段名污染。
    chk("① 定位只认 §4 那张表（§1 字段表里的 title/date/summary 不算标签）",
        "| `title` |" in DOC.read_text(encoding="utf-8") and ov["doc"]["extra"] == [],
        f"夹具里有 §1 字段表 = {'| `title` |' in DOC.read_text(encoding='utf-8')}，"
        f"extra={ov['doc']['extra']}")

    # ═══════════════════ 二、加 ═══════════════════
    print("\n二、加一个标签")
    print("-" * 66)
    before_text = SRC.read_text(encoding="utf-8")
    before_doc = DOC.read_text(encoding="utf-8")
    ov2 = C.add_tag("读书")
    after_text = SRC.read_text(encoding="utf-8")
    after_doc = DOC.read_text(encoding="utf-8")
    chk("② 注册表里多了一个", ov2["items"] == ["AI", "随笔", "教程", "工具", "读书"],
        f"{ov2['items']}")
    chk("② 文件里真的是 TS 数组（单引号、逗号+空格，与站上写法一致）",
        inner(after_text) == "'AI', '随笔', '教程', '工具', '读书'",
        inner(after_text))
    chk("② ★ 与改动前逐行 diff 恰好 1 增 1 删（其余一行都没动）",
        len(changed_lines(before_text, after_text)) == 2,
        f"变了 {len(changed_lines(before_text, after_text))} 行")
    chk("② 注释、import、as const、类型导出全部原样",
        "as const;" in after_text and "export type Tag" in after_text
        and after_text.splitlines()[0] == before_text.splitlines()[0],
        after_text.splitlines()[0][:40])
    chk("② 新标签立刻被算作孤儿（面板会提醒「构建会红」）", "读书" in ov2["unused"],
        f"{ov2['unused']}")

    # ★ 2026-10-09 站主授权：「之后再加新的自动补」—— 加标签时顺手把 §4 的表格补一行
    chk("② ★ 顺手往 docs/content-model.md §4 的表尾补了一行",
        ov2["doc"].get("appended") is True, f"note={ov2['doc'].get('note')}")
    d_lines = changed_lines(before_doc, after_doc)
    chk("② ★ 文档那侧也只有 1 增 0 删（只插一行，别处一行没动）",
        len(d_lines) == 1 and d_lines[0].startswith("+ | `读书` |"), str(d_lines))
    chk("② 补进去的行里，「含义边界」是占位（那列只能人写，程序不编）",
        C.DOC_STUB in after_doc, C.DOC_STUB)
    chk("② 补完之后「还缺哪些」不再报它",
        "读书" not in ov2["doc"]["missing"], f"{ov2['doc']['missing']}")
    chk("② 文档里别的表（§1 字段表那种 ``| `title` |`` 形状）没被误当成标签",
        ov2["doc"]["extra"] == [], f"{ov2['doc']['extra']}")

    chk("② 只动了这两个文件：tag-registry.ts + docs/content-model.md",
        {k: v for k, v in tree(FIX).items()
         if k not in ("src/tag-registry.ts", "docs/content-model.md")}
        == {k: v for k, v in snapshot.items()
            if k not in ("src/tag-registry.ts", "docs/content-model.md")})

    # ═══════════════════ 三、校验（被拒时不许留痕）═══════════════════
    print("\n三、不合格的名字一律拒 —— 且文件不许被碰")
    print("-" * 66)
    for name, label in (("", "空字符串"),
                        ("   ", "只有空白"),
                        ("书", "1 个字（下限 2）"),
                        ("一二三四五六", "6 个字（上限 5）"),
                        ("读 书", "名字里有空格"),
                        ("读书,写作", "名字里有逗号（会拆坏 tags: [甲, 乙]）"),
                        ("读\"书", "名字里有双引号"),
                        ("读'书", "名字里有单引号"),
                        ("123", "纯数字（YAML 会读成数字）"),
                        ("true", "布尔字面量"),
                        ("随笔", "与已登记的完全重名")):
        refuses(lambda n=name: C.add_tag(n), f"③ 拒绝：{label}", f"「{name}」")

    # 大小写重名：夹具里有 AI
    refuses(lambda: C.add_tag("ai"), "③ 拒绝：只差大小写也算重名（AI / ai）", "「ai」")

    chk("③ 拒绝完之后注册表还是原来那 5 个",
        C.read_tags() == ["AI", "随笔", "教程", "工具", "读书"], f"{C.read_tags()}")

    # ═══════════════════ 四、删 ═══════════════════
    print("\n四、删一个标签")
    print("-" * 66)
    refuses(lambda: C.remove_tag("随笔"),
            "④ ★ 正在被文章用的标签删不掉（删了那篇文章构建失败）", "「随笔」")
    refuses(lambda: C.remove_tag("不存在"), "④ 删一个没登记过的 → 报错", "「不存在」")

    before_text = SRC.read_text(encoding="utf-8")
    ov3 = C.remove_tag("读书")
    after_text = SRC.read_text(encoding="utf-8")
    chk("④ 孤儿标签能删掉", "读书" not in ov3["items"], f"{ov3['items']}")
    chk("④ 删除同样只有 1 增 1 删（不重排文件）",
        len(changed_lines(before_text, after_text)) == 2,
        f"变了 {len(changed_lines(before_text, after_text))} 行")
    chk("④ 删干净了（数组里没有残留的 , 或空串）",
        inner(after_text) == "'AI', '随笔', '教程', '工具'", inner(after_text))
    # ★ 删标签**不**代改文档（删是站主要点头的方向）⇒ 表里会留下一行幽灵。
    #   那不是"漏"，是**反方向的漂移**；不报出来就成了安静的假话。
    chk("④ ★ 删标签不代改文档 ⇒ 表里留了一行「幽灵」，面板要报出来",
        ov3["doc"]["extra"] == ["读书"], f"{ov3['doc']}")

    # ═══════════════════ 五、形状不认识就收手 ═══════════════════
    print("\n五、文件形状变了 —— 收手，或者跟着它的写法走")
    print("-" * 66)
    multi = "export const TAGS = [\n  'AI',\n  '随笔',\n] as const;\n"
    SRC.write_text(multi, encoding="utf-8")
    C.add_tag("读书")
    got = SRC.read_text(encoding="utf-8")
    chk("⑤ 被折成多行时跟着折（不会把整个数组压成一行、diff 没法看）",
        got == "export const TAGS = [\n  'AI',\n  '随笔',\n  '读书',\n] as const;\n", repr(got))
    SRC.write_text("export const TAGS = [\n    'AI',\n    '随笔'\n] as const;\n", encoding="utf-8")
    C.add_tag("读书")
    got = SRC.read_text(encoding="utf-8")
    chk("⑤ 多行 + 4 空格缩进 + 没有尾逗号 → 都沿用",
        got == "export const TAGS = [\n    'AI',\n    '随笔',\n    '读书'\n] as const;\n", repr(got))
    SRC.write_text("// 注释还在，但数组没了\nexport const OTHER = 1;\n", encoding="utf-8")
    refuses(lambda: C.add_tag("读书"), "⑤ 找不到 TAGS 这个导出 → 报错（不猜、不改）", "没有数组")
    SRC.write_bytes(SRC_BEFORE)

    # ★ 文档那侧的"形状不认识了"该怎么办：**不抛错、注册表照加，但如实说"没补上"**。
    #   为什么和注册表那边不一样（那边是直接报错收手）：注册表是**唯一事实来源**，
    #   认不出形状就绝不能再动它；而文档只是镜像，标签已经写进注册表了，
    #   这时候把整件事回滚反而更坏（人会以为标签没加上）。所以这里降级成"报警"。
    DOC.write_text("# 换了形状\n\n## 4. 标签体系\n\n（表被删了）\n", encoding="utf-8")
    ov5 = C.add_tag("读书")
    chk("⑤ 文档 §4 的表找不到 ⇒ 注册表照加，但如实报「没补上」",
        ov5["doc"]["appended"] is False and "读书" in ov5["doc"]["missing"]
        and ov5["items"][-1] == "读书", f"{ov5['doc']}")
    chk("⑤ 失败原因说得清（不是一句干巴巴的失败）",
        "形状" in (ov5["doc"]["note"] or ""), ov5["doc"]["note"])
    DOC.write_bytes(DOC_BEFORE)
    C.remove_tag("读书")

    # ═══════════════════ 六、行尾 ═══════════════════
    print("\n六、行尾沿用原文件")
    print("-" * 66)
    crlf = SRC_BEFORE.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    SRC.write_bytes(crlf)
    C.add_tag("读书")
    raw = SRC.read_bytes()
    chk("⑥ CRLF 的文件写回去还是 CRLF（不会把整文件的 diff 搅乱）",
        raw.count(b"\r\n") == raw.count(b"\n") and raw.count(b"\n") > 0,
        f"CRLF {raw.count(b'\r\n')} / LF {raw.count(b'\n')}")
    SRC.write_bytes(SRC_BEFORE)

    # ═══════════════════ 八、文档镜像：新增时自动补行 ═══════════════════
    print("\n八、docs/content-model.md §4：加标签时自动补一行")
    print("-" * 66)
    DOC.write_bytes(DOC_BEFORE)
    doc0 = DOC.read_text(encoding="utf-8")
    ovA = C.add_tag("备忘")
    doc1 = DOC.read_text(encoding="utf-8")
    chk("⑧ 新标签补在 §4 表尾（且只有这一行是新的）",
        ovA["doc"]["appended"] is True and "| `备忘` |" in doc1
        and len(changed_lines(doc0, doc1)) == 1, str(changed_lines(doc0, doc1)))
    chk("⑧ 表格结构没被碰（表头、分隔行都还在，位置也没挪）",
        "| 标签 | 含义边界（什么算、什么不算） | 现有文章 |" in doc1
        and "|---|---|---|" in doc1
        and doc1.index("| 标签 |") == doc0.index("| 标签 |"), "表头/分隔行齐全")
    d_lines = doc1.split("\n")
    ri = [n for n, l in enumerate(d_lines) if l.startswith("| `备忘` |")]
    chk("⑧ ★ 补的那一行在**表尾**（它下面紧跟的就是表外内容，不是别的表格行）",
        len(ri) == 1 and not d_lines[ri[0] + 1].startswith("|"),
        f"下一行 = {d_lines[ri[0] + 1]!r}" if ri else "没找到那行")

    # ★ 关键那条：表里**已经有**同名行时不能再加一遍。
    #   要走到这个分支，得让"注册表里没有、文档里有"—— 也就是先加、再删、再加。
    C.remove_tag("备忘")                     # 删标签不代改文档 ⇒ 表里那行还在
    b4 = DOC.read_bytes()
    ovB = C.add_tag("备忘")
    chk("⑧ ★ 表里已有同名行时不再重复加（文档零字节变化，并说清原因）",
        ovB["doc"]["appended"] is False and DOC.read_bytes() == b4
        and "已经有" in (ovB["doc"]["note"] or ""), ovB["doc"]["note"])
    chk("⑧ 文档里 `备忘` 只有一行（没有叠成两行）",
        DOC.read_text(encoding="utf-8").count("| `备忘` |") == 1,
        f"{DOC.read_text(encoding='utf-8').count('| `备忘` |')} 行")
    C.remove_tag("备忘")
    DOC.write_bytes(DOC_BEFORE)

    # ═══════════════════ 七、用法跟着 frontmatter 走 ═══════════════════
    print("\n七、用法跟着文章 frontmatter 走（面板的红标会自己消失）")
    print("-" * 66)
    C.add_tag("读书")                      # 先登记，再拿去用
    post = FIX / "content" / "posts" / "hello-world" / "index.md"
    post_before = post.read_bytes()
    try:
        old_line = "tags: [AI, 随笔]".encode("utf-8")
        chk("⑦ 夹具文章的 tags 行找得到（不然下面这条断言是空跑）",
            post_before.count(old_line) == 1, f"{post_before.count(old_line)} 处")
        post.write_bytes(post_before.replace(old_line, "tags: [AI, 随笔, 读书]".encode("utf-8")))
        ov4 = C.tags_overview()
        chk("⑦ 把它写进一篇文章后就不再是孤儿（面板红标消失）",
            "读书" not in ov4["unused"] and ov4["usage"].get("读书") == ["hello-world"],
            f"usage={ov4['usage'].get('读书')}　unused={ov4['unused']}")
    finally:
        post.write_bytes(post_before)
    C.remove_tag("读书")
    chk("⑦ 文章的 tags 又变回 1–3 个（夹具没被弄脏）",
        C.read_tags() == ["AI", "随笔", "教程", "工具"], f"{C.read_tags()}")

finally:
    # ── 还原夹具：逐字节 ──
    SRC.write_bytes(SRC_BEFORE)
    DOC.write_bytes(DOC_BEFORE)
    same = tree(FIX) == snapshot
    print()
    print("-" * 66)
    print("夹具逐字节还原：", "是" if same else "★ 否 —— 有文件没还原回去")
    if not same:
        now = tree(FIX)
        for k in sorted(set(now) | set(snapshot)):
            if now.get(k) != snapshot.get(k):
                print(f"    ★ {k}")

bad = [n for n, ok, _ in checks if not ok]
print()
print(f"合计 {len(checks)} 项，通过 {len(checks) - len(bad)}，失败 {len(bad)}")
for n in bad:
    print(f"  失败：{n}")
sys.exit(1 if (bad or not same) else 0)
