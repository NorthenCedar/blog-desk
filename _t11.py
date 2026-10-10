# -*- coding: utf-8 -*-
"""图片入库的扩展名纠正 —— 变异验证（2026-10-10 A 方案）。

**门闩与本次变更严格挂钩**：只测「名实不符时会不会被纠正」这一件事，
不写泛用检查。

★为什么要变异验证：这个修复的价值全在「它会不会咬住」。
  只跑正例的话，一个空实现也能「通过」。所以下面每个判据都配一个
  **故意改坏的版本**（mutation），必须被咬住才算数。

用法：python _t11.py          （正常跑）
"""
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from PIL import Image

from lib import images
from lib.images import ImageError

PASS, FAIL = [], []


def chk(name, got, want):
    if got == want:
        PASS.append(name)
        print(f"  ✓ {name}")
    else:
        FAIL.append(f"{name}: 期望 {want!r}，实得 {got!r}")
        print(f"  ✗ {name}  期望 {want!r}，实得 {got!r}")


def img(fmt, size=(900, 600), mode="RGB"):
    buf = io.BytesIO()
    m = "RGBA" if mode == "RGBA" else "RGB"
    Image.new(m, size, (180, 200, 190) if m == "RGB" else (180, 200, 190, 255)).save(buf, fmt)
    return buf.getvalue()


class C:
    """最小 content 替身。三个目录都照 lib/content.py:190-193 的形状：Path 属性。"""
    def __init__(self, root):
        root = Path(root)
        self.posts_dir = root / "posts"
        self.gallery_dir = root / "gallery"
        self.talks_assets = root / "talks"


