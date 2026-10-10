# -*- coding: utf-8 -*-
"""测试用：把服务起在固定端口并一直挂着（给无头浏览器打）。

用法：python _serve.py <博客仓库> [端口]
      BLOG_REPO 环境变量亦可；两者都不给就直接报错退出 ——
      不设兜底默认值，因为"某个人的绝对路径"对别人毫无意义。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as S                     # noqa: E402
from lib import config as C            # noqa: E402

_repo = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BLOG_REPO", "")).strip()
if not _repo:
    sys.exit("请给出博客仓库路径：python _serve.py <博客仓库> [端口]（或设 BLOG_REPO）")

repo = Path(_repo)
port = int(sys.argv[2] if len(sys.argv) > 2 else 8765)
httpd, real, app = S.serve(repo, C.load_config(), port=port)
print(f"UP {real}", flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass