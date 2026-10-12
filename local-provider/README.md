# 造物本地生成服务

独立的本地图片与视频 API 服务，桌面客户端名称为「造物生成服务」，卡密项目标识为 `zaowu`。当前实现包含：

- 启动首屏输入 `zaowu` 卡密，验证通过后直接进入管理台，可在服务设置中生成造物连接 Token；
- Wan 管理页直接展示单一账号池，不再提供模型和生成测试选项卡；支持批量导入、余额核查和每日/每周任务领取，账号池内直接提供核查全部、每日领取、每周领取、活动报名、兑换码核销、单账号领取、任务刷新和失效清理按钮，操作完成后自动刷新余额、状态和任务摘要；请求头、成长中心读取、Canvas 项目节点保存、任务提交、轮询和结果下载按 `custom-video-wan` 的 Canvas 协议适配；
- Wan 视频任务提交、上游轮询、结果下载和任务恢复；
- Q4 / Claw 视频和图片生成 provider 共用一套 Cookie 账号池；视频积分、图片积分、Q4 免费次数、Claw 工作区状态和图片试用次数分别保存，支持批量导入、并发核查、登录失效与访问受阻识别、失效清理、模型能力查询、任务提交、轮询和结果下载；
- Wan 管理动作包含余额核查、每日/每周任务、活动报名、兑换码、三项免费体验、失效账号 API 登录和独立浏览器成长中心；批量领取最多并发处理 6 个账号，领取后复核任务并等待钱包到账，失败与待核实结果显示在账号行中；Claw 管理动作包含固定测试消息；
- 本机监听与公开 HTTPS 参考素材校验。

`custom-image`、`custom-video-wan` 仅用于协议参考；本目录不导入 ZZDH，也不加载插件源码或 `.pyd`。

任务接口按生成渠道映射到实际账号池：Q4、Claw 和图片使用 `vidu-cookie`，再按视频、图片或通用用途筛选。图片轮询按图片客户端协议调用，下载重试保留原任务渠道且不重新提交；某个任务查询或下载中断后，队列继续处理后续任务。图片下载按真实内容返回格式与后缀，视频使用 `.mp4`。

账号从 Wan 插件导出的 `.vault` 或旧版 `.json` 文件批量导入。`.vault` 使用
`WANVLT01` 协议解密，默认读取宿主机
`D:\ZZDH\_internal\plugins\video_plugins\custom-video-wan\account_vault_private.pem`；
也可以通过 `ZAOWU_PROVIDER_ACCOUNT_PRIVATE_KEY` 指定私钥。导入后令牌只以 Windows
DPAPI 加密形式保存在本地数据库，原始账号文件不会被修改或上传。导入文件包含登录邮箱时也会加密保存，供失效账号 API 登录使用；本次登录密码不会保存，提交后清空输入框。历史账号没有邮箱时仍可核查和生成，只是不能使用 API 登录恢复。

Wan 批量导入使用中文选择按钮，显示文件数量及原始文件名。导入失败逐文件显示原因，成功文件从选择列表移除，失败文件保留供重试；导入后自动刷新账号表格。账号 JSON 仍按插件的 UTF-8 格式读取，不猜测其他编码或改写账号内容。

## 本地启动

### Windows 客户端

安装 `desktop/release/zaowu-provider-setup-0.1.4.exe` 后，首次打开「造物生成服务」输入有效卡密即可管理账号和运行设置，不需要管理员密码。
客户端内置 Python 服务与管理页面，无需另外安装 Python 或启动命令行。

关闭管理窗口会隐藏到托盘，生成 API 继续运行；托盘菜单可以重新打开界面或退出服务。
造物启动时会后台唤起已经安装的生成服务；设置中的「本地生成服务」提供连接状态、模型同步和打开管理客户端入口。
首次输入的卡密使用 Windows DPAPI 加密保存；后续启动自动在线验证并恢复管理登录，会话到期时也可自动恢复。
卡密失效、过期、凭据无法解密或网络验证失败时显示输入界面和错误，不自动跳过验证。
主动点击「退出」会暂停自动登录，重新手动输入有效卡密后恢复自动登录。卡密和账号管理均在独立客户端完成。

