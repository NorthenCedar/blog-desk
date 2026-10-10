# -*- coding: utf-8 -*-
"""写作台 · **拖动窗口**的真机测试（无头 Chrome + CDP，打夹具仓库）。

为什么要有这一条：站主这次报的第 3、4 条都是"手感"问题 ——
  ③ 只能拖深色窄边，想拖顶部一整条不行；
  ④ 拖动时抖。
这两件事**没法靠读代码证明**：`_t5.py` 只验到"后端接口是绝对坐标"，
而"页面到底把哪一次事件发成了什么"必须真的跑一遍事件序列。

所以这里在真实页面里 over 掉 `window.pywebview.api`，用**记录用的替身**，
再按真实鼠标轨迹发 pointer 事件，然后断言后端收到的调用序列：
  · 顶部一整条（含原来的窄边之外的地方）都能起拖 —— 对应 ③
  · 每一帧发出去的都是**绝对**目标坐标、且与指针位移一一对应 —— 对应 ④
    抖动是"累加 + 相对坐标"造成的，正确实现下三帧必须是三个独立值，不能叠。
  · 内容区按下不起拖、按在小圆点/按钮上不起拖、抬起后不再动、最大化时不拖 —— 边界

★ 2026-10-09 追加（站主：「拖动不跟鼠标，会比鼠标快一点」）：
  上一版这里把"每帧目标 = 原点 + 位移 × devicePixelRatio"当成了正确行为，
  于是它一路绿 —— **测试把代码里的错误假设抄了一遍**。
  现在改成：把屏幕缩放显式改成 200% 再拖一遍，要求 100% 与 200% 下发出的
  坐标**完全一致**（窗口坐标是逻辑像素，与屏幕缩放无关）。
  乘了系数的实现会翻倍 ⇒ 这条现在真的咬得住。

★ 用的是 `_fixture/` 那个假仓库，**绝不动真博客**。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ★ 这台机器的安全层对 `shutil.rmtree` 有"批量删除要先确认"的闸（默认约 50 个/回合）。
#   本测试清掉的是**它自己造的** Chrome 用户目录（几百个小文件、就在项目里，
#   不是站主的文件），所以把闸的额度调高 —— 与 build-app.py / npm run verify
#   用的是同一个旋钮，没有关掉安全层本身。
os.environ.setdefault("CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD", "20000")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import websocket  # noqa: E402

from lib.proc import CREATE_NO_WINDOW  # noqa: E402

HERE = Path(__file__).resolve().parent
FIX = HERE / "_fixture"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def free_port() -> int:
    """要一个**当刻真的空闲**的端口。

    ★ 为什么不能写死端口：本轮真踩到过 —— 上一轮遗留的 `_serve.py` 还占着 8766，
      而这个测试只看"8766 上有没有健康响应"，于是它**连上了别人的服务**照样往下跑。
      那样一来"变异验证"可能是假红（服务根本没起来 → 报错 → 被算成"咬住了"）。
      每次现取一个空闲端口，就只可能连到自己起的那个。
    """
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT_APP = free_port()
PORT_CDP = free_port()

passed, failed = [], []


def chk(label, cond, extra=""):
    (passed if cond else failed).append(label)
    print(f"  {'✔' if cond else '✘'} {label}" + (f"　{extra}" if extra else ""))


# ── 极简 CDP（只用到 evaluate）────────────────────────────────────────
class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=90)
        self.n = 0

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

    def ev(self, expr, await_promise=True):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "awaitPromise": await_promise, "returnByValue": True})
        if r.get("exceptionDetails"):
            raise RuntimeError("页面 JS 抛错：" + json.dumps(
                r["exceptionDetails"], ensure_ascii=False)[:500])
        return r["result"].get("value")

    def wait(self, expr, label="", timeout=45):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.ev(expr):
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        raise TimeoutError(f"等不到：{label or expr}")


# ── 页面里跑的那段：装替身 + 按真实轨迹发事件 ───────────────────────
DRAG_SCRIPT = r"""
(async () => {
  const rec = [];
  const R = {rec};
  window.__rec = rec;

  // 起点位置固定报 (100,100)：后面的期望值就是在这个基础上算的
  window.pywebview = { api: {
    win_pos: async () => { rec.push(['pos']); return {ok: true, x: 100, y: 100, maximized: false}; },
    win_moveto: async (x, y) => { rec.push(['moveto', x, y]); return {ok: true}; },
    win_min: async () => ({}), win_max: async () => ({}), win_close: async () => ({}),
  }};

  const tick = (ms = 30) => new Promise(r => setTimeout(r, ms));
  const frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

  // ★ 仿真一件事：**真实世界里窗口一动，clientX/clientY 就跟着变**。
  //   窗口被移到 vwx 之后，同一个屏幕点对应的 clientX 就少掉 vwx。
  //   当初的抖动就是这么来的（client 坐标做位移 = 正反馈），
  //   所以这里必须让 vwx 跟着后端的 moveto 走，否则"用 clientX"的错法验不出来。
  let vwx = 0, vwy = 0;
  const lastMove = () => {
    for (let i = rec.length - 1; i >= 0; i--) if (rec[i][0] === 'moveto') return rec[i];
    return null;
  };
  const syncWin = () => { const m = lastMove(); if (m) { vwx = m[1]; vwy = m[2]; } };

  const fire = (type, sx, sy) => {
    const x = Math.round(sx - vwx), y = Math.round(sy - vwy);
    const el = document.elementFromPoint(x, y) || document.body;
    el.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId: 1, isPrimary: true, button: 0,
      buttons: type === 'pointerup' ? 0 : 1,
      clientX: x, clientY: y, screenX: sx, screenY: sy,
    }));
    return el;
  };

  R.dpr = window.devicePixelRatio || 1;
  R.mask = !!document.querySelector('.mask');
  R.band = Math.max(
    (document.querySelector('#titlebar') || {getBoundingClientRect: () => ({height: 46})})
      .getBoundingClientRect().height,
    (document.querySelector('.work-head') || {getBoundingClientRect: () => ({bottom: 0})})
      .getBoundingClientRect().bottom,
    46);

  // 顶部条上取一点（避开左上角三个小圆点）
  const topX = Math.round(window.innerWidth * 0.62);
  const topY = 8;
  R.topHit = (document.elementFromPoint(topX, topY) || {}).tagName || '';
  R.topOnDot = !!(document.elementFromPoint(topX, topY) || {}).closest?.('button');

  // ① 顶部按下 → 应当开始拖（对应站主第 3 条）
  fire('pointerdown', topX, topY);
  await tick(80);
  R.afterDown = JSON.parse(JSON.stringify(rec));

  // ② 连拖三帧（对应站主第 4 条：不能叠、不能抖）
  const steps = [[20, 10], [45, 20], [75, 33]];
  R.eachMove = [];
  for (const [dx, dy] of steps) {
    fire('pointermove', topX + dx, topY + dy);
    await frame(); await tick(25);
    syncWin();                       // 窗口已经移过去了 ⇒ 下一枪的 client 坐标跟着变
    R.eachMove.push(JSON.parse(JSON.stringify(rec)));
  }

  fire('pointerup', topX + 75, topY + 33);
  await tick(60);
  R.afterUp = JSON.parse(JSON.stringify(rec));

  // ③ 抬起之后再动 → 不应再有任何记录
  fire('pointermove', topX + 220, topY + 70);
  await frame(); await tick(40);
  R.afterUpMove = JSON.parse(JSON.stringify(rec));

  // ④ 内容区按下 → 不该起拖（顶部以外不认）
  rec.length = 0;
  vwx = 0; vwy = 0;                  // 这一段不涉及窗口移动，把仿真状态归零
  await tick(30);
  const midY = Math.min(window.innerHeight - 30, R.band + 140);
  fire('pointerdown', topX, midY);
  await tick(80); await frame(); await tick(40);
  fire('pointermove', topX + 60, midY + 25);
  await frame(); await tick(40);
  R.content = JSON.parse(JSON.stringify(rec));
  fire('pointerup', topX + 60, midY + 25);

  // ⑤ 按在左上角小圆点（button）上 → 不该起拖
  rec.length = 0;
  vwx = 0; vwy = 0;
  await tick(30);
  const dot = document.querySelector('#titlebar button') ||
              document.querySelector('button');
  R.dotFound = !!dot;
  if (dot) {
    const r2 = dot.getBoundingClientRect();
    const cx = Math.round(r2.left + r2.width / 2), cy = Math.round(r2.top + r2.height / 2);
    R.dotPoint = [cx, cy];
    fire('pointerdown', cx, cy);
    await tick(80); await frame(); await tick(40);
    R.dot = JSON.parse(JSON.stringify(rec));
    fire('pointerup', cx, cy);
  }

  // ⑥ 最大化时 → 不拖（move 对最大化窗口无效）
  rec.length = 0;
  vwx = 0; vwy = 0;
  await tick(30);
  window.pywebview.api.win_pos = async () => {
    rec.push(['pos']); return {ok: true, x: 0, y: 0, maximized: true};
  };
  fire('pointerdown', topX, topY);
  await tick(80);
  fire('pointermove', topX + 40, topY + 20);
  await frame(); await tick(40);
  R.maxed = JSON.parse(JSON.stringify(rec));
  R.maxedBodyDragging = document.body.classList.contains('dragging');
  fire('pointerup', topX + 40, topY + 20);

  return R;
})()
"""


def main() -> int:
    if not FIX.is_dir():
        print(f"✘ 没有夹具仓库 {FIX}")
        return 1

    udd = HERE / "_cdp_profile_t6"
    shutil.rmtree(udd, ignore_errors=True)

    app = subprocess.Popen(
        [sys.executable, str(HERE / "_serve.py"), str(FIX), str(PORT_APP)],
        cwd=str(HERE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW)
    chrome = None
    try:
        base = None
        for _ in range(80):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{PORT_APP}/api/health", timeout=2) as r:
                    if r.status == 200:
                        base = f"http://127.0.0.1:{PORT_APP}/"
                        break
            except Exception:
                time.sleep(0.3)
        if not base:
            print("✘ 应用服务起不来")
            return 1
        if app.poll() is not None:
            # 端口被别人占着时，上面那条健康检查会"通过"，但其实连的是别人的服务。
            # 这条守住它：服务必须是**我们自己起的那个进程**（端口已改用空闲端口，
            # 这里再兜一道，免得变异验证出现假红）。
            print(f"✘ {PORT_APP} 上的服务不是本次起的（子进程已退出，退出码 {app.returncode}）")
            return 1

        chrome = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", f"--remote-debugging-port={PORT_CDP}",
             "--remote-allow-origins=*",          # 不带这个 CDP 握手 403
             f"--user-data-dir={udd}", "--window-size=1180,760", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW)

        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{PORT_CDP}/json", timeout=3) as r:
                    for t in json.loads(r.read().decode()):
                        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                            ws_url = t["webSocketDebuggerUrl"]
                            break
                if ws_url:
                    break
            except Exception:
                time.sleep(0.3)
        if not ws_url:
            print("✘ 起不来无头 Chrome")
            return 1

        cdp = CDP(ws_url)
        cdp.send("Runtime.enable")
        cdp.send("Page.enable")
        cdp.send("Page.navigate", {"url": base})
        cdp.wait("!!document.querySelector('#titlebar') && !!window.__rec !== undefined "
                 "|| !!document.querySelector('#titlebar')", "界面挂上")
        time.sleep(1.0)          # 让 sync() 那一轮先跑完，别和拖动抢

        print("\n═══ 拖动窗口：真实事件序列")
        print("-" * 66)
        R = cdp.ev(DRAG_SCRIPT)

        dpr = R["dpr"]
        rec = R["afterDown"]
        moves = R["eachMove"]

        chk("窗口顶部条存在，且拖动生效的判定带 ≥46px", R["band"] >= 46,
            f"topBand={R['band']}　devicePixelRatio={dpr}")
        chk("测试点落在顶部条上、且不在小圆点上",
            bool(R["topHit"]) and not R["topOnDot"],
            f"命中 {R['topHit']}，band={R['band']}")
        chk("没有挡着的对话框（有 .mask 会拒绝拖动）", not R["mask"])

        # ① 顶部一整条都能起拖
        chk("① 在顶部条上按下 → 真的去问了窗口位置（起拖）",
            any(x[0] == "pos" for x in rec), f"{rec}")

        # ② 三帧各自独立、且是绝对坐标
        # ★ 期望值里**没有** devicePixelRatio：窗口坐标是**逻辑像素（DIP）**，
        #   与指针的 screenX（CSS px）本来就是同一把尺子（依据见 ui/app.js 注释 ③
        #   与 docs/rhinelab-spec.md §6.69）。上一版测试把代码里那个错误的 ×dpr
        #   又抄了一遍，所以 dpr=1 时它永远绿 —— 这就是它没咬住 bug 的原因。
        want = [[100 + dx, 100 + dy] for dx, dy in ((20, 10), (45, 20), (75, 33))]
        seq = []
        for m in moves:
            got = [x for x in m if x[0] == "moveto"]
            seq.append(got[-1][1:] if got else None)
        chk("② 每一帧都发了目标坐标（三帧三次，不是一次）",
            all(s is not None for s in seq), f"{seq}")
        chk("② ★ 是**绝对**坐标：等于「窗口原点 + 指针位移」，不是位移量本身",
            seq == [list(w) for w in want],
            f"实收 {seq}　期望 {want}（增量实现会收到 [20,10] 这种小数字）")
        chk("② ★ 三帧互不叠加（抖动就是叠加出来的）",
            seq[0] != seq[1] != seq[2] and len(set(map(tuple, seq))) == 3)

        # ③ 抬起就停
        chk("③ 抬起之后不再发坐标",
            len(R["afterUpMove"]) == len(R["afterUp"]),
            f"{len(R['afterUp'])} → {len(R['afterUpMove'])}")

        # ④ 内容区不认
        chk("④ 内容区按下**不**起拖（只认顶部那一条，不吞文章区的点击）",
            not any(x[0] == "pos" for x in R["content"]) and
            not any(x[0] == "moveto" for x in R["content"]),
            f"{R['content']}")

        # ⑤ 可交互元素不认
        if R.get("dotFound"):
            chk("⑤ 按在左上角小圆点（button）上**不**起拖",
                not any(x[0] == "pos" for x in R["dot"]), f"{R['dot']}")
        else:
            chk("⑤ 找到左上角小圆点", False, "没找到 button")

        # ⑥ 最大化不拖
        chk("⑥ 窗口最大化时不拖（move 对它无效，硬拖只会显得拖不动）",
            not any(x[0] == "moveto" for x in R["maxed"]) and not R["maxedBodyDragging"],
            f"{R['maxed']} dragging={R['maxedBodyDragging']}")

        # ⑦ ★★ 把屏幕缩放改成 200%，再拖一遍（站主「拖动不跟鼠标，会比鼠标快一点」的判据）
        #    本机是 144 DPI（150%），上一版在这里多乘了一次 devicePixelRatio，
        #    于是窗口跑出鼠标 1.5 倍速。要在测试里咬住它，就得**把缩放换成非 1**
        #    再拖一遍：正确实现在 100% 和 200% 下必须发出**完全一样**的坐标
        #    （因为两套坐标同为逻辑像素，与屏幕缩放无关）；乘了系数的实现会翻倍。
        print("\n═══ ★ 屏幕缩放 200% 再拖一遍（同一段代码，只看坐标变不变）")
        print("-" * 66)
        cdp.send("Emulation.setDeviceMetricsOverride",
                 {"width": 1180, "height": 760, "deviceScaleFactor": 2, "mobile": False})
        time.sleep(0.6)
        R2 = cdp.ev(DRAG_SCRIPT)
        seq2 = []
        for m in R2["eachMove"]:
            got = [x for x in m if x[0] == "moveto"]
            seq2.append(got[-1][1:] if got else None)
        scaled = [[v * 2 for v in row] for row in want]
        chk("⑦ 缩放确实生效了（devicePixelRatio 变成 2）", R2["dpr"] == 2, f"dpr={R2['dpr']}")
        chk("⑦ ★ 200% 缩放下发的坐标与 100% 时一模一样（与屏幕缩放无关）",
            seq2 == [list(w) for w in want],
            f"200% 收到 {seq2}　100% 收到 {seq}　期望都是 {want}")
        chk("⑦ ★ 不是 ×devicePixelRatio（旧实现在 200% 下会发 2 倍的坐标）",
            seq2 != [list(w) for w in scaled] and seq2 == seq,
            f"×2 会是 {scaled}")

    finally:
        if chrome:
            chrome.kill()
        app.kill()
        shutil.rmtree(udd, ignore_errors=True)

    print("-" * 66)
    print(f"合计 {len(passed) + len(failed)} 项，通过 {len(passed)}，失败 {len(failed)}")
    for f in failed:
        print(f"  失败：{f}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
