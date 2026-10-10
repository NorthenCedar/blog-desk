# -*- coding: utf-8 -*-
"""写作台 · 内容层读写（只动 content/ 与 src/assets/talks/）。

════════════════════════════════════════════════════════════════════════
三条铁律（每条都是踩过的坑，改这个文件前先读完）
════════════════════════════════════════════════════════════════════════
① **不许整篇 YAML 往返**。
   `content/pages/talks.md` 与 `log.md` 的 frontmatter 里有一大段 `#` 说明注释
   （发说说的写法、日志的体例、站主的历次澄清）。用 yaml.safe_load → safe_dump
   走一遍，**注释会被静默吃掉**，而 git diff 看起来只是"少了几行"——
   等发现时已经推上线了。
   ⇒ 本模块只做「定点改写」：保留 `items:` 之前的**全部原文**（含注释），
     只重排 items 段。文章（posts）的 frontmatter 没有注释，那才允许整段重写。

② **标量必须能安全落进 YAML**。
   log 的 changes 是整句中文；一旦出现裸「冒号 + 空格」，YAML 会把它读成映射，
   页面上就印出 [object Object]（站上真出过这个事故）。
   ⇒ 只在必要时加双引号（yaml_scalar），不做无脑全引号（那会把 diff 搅乱）。

③ **行尾沿用原文件**。
   仓库 core.autocrlf=true，磁盘上可能是 CRLF 也可能是 LF。
   写回时逐字节沿用原文件的行尾；新建文件用 LF（git 会在提交时归一化）。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# ────────────────────────────── 标量序列化 ──────────────────────────────

# YAML 里「不能当裸标量用」的开头字符
_BAD_HEAD = set("-?:,[]{}#&*!|>'\"%@` \t")
# 会被 YAML 读成非字符串的字面量（文本字段里必须加引号，否则类型变了）
_LOOKS_NON_STR = re.compile(
    r"^(?:"
    r"~|null|Null|NULL|true|True|TRUE|false|False|FALSE|"
    r"yes|Yes|YES|no|No|NO|on|On|ON|off|Off|OFF|"
    r"[-+]?\d+|[-+]?\d*\.\d+(?:[eE][-+]?\d+)?|"
    r"0[xX][0-9a-fA-F]+"
    r")$"
)


def yaml_scalar(v) -> str:
    """把一个 Python 值渲染成**安全**的 YAML 标量（必要时才加引号）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = "" if v is None else str(v)

    if s == "":
        return "''"
    needs_quote = (
        s[0] in _BAD_HEAD
        or s[-1] in " \t"
        or ": " in s
        or s.endswith(":")
        or " #" in s
        or "\n" in s
        or "\r" in s
        or "\t" in s
        or _LOOKS_NON_STR.match(s) is not None
    )
    if not needs_quote:
        return s
    # 双引号 + 转义（YAML 双引号里只有 \ 和 " 需要转义）
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _detect_newline(raw: bytes) -> str:
    """原文件以哪种行尾为主。新建文件用 LF。"""
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


class ContentError(Exception):
    """内容层错误：消息直接给用户看，所以一律写中文、说清楚怎么办。"""


# ────────────────────────────── frontmatter ──────────────────────────────

_FM_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n)?(.*)$", re.S)


def split_frontmatter(text: str) -> tuple[str, str]:
    """切成 (frontmatter 原文, 正文)。没有 frontmatter 就抛错。"""
    m = _FM_RE.match(text)
    if not m:
        raise ContentError("这个文件没有 YAML frontmatter（开头缺少 --- 分隔块）")
    return m.group(1), m.group(2)


def _norm_dates(obj):
    """把 YAML 隐式解析出来的日期对象归一成字符串。

    ★ 为什么必须有这一步：YAML 会把未加引号的 `date: 2026-10-09` 读成
      `datetime.date` —— 那玩意儿 **json.dumps 直接抛 TypeError**，
      而且跟字符串比也不相等（`d == "2026-10-09"` 是 False）。
      站上的 `src/lib/schema.ts` 里有同一个归一化（同一件事在两个语言里各做一次），
      口径一致：一律落成 `YYYY-MM-DD`。
    """
    import datetime as _dt
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()[:10]
    if isinstance(obj, dict):
        return {k: _norm_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm_dates(v) for v in obj]
    return obj


