# -*- coding: utf-8 -*-
"""用修好的推送路径，把站主卡住的那个提交推上去（2026-10-09）。一次性脚本。

用法：python _do_push.py [博客仓库]
      BLOG_REPO 环境变量亦可；两者都不给就报错退出。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib import gitops  # noqa: E402

_repo = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BLOG_REPO", "")).strip()
if not _repo:
    sys.exit("请给出博客仓库路径：python _do_push.py <博客仓库>（或设 BLOG_REPO）")
REPO = Path(_repo)


def main() -> int:
    r = gitops.push(REPO, on_log=lambda m: print("  " + m, flush=True))
    print()
    print("结果：", {k: v for k, v in r.items() if k != "messages"})
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())