def main():
    print("=" * 68)
    print("① 名实不符 ⇒ 纠正（站主那次的事故场景）")
    print("=" * 68)
    jpeg = img("JPEG")
    with tempfile.TemporaryDirectory() as td:
        c = C(td)
        (c.posts_dir / "p").mkdir(parents=True)

        r = images.save_for_post(c, "p", "photo-uuid.png", jpeg)
        print(f"     上传真 JPEG，名字给 .png")
        print(f"     落盘: {r['name']}")
        chk("JPEG 传成 .png ⇒ 落盘变 .jpg", Path(r["name"]).suffix, ".jpg")
        chk("主名不被改动", Path(r["name"]).stem, "photo-uuid")
        chk("rel 指向纠正后的名字（markdown 靠它）", r["rel"], "./photo-uuid.jpg")

        saved = Path(r["path"]).read_bytes()
        with Image.open(io.BytesIO(saved)) as im:
            chk("落盘内容的真实格式", im.format, "JPEG")

    print()
    print("=" * 68)
    print("② 名实相符 ⇒ 什么都不动（绝大多数情况不能被影响）")
    print("=" * 68)
    with tempfile.TemporaryDirectory() as td:
        c = C(td)
        (c.posts_dir / "p").mkdir(parents=True)
        r = images.save_for_post(c, "p", "Image_1790268606244_504.png", img("PNG"))
        chk("PNG 传成 .png ⇒ 名字不变", r["name"], "Image_1790268606244_504.png")

        r2 = images.save_for_post(c, "p", "shot.jpg", img("JPEG"))
        chk("JPEG 传成 .jpg ⇒ 名字不变", r2["name"], "shot.jpg")

    print()
    print("=" * 68)
    print("③ 三个入口都要纠正（post / talk / gallery 同一个内核）")
    print("=" * 68)
    with tempfile.TemporaryDirectory() as td:
        c = C(td)
        (c.posts_dir / "p").mkdir(parents=True)
        (Path(td) / "gallery" / "alb").mkdir(parents=True)

        webp = img("WEBP")
        rt = images.save_for_talk(c, "20260928-1.png", webp)
        chk("说说图：WebP 传成 .png ⇒ 纠正", Path(rt["name"]).suffix, ".webp")
        chk("说说图的 rel 不带 ./（它进 src/，写法不同）",
            rt["rel"], "20260928-1.webp")

        rg = images.save_for_gallery(c, "alb", "IMG_0001.jpeg", img("JPEG"))
        chk("相册图：.jpeg 归一成 .jpg", Path(rg["name"]).suffix, ".jpg")
        chk("相册图主名保留", Path(rg["name"]).stem, "IMG_0001")

    print()
    print("=" * 68)
    print("④ 动图 / 小图 这两条最容易出错的分支")
    print("=" * 68)
    with tempfile.TemporaryDirectory() as td:
        c = C(td)
        (c.posts_dir / "p").mkdir(parents=True)

        # 够小 ⇒ 走「不重编码」分支（站主那张 182KB 图走的就是这条）
        small = img("JPEG", size=(60, 40))
        r = images.save_for_post(c, "p", "tiny.png", small)
        chk("够小走跳过分支时仍要纠正", Path(r["name"]).suffix, ".jpg")

        # 动图：compress_image 一律原样返回
        frames = [Image.new("P", (40, 40), i) for i in range(3)]
        gbuf = io.BytesIO()
        frames[0].save(gbuf, "GIF", save_all=True, append_images=frames[1:])
        gif = gbuf.getvalue()
        rg = images.save_for_post(c, "p", "anim.png", gif)
        chk("动图：GIF 传成 .png ⇒ 纠正", Path(rg["name"]).suffix, ".gif")
        chk("动图字节未被改写（没被压成单帧）",
            Path(rg["path"]).read_bytes() == gif, True)

    print()
    print("=" * 68)
    print("⑤ 不是图片 / 不支持的扩展名：仍然要报原来的错")
    print("=" * 68)
    with tempfile.TemporaryDirectory() as td:
        c = C(td)
        (c.posts_dir / "p").mkdir(parents=True)

        try:
            images.save_for_post(c, "p", "note.png", b"this is plain text")
            chk("非图片内容要报错", "没报错", "ImageError")
        except ImageError as e:
            chk("非图片内容要报错", "这不是能识别的图片" in str(e), True)

        try:
            images.save_for_post(c, "p", "doc.pdf", b"%PDF-1.4")
            chk("不支持的扩展名要报错", "没报错", "ImageError")
        except ImageError as e:
            chk("不支持的扩展名要报错", "不支持的图片格式" in str(e), True)

    print()
    print("=" * 68)
    print("⑥ 同名冲突仍要正确去重（纠正扩展名后可能撞名）")
    print("=" * 68)
    with tempfile.TemporaryDirectory() as td:
        c = C(td)
        (c.posts_dir / "p").mkdir(parents=True)
        a = images.save_for_post(c, "p", "same.png", img("JPEG"))
        b = images.save_for_post(c, "p", "same.png", img("JPEG"))
        chk("第一次落盘", a["name"], "same.jpg")
        chk("第二次不覆盖，追加序号", b["name"], "same-2.jpg")
        chk("两个文件都在", sorted(p.name for p in (c.posts_dir / "p").iterdir()),
            ["same-2.jpg", "same.jpg"])

    print()
    print("=" * 68)
    print("⑦ fix_ext 单独判：不给「二次校正」擦屁股的机会")
    print("=" * 68)
    # 为什么必须单测：走完整链路时，压缩后那次 fix_ext 会把上一次
    # 的错误**盖掉** —— 实测注入「永远改成 .png」的变异体，
    # 因为二次校正还在，最终落盘仍是 .jpg，全绿通过。
    # ⇒ 只测端到端的话，这一类错根本咬不住。单元层才是真判据。
    jpeg2 = img("JPEG")
    png2 = img("PNG", mode="RGBA")
    webp2 = img("WEBP")

    chk("真 JPEG + .png 名 ⇒ 改成 .jpg",
        images.fix_ext("a.png", jpeg2)[0], "a.jpg")
    chk("真 PNG + .jpg 名 ⇒ 改成 .png",
        images.fix_ext("a.jpg", png2)[0], "a.png")
    chk("真 WebP + .png 名 ⇒ 改成 .webp",
        images.fix_ext("a.png", webp2)[0], "a.webp")
    chk("真JPEG + .jpeg 名 ⇒ 归一成 .jpg",
        images.fix_ext("a.jpeg", jpeg2)[0], "a.jpg")
    chk("名实相符 ⇒ 不动",
        images.fix_ext("a.png", png2)[0], "a.png")
    chk("主名含多个点也不受影响",
        images.fix_ext("20260928-1.png", jpeg2)[0], "20260928-1.jpg")
    chk("认不出的内容 ⇒ 名字不动也不报错",
        images.fix_ext("a.png", b"not an image at all")[0], "a.png")
    chk("认不出的内容 ⇒ 第二个返回值为 None",
        images.fix_ext("a.png", b"not an image at all")[1], None)
    chk("无扩展名 ⇒ 补上真实扩展名",
        images.fix_ext("noext", jpeg2)[0], "noext.jpg")

    # ★ 归一必须单独判：.jpg 与 .jpeg **都是合法 JPEG 扩展名**，
    #   所以「名实相符」那类判据根本区分不出来。实测把 CANON_EXT 的
    #   .jpg 写成 .jpeg，34项照样全绿 —— 这里补上唯一能咬住它的判据。
    chk("真 JPEG + .JPEG 大写 ⇒ 归一成小写 .jpg",
        images.fix_ext("a.JPEG", jpeg2)[0], "a.jpg")
    chk("真 JPEG + .JPG 大写 ⇒ 归一成小写 .jpg",
        images.fix_ext("a.JPG", jpeg2)[0], "a.jpg")
    chk("真 PNG + .PNG 大写 ⇒ 归一成小写 .png",
        images.fix_ext("a.PNG", png2)[0], "a.png")
    chk("真 JPEG + .jpeg 名 ⇒ 落盘用规范的 .jpg（不是 .jpeg）",
        images.fix_ext("a.jpeg", jpeg2)[0], "a.jpg")
    chk("CANON_EXT 表本身：.jpg 的规范形是 .jpg",
        images.CANON_EXT[".jpg"], ".jpg")
    chk("CANON_EXT 表本身：.jpeg 的规范形也是 .jpg",
        images.CANON_EXT[".jpeg"], ".jpg")
    chk("CANON_EXT 表本身：.tif 的规范形是 .tiff",
        images.CANON_EXT[".tif"], ".tiff")

    # 落盘名必须**逐字**等于期望值，而不只是「后缀对」
    with tempfile.TemporaryDirectory() as td:
        c = C(td)
        (c.posts_dir / "p").mkdir(parents=True)
        r = images.save_for_post(c, "p", "UPPER.JPEG", jpeg2, compress=False)
        chk("落盘文件名整体小写归一（不是 UPPER.JPEG 也不是 UPPER.jpeg）",
            r["name"], "UPPER.jpg")

    # ★ M3 那类变异改的是 `_save_image` 里的**编排**，不是 fix_ext 本身，
    #   所以单元测试天然咬不住它。必须单独判「第一次纠正的结果没被吃掉」。
    print()
    print("--- 编排层：第一次纠正必须真的影响最终落盘名 ---")
    with tempfile.TemporaryDirectory() as td:
        c = C(td)
        (c.posts_dir / "p").mkdir(parents=True)

        # ★ compress=False是这一组的关键：**二次校正成了恒等** ——
        #   fix_ext 对一个已经正确的名字是 no-op，所以最终名完全由第一次纠正决定。
        #   若编排里第一次被无视/ 反向（强设 .jpg），真 PNG 就会错成 .jpg。
        r_png = images.save_for_post(c, "p", "shot.png", img("PNG"), compress=False)
        chk("compress=False + 真 PNG ⇒ 落盘 .png（不是被强设的 .jpg）",
            Path(r_png["name"]).suffix, ".png")

        r_webp = images.save_for_post(c, "p", "shot.png", img("WEBP"), compress=False)
        chk("compress=False + 真 WebP ⇒ 落盘 .webp",
            Path(r_webp["name"]).suffix, ".webp")

        r_gif = images.save_for_post(c, "p", "shot.png", img("GIF"), compress=False)
        chk("compress=False + 真 GIF ⇒ 落盘 .gif",
            Path(r_gif["name"]).suffix, ".gif")

        # 小图（跳过压缩）：同样只有第一次纠正在起作用
        r_small = images.save_for_post(c, "p", "order.png", img("JPEG", size=(60, 40)),
                                       compress=True)
        chk("小图（跳过压缩）时第一次纠正就要生效",
            Path(r_small["name"]).suffix, ".jpg")

        # 真 JPEG 不能被强设成 .png/.jpg 之外的任何东西
        r_j = images.save_for_post(c, "p", "x.png", img("JPEG"), compress=False)
        chk("compress=False + 真 JPEG 传成 .png ⇒ 落盘 .jpg",
            Path(r_j["name"]).suffix, ".jpg")

        # 落盘内容的真实格式必须与扩展名一致（这是「名实相符」的终判）
        with Image.open(Path(r_png["path"])) as im:
            chk("PNG 那张落盘内容确实是 PNG", im.format, "PNG")
        with Image.open(Path(r_webp["path"])) as im:
            chk("WebP 那张落盘内容确实是 WEBP", im.format, "WEBP")

    print()
    print("=" * 68)
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    print("=" * 68)
    for f in FAIL:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())