账号、授权、任务数据库和参考图继续使用原 `%LOCALAPPDATA%\ZaowuProvider` 数据目录。
生成结果统一写入造物「设置 → 文件存储」选择的位置下的 `生成结果`，造物素材写入同一位置下的 `项目素材`。默认根目录为系统文档目录内的 `造物文件`；两端读取 `%LOCALAPPDATA%\Zaowu\storage.json`，中间客户端通用设置显示目录并支持打开。
更换位置后点击造物中的「重启并应用」，活动任务期间拒绝重启。启动时复制并校验历史文件，保留原目录备份，成功后更新任务结果路径；同名内容冲突会停止迁移。覆盖安装和卸载不删除应用数据；数据库、卡密和账号不随文件目录迁移。
安装程序在当前用户注册表 `Software\Zaowu\Provider` 保存安装位置，供造物发现客户端。

### 源码运行

```powershell
cd local-provider
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
cd ui
bun install
bun run build
cd ..
.\.venv\Scripts\python run.py
```

管理台：`http://127.0.0.1:8787`

启动后会自动打开浏览器；如需只启动服务，可设置
`$env:ZAOWU_PROVIDER_OPEN_BROWSER = "0"`。

源码运行可使用 `ZAOWU_PROVIDER_RESULT_DIR` 指定独立结果目录；必须是绝对路径。留空沿用已生效位置，首次使用服务数据根下的 `results`。此项不改变 `ZAOWU_PROVIDER_DATA_DIR`，也不迁移账号或授权数据库。

正式模式默认连接 `https://license.jiaenxinxi.dpdns.org`，按 `zaowu` 项目校验卡密、设备和服务器签名，并运行授权心跳。需要更换授权服务时设置：

```powershell
$env:ZAOWU_PROVIDER_LICENSE_SERVER_URL = "https://LICENSE_SERVER"
$env:ZAOWU_PROVIDER_LICENSE_PUBLIC_KEY_PEM = @"
-----BEGIN PUBLIC KEY-----
PUBLIC_KEY
-----END PUBLIC KEY-----
"@
```

仅本地开发可显式设置：

```powershell
$env:ZAOWU_PROVIDER_DEV_MODE = "1"
$env:ZAOWU_PROVIDER_ALLOW_DEV_MODE = "1"
```

开发模式只用于本机联调，不应放进交付包。

管理台只需卡密，不设置管理员密码。卡密验证成功后自动签发管理会话，默认有效 24 小时，数据库只保存令牌哈希及到期时间；服务重启不清除有效会话，但仍需重新验证服务授权。浏览器令牌保存在当前标签页的 `sessionStorage` 中。普通浏览器退出或会话过期后需要重新输入卡密；独立客户端会通过本机私有进程管道读取已加密卡密，调用原激活接口在线校验后取得新会话，不将卡密交给页面或写入命令行和日志。主动退出后暂停自动恢复；再次验证卡密会替换旧会话。授权失效时，有效期内的管理会话同样无法访问管理 API。`ZAOWU_PROVIDER_ADMIN_SESSION_HOURS` 可设置为 1–168 小时。

## 当前接入边界

