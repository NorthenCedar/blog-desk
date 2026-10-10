# -*- coding: utf-8 -*-
"""端到端验证：真的起一个写作台服务，用 HTTP 打上传接口。

不走单元测试 —— 单元测试验的是 `lib/images.py`，
而站主用的是**打包后的 exe**。这里起真实服务、走真实路由
（`server.py:299` 的 `/api/upload`），用真 JPEG 冒充 .png，
确认落盘名被纠正、rel 指向纠正后的名字。

★ 只往临时仓库写，绝不碰 D:\\router\\blog。
★ 用完自动清理临时目录。

用法：python _e2e_upload.py
"""
import base64
import io
import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))


def make_jpeg(size=(60, 40)):
    """一张真 JPEG。故意做小（< 300KB），好走 compress_image 的「不重编码」分支
    —— 那正是站主那次出问题的那条路径。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (170, 200, 185)).save(buf, "JPEG", quality=70)
    return buf.getvalue()


def build_skeleton(root: Path):
    """搭一个够用的博客仓库骨架，让 Content 能初始化。"""
    (root / "content" / "posts" / "e2e-post").mkdir(parents=True)
    (root / "content" / "posts" / "e2e-post" / "index.md").write_text(
        "---\ntitle: t\ndate: 2026-10-10\nsummary: s\ntags: [随笔]\ndraft: true\n---\n\n正文\n",
        encoding="utf-8")
    (root / "content" / "site.md").write_text(
        "name: t\nwordmark: t\nintro: t\nmetaDescription: t\n"
        "email: a@b.co\ngithub: https://github.com/x/y\n", encoding="utf-8")
    for d in ("content/gallery", "src/assets/talks"):
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


def main():
    tmp = Path(tempfile.mkdtemp(prefix="writedesk-e2e-"))
    repo = build_skeleton(tmp / "blog")
    base = None
    httpd = None
    try:
        # 直接调 serve()，不起子进程 —— 省掉端口/参数对齐的麻烦
        import server as srv

        cfg = {"repo": str(repo), "site": "https://example.com",
               "admin_token": "", "max_kb": 300,
               "window": {"width": 1000, "height": 700}}

        httpd, port, app = srv.serve(repo, cfg)
        base = f"http://127.0.0.1:{port}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.4)
        print("服务已起:", urllib.request.urlopen(base + "/api/health", timeout=5).read().decode())

        jpeg = make_jpeg()
        print(f"\n上传：真 JPEG（{len(jpeg)} 字节，走「够小不重编码」分支），但文件名给 .png")

        payload = {"where": "post", "slug": "e2e-post", "name": "shot.png",
                   "data": base64.b64encode(jpeg).decode(), "max_kb": 300}
        req = urllib.request.Request(base + "/api/upload",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())

        print("接口返回:", json.dumps(resp, ensure_ascii=False))
        landed = repo / "content" / "posts" / "e2e-post" / resp["name"]
        actual = landed.read_bytes()

        print(f"\n落盘: {resp['name']}")
        print(f"rel  : {resp['rel']}")
        print(f"首字节: {actual[:4].hex()}  （FFD8FF = JPEG）")
        print(f"目录里实际有哪些文件: {sorted(p.name for p in landed.parent.iterdir() if p.suffix.lower() in {'.png', '.jpg', '.jpeg'})}")

        checks = [
            ("扩展名被纠正成 .jpg", Path(resp["name"]).suffix == ".jpg"),
            ("主名未被改动", Path(resp["name"]).stem == "shot"),
            ("落盘内容确实是 JPEG", actual[:3] == b"\xff\xd8\xff"),
            ("rel 指向纠正后的名字（markdown 靠它）", resp["rel"] == "./shot.jpg"),
            ("目录里没有留下 .png", not landed.with_suffix(".png").exists()),
        ]
        print()
        allok = True
        for label, passed in checks:
            print(f"  {'✓' if passed else '✗'} {label}")
            allok = allok and passed

        # 顺带验一下归一
        print("\n再传一次，这次名字给 .JPEG（大写别名）：")
        payload2 = dict(payload, name="UPPER.JPEG")
        req2 = urllib.request.Request(base + "/api/upload",
                                      data=json.dumps(payload2).encode(),
                                      headers={"Content-Type": "application/json"})
        resp2 = json.loads(urllib.request.urlopen(req2, timeout=30).read().decode())
        print(f"  落盘: {resp2['name']}")
        ok2 = resp2["name"] == "UPPER.jpg"
        print(f"  {'✓' if ok2 else '✗'} 大写别名归一成 .jpg")
        return 0 if (allok and ok2) else 1
    finally:
        if httpd is not None:
            httpd.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"\n临时目录已清理: {tmp}")


if __name__ == "__main__":
    sys.exit(main())