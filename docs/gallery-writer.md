# 本地相册编辑（2026-11-01）

入口位于侧栏「相册编辑」；原有「相册照片」工具保留。新建时立即建立空草稿，标识支持中文，之后固定。可批量上传、填写图注，拖动或用上下按钮调整顺序，单独选择封面。移除项只解除引用，原图保留。

「封面整套编排」提供三套真实缩略图；「进一步调整」分别设置构图、翻页动作、笔势、笔触。「重新推荐」是明确操作；无图且尚未手动配置时不固定推荐，第一次有真实封面保存后推荐和封面持久化，换序不会换封面。空册主动选择的编排仍会保存。预览复用站点正式模块和 CSS，可切桌面/手机、亮/暗、播放一次及快速往返。只读预览不写内容文件。

「保存草稿 / 修改」允许空册及暂缺图注。正式发布准备要求标题、有效日期、至少一张真实图片，每图图注非空，封面属于本册。保存按完整文件版本检查，冲突返回 409 并保留输入。未知 YAML 字段、未改文本和正文及其混合行尾保留。

「准备本册发布」先保存，再产生不可变 Git 树，列出本册正文和实际引用图片。准备动作不会修改本地 draft、HEAD、真实暂存区，不提交或推送。快照中 draft 变为 false；未引用的新原图不会纳入，历史上已经入库的原图继续保留 HEAD 原字节。

「确认发布本册」只在用户点击并确认后执行：要求本地 main 和远端 main 完全一致，在独立临时 worktree 检出这棵树，执行现有 npm run verify，确认校验没有改写源码/内容，普通 git commit（保留 hooks），再次核对提交树和父提交，然后普通推送固定 SHA，回读远端提交。其他文章的未提交/已暂存内容均不会纳入，源工作区、HEAD、索引保持原状。发布成功后需先处理本地其他改动，再同步远端 main；界面会提示这一点。校验或快照变化时不推送；推送失败也不会改写源工作区。

本次未替换安装版、未重启现有窗口、未改本机配置、未推送测试相册。体验源码版时，在新终端使用：

```powershell
Set-Location D:/router/work/blog-desk-visual-writer
& 'C:/Users/A2817/AppData/Local/Programs/写作台-build-venv/Scripts/python.exe' app.py --browser --repo D:/router/work/blog-cloud-writer
```

该命令启动新服务；现有写作台窗口保持原状。首次体验建议给 `--repo` 指定另一个测试博客副本。若只需服务测试，用 `--no-open --port 18742` 替代 `--browser`。构建安装包和替换安装版需要另行执行，本次没有执行。

共享预览的固定镜像清单在 `sync-album-preview.py`，通过 `python sync-album-preview.py <站点源码目录>` 同步。`_test_gallery.py` 对照站点源码逐字节检验镜像及 Python/JavaScript 推荐结果。打包脚本现有 `--add-data ui;ui` 会包含这些静态模块与字体。

回归：`python _test_gallery.py`、`node _test_gallery.cjs`、`python _test_forge.py`、`node _test_forge.cjs`、`python _test_publish_snapshot.py`、`python _test_publish_safety.py`。Git 发布测试使用 work 下的临时仓库和本地 bare remote；不会联网推送正式站。
