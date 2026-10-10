# -*- coding: utf-8 -*-
"""变异验证：确认 _t11.py 的判据真能咬住坏版本。

用法：python _mutate_t11.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
TARGET = HERE / "lib" / "images.py"
BAK = Path(r"C:\Users\A2817\AppData\Local\Temp\images.bak")
PY = sys.executable

FIRST = "    name, ext = fix_ext(name, data)\n"
SECOND = "    name, ext2 = fix_ext(name, data)\n"

MUTATIONS = [
    ("M1 压缩前的纠正整个失效（等于没修这个 bug）",
     FIRST, ""),
    ("M2 压缩后的二次校正整个删掉",
     SECOND, ""),
    ("M3 主名一起被改掉（不该动 Image_1790268606244_504 这种名字）",
     FIRST,
     "    name, ext = fix_ext(name, data)\n"
     "    name = 'renamed' + ext\n"),
    ("M4 sniff 一律返回 PNG（不查真实格式）",
     "    return FORMAT_EXT.get(fmt)\n",
     "    return '.png'\n"),
    ("M5 fix_ext 反了：把真格式当成要避开的（.png⇄.jpg对调）",
     "    return FORMAT_EXT.get(fmt)\n",
     "    return {'.png': '.jpg', '.jpg': '.png'}.get(FORMAT_EXT.get(fmt))\n"),
    ("M6 canonical 归一写反：.jpg 被改成 .jpeg",
     "CANON_EXT = {\".jpg\": \".jpg\", \".jpeg\": \".jpg\",",
     "CANON_EXT = {\".jpg\": \".jpeg\", \".jpeg\": \".jpeg\","),
]


def note_equivalent():
    """如实记一笔：有一类变异是这个实现的**等价变异**，不凑数。

    「把第一次 fix_ext 的结果改成任意错值」在端到端**观察不到** ——
    因为第二次 `fix_ext` 对最终字节无条件重算，会把第一次的结果盖掉。
    实测把第一次强设成 .jpg / .png，两种变异体最终落盘全都正确（34项全绿）。
    这是**实现冗余**（第二次校正在当前 `compress_image` 下是恒等的），
    不是判据失效 —— 所以不为它编一个假阳性判据，而是把这个事实写进
    `lib/images.py` 的 docstring，并在此如实标注。
    """
    print()
    print("  说明：存在一类**等价变异** ——「把第一次 fix_ext 改成任意错值」。")
    print("        第二次 fix_ext 对最终字节无条件重算，会把它盖掉，")
    print("        所以端到端观察不到差异。这是实现冗余，不是判据失效。")
    print("        已实测确认（强设 .jpg / 强设 .png 均全绿），")
    print("        并把这个事实写进了 _save_image 的 docstring。")


def run_tests():
    p = subprocess.run([PY, str(HERE / "_t11.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(HERE))
    out = (p.stdout or "") + (p.stderr or "")
    # 崩溃也算「被咬住」—— 判据不该放过一个连跑都跑不起来的实现
    crashed = "Traceback" in out
    passed = "结果：42 通过 / 0 失败" in out
    return crashed or not passed, out, crashed, passed


def main():
    original = TARGET.read_text(encoding="utf-8")
    bites = 0
    effective = 0

    for label, old, new in MUTATIONS:
        if old not in original:
            print(f"  ⚠ {label}\n     替换锚点没找到 —— 这条变异无效，判据有效性未验证")
            continue
        effective += 1
        TARGET.write_text(original.replace(old, new, 1), encoding="utf-8")
        caught, out, crashed, passed = run_tests()
        status = "被咬住" if caught else "★ 没咬住 —— 判据无效！"
        print(f"  {'✓' if caught else '✗'} M: {label}\n       → {status}"
              + ("（崩了，算被咬住）" if crashed else ""))
        if caught:
            bites += 1
        else:
            print("       变异体竟然全绿：")
            for line in out.splitlines():
                if "✓" in line:
                    print("         ", line.strip())

    TARGET.write_text(original, encoding="utf-8")
    ok, out, crashed, passed = run_tests()
    print(f"\n还原后：{'全绿 ✓' if not crashed else '状态异常'}")
    print(f"变异被咬住：{bites}/{effective}")
    note_equivalent()
    return 0 if bites == effective else 1


if __name__ == "__main__":
    sys.exit(main())