- 卡密已经对齐参考插件的在线 `/api/verify`、RSA 响应验签、设备绑定、`/api/heartbeat` 和 15 分钟断网宽限；服务器项目标识固定为 `zaowu`。
- 如果卡密服务器不是参考插件使用的地址，需要同时配置服务地址和对应的 RSA 公钥，不能只修改项目标识。
- 造物桌面端已接入提交、查询和结果回传协议，隔离环境验证使用固定媒体，不等同于真实上游生成验收。
- 当前任务仅支持单个结果 URL 和文件，拒绝多图数量请求；批量多图交付仍未实现。
- 生成账号选择使用各渠道自己的最低积分设置；实时额度、报价与试用次数完整校验仍需进一步对照插件，管理页核查成功不能证明生成时余额足够。
- Cookie 上游请求目前关闭 TLS 证书验证，结果下载仍复用带登录 Cookie 的 Session，需补证书验证与下载凭据隔离；参考图签名上传已改为独立且验证证书的媒体会话。
- 查询或下载中断后取消任务的状态收敛，以及本地参考文件错误的提交状态分类，仍待修复；提交状态不确定的任务继续禁止自动重放。
- Wan 生成 API 支持本机及内联 PNG/JPEG/WebP 参考图，按预签名 OSS 协议上传，并按设置压缩图片；上传使用不带账号登录头的媒体会话。造物当前 Wan 协议仍提交公开 HTTPS 地址，宿主本机素材到 Wan 上传链路仍需单独联调。
- Q4 的防护策略可能要求浏览器指纹或浏览器辅助；当前 adapter 先走独立 HTTP 协议，遇到上游拒绝时需要补浏览器传输层。
- Q4 / Claw / 图片不复用 Wan 的积分领取接口，余额核查读取 `/credit/v1/credits/me`，图片试用读取 `/credit/v1/trials/me/all`；图片积分小号通过 `credit_profile=image` 区分。Wan、Q4 和 Claw 提供独立 Chrome / Edge 账号窗口，凭据通过子进程标准输入传递，不进入命令行、页面地址或日志；图片渠道暂没有账号窗口。
- Cookie 账号解密后先展开存储记录的 `cookies` 字段，再发送 JWT、SSUID 和 WAF Token；视频与图片共用核查传输层。Q4 免费次数从试用接口的模型 `3.4` 条目按分辨率读取，不预填默认次数。余额查询成功后，Claw 或试用接口失败只记录部分核查错误，不覆盖已确认的余额和登录状态。余额接口 401 且没有浏览器验证标志时记录登录失效；403 或浏览器验证响应记录核查失败，不作为失效清理依据。

## Q4 / Claw 账号池

Q4、Claw 和图片页面直接显示同一张 Cookie 账号表，保留视频渠道切换，移除模型与生成测试选项卡。参考 `custom-video/ui/index.html`、`custom-video/main.py` 和 `custom-image/ui/index.html` 的操作，视频提供重新加载、核查全部余量、失效/耗尽清理、重置状态、单账号核查、登录、停用和删除；Claw 额外提供单账号与批量固定消息按钮；图片提供重新加载、核查积分、清理已耗尽和清理失效账号。表格按渠道显示 Q4 免费次数、Claw 工作区/消息状态或图片积分。导入使用中文文件选择界面并显示逐文件失败原因。

运行参数统一进入服务设置，图片与视频独立保存。Claw 发送并发为 0 时表示本批全部账号并发，单批最多 200 个账号；生成模型、画面比例、时长和分辨率由造物请求传入。

| 接口 | 功能 |
| --- | --- |
| `PUT /api/admin/video-settings` | 校验并保存 Q4 / Claw 视频参数 |
| `PUT /api/admin/image-settings` | 校验并保存独立图片参数 |
| `POST /api/admin/accounts/check` | `account_ids` 限定账号，`concurrency` 为 1–50；视频核查同时读取 Q4 余量与 Claw 工作区 |
| `POST /api/admin/claw/test-message` | 对 `account_id` 发送固定「你好」消息；先核查实际余额，要求大于 180 分 |
| `POST /api/admin/claw/test-messages` | 对非空 `account_ids` 批量执行；返回成功、跳过、失败数量及逐账号结果 |
| `POST /api/admin/cookie-accounts/reset` | 清空核查状态和 Q4 余量缓存，保留账号凭据、积分值、消息回执及待核实状态；忙碌账号跳过 |
| `POST /api/admin/image-accounts/clean-exhausted` | 清理已核查且图片积分低于 50、没有剩余图片试用次数的图片小号；通用账号、忙碌账号和仍有试用次数的账号保留 |
| `POST /api/admin/cookie-accounts/{id}/browser?channel=q4-video` | 在独立 Chrome / Edge 会话注入该账号 Cookie 并打开 Q4 页面；`claw-video` 打开 Claw 页面 |
| `POST /api/admin/accounts/clean-invalid?provider=vidu-cookie&include_exhausted=true` | 清理凭证缺失、登录失效及已核查为零分的视频小号；零分通用/图片账号保留，忙碌账号跳过，清理记录加密归档 |

已成功发送的消息不重复提交；提交前保存 `sending`，提交过程失败保留 `unknown`，等待人工核实，不盲目重发。普通核查和重置不会擦除这些记录。账号页「登录」打开独立浏览器供人工处理，更新登录后需重新导入 Cookie。