def parse_frontmatter(fm_text: str) -> dict:
    data = yaml.safe_load(fm_text)
    if not isinstance(data, dict):
        raise ContentError("frontmatter 不是一个字典（键值对）")
    return _norm_dates(data)


def _fm_head_upto(text: str, key: str) -> tuple[str, str]:
    """把 frontmatter 切成 (key 之前的所有原文, 从 key 那一行开始的原文)。

    ★ 这是「保住注释」的关键：`key` 之前的内容（含 `#` 注释）原样带走。
      找不到 key 时，整个 frontmatter 都算 head，tail 为空。
    """
    fm, body = split_frontmatter(text)
    lines = fm.split("\n")
    pat = re.compile(r"^" + re.escape(key) + r"\s*:")
    for i, line in enumerate(lines):
        # 只认顶格的键（注释行以 # 开头，缩进的键属于上一键的子项）
        if pat.match(line):
            return "\n".join(lines[:i]), "\n".join(lines[i:]), 
    return fm, ""


def replace_frontmatter_key(text: str, key: str, value) -> str:
    """把 frontmatter 里 `key:` 的值换掉（其余原文一字不动）。"""
    fm, body = split_frontmatter(text)
    lines = fm.split("\n")
    pat = re.compile(r"^(" + re.escape(key) + r"\s*:)(.*)$")
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key}: {yaml_scalar(value)}"
            new_fm = "\n".join(lines)
            return f"---\n{new_fm}\n---\n{body}"
    # 没有这个键 ⇒ 追加到末尾
    new_fm = fm.rstrip("\n") + f"\n{key}: {yaml_scalar(value)}"
    return f"---\n{new_fm}\n---\n{body}"


# ────────────────────────────── 文章 posts ──────────────────────────────

# frontmatter 字段的**规范顺序**（content-model.md §1 的 9 个字段）。
# 写回时按这个顺序排，diff 才稳定。
POST_FIELD_ORDER = [
    "title", "date", "updated", "summary", "tags", "draft", "cover", "coverAlt", "slug",
]


def render_post_frontmatter(fm: dict) -> str:
    """按规范顺序渲染文章 frontmatter。tags 用行内数组（与站上现有一致）。"""
    out = []
    for k in POST_FIELD_ORDER:
        if k not in fm or fm[k] is None:
            continue
        v = fm[k]
        if k == "tags":
            items = ", ".join(yaml_scalar(t) for t in v)
            out.append(f"tags: [{items}]")
        elif k == "draft":
            out.append(f"draft: {'true' if v else 'false'}")
        else:
            out.append(f"{k}: {yaml_scalar(v)}")
    # 未登记字段原样保留（内容模型规则 6：只警告不失败）—— 这里不主动丢
    for k, v in fm.items():
        if k not in POST_FIELD_ORDER and v is not None:
            out.append(f"{k}: {yaml_scalar(v)}")
    return "\n".join(out)


