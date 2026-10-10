# -*- coding: utf-8 -*-
"""写作台 · 标签页端到端（**打的是打包出来的 exe**，不是源码直接跑）。

为什么要单独有这么一支：

  `_t7.py` 验的是 `lib/content.py` 的**契约**（加减标签、非法名字、在用标签删不掉…），
  它直接 import Python 模块，**一行前端代码都没碰**。而站上那条铁律是
  「交付前必须做**线上产物核对**」—— 落到本应用就是：**界面真的接上了没有**。
  界面接不上的典型症状（都是构建全绿、契约全绿、肉眼一开才发现）：
    · 导航里那个按钮 `data-view="tags"` 打错一个字 ⇒ 点了没反应；
    · `S.tagInfo` 忘了赋值 ⇒ 列表永远空；
    · `POST` 的字段名写成 `tag` 而服务端读 `name` ⇒ 永远报「名字不合法」；
    · 删除按钮的 `disabled` 逻辑反了 ⇒ 在用标签能删掉（这条会**真的写坏仓库**）。

★ 为什么必须打**打包出来的 exe**：
  PyInstaller 的 `--add-data ui;ui` 一旦漏了某个文件（`app.js`/`style.css`），
  源码跑得好好的，装出来的应用就是白屏。只有真 exe 能咬住这类。

★ 为什么打 `_fixture/`：
  标签页会**真的写 `src/tag-registry.ts`**。打真仓库 = 直接改站上源码。
  夹具里 `教程` / `工具` 是故意留的孤儿标签，`AI` / `随笔` 有文章在用 —— 两种情形齐了。

★ 夹具在 `finally` 里**按 sha 还原**，跑完 `_fixture` 一个字节都不变。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import websocket

# 测试会自造 Chrome 用户目录（几百个小文件），清它撞安全层的批量阈值。
# ★ 只调**额度**，不关安全层（绝不用 CODEBUDDY_SAFE_DELETE_ENABLED=0）。
os.environ.setdefault("CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD", "20000")

HERE = Path(__file__).resolve().parent
FIX = HERE / "_fixture"
EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "写作台" / "写作台.exe"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CREATE_NO_WINDOW = 0x08000000

# 会动的三个文件：跑完要按 sha 还原
TOUCHED = [
    "src/tag-registry.ts",
    "content/posts/hello-world/index.md",
    "docs/content-model.md",
]

passed: list[str] = []
failed: list[str] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    (passed if cond else failed).append(label)
    print(f"  {'✔' if cond else '✘'} {label}" + (f"　{extra}" if extra else ""), flush=True)


def free_port() -> int:
    """要一个**当刻真的空闲**的端口。

    ★ 写死端口的账上一轮已经付过一次（`_mutate_t6.py` 连上了别人遗留的服务，
      跑了 13 分钟）。这里每个端口现取。
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def wait_health(port: int, proc: subprocess.Popen, timeout: float = 40) -> bool:
    """等 `/api/health` 通，**并确认这个服务是我们自己起的那个进程**。

    只看"端口上有响应"不够 —— 端口要是被别人占着，会连上别人的服务，
    整个测试的前提就塌了（上一轮真踩过）。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            print(f"  ✘ 服务进程已退出（退出码 {proc.returncode}）")
            return False
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=60)
        self.n = 0
        self.exceptions: list[str] = []

    def send(self, method: str, params: dict | None = None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method} 失败：{m['error']}")
                return m.get("result", {})
            if m.get("method") == "Runtime.exceptionThrown":
                self.exceptions.append(
                    str(m["params"].get("exceptionDetails", {}).get("text")))

    def ev(self, expr: str, await_promise: bool = True):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "awaitPromise": await_promise, "returnByValue": True,
        })
        if r.get("exceptionDetails"):
            raise RuntimeError("页面 JS 抛错：" + json.dumps(
                r["exceptionDetails"], ensure_ascii=False)[:300])
        return r["result"].get("value")

    def wait(self, expr: str, label: str = "", timeout: float = 25) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.ev(expr):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)
        print(f"  ！等不到：{label or expr}")
        if self.exceptions:
            print(f"  ！页面异常 = {self.exceptions[-1][:200]}")
        return False


def chrome_ws(port: int, timeout: float = 30) -> str | None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=3) as r:
                for t in json.loads(r.read().decode()):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return None


def main() -> int:
    src_mode = bool(os.environ.get("DESK_T8_SRC"))
    if not src_mode and not EXE.is_file():
        print(f"✘ 找不到 exe：{EXE}\n  先跑 build-app.py")
        return 1
    if not Path(CHROME).is_file():
        print(f"✘ 找不到 Chrome：{CHROME}")
        return 1

    # ★ 备份的是**字节**，不只是 sha —— 还原要靠它，光有 sha 无法还原。
    backup: dict[str, bytes | None] = {}
    for rel in TOUCHED:
        p = FIX / rel
        backup[rel] = p.read_bytes() if p.is_file() else None
    print("夹具起始 sha：",
          {k: (hashlib.sha256(v).hexdigest()[:16] if v is not None else "（无）")
           for k, v in backup.items()}, flush=True)

    port_app = free_port()
    port_cdp = free_port()
    profile = HERE / "_cdp_profile_t8"
    shutil.rmtree(profile, ignore_errors=True)

    # ★ 默认打**打包出来的 exe**（这是本测试存在的理由，见文件头）。
    #   但变异验证要跑好几轮，每轮重打包一次不现实 ⇒ `DESK_T8_SRC=1` 时改用
    #   `_serve.py`（源码直起服务，ui/ 从磁盘读，改了立刻生效），用来验**界面逻辑**。
    #   两条路都打同一个夹具，断言一模一样。
    if os.environ.get("DESK_T8_SRC"):
        cmd = [sys.executable, str(HERE / "_serve.py"), str(FIX), str(port_app)]
        print("（源码模式：_serve.py，不打 exe）", flush=True)
    else:
        cmd = [str(EXE), "--repo", str(FIX), "--no-open", "--port", str(port_app)]

    app = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW)
    chrome = None
    try:
        # ① 服务
        chk("① 能对夹具起服务（/api/health 200）",
            wait_health(port_app, app),
            f"port={port_app}　{'源码' if src_mode else 'exe'}")

        # ② 无头 Chrome
        chrome = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", f"--remote-debugging-port={port_cdp}",
             "--remote-allow-origins=*", f"--user-data-dir={profile}",
             "--window-size=1280,860", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW)
        ws = chrome_ws(port_cdp)
        if not ws:
            print("✘ 起不来无头 Chrome")
            return 1
        cdp = CDP(ws)
        cdp.send("Runtime.enable")
        cdp.send("Page.enable")
        cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{port_app}/"})
        # ★ 等的是 **boot() 走完的显式信号**，不是"某个元素出现了"。
        #   `#nav button` 是 index.html 里的静态 HTML，页面一解析完就在了 ——
        #   那时 app.js 还没跑、事件还没绑，此时 click() 会被**静默吞掉**
        #   （没异常、没提示），表现成"点了没反应"的假红。本轮真踩到过。
        chk("② 界面加载出来了（app.js 启动完成）",
            cdp.wait("window.__deskReady === true", "boot 完成"))
        chk("② 界面加载时没有 JS 异常", not cdp.exceptions,
            str(cdp.exceptions[:1]))

        # ③ 切到标签页（点的是**导航里那个按钮**，选择器写全，别和下面那个 section 混）
        cdp.ev("document.querySelector('#nav button[data-view=\"tags\"]').click()")
        chk("③ 标签页渲染出 4 行（AI / 随笔 / 教程 / 工具）",
            cdp.wait("document.querySelectorAll('#tag-list .tagrow').length===4", "4 行"),
            f"实际 {cdp.ev('document.querySelectorAll(\"#tag-list .tagrow\").length')}")
        chk("③ 计数写着 4 个",
            (cdp.ev("document.querySelector('#tag-count').textContent") or "").strip() == "4 个",
            repr(cdp.ev("document.querySelector('#tag-count').textContent")))

        # ④ 两条硬约束在界面上真的看得见
        orph = cdp.ev("""Array.from(document.querySelectorAll('#tag-list .tagrow'))
            .filter(r=>r.classList.contains('orphan')).map(r=>r.querySelector('.name').textContent)""")
        chk("④ 孤儿标签（教程 / 工具）被标出来了", sorted(orph or []) == ["工具", "教程"],
            f"实际 {orph}")

        def del_disabled(name: str) -> bool | None:
            return cdp.ev("""(()=>{const r=Array.from(document.querySelectorAll('#tag-list .tagrow'))
                .find(x=>x.querySelector('.name').textContent==='%s');
                return r ? r.querySelector('button').disabled : null;})()""" % name)

        chk("④ ★ 正在被文章用的标签（AI / 随笔）**删除按钮是禁用的**",
            del_disabled("AI") is True and del_disabled("随笔") is True,
            f"AI={del_disabled('AI')} 随笔={del_disabled('随笔')}")
        chk("④ ★ 孤儿标签（工具）的删除按钮**可点**", del_disabled("工具") is False)
        chk("④ 文档漂移被报出来（未登记到表格的是「工具」）",
            "工具" in (cdp.ev("document.querySelector('#tag-note').textContent") or ""),
            repr((cdp.ev("document.querySelector('#tag-note').textContent") or "")[:60]))

        # ⑤ 加一个标签：走**界面**，落到**文件**
        reg = FIX / "src/tag-registry.ts"
        cdp.ev("""(()=>{const e=document.querySelector('#tag-new');
            e.value='读书';e.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        cdp.ev("document.querySelector('#btn-add-tag').click()")
        chk("⑤ 加了之后列表变 5 行",
            cdp.wait("document.querySelectorAll('#tag-list .tagrow').length===5", "5 行"),
            f"实际 {cdp.ev('document.querySelectorAll(\"#tag-list .tagrow\").length')}")
        chk("⑤ ★ 文件里真的有 '读书' 了", "'读书'" in reg.read_text(encoding="utf-8"))
        chk("⑤ 注释与 as const 原样还在",
            "校验的唯一事实来源" in reg.read_text(encoding="utf-8")
            and "as const;" in reg.read_text(encoding="utf-8"))
        chk("⑤ 新标签被标成孤儿（还没文章用它）",
            cdp.ev("""Array.from(document.querySelectorAll('#tag-list .tagrow'))
                .find(r=>r.querySelector('.name').textContent==='读书')
                .classList.contains('orphan')""") is True)

        # ★ 2026-10-09 站主授权：新增标签时**自动补一行**到 docs/content-model.md §4。
        #   这一条必须打在**界面**上（后端那半由 _t7.py 守）—— 「后端写对了、界面没接上」
        #   正是 _t8.py 存在的理由。
        doc = FIX / "docs" / "content-model.md"
        doc_txt = doc.read_text(encoding="utf-8")
        chk("⑤ ★ 界面上加标签，文档 §4 的表格也跟着补了一行（不是只改注册表）",
            "| `读书` |" in doc_txt, "docs/content-model.md 里有 `读书` 那一行")
        d_lines = doc_txt.split("\n")
        ri = [n for n, l in enumerate(d_lines) if l.startswith("| `读书` |")]
        chk("⑤ 补的那行落在**表尾**（它下面紧跟的就是表外内容）",
            len(ri) == 1 and not d_lines[ri[0] + 1].startswith("|"),
            f"下一行 = {d_lines[ri[0] + 1]!r}" if ri else "没找到那行")
        chk("⑤ 提示条说清了「已补一行、占位要你填」",
            "§4 表尾" in (cdp.ev("document.querySelector('#toast').textContent") or ""),
            repr((cdp.ev("document.querySelector('#toast').textContent") or "")[:80]))

        # ⑥ 非法名字：界面给出中文提示，且**文件不变**
        s_before = reg.read_text(encoding="utf-8")
        cdp.ev("(()=>{const t=document.querySelector('#toast');t.textContent='';})()")
        cdp.ev("""(()=>{const e=document.querySelector('#tag-new');
            e.value='读书';e.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        cdp.ev("document.querySelector('#btn-add-tag').click()")
        chk("⑥ ★ 重名被拒，文件一个字节没变",
            cdp.wait("(document.querySelector('#toast').textContent||'').includes('已经有一个')", "重名提示", 15)
            and reg.read_text(encoding="utf-8") == s_before,
            repr((cdp.ev("document.querySelector('#toast').textContent") or "")[:70]))

        # ⑦ 删一个孤儿：走**界面**（含确认框），文件跟着变
        cdp.ev("""(()=>{const r=Array.from(document.querySelectorAll('#tag-list .tagrow'))
            .find(x=>x.querySelector('.name').textContent==='工具');
            r.querySelector('button').click();})()""")
        chk("⑦ 删除会先弹确认框",
            cdp.wait("!!document.querySelector('.mask .dialog [data-x=ok]')", "确认框出现"))
        cdp.ev("document.querySelector('.mask .dialog [data-x=ok]').click()")
        chk("⑦ 删完列表回到 4 行",
            cdp.wait("document.querySelectorAll('#tag-list .tagrow').length===4", "回到 4 行"),
            f"实际 {cdp.ev('document.querySelectorAll(\"#tag-list .tagrow\").length')}")
        chk("⑦ ★ 文件里 '工具' 已经没了", "'工具'" not in reg.read_text(encoding="utf-8"))
        chk("⑦ 剩下的（AI / 随笔 / 教程 / 读书）都在",
            all(f"'{t}'" in reg.read_text(encoding="utf-8")
                for t in ("AI", "随笔", "教程", "读书")))
        # ⑦b 删完之后面板说「一致」—— 因为 `工具` **本来就不在夹具的表格里**
        #     （夹具那份文档是故意漏掉它来演"漂移"的）。删掉它两边的差集都空了。
        note7 = cdp.ev("document.querySelector('#tag-note').textContent") or ""
        chk("⑦b 删完之后面板说「一致」（工具本来就不在表格里，两个方向都不差）",
            "一致" in note7, repr(note7[:70]))

        # ⑧ 在用标签的删除：界面根本点不动（不弹框、文件不变）
        s_now = reg.read_text(encoding="utf-8")
        cdp.ev("""(()=>{const r=Array.from(document.querySelectorAll('#tag-list .tagrow'))
            .find(x=>x.querySelector('.name').textContent==='随笔');
            r.querySelector('button').click();})()""")
        time.sleep(0.8)
        chk("⑧ ★ 点正在被用的标签不会弹确认框（按钮是禁用的）",
            cdp.ev("!document.querySelector('.mask .dialog')") is True)
        chk("⑧ ★ 文件也没动", reg.read_text(encoding="utf-8") == s_now)

        # ⑨ 面板上显示「在哪几篇用」——用法跟着 frontmatter 走
        chk("⑨ 「AI」那行写出了用到它的文章",
            "hello-world" in (cdp.ev("""Array.from(document.querySelectorAll('#tag-list .tagrow'))
                .find(r=>r.querySelector('.name').textContent==='AI').textContent""") or ""),
            repr(cdp.ev("""Array.from(document.querySelectorAll('#tag-list .tagrow'))
                .find(r=>r.querySelector('.name').textContent==='AI').textContent""")))

        # ⑨b ★ 反方向的漂移也得让面板说出来：`读书` 是**加出来的**（⑤ 时表格里补了行），
        #     删掉它之后，注册表里没有了、表格里那行还在 ⇒ 面板必须报「表格里多出」。
        #     删标签**不**代改文档（删是站主要点头的方向），所以那一行得由人划掉。
        cdp.ev("""(()=>{const r=Array.from(document.querySelectorAll('#tag-list .tagrow'))
            .find(x=>x.querySelector('.name').textContent==='读书');
            r.querySelector('button').click();})()""")
        cdp.wait("!!document.querySelector('.mask .dialog [data-x=ok]')", "确认框出现")
        cdp.ev("document.querySelector('.mask .dialog [data-x=ok]').click()")
        chk("⑨b 删掉「读书」之后列表回到 3 行",
            cdp.wait("document.querySelectorAll('#tag-list .tagrow').length===3", "回到 3 行"),
            f"实际 {cdp.ev('document.querySelectorAll(\"#tag-list .tagrow\").length')}")
        chk("⑨b ★ 文件里 '读书' 没了，可文档表格里那一行还在（不代改文档）",
            "'读书'" not in reg.read_text(encoding="utf-8")
            and "| `读书` |" in (FIX / "docs" / "content-model.md").read_text(encoding="utf-8"))
        note9b = cdp.ev("document.querySelector('#tag-note').textContent") or ""
        chk("⑨b ★ 面板报出「§4 表格里多出：读书」",
            "读书" in note9b and "多出" in note9b, repr(note9b[:90]))

        # ⑩ 页面全程无 JS 异常
        chk("⑩ 整个流程没有未处理的 JS 异常", not cdp.exceptions, str(cdp.exceptions[:1]))
    finally:
        if chrome:
            chrome.kill()
        app.kill()
        # 按**字节**还原夹具（跑之前存下来的）
        restored = []
        for rel, blob in backup.items():
            p = FIX / rel
            if blob is None:
                if p.is_file():
                    p.unlink()
                    restored.append(rel)
                continue
            if not p.is_file() or p.read_bytes() != blob:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(blob)
                restored.append(rel)
        print("  ↺ 夹具还原：" + ("、".join(restored) if restored else "无需还原（一个字节都没变）"),
              flush=True)
        # 再核一遍：还原完的 sha 必须等于跑之前记下的那个
        bad = [rel for rel, blob in backup.items()
               if blob is not None and sha(FIX / rel) != hashlib.sha256(blob).hexdigest()[:16]]
        if bad:
            print(f"  ✘ 夹具没还原干净：{bad}", flush=True)
            failed.append("夹具还原")
        shutil.rmtree(profile, ignore_errors=True)

    print("\n" + "=" * 62, flush=True)
    print(f"通过 {len(passed)} · 失败 {len(failed)}", flush=True)
    for f in failed:
        print(f"  ✘ {f}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
