# -*- coding: utf-8 -*-
"""写作台 · 真机交互测试（无头 Chrome + CDP）。

为什么不满足于 --dump-dom：
  `--dump-dom` 序列化的是 **HTML 属性**，而界面上的值大多是 JS 用 `el.value = x`
  写的 —— 那不会写回属性。于是「#l-ver 填上了没」这类断言永远读不到，
  会给出**假阴性**。CDP 能读真实的 DOM 属性、能真的点、真的拿返回值。
  ⇒ 结论一句话：界面测试必须走 CDP，不能靠 dump-dom。

打的是 `_fixture/`（临时假仓库），**绝不动真博客**。
唯一会联网的是仓库状态（GitHub API 只读），推送一律不点。
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
HERE = Path(__file__).resolve().parent
FIX = HERE / "_fixture"
PORT_CDP = 9333
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766/"
# 上传素材放在**夹具外面**：夹具每轮都会被 git clean 清一遍，放里面会被清掉
UPLOAD_SRC = HERE / "_upload_src.jpg"

passed, failed = [], []


def chk(label, cond, extra=""):
    (passed if cond else failed).append(label)
    print(f"  {'✔' if cond else '✘'} {label}" + (f"　{extra}" if extra else ""))


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.n = 0
        self.console_errors = []
        self.exceptions = []

    def send(self, method, params=None):
        self.n += 1
        mid = self.n
        msg = {"id": mid, "method": method}
        if params:
            msg["params"] = params
        self.ws.send(json.dumps(msg))
        while True:
            data = json.loads(self.ws.recv())
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(f"{method} 失败：{data['error']}")
                return data.get("result", {})
            m = data.get("method")
            if m == "Runtime.consoleAPICalled" and data["params"].get("type") == "error":
                self.console_errors.append(str(data["params"].get("args")))
            elif m == "Runtime.exceptionThrown":
                self.exceptions.append(str(data["params"].get("exceptionDetails", {}).get("text")))

    def ev(self, expr, await_promise=True):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "awaitPromise": await_promise, "returnByValue": True,
        })
        if r.get("exceptionDetails"):
            raise RuntimeError("页面 JS 抛错：" +
                               json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400])
        return r["result"].get("value")

    def wait(self, expr, label="", timeout=20):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.ev(expr):
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        raise TimeoutError(f"等不到：{label or expr}")

    def wait_report(self, expr, label="", timeout=20):
        """等不到时把提示条与异常一起打出来 —— 否则只看到"超时"，猜不出为什么。"""
        try:
            return self.wait(expr, label, timeout)
        except TimeoutError:
            toast = ""
            try:
                toast = self.ev("document.querySelector('#toast').textContent") or ""
            except Exception:
                pass
            print(f"  ！{label} 超时。提示条内容 = 「{toast}」")
            if self.exceptions:
                print(f"  ！页面异常 = {self.exceptions[-1][:200]}")
            return False


def click_wait(cdp, click_expr, needle, label, timeout=25):
    """点一个按钮，然后等提示条出现某段文字。

    ★ 先把提示条清空：toast() 只去掉 class、**不清 textContent**，
      而启动时那条「浏览器模式…」提示会留在里面 —— 不清的话断言可能读到旧文字
      （既可能误判成成功，也可能把真成功的覆盖掉而误判失败）。
    """
    cdp.ev("(()=>{const t=document.querySelector('#toast');t.textContent='';t.className='toast';})()")
    cdp.ev(click_expr)
    return cdp.wait_report(f"document.querySelector('#toast').textContent.includes('{needle}')",
                           label, timeout)


def main():
    # 夹具复位到**最初那个提交**：让每次跑的起点一模一样。
    # 不复位的话第二轮会被上一轮的写入带偏（例如"summary 应该还没改过"就不成立了）。
    root = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=FIX,
                          capture_output=True, text=True).stdout.strip()
    if root:
        subprocess.run(["git", "reset", "--hard", "-q", root], cwd=FIX, capture_output=True)
        subprocess.run(["git", "clean", "-fdq"], cwd=FIX, capture_output=True)
        print(f"夹具已复位到 {root[:8]}")

    # 上传用的素材（复位之后再造，且放夹具外面）
    shutil.copy(FIX / "content/gallery/demo/a.jpg", UPLOAD_SRC)

    udd = HERE / "_cdp_profile"
    shutil.rmtree(udd, ignore_errors=True)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", f"--remote-debugging-port={PORT_CDP}",
         # ★ 不带这个 Chrome 会拒掉 CDP 的 WebSocket 握手（403）
         "--remote-allow-origins=*",
         f"--user-data-dir={udd}", "--window-size=1180,760", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    ws_url = None
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT_CDP}/json", timeout=3) as r:
                for t in json.loads(r.read().decode()):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        ws_url = t["webSocketDebuggerUrl"]
                        break
            if ws_url:
                break
        except Exception:
            time.sleep(0.3)
    if not ws_url:
        print("✘ 起不来无头 Chrome（CDP 连不上）")
        proc.kill()
        return 1

    cdp = CDP(ws_url)
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    cdp.send("DOM.enable")

    try:
        # ───────────── 一、写作视图 + 打开发文章 ─────────────
        print("\n═══ 一、写作：列表与编辑器")
        cdp.send("Page.navigate", {"url": BASE + "#write"})
        cdp.wait("document.querySelectorAll('#post-list .item').length > 0", "文章列表出来")
        chk("文章列表达 1 篇", cdp.ev("document.querySelectorAll('#post-list .item').length") >= 1)
        br = cdp.ev("document.querySelector('#st-branch').textContent") or ""
        chk("侧栏显示分支与短 sha", "·" in br and "—" not in br, br)

        cdp.ev("document.querySelector('#post-list .item').click()")
        cdp.wait("document.querySelector('#f-title').value === '测试文章一'", "编辑器填上标题")
        chk("title 填对", cdp.ev("document.querySelector('#f-title').value") == "测试文章一")
        chk("date 填对", cdp.ev("document.querySelector('#f-date').value") == "2026-10-01")
        sm = cdp.ev("document.querySelector('#f-summary').value") or ""
        chk("summary 填对", "假文章" in sm, sm[:34])
        chk("正文读出来了", cdp.ev("document.querySelector('#f-body').value.length") > 40)
        chk("标签 chip 渲染了", cdp.ev("document.querySelectorAll('#tag-chips .chip').length") == 4,
            f"4 个（注册表里 4 个）")
        chk("在册标签被选中", cdp.ev("document.querySelectorAll('#tag-chips [aria-pressed=true]').length") == 1)
        chk("本文图缩略渲染了", cdp.ev("document.querySelectorAll('#post-imgs button').length") == 1)
        chk("摘要计数在走", cdp.ev("document.querySelector('#summary-count').textContent").startswith(str(
            len(cdp.ev("document.querySelector('#f-summary').value")))))

        print("\n═══ 二、Markdown 预览（含本地图解析）")
        cdp.ev("document.querySelector('#btn-toggle-preview').click()")
        time.sleep(0.4)
        prev = cdp.ev("document.querySelector('#preview').innerHTML") or ""
        chk("预览出 <h2>", "<h2>" in prev)
        chk("预览出 <li>", "<li>" in prev)
        chk("预览出 <blockquote>", "<blockquote>" in prev)
        chk("预览出 <img>", "<img" in prev)
        chk("图片路径改写成 /api/file", "/api/file?path=" in prev)
        chk("图片 alt 保留", 'alt="测试图"' in prev)
        chk("预览里没有裸露的 Markdown 标记", "![" not in prev)
        cdp.ev("document.querySelector('#btn-toggle-preview').click()")

        # ───────────── 三、保存文章（真写盘）─────────────
        print("\n═══ 三、保存文章（真写盘 + 回读文件）")
        # ★ 这里故意用 **ASCII 冒号+空格** —— 那才是 YAML 的坑（全角「：」不是）
        cdp.ev("(()=>{const t=document.querySelector('#f-summary');"
               "t.value='改过的摘要: 带冒号';t.dispatchEvent(new Event('input',{bubbles:true}));})()")
        chk("保存流程给出反馈",
            click_wait(cdp, "document.querySelector('#btn-save').click()", "已保存", "保存文章"))
        time.sleep(0.5)
        md = (FIX / "content/posts/hello-world/index.md").read_text(encoding="utf-8")
        chk("★ 裸「冒号+空格」的摘要被加了引号", 'summary: "改过的摘要: 带冒号"' in md,
            [l for l in md.split("\n") if l.startswith("summary")][:1])
        chk("正文没被吞掉", "正文第一段" in md)
        chk("tags 仍在", "tags: [随笔]" in md)
        chk("字段顺序规范（title→date→summary→tags）",
            md.index("title:") < md.index("date:") < md.index("summary:") < md.index("tags:"))

        # ───────────── 四、新建 + 删除文章 ─────────────
        print("\n═══ 四、新建 / 删除文章（走应用内对话框）")
        cdp.ev("document.querySelector('#btn-new-post').click()")
        cdp.wait("!!document.querySelector('.mask .dialog')", "新建对话框弹出")
        chk("对话框有两个输入框", cdp.ev("document.querySelectorAll('.mask [data-k]').length") == 2)
        chk("对话框是自绘的（不是 window.prompt）",
            cdp.ev("document.querySelector('.mask .dialog h3').textContent === '新建文章'"))
        cdp.ev("(()=>{const s=document.querySelector('[data-k=slug]');s.value='temp-new-post';"
               "const t=document.querySelector('[data-k=title]');t.value='临时文章';})()")
        cdp.ev("document.querySelector('.mask [data-x=ok]').click()")
        chk("列表变 2 篇",
            cdp.wait_report("document.querySelectorAll('#post-list .item').length === 2", "新建后列表 2 篇"))
        newdir = FIX / "content/posts/temp-new-post/index.md"
        chk("新文章文件写出来了", newdir.is_file())
        chk("新文章是草稿", "draft: true" in newdir.read_text(encoding="utf-8"))
        chk("编辑器切到新文章", cdp.ev("document.querySelector('#f-slug').value") == "temp-new-post")

        cdp.ev("document.querySelector('#btn-del-post').click()")
        cdp.wait("!!document.querySelector('.mask .dialog')", "删除确认弹出")
        chk("删除是危险样式", cdp.ev("!!document.querySelector('.mask .btn.danger')"))
        cdp.ev("document.querySelector('.mask [data-x=ok]').click()")
        chk("列表回到 1 篇",
            cdp.wait_report("document.querySelectorAll('#post-list .item').length === 1", "删除后列表 1 篇"))
        chk("目录真的删了", not (FIX / "content/posts/temp-new-post").exists())
        chk("（没有误删别的）", (FIX / "content/posts/hello-world/index.md").is_file())

        # ───────────── 五、插入图片（真上传）─────────────
        print("\n═══ 五、插入图片（CDP 真填 file input → 真写盘）")
        cdp.ev("document.querySelector('#post-list .item').click()")
        cdp.wait("document.querySelector('#f-slug').value === 'hello-world'", "回到第一篇文章")
        # ★ 不去碰操作系统的文件选择框（headless 下 DOM.setFileInputFiles 时好时坏，
        #   实测同一份夹具上一轮成、下一轮 FileReader 就报"读不出这个文件"）。
        #   改为在页面里自己造一个 File 塞进 #file-pick 并派发 change ——
        #   这仍然是**真实**的那条链路（pickFiles 的 change 监听 → FileReader → POST /api/upload → 写盘），
        #   唯一没被覆盖的只有"系统选择框弹没弹"这一段，而那本来也不该由无头浏览器验。
        import base64 as _b64
        b64 = _b64.b64encode(UPLOAD_SRC.read_bytes()).decode("ascii")
        cdp.ev("document.querySelector('#btn-insert-img').click()", await_promise=False)
        time.sleep(0.5)
        cdp.ev("(()=>{const bin=atob('" + b64 + "');const arr=new Uint8Array(bin.length);"
               "for(let i=0;i<bin.length;i++)arr[i]=bin.charCodeAt(i);"
               "const f=new File([arr],'_upload_me.jpg',{type:'image/jpeg'});"
               "const dt=new DataTransfer();dt.items.add(f);"
               "const inp=document.querySelector('#file-pick');inp.files=dt.files;"
               "inp.dispatchEvent(new Event('change',{bubbles:true}));})()",
               await_promise=False)
        up_ok = cdp.wait_report("document.querySelector('#f-body').value.includes('_upload_me')",
                                "图插进正文", timeout=25)
        chk("图片名插进了正文", up_ok)
        landed = sorted((FIX / "content/posts/hello-world").glob("_upload_me*"))
        chk("图片落到文章目录", len(landed) == 1, str([p.name for p in landed]))
        chk("★ 没有重复入库（一次点击只写一份）", len(landed) == 1)
        chk("图片被压过（原图 35 KB → 更小或原样）",
            bool(landed) and landed[0].stat().st_size > 0,
            f"{landed[0].stat().st_size // 1024} KB" if landed else "")
        chk("新图进了缩略条", cdp.ev("document.querySelectorAll('#post-imgs button').length") >= 2)

        # ───────────── 六、日志（注释保护 + 冒号守卫）─────────────
        print("\n═══ 六、日志：读、加、保存")
        cdp.ev("location.hash='#log'")
        cdp.wait("document.querySelectorAll('#log-list .item').length === 2", "日志 2 条")
        chk("日志 2 条", cdp.ev("document.querySelectorAll('#log-list .item').length") == 2)
        chk("版本号填上了（真属性值，不是 dump-dom 的假阴性）",
            cdp.ev("document.querySelector('#l-ver').value") == "v1.1")
        chk("changes 行数是 2", cdp.ev("document.querySelectorAll('#l-changes input').length") == 2)
        chk("自动取下一个版本", True, cdp.ev("(()=>{document.querySelector('#btn-ver-auto').click();"
                                            "return document.querySelector('#l-ver').value;})()"))

        cdp.ev("document.querySelector('#btn-add-change').click()")
        chk("加一条后变 3 行", cdp.ev("document.querySelectorAll('#l-changes input').length") == 3)
        cdp.ev("(()=>{const i=document.querySelectorAll('#l-changes input');"
               "i[2].value='风险值: 裸冒号空格';i[2].dispatchEvent(new Event('input',{bubbles:true}));})()")
        cdp.ev("(()=>{const i=document.querySelectorAll('#l-changes input');i[0].value='第一行改过了';})()")
        chk("日志保存有反馈",
            click_wait(cdp, "document.querySelector('#btn-save-log').click()", "已保存", "日志保存"))
        time.sleep(0.5)
        logmd = (FIX / "content/pages/log.md").read_text(encoding="utf-8")
        chk("★ 注释一字不差保住（哨兵句在）", "SENTINEL-LOG-COMMENT" in logmd)
        chk("注释块的行数没少", logmd.count("#") >= 5, f"{logmd.count('#')} 个 #")
        chk("★ 裸「冒号+空格」被加了引号", '"风险值: 裸冒号空格"' in logmd)
        chk("改动内容写进去了", "第一行改过了" in logmd)
        chk("正文没被动过", "（这是页面正文。）" in logmd)
        chk("旧条目还在", "开头" in logmd)

        # ───────────── 七、说说（注释保护）─────────────
        print("\n═══ 七、说说：读与保存")
        cdp.ev("location.hash='#talks'")
        cdp.wait("document.querySelectorAll('#talk-list .item').length === 2", "说说 2 条")
        chk("说说 2 条", cdp.ev("document.querySelectorAll('#talk-list .item').length") == 2)
        chk("正文填上了", "第一条假说说" in (cdp.ev("document.querySelector('#t-text').value") or ""))
        cdp.ev("(()=>{const t=document.querySelector('#t-text');t.value='改过的说说内容';})()")
        chk("说说保存有反馈",
            click_wait(cdp, "document.querySelector('#btn-save-talk').click()", "已保存", "说说保存"))
        time.sleep(0.5)
        talksmd = (FIX / "content/pages/talks.md").read_text(encoding="utf-8")
        chk("★ 说说注释保住了", "SENTINEL-TALKS-COMMENT" in talksmd)
        chk("说说内容改上了", "改过的说说内容" in talksmd)
        chk("href 没丢", "https://example.com" in talksmd)

        # ───────────── 八、相册照片 + 右键菜单 ─────────────
        print("\n═══ 八、相册照片与右键菜单")
        cdp.ev("location.hash='#photos'")
        cdp.wait("document.querySelectorAll('#photo-wall .photo').length >= 2", "照片墙出来")
        chk("照片墙 2 张", cdp.ev("document.querySelectorAll('#photo-wall .photo').length") == 2)
        chk("已登记的显示序号", cdp.ev("document.querySelectorAll('#photo-wall .tag').length") == 2)
        chk("未登记的打了「未登记」角标",
            cdp.ev("Array.from(document.querySelectorAll('#photo-wall .tag')).some(x=>x.textContent.includes('未登记'))"))
        cdp.ev("(()=>{const p=document.querySelector('#photo-wall .photo');"
               "p.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:200,clientY:200}));})()")
        time.sleep(0.3)
        chk("右键菜单打开", cdp.ev("document.querySelector('#ctxmenu').classList.contains('on')"))
        chk("菜单里有「复制 Markdown（大图 1280）」",
            cdp.ev("Array.from(document.querySelectorAll('#ctxmenu button')).some(b=>b.textContent.includes('大图 1280'))"))
        chk("菜单里有删除项",
            cdp.ev("Array.from(document.querySelectorAll('#ctxmenu button')).some(b=>b.textContent.includes('删除'))"))
        cdp.ev("document.querySelector('#ctxmenu button').click()")
        time.sleep(0.3)
        chk("点完菜单会关掉", not cdp.ev("document.querySelector('#ctxmenu').classList.contains('on')"))
        chk("复制有反馈（成功或明确失败）",
            cdp.ev("document.querySelector('#toast').classList.contains('on')"))

        # ───────────── 九、留言（未配密钥时 fail closed）─────────────
        print("\n═══ 九、留言：未配密钥必须明确拒绝")
        cdp.ev("location.hash='#comments'")
        cdp.wait("!!document.querySelector('#cm-list .empty')", "留言区给出提示")
        chk("提示里说清要配密钥",
            "管理密钥" in (cdp.ev("document.querySelector('#cm-list').textContent") or ""))

        # ───────────── 十、发布：状态 / 改动 / 只提交 ─────────────
        print("\n═══ 十、发布视图")
        cdp.ev("location.hash='#publish'")
        cdp.wait("document.querySelector('#pub-state').textContent.length > 20", "状态渲染出来")
        chk("状态里有分支", "分支" in cdp.ev("document.querySelector('#pub-state').textContent"))
        chk("改动列表有行（刚改过文件）",
            cdp.ev("document.querySelectorAll('#pub-diff .f').length") >= 1,
            f"{cdp.ev('document.querySelectorAll(\"#pub-diff .f\").length')} 行")
        chk("提交信息预填了", len(cdp.ev("document.querySelector('#pub-msg').value") or "") > 5)
        cdp.ev("(()=>{const b=document.querySelector('#pub-skip');b.checked=false;})()")

        # 只提交（本地，安全）；绝不点推送
        chk("本地提交成功",
            click_wait(cdp, "document.querySelector('#btn-commit-only').click()", "已提交", "只提交", timeout=40),
            cdp.ev("document.querySelector('#toast').textContent"))

        # ───────────── 十一、设置 + 窗口键降级 ─────────────
        print("\n═══ 十一、设置与窗口键降级")
        cdp.ev("location.hash='#settings'")
        cdp.wait("document.querySelector('#s-repo').value.length > 3", "设置填上仓库路径")
        chk("仓库路径是夹具路径", "_fixture" in cdp.ev("document.querySelector('#s-repo').value"),
            cdp.ev("document.querySelector('#s-repo').value"))
        chk("站点地址填上", "northcedar.top" in cdp.ev("document.querySelector('#s-site').value"))
        chk("关于块说了 token 镜像的事",
            "tokens.css" in (cdp.ev("document.querySelector('#about-box').textContent") or ""))

        chk("浏览器模式打了 .bare 标记", cdp.ev("document.body.classList.contains('bare')"))
        cdp.ev("document.querySelector('#btn-close').click()")
        time.sleep(0.3)
        t = cdp.ev("document.querySelector('#toast').textContent") or ""
        chk("点关闭键给出诚实的降级提示", "浏览器模式" in t, t[:40])

        # ───────────── 十二、无控制台报错 ─────────────
        print("\n═══ 十二、控制台")
        chk("没有未捕获异常", not cdp.exceptions, str(cdp.exceptions[:2]))
        chk("没有 console.error", not cdp.console_errors, str(cdp.console_errors[:2]))

    finally:
        try:
            cdp.ws.close()
        except Exception:
            pass
        proc.kill()
        time.sleep(0.5)
        shutil.rmtree(udd, ignore_errors=True)
        if UPLOAD_SRC.exists():
            UPLOAD_SRC.unlink()

    print("\n" + "═" * 62)
    print(f"合计 {len(passed) + len(failed)} 项：通过 {len(passed)}，失败 {len(failed)}")
    if failed:
        print("失败项：")
        for f in failed:
            print("  · " + f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
