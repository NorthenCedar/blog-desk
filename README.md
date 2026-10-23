# 写作台（WriteDesk）

「北杉札记」博客的本地编辑器。文章、标签、图片、相册都在界面里改，
改完点「发布」= 构建 + 提交 + 推送。推到 `main` 就等于上线 ——
Cloudflare Pages 会在 1–3 分钟内自动部署。

配套站点源码在同一个站主的另一个仓库（personal），两边通过 `content/` 目录的
Markdown 文件对接。

```
北杉札记（Astro 站点）        写作台（本仓库）
  content/posts/*.md   ←──────   读写同一批文件
  content/pages/*.md   ←──────
  public/ 下静态资源           上传/转存图片
  git main                ←──────   推送即部署
```

## 它能做什么

- **写作**：新建、修改、删除文章；标签选择器与标签管理器（新增标签自动补进
  `docs/content-model.md` §4 的表尾）
- **图片**：上传到指定栏目（自动转码压缩）、相册原图一键转存到随笔配图
- **视频**：文章、说说上传 MP4 / WebM（单个最多 25 MiB），填写说明、保存和本地播放预览
- **相册**：管理 `content/gallery/` 下的图与专辑
- **发布**：一键「构建 + 提交 + 推送」，或只推不构建
- **留言管理**：读 / 删线上的留言（走站点的 `/api/admin/comments` 管理接口）
- **窗口**：桌面应用形态（pywebview + WebView2），窗口起不来会自动降级到浏览器

## 快速开始

### 视频与 AI 并行编辑

文章正文上方增加「正文／章节标题／小标题」按钮，作用于光标所在段落或选中的行。
章节标题保存为 H2，小标题保存为 H3，发布后自动生成文章的「本文目录」。
桌面目录在正文右侧，手机在正文前；单纯加粗不会进入目录。代码围栏与缩进代码保持原样。
安装版需要重新构建更新，重开旧版 exe 不会自动读取源码中的界面改动。

文章使用「插入视频」，说说使用「上传视频」。支持 MP4 / WebM（每个不超过 25 MiB），
MP4 推荐 H.264 + AAC。填写说明后保存，再去发布。原件存入博客的 `content/videos/`，
按 SHA-256 去重；仅公开页面引用的视频会随博客构建导出，草稿与未引用原件不会上线。
本地播放器支持字节区间请求与进度跳转，不会自动播放。

保存文章、说说和日志时会比对读取版本；若 AI 或另一窗口改过文件，返回冲突并保留当前编辑内容，
请先复制草稿，再重新读取合并。说说旧评论标识与视频字段都会保留。

发布已改为普通非强制 Git 推送，保留每一笔提交。源码范围外的工作区改动、远端领先或分叉、
校验期间文件变化都会阻止发布；不自动 stash、rebase 或 reset。对同一文件的同时编辑仍需要合并，
这套检查的目的就是让冲突显式出现，不静默覆盖。博客与写作台源码分别存放在两个仓库，更新两边后再发布。

内容保存采用版本比对和同目录原子替换，临近替换时再次检查；这是乐观并发保护，
不等于任意外部编辑器都遵守的跨进程锁。同一篇内容请避免两个工具同时保存，发生冲突先复制草稿再合并。

需要 **Windows** 与 **Python 3.10+**（含 `pywebview` 与 `pythonnet`，
两者都是纯 pip 包，不需要 Node、不需要系统级安装器）。

```bash
git clone <this-repo> blog-desk
cd blog-desk

# 建议装进虚拟环境
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt      # 如果仓库里没有，用下面这行
pip install pywebview pythonnet Pillow PyYAML

# ★★ venv **不要建在本仓库里**（2026-10-15 实测，见 build-app.py 的 preflight）：
#   本仓库所在的 D:\ 整棵树挂着 `Mandatory Label\Low Mandatory Level` 低完整性标签，
#   在这里建的 .venv 会**继承**这个标签 ⇒ 低完整性进程写不进
#   %LOCALAPPDATA%\Programs\，装到一半 Permission denied。
#   ⇒ 建在工作区外面，例如：
#   python -m venv "$env:LOCALAPPDATA\Programs\写作台-build-venv"
#
# ★★ `Pillow` 与 `PyYAML` 别漏（2026-10-15 实测）：`lib/images.py` 顶层
#   `from PIL import Image`、`lib/content.py` 顶层 `import yaml`，缺了照样能"构建成功"，
#   只是产出一个双击就崩的 exe。现在 `build-app.py` 会在跑 PyInstaller **之前**
#   从源码里扫出依赖并逐个 import，缺了就直接停下 —— 但第一次装还是按上面那行装全。

# 复制配置模板
copy config.example.json config.json
```

然后**双击 `写作台.bat`**（无控制台窗口），或在设置界面里填两项：

| 配置项 | 说明 |
|---|---|
| `repo` | 你的博客仓库路径，就是那个含 `content/` 的目录 |
| `admin_token` | 站点环境变量 `ADMIN_TOKEN` 的同一个值。**不填则留言管理不可用** |

