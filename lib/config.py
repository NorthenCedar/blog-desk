# -*- coding: utf-8 -*-
"""写作台 · 配置读写（config.json，放在应用目录旁边）。

★ 为什么单独一个模块：启动器（app.py）和服务端（server.py）都要读写同一份
  `config.json`。各写一份迟早漂移 —— 比如一边认 `admin_token` 一边认 `token`，
  症状是"在设置里存了密钥却说没配"。
  ⇒ 唯一事实来源就放这里。

密钥（`admin_token`）只落本机磁盘，**不回传给页面**：界面只知道
「有没有配」（has_token），不知道值。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ══════════════════ 两种运行形态，两套路径（2026-10-09 加打包后） ══════════════════
#  · 源码跑（python app.py）：应用目录 = 本文件往上两级（blog-desk/）
#  · 打包成 exe（PyInstaller）：代码与 ui/ 被塞进一个临时解包目录（sys._MEIPASS），
#    但那个目录**是只读且每次启动都变**的 ⇒ config.json 绝不能放那儿，
#    否则站主的密钥与仓库路径每次开机都"丢"。可写的只有 exe 自己所在的那个目录。
#  ⇒ 分清两个概念：
#      APP_DIR（可写）—— config.json、运行日志.txt 放这里
#      RES_DIR（只读）—— 打包进去的 ui/ 从这里读
IS_FROZEN = bool(getattr(sys, "frozen", False))

if IS_FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    RES_DIR = APP_DIR

# 兼容旧引用（原来只有 HERE 一个概念，等于现在这两个在源码形态下是同一个）
HERE = APP_DIR

CONFIG_PATH = APP_DIR / "config.json"

# 博客仓库路径的**兜底默认值**。这里刻意不给真实路径：
#   · 真实值是站主本机的目录布局（D:\router\blog），写死在源码里等于开源时顺带公开它；
#   · 而它本来也不该是"默认值" —— 仓库路径是每台机器自己的事，config.json 里那份才是准的。
# 取值优先级：config.json 的 repo → 环境变量 BLOG_REPO → 空（启动器会明确提示去设置里填）。
DEFAULT_REPO = os.environ.get("BLOG_REPO", "")

DEFAULT_CONFIG = {
    "repo": DEFAULT_REPO,
    "site": "https://northcedar.top",
    "admin_token": "",
    "max_kb": 300,
    "window": {"width": 1180, "height": 760},
}

# 允许从界面改的键（白名单 —— 别让页面随手塞任意字段进配置文件）
WRITABLE = {"repo", "site", "admin_token", "max_kb", "window"}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception as e:  # noqa: BLE001
            print(f"[desk] config.json 读不动，先用默认值：{e}")
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def public_config(cfg: dict) -> dict:
    """给界面看的配置 —— 去掉密钥，只留"配没配"。"""
    out = dict(cfg)
    out.pop("admin_token", None)
    out["has_token"] = bool((cfg.get("admin_token") or "").strip())
    return out
