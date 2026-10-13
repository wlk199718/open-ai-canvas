# 造物桌面端交接说明

## 在做什么

把开源项目 open-ai-canvas（原名「影策」，上游仓库 ddcat-ai/open-ai-canvas）做成 Windows 桌面应用，并改名为「造物」(zaowu)。本分支已合并上游至 `74661198`（源码版本 `v1.6.7`）；上游版权、许可证、Go module 与插件协议标识继续保留。

方案：Electron 44 外壳 + Go 后端作为子进程，前端、Agent 运行时、官方插件包、LGPL ffmpeg 一起打进安装包。工作台与 SQLite 在本机运行；模型生成仍调用在线供应商，生成服务卡密仍需在线验证。

当前桌面包版本：造物与造物生成服务均为 `0.1.5`，已验证实际覆盖安装、本地文件定位和另存为，原有数据保留。本次上游合并只更新源码和验证构建，尚未发布新的安装包；数据库隔离副本已完成结构 50 → 54 迁移校验，在用客户端数据库仍为 50。详细范围见待测试清单；本轮未提交计费生成请求。

## 代码位置

源码分别位于 `desktop/`、`local-provider/`、`desktop-shared/`，桌面适配同时涉及 `backend/` 和 `web/`。安装包、构建缓存、运行数据库、导入账号和验证产物均被 Git 忽略。

新增：
- `desktop/`：Electron 工程
  - `src/main.js`：窗口、单实例、旧数据目录 `%APPDATA%\影策` 自动迁移到新目录
  - `src/backend.js`：首次选择并持久化端口，升级沿用已有 IndexedDB 地址；设置环境变量、拉起 `zaowu-backend.exe`，stdin 关闭即后端退出
  - `src/local-account.js`：自动创建本地管理员，凭据用 Electron safeStorage 加密保存
  - `scripts/prepare-bundle.mjs`：收集后端、前端、Agent 运行时、插件包、ffmpeg 到 `desktop/bundle`
  - `scripts/make-icon.py`、`build/icon.ico|png`：图标
- `local-provider/`：独立 Python API、React 管理台和 Electron 客户端，管理 Wan 与 Cookie 账号池、卡密、运行参数和生成任务
- `desktop-shared/storage.cjs`：两端共享的目录配置、文件迁移与 SHA-256 校验
- `backend/cmd/server/webstatic.go`：`CANVAS_WEB_DIR` 托管前端 SPA
- `backend/cmd/server/desktop_ffmpeg.go`：桌面版前端的视频合成走本机 ffmpeg（`POST /api/desktop/ffmpeg/exec`，同源 + 会话校验 + 参数沙箱）
- `backend/internal/app/h264_encoder.go`：自动选择可用的 H.264 编码器（LGPL 版 ffmpeg 没有 libx264）
- `web/src/lib/canvas/desktop-ffmpeg.ts`、`ffmpeg-wasm-loader.ts`：桌面版不打包 GPL 的 ffmpeg.wasm

修改（主要的）：
- `backend/internal/database/database.go`：改用纯 Go 的 glebarez/sqlite，`CGO_ENABLED=0` 也能编译
- `backend/cmd/server/main.go`：stdin EOF 优雅退出等桌面开关
- `backend/internal/app/provider_media_hydration.go`、`resource.go`：需要公网素材地址时，提示去「设置 → 我的对象存储」配置
- 品牌改名：影策 → 造物，共 25 个文件。Go module `yingce/backend`、`.yingce-plugin` 扩展名、`yingce.plugin/v2`、localStorage 的 `yingce:*` 键**故意没改**（兼容性）

## 打包步骤

依赖：Go 1.25+、Bun、Node.js 和 npm；生成服务另需 Python 3.12+。ffmpeg 使用 LGPL 共享库版本，通过 `FFMPEG_DIR` 指定包含 ffmpeg、ffprobe 和动态库的目录；不要把机器路径写入源码。

