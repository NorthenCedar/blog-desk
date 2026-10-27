# -*- coding: utf-8 -*-
"""写作台 · `yaml_scalar` 能不能**安全落盘**（2026-10-27）

起因：一条两行的「说说」把整站构建打挂了。

    text: "不是哥们。。。。建议都去试试<这里是真换行>
    怎么微信支付里面接了个AI啊"

双引号跨了行、续行顶到第 0 列 ⇒ YAML 报 `Missing closing "quote"` ⇒
`content/pages/talks.md` 整篇解析失败 ⇒ 博客那边 `tests/writer-content.test.ts`
（它读**真实的** talks.md）红掉 ⇒ `npm run verify` 挂 ⇒ 17:44 那次发布失败。

而写作台的 `yaml_scalar` 当时写着「双引号里只有 \\ 和 " 需要转义」—— 这句话是错的：
**加引号 ≠ 安全**。引号内的控制字符不转义，引号就会被换行截断。

守七件事（每条都对应一次真事故或一条既有约定）：
  ① 多行文本要写成**转义换行**，YAML 能原样读回来 —— 事故本体
  ② CRLF 起行的多行文本同样安全（仓库 core.autocrlf=true，磁盘上两种都可能）
  ③ 制表符要转义（裸 tab 在 YAML 里是缩进字符，会把结构搅乱）
  ④ 其余 C0 / DEL 控制字符要转义（裸写会把解析器搞崩）
  ⑤ 引号与反斜杠本身要转义（`"` 会提前闭合；`\\` 不翻倍会被当成转义符）
  ⑥ 第一起事故（裸「冒号 + 空格」被读成映射）不能因为这次改动而失守
  ⑦ 不需要引号时**仍然不加引号** —— 无脑全引号会把 diff 搅乱（既有约定）

用法：python _t15.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib.content import yaml_scalar  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = ""):
    checks.append((name, bool(ok), detail))
    print(("  OK  " if ok else "  FAIL") + name + (f"  {detail}" if detail else ""))


def roundtrip(s: str) -> tuple[bool, str, str]:
    """把 s 序列化成一个 mapping 的值，再用 YAML 读回来。返回 (成立, 产物, 回读值/错误)。"""
    out = yaml_scalar(s)
    try:
        back = yaml.safe_load("k: " + out)["k"]
    except Exception as e:  # noqa: BLE001
        return False, out, f"{type(e).__name__}: {e}"
    if not isinstance(back, str):
        return False, out, f"回读成了 {type(back).__name__}（{back!r}）"
    return True, out, back


def same(s: str) -> tuple[bool, str]:
    """序列化再回读是否**逐字符**一致。允许 YAML 把 CRLF 折叠成 LF（规范行为）。"""
    ok, out, back = roundtrip(s)
    if not ok:
        return False, f"{out}  ← 回读失败：{back}"
    if back == s or back == s.replace("\r\n", "\n"):
        return True, out
    return False, f"{out}  ← 回读不一致：{back!r}"


# ── ① 事故本体：一条两行的说说 ────────────────────────────────────────────
ok, detail = same("不是哥们。。。。建议都去试试\n怎么微信支付里面接了个AI啊")
chk("① 多行文本（\\n）落盘后仍是合法 YAML，且内容无损", ok, detail)

# ── ② 同一件事换成 CRLF ─────────────────────────────────────────────────
ok, detail = same("第一行\r\n第二行\r\n第三行")
chk("② CRLF 起行的多行文本同样安全", ok, detail)

# ── ③ 制表符 ────────────────────────────────────────────────────────────
ok, detail = same("前面\t后面")
chk("③ 制表符被转义，不当作缩进", ok, detail)

# ── ④ 其它控制字符 ──────────────────────────────────────────────────────
ok, detail = same("控制字符\x00\x01\x07\x1f\x7f结尾")
chk("④ 其余 C0 / DEL 控制字符被转义，解析器不崩", ok, detail)

# ── ⑤ 引号与反斜杠 ──────────────────────────────────────────────────────
# ★ 这一段必须用**被迫加引号**的文本（这里靠「冒号 + 空格」触发）。
#   第一版写的是 `他说"这行"就完了` —— 它压根不触发引号，plain scalar 里的 "
#   本来就合法，于是"不转义引号"那条变异咬不住。假变异要从**判据**上修。
ok, detail = same('他说"这行": 就完了')
chk("⑤ 文本自带双引号、且被迫加引号时不提前闭合", ok, detail)
ok, detail = same("反斜杠 \\ 后面跟一个冒号: 空格")
chk("⑤ 文本自带反斜杠且被迫加引号时不被当成转义符", ok, detail)

# ── ⑥ 第一起事故：裸「冒号 + 空格」 ──────────────────────────────────────
ok, detail = same("时间: 2026-10-27")
chk("⑥ 含「冒号 + 空格」的文本仍被加引号并原样读回（[object Object] 那起）", ok, detail)

# ── ⑦ 不需要引号时不许加引号 ─────────────────────────────────────────────
plain = "为什么GPT老是在我上课和玩游戏的时候来抢我电脑。。"
chk("⑦ 普通中文句子不加引号（diff 噪声约定）", yaml_scalar(plain) == plain, yaml_scalar(plain)[:40])
chk("⑦ 数字样子的文本仍加引号（否则类型会从 str 变 int）", yaml_scalar("123") == '"123"', yaml_scalar("123"))

# ── ⑧ 仓库里真实的 talks.md（它才是被打挂的那份） ─────────────────────────
try:
    repo = Path(json.loads((HERE / "config.json").read_text(encoding="utf-8"))["repo"])
except Exception as e:  # noqa: BLE001
    repo = None
    print(f"  ~~ 跳过真实文件那一段：读不到 config.json 的 repo（{type(e).__name__}: {e}）")

if repo is not None:
    talks = repo / "content" / "pages" / "talks.md"
    if not talks.is_file():
        print(f"  ~~ 跳过真实文件那一段：{talks} 不存在（换了机器？）")
    else:
        raw = talks.read_text(encoding="utf-8")
        try:
            # frontmatter 就是首个 --- 与次个 --- 之间那段
            body = raw.split("---", 2)[1]
            doc = yaml.safe_load(body)
            err = ""
        except Exception as e:  # noqa: BLE001
            doc, err = None, f"{type(e).__name__}: {e}"
        chk("⑧ 仓库真实的 talks.md 能被 YAML 解析（它就是 17:44 被打挂的那份）",
            doc is not None, err)
        if isinstance(doc, dict):
            items = doc.get("items") or []
            texts = [str(it.get("text", "")) for it in items if isinstance(it, dict)]
            chk("⑧ 最新一条说说仍是多行（修的是格式，不是把内容压成一行）",
                bool(texts) and "\n" in texts[0],
                repr(texts[0])[:60] if texts else "（没有 items）")

bad = [c for c in checks if not c[1]]
print(f"\n通过 {len(checks) - len(bad)} / {len(checks)}")
sys.exit(1 if bad else 0)