Q4 可在服务设置中启用常驻 Chrome / Edge 页面辅助，按账号隔离窗口并通过页面提交、查询任务；窗口保留数量可配置，服务退出时关闭其拥有的辅助窗口。Claw 继续使用 API。页面遇到验证挑战时需人工处理，浏览器辅助不保证上游一定接受请求；真实上游仍待验收。

## 独立渠道运行设置

服务设置分为「通用」「Wan」「Q4 / Claw」「图片」四个选项卡，账号页不再重复编辑运行配置。设置采用独立数据库键，升级时一次性把旧 Cookie 参数复制到视频和图片，之后互不影响；Wan 原参数保留。

- 通用：更换总卡密、查看本机地址、重新生成连接令牌。
- Wan：核查／登录／领取并发，最低积分，总任务与请求／上传／提交／下载超时、轮询间隔、安全读取重试、上传前图片压缩、代理和订阅。
- Q4 / Claw：核查和消息并发、最低积分、总任务／生成等待／上传／提交／查询／下载超时、轮询、Q4 浏览器辅助及窗口上限、辅助重启、代理和订阅。
- 图片：核查并发、最低积分、独立总任务／上传／提交／查询／下载超时和轮询、代理和订阅。

模型、比例、时长、分辨率与数量继续由造物请求决定，导入目录改为批量导入文件；领取活动、兑换码和体验任务仍在 Wan 账号页显式执行，不由设置保存触发。

代理支持 HTTP / HTTPS / SOCKS5 节点及包含这些节点的 HTTPS 订阅（URI 列表、Base64 列表或 Clash YAML）。暂不支持 SS / VMess / VLESS。订阅限制为 1 MB，拒绝私网订阅地址和重定向；节点按账号稳定分配，缓存五分钟。代理地址使用 Windows DPAPI 加密，读取接口仅返回是否已配置，省略代理字段表示保留原配置，显式空字符串表示清除。

浏览器辅助的认证代理通过调试管道提供用户名和密码，不写入命令行；其认证 SOCKS5 模式暂不支持。API 支持认证 SOCKS5。配置代理不可用时返回失败，不在提交状态不确定时改用直连重发。

| 接口 | 功能 |
| --- | --- |
| `GET /api/admin/runtime-settings/{kind}` | 读取 `wan/video/image` 配置，不返回代理原文 |
| `PUT /api/admin/wan-settings` | 保存 Wan 配置 |
| `PUT /api/admin/video-settings` | 保存 Q4 / Claw 配置 |
| `PUT /api/admin/image-settings` | 保存图片配置 |
| `POST /api/admin/video-browser/restart` | 关闭空闲辅助窗口，下次 Q4 任务重新创建；运行中拒绝重启 |

总任务超时约束准备、提交、查询和下载请求；视频额外使用生成等待超时。提交可能已被上游接受的超时仍保留待核实，不自动重发。

## Wan 账号池接口

管理接口均需要有效卡密会话；表格刷新读取 `/api/admin/overview`，不会在渲染时领取任务或提交体验视频。

| 接口 | 功能 |
| --- | --- |
| `POST /api/admin/accounts/check` | 单个或批量核查账号余额 |
| `GET /api/admin/accounts/{id}/rewards` | 读取每日/每周任务、活动、体验余量与已提交体验的状态，保存摘要供表格使用 |
| `POST /api/admin/accounts/{id}/claim` | 单账号执行指定类别任务，或显式指定活动、兑换码、体验任务 |
| `POST /api/admin/wan/claim` | 批量执行；`categories` 为 `daily/weekly` 数组，活动用 `include_activities`，体验用 `experience_ids`，兑换用 `redemption_code` |
| `POST /api/admin/wan/login` | 用本次密码恢复失效账号，随后核查余额；`account_ids` 可限定账号，`concurrency` 为 1–20 |
| `POST /api/admin/accounts/{id}/browser` | 打开该 Wan 账号的独立成长中心窗口；失效账号需先完成 API 登录 |
| `POST /api/admin/accounts/clean-invalid?provider=wan-video` | 只清理 Wan 登录失效账号；有未结束任务的账号跳过，已清理记录保留加密归档 |

