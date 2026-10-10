# -*- coding: utf-8 -*-
"""用修好的推送路径，把站主卡住的那个提交推上去（2026-10-09）。一次性脚本。"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lib import gitops  # noqa: E402

REPO = Path(r"D:\router\blog")


def main() -> int:
    r = gitops.push(REPO, on_log=lambda m: print("  " + m, flush=True))
    print()
    print("结果：", {k: v for k, v in r.items() if k != "messages"})
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
