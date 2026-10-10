# -*- coding: utf-8 -*-
"""测试用：把服务起在固定端口并一直挂着（给无头浏览器打）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as S                     # noqa: E402
from lib import config as C            # noqa: E402

repo = Path(sys.argv[1] if len(sys.argv) > 1 else r"D:\router\blog")
port = int(sys.argv[2] if len(sys.argv) > 2 else 8765)
httpd, real, app = S.serve(repo, C.load_config(), port=port)
print(f"UP {real}", flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