class Content:
    """内容层门面。所有路径都相对 blog 仓库根。"""

    def __init__(self, repo: Path):
        self.repo = Path(repo)
        self.posts_dir = self.repo / "content" / "posts"
        self.pages_dir = self.repo / "content" / "pages"
        self.gallery_dir = self.repo / "content" / "gallery"
        self.talks_assets = self.repo / "src" / "assets" / "talks"

    # ---------- 工具 ----------

    def _read(self, path: Path) -> tuple[str, str]:
        """读文本，返回 (内容, 原行尾)。"""
        raw = path.read_bytes()
        return raw.decode("utf-8"), _detect_newline(raw)

    def _write(self, path: Path, text: str, newline: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.replace("\r\n", "\n").replace("\n", newline).encode("utf-8"))

    def slugify(self, title: str) -> str:
        """中文标题没法自动转 slug ⇒ 用日期+序号兜底，交给用户改。"""
        s = title.strip().lower()
        s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return s

    # ---------- 文章 ----------

    def list_posts(self) -> list[dict]:
        out = []
        if not self.posts_dir.is_dir():
            return out
        for d in sorted(self.posts_dir.iterdir()):
            md = d / "index.md"
            if not (d.is_dir() and md.is_file()):
                continue
            try:
                text, _ = self._read(md)
                fm = parse_frontmatter(split_frontmatter(text)[0])
            except Exception as e:  # noqa: BLE001
                out.append({"slug": d.name, "title": f"（读不出：{e}）", "broken": True})
                continue
            out.append({
                "slug": fm.get("slug") or d.name,
                "dir": d.name,
                "title": fm.get("title", ""),
                "date": str(fm.get("date", "")),
                "updated": str(fm.get("updated", "") or ""),
                "summary": fm.get("summary", ""),
                "tags": fm.get("tags", []) or [],
                "draft": bool(fm.get("draft", False)),
                "cover": fm.get("cover", "") or "",
                "coverAlt": fm.get("coverAlt", "") or "",
                "images": sorted(p.name for p in d.iterdir()
                                 if p.is_file() and p.suffix.lower() in
                                 (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")),
                "words": len(split_frontmatter(text)[1]),
            })
        out.sort(key=lambda x: (x.get("date") or ""), reverse=True)
        return out

    def read_post(self, slug: str) -> dict:
        d = self.posts_dir / slug
        md = d / "index.md"
        if not md.is_file():
            raise ContentError(f"找不到文章：content/posts/{slug}/index.md")
        text, nl = self._read(md)
        fm_text, body = split_frontmatter(text)
        return {
            "slug": slug,
            "frontmatter": parse_frontmatter(fm_text),
            "body": body,
            "newline": nl,
            "dir": str(d),
        }

    def write_post(self, slug: str, fm: dict, body: str) -> dict:
        d = self.posts_dir / slug
        md = d / "index.md"
        nl = "\n"
        if md.is_file():
            _, nl = self._read(md)
        text = f"---\n{render_post_frontmatter(fm)}\n---\n{body}"
        self._write(md, text, nl)
        return {"ok": True, "path": str(md)}

    def create_post(self, slug: str, fm: dict) -> dict:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ContentError(
                "slug 只能用小写字母、数字和单个连字符（例：my-new-post）。"
                "中文标题请另填一个英文 slug。"
            )
        d = self.posts_dir / slug
        if d.exists():
            raise ContentError(f"已经有一篇叫 {slug} 的文章了")
        d.mkdir(parents=True)
        self.write_post(slug, fm, "\n")
        return {"ok": True, "slug": slug}

    def delete_post(self, slug: str) -> dict:
        d = self.posts_dir / slug
        if not d.is_dir():
            raise ContentError(f"找不到文章目录：content/posts/{slug}")
        # 只删这个目录（文章图与正文同目录 ⇒ 一并走）
        removed = [p.name for p in d.rglob("*") if p.is_file()]
        for p in sorted(d.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            else:
                p.rmdir()
        d.rmdir()
        return {"ok": True, "removed": removed}

    # ---------- 说说 talks ----------

    def read_talks(self) -> dict:
        md = self.pages_dir / "talks.md"
        text, nl = self._read(md)
        fm_text, body = split_frontmatter(text)
        fm = parse_frontmatter(fm_text)
        head, _tail = _fm_head_upto(text, "items")
        return {
            "items": fm.get("items", []) or [],
            "title": fm.get("title", "说说"),
            "intro": fm.get("intro", ""),
            "head": head,      # items 之前的原文（含注释）—— 写回时原样用
            "body": body,      # ★ 收尾 `---` 之后的正文：写回时必须原样带回去，别丢
            "newline": nl,
        }

    def write_talks(self, items: list[dict]) -> dict:
        md = self.pages_dir / "talks.md"
        text, nl = self._read(md)
        head, _ = _fm_head_upto(text, "items")
        _, body = split_frontmatter(text)
        # 日期倒序（页面自己也会排，这里只是让文件本身好读）
        items = sorted(items, key=lambda x: str(x.get("date", "")), reverse=True)
        lines = ["items:"]
        for it in items:
            lines.append(f"  - date: {yaml_scalar(it.get('date', ''))}")
            lines.append(f"    text: {yaml_scalar(it.get('text', ''))}")
            if it.get("href"):
                lines.append(f"    href: {yaml_scalar(it['href'])}")
            imgs = it.get("images") or []
            if imgs:
                lines.append("    images:")
                for im in imgs:
                    lines.append(f"      - src: {yaml_scalar(im.get('src', ''))}")
                    lines.append(f"        alt: {yaml_scalar(im.get('alt', ''))}")
        new_text = f"---\n{head}\n" + "\n".join(lines) + "\n---\n" + body
        self._write(md, new_text, nl)
        return {"ok": True, "count": len(items)}

    # ---------- 日志 log ----------

    def read_log(self) -> dict:
        md = self.pages_dir / "log.md"
        text, nl = self._read(md)
        fm_text, body = split_frontmatter(text)
        fm = parse_frontmatter(fm_text)
        head, _ = _fm_head_upto(text, "items")
        return {
            "items": fm.get("items", []) or [],
            "title": fm.get("title", "日志"),
            "intro": fm.get("intro", ""),
            "head": head,
            "body": body,      # ★ 同 talks：收尾 `---` 之后的正文要原样带回去
            "newline": nl,
        }

    def write_log(self, items: list[dict]) -> dict:
        md = self.pages_dir / "log.md"
        text, nl = self._read(md)
        head, _ = _fm_head_upto(text, "items")
        _, body = split_frontmatter(text)
        items = sorted(items, key=lambda x: str(x.get("date", "")), reverse=True)
        lines = ["items:"]
        for it in items:
            lines.append(f"  - date: {yaml_scalar(it.get('date', ''))}")
            lines.append(f"    version: {yaml_scalar(it.get('version', ''))}")
            ch = it.get("changes") or []
            if ch:
                lines.append("    changes:")
                for c in ch:
                    lines.append(f"      - {yaml_scalar(c)}")
        new_text = f"---\n{head}\n" + "\n".join(lines) + "\n---\n" + body
        self._write(md, new_text, nl)
        return {"ok": True, "count": len(items)}

    def next_version(self) -> str:
        """按现有日志推下一个版本号（v2.10 → v2.11）。"""
        cur = self.read_log()["items"]
        best = (0, 0)
        for it in cur:
            m = re.fullmatch(r"v(\d+)\.(\d+)", str(it.get("version", "")).strip())
            if m:
                p = (int(m.group(1)), int(m.group(2)))
                if p > best:
                    best = p
        return f"v{best[0]}.{best[1] + 1}"

    # ---------- 相册 ----------

    def list_albums(self) -> list[dict]:
        out = []
        if not self.gallery_dir.is_dir():
            return out
        for d in sorted(self.gallery_dir.iterdir()):
            md = d / "index.md"
            if not (d.is_dir() and md.is_file()):
                continue
            text, _ = self._read(md)
            fm = parse_frontmatter(split_frontmatter(text)[0])
            imgs = fm.get("images", []) or []
            out.append({
                "name": d.name,
                "title": fm.get("title", d.name),
                "date": str(fm.get("date", "")),
                "count": len(imgs),
                "images": [{"src": i.get("src", ""), "alt": i.get("alt", "")} for i in imgs],
            })
        return out

    # ---------- 标签 ----------
    #
    # ★ 为什么这一小段写得这么小心：`src/tag-registry.ts` 是站上标签的**唯一事实来源**
    #   （`src/lib/schema.ts` 拿它校验 frontmatter、`src/lib/posts.ts` 拿它过滤标签页），
    #   而它本身是一份**手写的 TypeScript**（带注释、带 `as const`）。所以：
    #     · 只做「定点改一处字面量」：仅替换 `[...]` 里的内容，前后**一个字节都不碰**；
    #     · 行尾沿用原文件（仓库 core.autocrlf=true，磁盘上可能是 CRLF）；
    #     · 形状对不上（将来被改成多行之类）就**报错收手**，绝不半改半留。
    #   还有两条站的规矩在这里守住（都是构建/测试会直接拒的）：
    #     ① frontmatter 用的是行内数组 `tags: [a, b]` + `z.string()` ⇒
    #        标签名不能含引号/逗号/方括号，也不能是纯数字或布尔（YAML 会读成数字）；
    #     ② `tests/content-invariants.test.ts` 要求「每个登记标签至少有一篇文章用它」
    #        ⇒ 删掉一个**正被使用**的标签会让那些文章构建失败，必须拒绝并说清楚原因。
    #     ③ `docs/content-model.md` §4 那张表是注册表的**人类可读镜像**。
    #        2026-10-09 站主授权：**新增**标签时由这里自动往表尾补一行
    #        （见 `_doc_append_row`；「含义边界」那列只能人写，先落占位）。
    #        删除仍不代改 —— 删标签本身就被 ② 挡着，要走人工。

    TAGS_FILE = "src/tag-registry.ts"
    DOC_FILE = "docs/content-model.md"
    TAG_MIN, TAG_MAX = 2, 5
    _TAGS_RE = re.compile(r"(export\s+const\s+TAGS\s*=\s*\[)(.*?)(\]\s*as\s+const\s*;?)", re.S)
    # 会拆坏 TS 字面量或 `tags: [a, b]` 的字符
    _TAG_BAD = set("'\",[]{}#&*!|>%@`\\")
    # §4 那张表的表头行：「| 标签 | 含义边界（…） | 现有文章 |」
    _DOC_HEAD = re.compile(r"^\|\s*标签\s*\|", re.M)
    # 自动补行时「含义边界」先落的占位 —— 那一列只能人写，但留空比缺席好
    DOC_STUB = "（待补：什么算、什么不算）"

    def tags_path(self) -> Path:
        return self.repo / self.TAGS_FILE

    def doc_path(self) -> Path:
        return self.repo / self.DOC_FILE

    def read_tags(self) -> list[str]:
        """从 src/tag-registry.ts 读登记过的标签（唯一事实来源）。"""
        p = self.tags_path()
        if not p.is_file():
            return []
        text, _ = self._read(p)
        m = self._TAGS_RE.search(text)
        if not m:
            return []
        return re.findall(r"['\"]([^'\"]+)['\"]", m.group(2))

    def tag_usage(self) -> dict[str, list[str]]:
        """每个标签被哪些文章用了（按 slug）。只看 frontmatter，不碰正文。"""
        use: dict[str, list[str]] = {}
        if not self.posts_dir.is_dir():
            return use
        for d in sorted(self.posts_dir.iterdir()):
            md = d / "index.md"
            if not (d.is_dir() and md.is_file()):
                continue
            text, _ = self._read(md)
            fm = parse_frontmatter(split_frontmatter(text)[0])
            for t in (fm.get("tags") or []):
                use.setdefault(str(t), []).append(d.name)
        return use

    def _doc_rows(self, text: str) -> tuple[list[str], int, int] | None:
        """圈出 §4 那张表的**数据行**：返回 (按 `\\n` 切开的行, 首行下标, 末行下标+1)。

        形状对不上返回 None（调用方负责把原因说清楚）。

        ★ 为什么必须"先认表头、再顺着往下走"，而不是在整份文件里抓 `^| \\`名\\` |`：
          这份文档里**还有别的表**（§1 的字段表就是 `| \\`title\\` | ✅ | …` 那个形状），
          满文件乱抓会把 `title`/`date`/`summary` 这些**字段名**当成标签。
          旧的 `tag_doc_missing` 恰好是"拿注册表去减这个集合"，假阳性被减没了才没出错；
          可新加的 `tag_doc_extra`（表里有、注册表里没有）方向正好相反，
          那个假阳性会**当场全部露出来**。所以定位老老实实按结构来。
        """
        flat = text.replace("\r\n", "\n")
        m = self._DOC_HEAD.search(flat)
        if not m:
            return None
        lines = flat.split("\n")
        head_i = flat.count("\n", 0, m.start())
        i = head_i + 1
        if i >= len(lines) or not lines[i].startswith("|"):
            return None                       # 表头下面不是分隔行
        i += 1
        last = i
        while last < len(lines) and lines[last].startswith("|"):
            last += 1
        if last == i:
            return None                       # 一行数据都没有
        return lines, i, last

    @staticmethod
    def _doc_names(lines: list[str], i: int, last: int) -> list[str]:
        """从数据行里取第一列的标签名。"""
        return re.findall(r"^\|\s*`([^`]+)`\s*\|", "\n".join(lines[i:last]), re.M)

    def tag_doc_missing(self) -> list[str]:
        """登记了、但 `docs/content-model.md` §4 表格里没有的标签（文档镜像漂移）。

        ★ 2026-10-09 起 `add_tag` 会**自动补行**（§4 那张表是注册表的人类可读镜像，
          站主明确授权："之后再加新的自动补"），所以正常情况下这里**恒为空**。
          它现在的用途退化成**报警**：非空 ⇒ 补行失败（文档被挪走 / §4 表格换了形状）
          或历史遗留。面板照旧把它显示出来，让人看一眼。
        """
        doc = self.doc_path()
        if not doc.is_file():
            return []
        text, _ = self._read(doc)
        got = self._doc_rows(text)
        listed = set(self._doc_names(*got)) if got else set()
        return [t for t in self.read_tags() if t not in listed]

    def tag_doc_extra(self) -> list[str]:
        """反过来：§4 表里列了、但注册表里**没有**的标签（删过标签留下的死行）。

        ★ 为什么必须一起来：`add_tag` 自动补行，而 `remove_tag` **不代删**
          （删是站主要点头的方向，见 §6）。于是"加一个再删掉"就会在表里留下一行
          没人管的幽灵标签 —— 那不是"漏"，是**反方向的漂移**，一样不该看不见。
          面板把它显示出来，让人顺手划掉那一行。
        """
        doc = self.doc_path()
        if not doc.is_file():
            return []
        text, _ = self._read(doc)
        got = self._doc_rows(text)
        if not got:
            return []
        tags = set(self.read_tags())
        return [t for t in self._doc_names(*got) if t not in tags]

    def _doc_append_row(self, name: str) -> tuple[bool, str]:
        """往 `docs/content-model.md` §4 的表格**末尾追加一行**，返回 (改了没, 说明)。

        纪律与 `_write_tags` 一样：**只往表尾插一行**，别处一个字节不动，
        行尾沿用原文件。找不到表就**收手**（返回 False），交给面板把漂移报出来 ——
        宁可"没补上、看得见"，也不要"猜着改、改坏了看不见"。
        每种失败都有自己的说法，错误信息要能直接拿去修。
        """
        p = self.doc_path()
        if not p.is_file():
            return False, f"找不到 {self.DOC_FILE}（仓库路径对吗？）"
        text, nl = self._read(p)
        got = self._doc_rows(text)
        if not got:
            return False, f"{self.DOC_FILE} §4 的表头/分隔行/数据行没对上，形状变了"

        lines, _, last = got
        if name in self._doc_names(*got):
            return False, f"表里已经有「{name}」了，没重复加"

        lines.insert(last, f"| `{name}` | {self.DOC_STUB} | — |")
        self._write(p, "\n".join(lines), nl)
        # ★ 「含义边界」那列只能人写 ⇒ 只补行、不编内容（编出来就是假的事实）
        return True, "已在 §4 表尾补一行（「含义边界」那列留了占位，只能你写）"

    def _check_tag(self, name: str, tags: list[str]) -> str:
        """校验一个新标签名，返回规范化后的名字；不合格就抛 ContentError（中文、说清怎么办）。"""
        n = (name or "").strip()
        if not n:
            raise ContentError("标签不能为空")
        if not (self.TAG_MIN <= len(n) <= self.TAG_MAX):
            raise ContentError(
                f"标签要 {self.TAG_MIN}–{self.TAG_MAX} 个字（docs/content-model.md §4："
                f"短中文词、不加层级）——「{n}」是 {len(n)} 个字")
        if any(c.isspace() for c in n):
            raise ContentError(f"标签里不能有空格：「{n}」（站上写的是 tags: [甲, 乙] 行内数组）")
        bad = sorted({c for c in n if c in self._TAG_BAD})
        if bad:
            raise ContentError(
                f"标签里不能出现 {''.join(bad)} —— 会把 TypeScript 的字面量或 "
                f"frontmatter 的 tags: [甲, 乙] 拆坏")
        if _LOOKS_NON_STR.match(n):
            raise ContentError(
                f"「{n}」会被 YAML 读成数字或布尔而不是字符串 ⇒ 站点校验（z.string()）直接拒")
        low = n.lower()
        for t in tags:
            if t.lower() == low:
                raise ContentError(f"已经有一个「{t}」了（只差大小写也算重名）")
        return n

    def _write_tags(self, tags: list[str]) -> None:
        p = self.tags_path()
        text, nl = self._read(p)
        m = self._TAGS_RE.search(text)
        if not m:
            raise ContentError(
                f"{self.TAGS_FILE} 的形状变了（找不到 export const TAGS = [...] as const）"
                f"—— 没有动它。请手工改，改完再回来用这个面板。")
        inner = self._render_tags(m.group(2), tags)
        self._write(p, text[:m.start(2)] + inner + text[m.end(2):], nl)

    @staticmethod
    def _render_tags(old_inner: str, tags: list[str]) -> str:
        """渲染数组内容，**沿用原来那个写法**。

        站上现在是单行 `['AI', '建站', ...]`；但如果哪天被格式化工具折成多行，
        这里要跟着折（否则一次「加个标签」会把整个数组压成一行，diff 没法看）。
        多行时缩进与末尾逗号都沿用原文件。
        """
        if "\n" not in old_inner:
            return ", ".join(f"'{t}'" for t in tags)
        ind = "  "
        m = re.search(r"\n([ \t]+)\S", old_inner)
        if m:
            ind = m.group(1)
        tail = "," if old_inner.rstrip().endswith(",") else ""
        return "".join(f"\n{ind}'{t}'," for t in tags)[:-1] + tail + "\n"

    def tags_overview(self) -> dict:
        """面板要的全部信息：登记了哪些、各被谁用、文档镜像缺哪些。"""
        tags = self.read_tags()
        usage = self.tag_usage()
        return {
            "items": tags,
            "usage": {t: usage.get(t, []) for t in tags},
            "unused": [t for t in tags if not usage.get(t)],
            "doc": {"path": self.DOC_FILE,
                    "missing": self.tag_doc_missing(),
                    "extra": self.tag_doc_extra(),
                    "auto": True, "stub": self.DOC_STUB},
            "limit": {"min": self.TAG_MIN, "max": self.TAG_MAX},
        }

    def add_tag(self, name: str) -> dict:
        """加一个标签：写 `src/tag-registry.ts`，**并顺手把文档镜像补上**。

        ★ 两处都动是 2026-10-09 站主明确授权的（"之后再加新的自动补"）。
          在那之前这里是只报告、不代改 —— 因为 `docs/content-model.md` §6 写着
          「增删标签必须由站主明确同意后修改本文件」。现在同意给到了**新增**这一步，
          删除仍然不代改（删标签本来就被"在用不许删"挡着，要走人工）。
        """
        if not self.tags_path().is_file():
            raise ContentError(f"找不到 {self.TAGS_FILE}（仓库路径对吗？）")
        n = self._check_tag(name, self.read_tags())
        self._write_tags(self.read_tags() + [n])
        ok, why = self._doc_append_row(n)
        out = self.tags_overview()
        out["doc"]["appended"] = ok       # 面板据此决定提示是"已补"还是"要你自己补"
        out["doc"]["note"] = why
        return out

    def remove_tag(self, name: str) -> dict:
        tags = self.read_tags()
        if name not in tags:
            raise ContentError(f"注册表里没有「{name}」")
        used = self.tag_usage().get(name) or []
        if used:
            head = "、".join(used[:5]) + ("…" if len(used) > 5 else "")
            raise ContentError(
                f"「{name}」还有 {len(used)} 篇文章在用（{head}）。"
                f"从注册表里删掉它，那些文章会**构建失败**"
                f"（src/lib/schema.ts 会报「未登记的标签：{name}」）。"
                f"要删就先把那些文章的 tags 改掉。")
        self._write_tags([t for t in tags if t != name])
        return self.tags_overview()