```powershell
# 在仓库根目录开始。
Set-Location backend
$env:CGO_ENABLED = "0"
go build -trimpath -ldflags "-s -w" -o ../desktop-build/zaowu-backend.exe ./cmd/server

Set-Location ../web
bun install --frozen-lockfile
$env:VITE_DESKTOP = "1"
bun run build
Remove-Item Env:VITE_DESKTOP

Set-Location ../desktop
npm ci
$env:FFMPEG_DIR = "FFMPEG_DIRECTORY"
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm run dist
```

产物：`desktop/release/zaowu-setup-0.1.5.exe`。重新发布合并后的版本前应提升包版本，避免与已分发的旧包混淆。`FFMPEG_DIRECTORY` 是待替换的 LGPL ffmpeg 目录。开发调试用 `npm start`，先完成后端、前端构建及 `prepare-bundle`。生成服务单独按 [README](../local-provider/README.md) 构建；安装两个客户端后，造物会发现并启动本地生成服务。

## 注意事项

- 如果 shell 环境里有 `ELECTRON_RUN_AS_NODE=1`，Electron 窗口起不来，启动前先 unset。
- 后端 82 个旧的 `*_test.go` 仍然 import `gorm.io/driver/sqlite`（依赖 CGO）。不装 gcc 的话，用 `go test -overlay` 把它替换成 glebarez 再跑。`TestHydrate*` 里有 3 个用例在原始代码上就失败，不是这次改出来的。
- 用 Node spawn ffmpeg 时 stdin 必须设为 `ignore`，否则会报 EBUSY。
- 不需要 COOP/COEP：前端用的是单线程 ffmpeg core。
- Agent 看图在桌面端正常，本地资源会内联成 data URL。只有要求公网 URL 的视频协议（NewAPI 视频、Ark 视频、MiniMax 视频等）需要用户自己配置对象存储。
- 数据目录：`%APPDATA%\造物`。卸载时不删数据。
- 每次跑 prepare-bundle 会把旧 bundle 挪进 `desktop/.trash`，需要定期手动清理。
- 默认深色主题；文本模型使用「设置 → 个人渠道」填写地址和 API Key，再在「模型选择」设置默认文本模型。
- 图片和视频账号、卡密与供应商设置由生成服务客户端管理；造物「本地生成服务」负责连接与模型同步。
- 造物「设置 → 文件存储」管理共用保存根目录，两端读取 `%LOCALAPPDATA%\Zaowu\storage.json`；生成结果与项目素材分目录存放，账号和数据库位置不随之迁移。
- 上游数据库结构为 54，包含个人资料、头像、登录日志和独立工作台迁移。前后端需同时更新，升级前备份数据库；原 operations 保存、桌面自动登录、模型预设、本地生成连接和文件操作继续保留。
- 内置影视剧作工坊在 `.gitattributes` 中固定 LF 换行，确保 Windows 检出与嵌入包的 Python 文件哈希同上游一致。
- 上游新增生图、视频工作台入口默认关闭，仍受功能开放设置控制；本次合并没有自动开放入口或填充工具目录。

## 没做完 / 下一步

- 卸载流程还没测，需要手动测。
- 安装包没有代码签名，Windows 会弹 SmartScreen 警告。
- 生成服务已按参考插件协议实现独立适配器，不调用 ZZDH 宿主，不加载插件 `.pyd`。Wan 导入加密 `.vault` 需要用户提供相应私钥，仓库与安装包不包含私钥或真实账号。
- 六款图片、Q4 和三款 Wan 参考图视频已完成真实生成验证；Wan 三款各请求 5 秒并通过结果下载与全片解码。Claw 参考图、Wan 分辨率透传、完整额度检查和造物画布中的 Wan 交互仍待验收或补齐。
- 当前验证范围与未完成事项以 [待测试](../docs/content/docs/progress/pending-test.mdx) 为准。[旧版检查记录](REVIEW.md) 对应历史 `0.1.0` 包，不代表后续版本全部问题仍存在或均已修复。
