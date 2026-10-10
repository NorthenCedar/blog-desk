# -*- coding: utf-8 -*-
"""写作台图标：**本站的 ∞ 母题 + 打字机**（2026-10-09，站主第 3 条）。

站主原话：「图标结合本站无限符号元素和写字台/打字机你自己改一下」。

两件素材都来自站上，不另造形状：
  · ∞ 用 `public/art/inf-logo.webp` —— **本站那个 logo 的 alpha 通道**（粗 ∞，左环里一个 ×、
    右环里一道 ／）。它本身是"黑 + alpha"的蒙版素材，所以可以按任意颜色重出；
    这里重出成站上的墨绿 `--ink-motif: #2f7741`（2026-10-08 站主定的那个绿）。
  · 配色只用站上已有 token：纸白 `--paper #fafaf7`、墨色 `--ink #1a1a1a`、墨绿 `#2f7741`。

做法：一份 HTML → 无头 Chrome **一次性出图**（`--screenshot`）→ Pillow 出多尺寸 .ico。
∞ 那段用 CSS `mask-image` 染色（mask 里只有 alpha，颜色由 `background` 决定）。

★ 为什么不走 CDP 截图（2026-10-09 实测）：
  `Page.navigate` 是通的，但紧接着的 `Page.captureScreenshot` 会**当场把整条连接打断**
  （`ConnectionAbortedError [WinError 10053]`），换动态端口也一样 —— 与端口无关。
  同一台机器上 `chrome.exe --headless=new --screenshot=out.png <url>` 一次就成，
  所以这里干脆退回命令行出图，一条 CDP 都不用。

跑法：`python _icon.py`　→ 出 `_icon_<方案>_<配色>.png` + `_icon_sheet.png`（对比图）
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD", "20000")

from PIL import Image, ImageDraw  # noqa: E402

from lib.proc import CREATE_NO_WINDOW  # noqa: E402

HERE = Path(__file__).resolve().parent
# 图标素材取自站点仓库的 public/art。走 BLOG_REPO 环境变量，不钉死某人的绝对路径 ——
# 这个脚本要从「博客仓库 + 本写作台」两处取材，写死一处就等于把机器布局公开出去。
_BLOG = os.environ.get("BLOG_REPO")
ART = Path(_BLOG) / "public" / "art" if _BLOG else HERE.parent / "blog" / "public" / "art"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

SIZE = 512                      # 母版边长（256 的 2 倍，缩下去更干净）
TILE = 256                      # 页面里的逻辑尺寸（viewBox）
SCALE = SIZE // TILE            # 2

PAPER = "#fafaf7"
INK = "#1a1a1a"
GREEN = "#2f7741"

# 定稿方案（2026-10-09，四轮对比后选定）：见 typewriter() 的 kind 说明。
#   "t5" = 压纸卷与机身咬合 + 三排键（3+3+2）+ 纸从压纸卷后面升起来
#   —— 站主点名的两个元素（打字机、以及"正在写"）都齐了。
#   取舍记录：三排键比两排更像"键盘那一面"；那张纸在 256 上把"正在写作"讲清楚了，
#   代价是 ≤32px 时纸与近白的机身糊在一起。**不为了小尺寸牺牲大尺寸** ——
#   那个尺寸下能读成「∞ + 一台白机器」就够了，而 256 是资源管理器里最常见的展示尺寸。
#   试过把纸压暗一档（#cfd8d3）来保小尺寸，结果大尺寸上那块纸读成了「灰板」，作废。
#   另两档配色（dusk-ink / paper）留在 PALETTES 里，改一个常量就能换。
FINAL = "t5"


# ─────────────────────── ∞ 蒙版：从站上素材来 ───────────────────────

def inf_mask_data_url() -> tuple[str, float]:
    """把站的 ∞ logo 裁到不透明范围、压到 420px 宽，返回 (dataURL, 高宽比)。"""
    im = Image.open(ART / "inf-logo.webp").convert("RGBA")
    im = im.crop(im.getchannel("A").getbbox())
    w = 420
    h = max(1, round(im.size[1] * w / im.size[0]))
    im = im.resize((w, h), Image.LANCZOS)
    # mask 只认 alpha；RGB 给成全黑，免得某些实现把颜色也乘进去
    flat = Image.new("RGBA", im.size, (0, 0, 0, 255))
    flat.putalpha(im.getchannel("A"))
    buf = io.BytesIO()
    flat.save(buf, "PNG", optimize=True)
    return ("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()), h / w


# ─────────────────────────── 打字机（三套机身）───────────────────────────
#
# ∞ 占上半区（top=44，高约 69），打字机占下半区（y 约 132–228）。
# 三套只换"机身"，压纸卷（platen）+ 两端旋钮是打字机最认得出的特征，三套都留。

def typewriter(kind: str, tw: str, key: str, sheet_color: str) -> tuple[str, dict]:
    """返回 (打字机 SVG, ∞ 的位置与尺寸)。

    压纸卷（platen）+ 两端旋钮是打字机最认得出的特征，每套都留。

    kind:
      · "trap" —— 梯形机身 + 一排三键（第一版，实测读起来像音箱，留作对照）
      · "grid" —— 圆角矩形机身 + 两排键（3+2），但压纸卷与机身**之间留了缝** ⇒
                  连起来像「哑铃」，这一版留着当反例
      · "g2"   —— 压纸卷直接压在机身上（无缝）+ 旋钮收小 ⇒ 一个整体
      · "g3"   —— "g2" 后面再升起一张纸，交代"正在写"
      · "g4"   —— ∞ 印在纸上的版本：纸从压纸卷里升出来，整件东西是一个对象
    """
    if kind == "trap":
        body = f'<path d="M70 176 H186 L204 216 Q206 224 197 224 H59 Q50 224 52 216 Z" ' \
               f'fill="{tw}"/>'
        keys = "".join(f'<circle cx="{cx}" cy="200" r="8.5" fill="{key}"/>'
                       for cx in (98, 128, 158))
        return _wrap(body, keys), _INF_DEFAULT

    if kind == "grid":
        body = f'<rect x="58" y="170" width="140" height="58" rx="13" fill="{tw}"/>'
        keys = _two_rows(key, y1=188, y2=210, r=8)
        platen = f'<rect x="48" y="146" width="160" height="14" rx="7" fill="{tw}"/>'
        knobs = _knobs(tw, key, 44, 212, 153, 13, 5.5)
        return _wrap(body, keys, platen + knobs), _INF_DEFAULT

    if kind in ("g2", "g3"):
        # 压纸卷**压在**机身上：y 146 + h 19 = 165，机身从 163 起 ⇒ 咬合 2px，没有缝
        body = f'<rect x="58" y="163" width="140" height="63" rx="14" fill="{tw}"/>'
        keys = _two_rows(key, y1=183, y2=206, r=8.2)
        platen = f'<rect x="48" y="146" width="160" height="19" rx="9.5" fill="{tw}"/>'
        knobs = _knobs(tw, key, 46, 210, 155.5, 11, 4.5)
        sheet = ""
        if kind == "g3":
            sheet = f'<rect x="82" y="116" width="92" height="38" rx="4" fill="{sheet_color}"/>'
        # dy=-5：内容纵向跨度是 40..226，重心偏低 5px，整体上提做**视觉**居中
        return _wrap(body, keys, sheet + platen + knobs, dy=-5), {"w": 168, "top": 39}

    if kind == "t2":
        # 三排键（3+3+2）：最像"键盘那一面"，代价是小尺寸下会糊成一片
        body = f'<rect x="56" y="159" width="144" height="70" rx="14" fill="{tw}"/>'
        keys = ("".join(f'<circle cx="{cx}" cy="175" r="7.5" fill="{key}"/>'
                        for cx in (92, 128, 164))
                + "".join(f'<circle cx="{cx}" cy="196" r="7.5" fill="{key}"/>'
                          for cx in (92, 128, 164))
                + "".join(f'<circle cx="{cx}" cy="216" r="7.5" fill="{key}"/>'
                          for cx in (110, 146)))
        platen = f'<rect x="48" y="142" width="160" height="19" rx="9.5" fill="{tw}"/>'
        knobs = _knobs(tw, key, 46, 210, 151.5, 11, 4.5)
        return _wrap(body, keys, platen + knobs, dy=-2), {"w": 168, "top": 40}

    if kind == "t3":
        # 纸**清楚地**从压纸卷后面升起来：打字机最认得出的那个信号
        body = f'<rect x="58" y="167" width="140" height="59" rx="13" fill="{tw}"/>'
        keys = _two_rows(key, y1=186, y2=208, r=8)
        platen = f'<rect x="48" y="148" width="160" height="21" rx="10.5" fill="{tw}"/>'
        knobs = _knobs(tw, key, 46, 210, 158.5, 11, 4.5)
        sheet = f'<rect x="70" y="110" width="116" height="56" rx="5" fill="{sheet_color}"/>'
        # ∞ 收小一档，给纸让出位置（纸顶 110，∞ 底 106）
        return _wrap(body, keys, sheet + platen + knobs, dy=-4), {"w": 156, "top": 42}

    if kind == "t5":
        # t2 的三排键 + t3 的那张纸（两个"打字机信号"叠在一起，看会不会太满）
        body = f'<rect x="56" y="159" width="144" height="70" rx="14" fill="{tw}"/>'
        keys = ("".join(f'<circle cx="{cx}" cy="175" r="7.5" fill="{key}"/>'
                        for cx in (92, 128, 164))
                + "".join(f'<circle cx="{cx}" cy="196" r="7.5" fill="{key}"/>'
                          for cx in (92, 128, 164))
                + "".join(f'<circle cx="{cx}" cy="216" r="7.5" fill="{key}"/>'
                          for cx in (110, 146)))
        platen = f'<rect x="48" y="142" width="160" height="19" rx="9.5" fill="{tw}"/>'
        knobs = _knobs(tw, key, 46, 210, 151.5, 11, 4.5)
        sheet = f'<rect x="70" y="110" width="116" height="56" rx="5" fill="{sheet_color}"/>'
        return _wrap(body, keys, sheet + platen + knobs, dy=-3), {"w": 156, "top": 42}

    # g4 / t4：∞ 印在纸上 —— 打字机稍微压低、加大，纸从压纸卷里升上来
    body = f'<rect x="54" y="166" width="148" height="64" rx="14" fill="{tw}"/>'
    keys = _two_rows(key, y1=187, y2=210, r=8.5, row1=(92, 128, 164), row2=(110, 146))
    platen = f'<rect x="45" y="150" width="166" height="19" rx="9.5" fill="{tw}"/>'
    knobs = _knobs(tw, key, 42, 214, 159.5, 12, 5)
    sheet = f'<rect x="74" y="52" width="108" height="106" rx="6" fill="{sheet_color}"/>'
    return _wrap(body, keys, sheet + platen + knobs), {"w": 86, "top": 74}


_INF_DEFAULT = {"w": 168, "top": 44}


def _knobs(tw: str, key: str, xl: float, xr: float, cy: float,
           r: float, ri: float) -> str:
    return (f'<circle cx="{xl}" cy="{cy}" r="{r}" fill="{tw}"/>'
            f'<circle cx="{xr}" cy="{cy}" r="{r}" fill="{tw}"/>'
            f'<circle cx="{xl}" cy="{cy}" r="{ri}" fill="{key}"/>'
            f'<circle cx="{xr}" cy="{cy}" r="{ri}" fill="{key}"/>')


def _two_rows(key: str, y1: float, y2: float, r: float,
              row1=(96, 128, 160), row2=(112, 144)) -> str:
    return ("".join(f'<circle cx="{cx}" cy="{y1}" r="{r}" fill="{key}"/>' for cx in row1)
            + "".join(f'<circle cx="{cx}" cy="{y2}" r="{r}" fill="{key}"/>' for cx in row2))


def _wrap(body: str, keys: str, top_layer: str = "", dy: float = 0.0) -> str:
    g = f'<g transform="translate(0,{dy})">' if dy else "<g>"
    return f"""
    <svg width="{TILE}" height="{TILE}" viewBox="0 0 {TILE} {TILE}"
         style="position:absolute;left:0;top:0">
      {g}
      {top_layer}
      {body}
      {keys}
      </g>
    </svg>"""


PALETTES = {
    # 名字 → (底 CSS, 打字机色, ∞ 色, 键帽挖空色, 纸色)
    "dusk":     ("background: radial-gradient(120% 120% at 30% 18%, #131b17 0%, #0b100e 72%);"
                 "box-shadow: inset 0 0 0 1px #ffffff1f;",
                 "#fafaf7", "#4caf50", "#0b100e", "#f2f2ee"),
    "dusk-ink": ("background: radial-gradient(120% 120% at 30% 18%, #131b17 0%, #0b100e 72%);"
                 "box-shadow: inset 0 0 0 1px #ffffff1f;",
                 "#fafaf7", "#2f7741", "#0b100e", "#f2f2ee"),
    "paper":    ("background: #fafaf7; box-shadow: inset 0 0 0 1px #d8d5ce;",
                 "#1a1a1a", "#2f7741", "#fafaf7", "#e2e2da"),
}
PAL_ORDER = ["dusk", "dusk-ink", "paper"]


def build_html(pal: str, kind: str) -> str:
    """配色三档，全部取自站上 token（`ui/style.css` 的 `:root`），不自造颜色。

    ★ 上一版我在这里编了 `#1b2620 / #0b100e / #6fbf85` 三个色，其实站上早就有对应的：
        · 深场那一族   `--dusk-2 #131b17` → `--dusk-1 #0b100e`
        · 深场上的绿   `--accent-bright #4caf50`
          （原注释：「对 --ink 6.26:1；对纸白只 2.66 ⇒ **只许用在深色底**」）
        · ∞ 母题绿     `--ink-motif #2f7741`（站上 ∞ 素材本身就是这个色）
        · 深场分隔线   `--dglass-line #ffffff1f`
        · 浅色底分隔线 `--hair #d8d5ce`（用 `--glass-line #1a1a1a14` 在浅色任务栏上
          几乎看不见，整个方块会"糊"在背景里，所以浅色版改用 hair 描边）
    """
    mask, ratio = inf_mask_data_url()
    tile_css, tw, ink, key, sheet = PALETTES[pal]

    tw_svg, inf = typewriter(kind, tw, key, sheet)
    inf_color = GREEN if kind == "g4" else ink     # 印在纸上 ⇒ 用站上那个墨绿
    inf_w, inf_top = inf["w"], inf["top"]
    inf_h = round(inf_w * ratio)                   # ≈69 / ≈35

    return f"""<!doctype html><meta charset="utf-8">