也可以命令行覆盖仓库路径：

```bash
python app.py --repo "D:\path\to\your\blog-repo"
```

## 配置

配置文件是 `config.json`（放在程序目录旁边，不在源码目录）。仓库里只提供
`config.example.json` 模板。

| 键 | 默认 | 说明 |
|---|---|---|
| `repo` | 空 | 博客仓库路径。留空时启动器会明确提示去设置里填 |
| `site` | `https://northcedar.top` | 站点根地址，用来拼管理接口的 URL |
| `admin_token` | 空 | 站点 `ADMIN_TOKEN` 的同一个值。**绝不要提交进版本库** |
| `max_kb` | 300 | 上传图片的体积上限 |
| `window` | 1180×760 | 窗口尺寸 |

也可以用环境变量 `BLOG_REPO` 指定仓库路径（优先级：命令行 `--repo` >
`config.json` > `BLOG_REPO` > 空）。

## 密钥怎么处理

**密钥只落本机磁盘，不回传给页面。** 界面只知道「配没配」（`has_token`），
不知道值 —— `lib/config.py` 的 `public_config()` 负责把它从返回结构里摘掉。

`.gitignore` 已经把 `config.json` 挡在版本库之外。如果你是在别处 clone 的
这份代码，**第一步永远是先 `copy config.example.json config.json`**。

## 打包成 exe（可选）

```bash
pip install pyinstaller
python build-app.py
```

产物装到 `%LOCALAPPDATA%\Programs\写作台\`，会**自动跑一次真实的服务自检**
（起服务、打 `/api/health`、拿到 200 才算过）。没过的会直说"打出来了但起不来"，
并返回非 0 —— "文件在"和"起得来"是两件事。

### 升级是"装到新目录 → 自检 → 切换"，不是覆盖

打包**不往现有安装目录里覆盖**，而是：

```
构建产物 → 写作台-新（全新目录，零残留）
        → 对新目录真跑一次自检
        → 过了才切换：写作台 → 写作台-旧，写作台-新 → 写作台
        → 旧的进回收站
```

为什么（2026-10-15 实测）：原来的 `copytree(dirs_exist_ok=True)` 只覆盖、
**不删除**，于是安装目录变成「新构建 ∪ 旧残留」。量了一次 —— 残留
**54 个文件 / 52.75 MB**，占安装目录（91 MB）的一半以上，其中混着
**cp313** 的 numpy 二进制（构建用的是 cp314）—— 永远加载不了的死重；
还有 5 个 0 字节空壳文件，其中一个 `_internal/PIL` 冒充成"已经装好了"，
把「没装 Pillow」这件事**遮住**，直到双击才报 `unknown location`。

换掉之后同一台机器上：177 文件 / 90.64 MB → **126 文件 / 37.9 MB**，
cp313 二进制 0 个。安装目录与构建产物**逐文件一一对应**，遮蔽没有生长的余地。

中途任何一步没过，**现有安装原封不动**：自检不过就把 `写作台-新` 收回回收站；
切换第二步失败会自动把 `写作台-旧` 改回 `写作台`。升级失败不会把你留在
"没有应用"的状态。

`config.json` 与运行日志会从旧安装带过来（新建目录天生没有它们）。

## 测试

`_t1.py` … `_t10.py` 是逐组的能力测试，`_mutate_t*.py` 是对应的变异验证
（故意改坏生产代码，确认测试真的会红）。跑法：

```bash
python _t5.py
python _mutate_t5.py
```

变异验证的意义：只有"把代码改坏、测试确实变红"才能证明那条测试真的在守这个
不变量。全绿但改坏了照样还绿，等于没测。

## 目录

```
app.py            启动器（pywebview 窗口、命令行参数、自检模式）
server.py         本地 HTTP 服务 + JSON API（127.0.0.1，只监听回环）
lib/config.py     配置读写；public_config() 负责不给页面回传密钥
lib/content.py    文章/页面/标签的读写，含 content-model §4 的同步
lib/gitops.py     普通 Git 发布、远端关系与内容范围检查（旧 API 工具不参与发布）
lib/images.py     图片转码与体积控制
lib/proc.py       外部进程调用
ui/               界面（原生 HTML/CSS/JS，无构建步骤、无 CDN）
build-app.py      PyInstaller 打包 + 装到新目录自检 + 切换 + 回收旧版
make_shortcut.py  桌面/开始菜单快捷方式
make_readme.py    把说明.source.txt 转成 GBK 的 说明.txt
```

## 安全边界

- 服务只绑 `127.0.0.1`，不对外网开放；没有鉴权，靠"只在本机跑"这一条成立。
  **不要用 `--port` 之类的方式把它暴露到公网。**
- 仓库路径相关的写操作都限制在 `repo` 目录内（`/api/reveal` 会拒绝仓库外路径）。
- 界面渲染用户输入时统一走 `esc()` 转义。

## 许可

代码部分 MIT，见 [LICENSE](LICENSE)。
「北杉札记」站点的文章内容与美术素材不在此授权范围内。