任务领取以网站返回的周期标识去重；提交超时后读取权威状态，确认不了的记录保留 `unknown`，不会盲目重放 POST。兑换码只持久化摘要，不保存原文。免费体验使用随服务保存的三份固定模板定义，先核查剩余额度，提交前记录尝试，接受后保存任务编号；后续刷新只查询已有任务。完成后再次读取并领取网站已完成的每日/每周奖励，体验任务本身不等同于积分到账。

当前 Wan 新功能已通过隔离数据库与模拟上游测试，管理页面通过真实组件的浏览器交互检查；真实 Wan 登录、领取、免费体验和浏览器窗口仍需实际账号验收。

后续仍需验收真实 Wan 账号、Q4 浏览器传输层、任务取消与恢复，以及真实视频生成和预览。

会话回归使用隔离的临时数据库，不请求真实上游：

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```
## 桌面生成连接

造物桌面端设置中的「本地生成服务」可以把本机服务配置为图片、Q4、Claw、Wan 四个生成渠道。
点击「连接并同步模型」后从 `/api/v1/models` 读取模型能力，按真实模型限制配置比例、时长、分辨率和参考图数量。
管理会话只用于账号操作，生成使用独立连接令牌；桌面端通过系统加密保存令牌，启动后自动恢复。
连接时造物主进程通过已安装服务的私有标准输出管道读取系统加密保存的连接凭据；
读取前检查服务已经验证有效卡密且未处于开发模式。凭据不进入命令行、日志或浏览器持久化配置。
首次连接尚无令牌时才创建令牌；旧令牌只有哈希而无加密凭据时不会自动轮换，需要在管理客户端主动重新生成。
已有加密连接在服务暂时不可用时保留，重新启动服务后可再次同步模型。

生成协议插件为 `zaowu-local`，提交、查询和结果下载分别使用 `/api/v1/tasks`、
`/api/v1/tasks/{id}`、`/api/v1/tasks/{id}/result`。创建携带幂等键。
提交请求执行期间状态为 `submitting`，造物继续等待；请求异常或提交期间服务重启才进入
`submission_unknown`，禁止自动重放。已有上游编号的失败图片和视频可以在造物任务中心
手动查询并拉回结果，不重新生成。
图片与 Q4 支持 PNG/JPEG/WebP 内联参考图（每张最多 20 MB）和原有本机文件路径。
图片与 Q4 的参考图按网页上传协议转换为有尺寸信息的 WebP，初始化携带 `scene` 和宽高元数据，上传完成使用 `PUT`；签名媒体上传不携带账号 Cookie。参考图生成参数包含完整选区，Q4 分别上传主图和源图。图生视频的输出比例可能跟随参考图，不能仅按请求的比例断言交付画幅。
当前每个任务仅交付一个结果，拒绝多图数量，避免消耗多张积分却只返回一张。
造物当前 Wan 渠道仍发送公开 HTTPS 参考图；本地服务 API 也接受内联图片并上传，宿主直接传入本机参考图的完整链路仍待联调。

## 打包客户端

使用 Windows Python 3.12、Node.js 和 Bun 构建：

```powershell
cd local-provider
python -m pip install -r requirements-build.txt
cd ui
bun install --frozen-lockfile
cd ..
cd desktop
npm ci --ignore-scripts --no-audit --no-fund
cd ..
.\build-desktop.ps1
```

构建脚本先编译管理页面，再用 PyInstaller 收集服务、页面与 Wan 体验任务配置，最后生成 Electron NSIS 安装包。
生成文件位于 `desktop/release/`。交付客户端启动时禁用开发模式，默认只监听 `127.0.0.1:8787`。

真实生成验收已覆盖六个图片模型的参考图生图、Q4 参考图五秒视频，以及 Wan 3.0 Video、HappyHorse 1.1 R2V、MiniMax H3 各一次参考图五秒视频的生成、完整解码和鉴权下载。Wan 复测通过本地服务 API 发起，覆盖本机参考图上传与统一结果目录保存；造物画布中的 Wan 交互仍需单独验收。Wan 适配器尚未向上游传递分辨率参数，三款实测分别输出 1920×1080、1920×1080、864×480，不能视为 720p 设置已生效。Claw 当前本地适配器未透传参考图和生成时长，补充文本请求返回服务器错误并保留待核实状态，不能视为已支持参考图视频。