<style>
  html, body {{ margin: 0; padding: 0; background: transparent; }}
  #tile {{
    position: relative; width: {TILE}px; height: {TILE}px; border-radius: 52px;
    {tile_css}
    overflow: hidden;
  }}
  #inf {{
    position: absolute; left: {(TILE - inf_w) // 2}px; top: {inf_top}px;
    width: {inf_w}px; height: {inf_h}px; background: {inf_color};
    -webkit-mask-image: url({mask}); -webkit-mask-size: 100% 100%;
    -webkit-mask-repeat: no-repeat;
  }}
</style>
<div id="tile">{tw_svg}<div id="inf"></div></div>"""


# ─────────────────── 无头 Chrome 一次性出图（不走 CDP）───────────────────
#
# ★ 为什么这么写：见文件头。CDP 的 `captureScreenshot` 在这台机器上必断。
#   `--force-device-scale-factor=2` + `--window-size=256,256` ⇒ 出 512×512。
#   `--default-background-color=00000000` ⇒ 圆角之外保持透明。

def shoot(html_path: Path, out: Path, profile: Path) -> None:
    prof = profile
    shutil.rmtree(prof, ignore_errors=True)
    out.unlink(missing_ok=True)
    proc = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", f"--user-data-dir={prof}",
         f"--window-size={TILE},{TILE}", f"--force-device-scale-factor={SCALE}",
         "--default-background-color=00000000",
         f"--screenshot={out}", html_path.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90, creationflags=CREATE_NO_WINDOW)
    if not out.exists():
        raise RuntimeError(f"Chrome 没出图（返回码 {proc.returncode}）："
                           f"{(proc.stderr or proc.stdout or '')[-400:]}")
    im = Image.open(out)
    if im.size != (SIZE, SIZE):
        raise RuntimeError(f"图尺寸不对：{im.size}，期望 {(SIZE, SIZE)}")


# ─────────────────────────── 对比图（给我自己看）───────────────────────────

def contact_sheet(items: list[tuple[str, Path]], out: Path) -> None:
    """把各方案拼一张对比图，浅灰底（透明处能看出来）+ 顶部标签条。"""
    cols = 4
    pad, label_h, cell = 16, 22, SIZE // 2
    rows = (len(items) + cols - 1) // cols
    W = pad + cols * (cell + pad)
    H = pad + rows * (cell + label_h + pad)
    sheet = Image.new("RGB", (W, H), (232, 232, 228))
    d = ImageDraw.Draw(sheet)
    for i, (name, png) in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad)
        y = pad + r * (cell + label_h + pad)
        d.text((x + 2, y + 4), name, fill=(40, 40, 40))
        icon = Image.open(png).convert("RGBA").resize((cell, cell), Image.LANCZOS)
        sheet.paste(icon, (x, y + label_h), icon)
    sheet.save(out)


# ─────────────────────── 尺寸递减预览（挑配色用）───────────────────────
#
# ★ 只看 512 的母版挑不出配色：真正决定成败的是 **16px** 还剩不剩下"∞ + 打字机"。
#   所以把两个配色各缩一条 256→16 排在一起，按真实尺寸看。

RAMP = [256, 128, 64, 48, 32, 16]


def ramp_sheet(pngs: dict[str, Path], out: Path) -> None:
    """浅底一张、深底一张：同一批图铺在浅色 / 深色底上，按真实尺寸从 256 排到 16。"""
    row_h = 300
    W = 10 + sum(s + 14 for s in RAMP) + 10
    H = 16 + len(pngs) * (row_h + 8)
    for label, bg, ink in (("light", (250, 250, 247), (150, 150, 150)),
                           ("dark", (28, 28, 28), (130, 130, 130))):
        sheet = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(sheet)
        y = 8
        for pal, png in pngs.items():
            d.text((10, y + 2), f"{label}bg / {pal}", fill=ink)
            icon = Image.open(png).convert("RGBA")
            x = 10
            for s in RAMP:
                small = icon.resize((s, s), Image.LANCZOS)
                sheet.paste(small, (x, y + 20), small)
                d.text((x, y + 22 + s), str(s), fill=ink)
                x += s + 14
            y += row_h
        suffix = "" if label == "light" else "_darkbg"
        sheet.save(out.with_name(out.stem + suffix + out.suffix))


# ─────────────────────────── .ico（给打包用）───────────────────────────
#
# ★ 文件名用 ASCII `writedesk.ico` 而不是 `写作台.ico`：
#   它要喂给 PyInstaller 的 `icon=` 参数，而那一路在 Windows 上要过一层
#   命令行/临时目录，非 ASCII 名字是没必要的风险。**对站主可见的名字是 exe 自己的
#   `写作台.exe`**（spec 里的 `name=`），ico 的文件名谁也看不到。

FINAL_PAL = "dusk"          # 见文件头/dusk-ink 那两档的取舍说明
ICO_SIZES = (256, 128, 64, 48, 32, 24, 16)


def make_ico(master: Path, out: Path) -> None:
    """由 512 母版出多尺寸 .ico。

    先折半到 256 再交给 Pillow：Pillow 的 ICO 写出里，256 那条存 PNG、
    小尺寸存 BMP(DIB + AND 掩码)，两种都要有 Windows 才认全。
    """
    im = Image.open(master).convert("RGBA").resize((256, 256), Image.LANCZOS)
    im.save(out, format="ICO", sizes=[(s, s) for s in ICO_SIZES])


def verify_ico(path: Path) -> list[tuple[int, str, bool]]:
    """读回来逐个尺寸核一遍 —— "文件生成了"和"里面真有这些尺寸"是两件事。

    ★ 判据必须**与版面无关**。第一版我写了"中心像素必须是深色"，那其实是照着当时
      那一版的构图写的：换成"∞ 在纸上"或"纸升到中间"的版面，中心就变成纸白，
      好图当场被判成坏图。所以只留三条真正与构图无关的：
        ① 角上近透明（圆角之外是透明底）—— 放宽到 alpha ≤ 12，因为 32/24 这种
           小尺寸下抗锯齿会留下 alpha=1~2 的像素，判 `==0` 会误报；
        ② 有**全透明**像素（圆角真的存在，不是一整块方图）——
           阈值只能取 1%：52px 圆角在 256 边上切掉的面积 = 4×52²×(1−π/4) ≈ 2321px ≈ **3.5%**
           （再算上抗锯齿约 2.9%），写 5% 会把好图判成坏图；方图则是 0%，1% 照样分得开。
        ③ 颜色数 ≥ 3（不是一张空白图）。
    """
    got = []
    for s in ICO_SIZES:
        im = Image.open(path)
        im.size = (s, s)
        rgba = im.convert("RGBA")
        px = list(rgba.get_flattened_data())
        n = len(px)
        corner = px[0]
        # 算"近透明"而不是"恰好 0"：小尺寸下圆角的抗锯齿像素 alpha 落在 1~5，
        # 用 ==0 会低估到 0.8%（32px 那次就是），1% 的闸就守不住了。
        clear = sum(1 for p in px if p[3] < 16) / n
        colors = len(set(px))
        why = []
        if corner[3] > 12:
            why.append(f"角不透明 {corner}")
        if clear < 0.01:
            why.append(f"几乎没有透明像素 {clear:.1%}")
        if colors < 3:
            why.append(f"只有 {colors} 种颜色")
        got.append((s, f"透明 {clear:.1%} · {colors} 色 · 角 {corner}", not why))
    return got


def main() -> int:
    profile = HERE / "_cdp_profile_icon"
    ico = HERE / "writedesk.ico"

    # `--ico-only`：不重跑 Chrome，直接拿现有母版重出 .ico（调尺寸表时用）
    if "--ico-only" in sys.argv:
        make_ico(HERE / f"_icon_{FINAL}_{FINAL_PAL}.png", ico)
        print(f"  .ico {ico.name}  {ico.stat().st_size} 字节（母版 {FINAL_PAL}）")
        for s, note, ok in verify_ico(ico):
            print(f"    {'✔' if ok else '✘'} {s:>3}px  {note}")
        return 0

    only_final = "--all" not in sys.argv
    kinds = [FINAL] if only_final else ["t2", "t3", "t5", "g2"]
    pales = PAL_ORDER if only_final else ["dusk", "paper"]
    made: list[tuple[str, Path]] = []
    finals: dict[str, Path] = {}
    try:
        for pal in pales:
            for kind in kinds:
                html = HERE / f"_icon_{kind}_{pal}.html"
                html.write_text(build_html(pal, kind), encoding="utf-8")
                png = HERE / f"_icon_{kind}_{pal}.png"
                shoot(html, png, profile)
                made.append((f"{kind} / {pal}", png))
                if kind == FINAL:
                    finals[pal] = png
                print(f"  出图 {png.name}  {png.stat().st_size} 字节")
        if not only_final:
            contact_sheet(made, HERE / "_icon_sheet.png")
            print("  对比图 _icon_sheet.png")
        ramp_sheet(finals, HERE / "_icon_ramp.png")
        print("  尺寸预览 _icon_ramp.png / _icon_ramp_darkbg.png")

        make_ico(finals[FINAL_PAL], ico)
        print(f"  .ico {ico.name}  {ico.stat().st_size} 字节（母版 {FINAL_PAL}）")
        for s, note, ok in verify_ico(ico):
            print(f"    {'✔' if ok else '✘'} {s:>3}px  {note}")
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
