# -*- coding: utf-8 -*-
"""逐页冒烟：用无头 Chrome 把每个视图各渲染一次，核对关键数据是否真到位。

为什么这么测：界面全是 JS 拉的，静态读完 HTML 看不出对错。
无头 Chrome 里没有 pywebview ⇒ body 会带 .bare（三键降级），正好顺带验证降级路径。
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = "http://127.0.0.1:8765/"
HERE = Path(__file__).resolve().parent
TMP = HERE / "_cdtmp"

CHECKS = {
    "write": [
        (r'<div class="item"[^>]*>', 5, "文章列表 5 项"),
        (r'id="editor-empty"[^>]*>', 1, "编辑器空态在"),
        (r'正文用 Markdown', 1, "编辑器表单在"),
    ],
    "log": [
        (r'<div class="item"[^>]*>', 38, "日志 38 条"),
        (r'id="l-changes"', 1, "changes 容器在"),
        # ★ 别断言 value="…"：JS 用 el.value= 赋的值**不会写回 HTML 属性**，
        #   dump-dom 读到的永远是原始属性 —— 那是假阴性（见 _cdp.py 开头）。
        #   改数「真实存在的 DOM 节点」：changes 行是靠 JS 建出来的。
        (r'placeholder="改了什么（写清楚：为什么要改）"', 1, "changes 行建出来了"),
    ],
    "talks": [
        (r'<div class="item"[^>]*>', 2, "说说 2 条"),
        (r'id="t-imgs"', 1, "配图容器在"),
    ],
    "photos": [
        (r'<div class="photo"', 1, "照片墙有图"),
        (r'未登记', 0, "（可能没有未登记项）"),
        (r'id="photo-album"', 1, "相册选择器在"),
    ],
    "comments": [
        (r'还没配管理密钥', 1, "未配密钥时给出明确提示"),
    ],
    "publish": [
        (r'远端 main', 1, "远端状态行在"),
        (r'id="pub-diff"', 1, "改动区在"),
        (r'id="pub-msg"', 1, "提交信息框在"),
    ],
    "settings": [
        # ★ 同理，别断言 input 的 value 属性（JS 赋的值不写回属性）。
        #   改看「仓库路径有没有作为文本渲染出来」—— 它在「关于」块里是 <code> 文本。
        (r'router\\blog', 2, "仓库路径作为文本出现（品牌副标题 + 关于块）"),
        (r'id="about-box"', 1, "关于块在"),
        (r'northcedar\.top', 1, "站点地址出现"),
    ],
    "shortcuts": [
        (r'data-open="repo"', 1, "打开仓库按钮在"),
        (r'id="lnk-gh"', 1, "GitHub 链接在"),
    ],
}


def dump(view):
    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    out = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", f"--user-data-dir={TMP}",
         "--virtual-time-budget=9000", "--dump-dom", BASE + "#" + view],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    return out.stdout or ""


ok_all = True
for view, checks in CHECKS.items():
    html = dump(view)
    print(f"\n═══ #{view}　（DOM {len(html)} 字符）")
    errs = []
    for pat, want, label in checks:
        n = len(re.findall(pat, html))
        if want == 0:
            print(f"  · {label}：{n} 处（不判定）")
            continue
        good = n >= want if want > 1 else n >= 1
        print(f"  {'✔' if good else '✘'} {label}：{n} 处（要 ≥{want}）")
        if not good:
            errs.append(label)
    # 每页都该有：标题栏 + 完整节点
    for must in ['id="titlebar"', 'class="light l-red"', 'id="toast"', 'id="ctxmenu"']:
        if must not in html:
            errs.append("缺 " + must)
            print(f"  ✘ 缺节点 {must}")
    if errs:
        ok_all = False
        print("  ✘ 这一页有问题：" + "、".join(errs))
    else:
        print("  ✔ 这一页通过")

print("\n" + "═" * 60)
print("逐页冒烟：" + ("全部通过" if ok_all else "有失败"))
sys.exit(0 if ok_all else 1)
