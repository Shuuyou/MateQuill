# MateQuill · 场景有据

拍一张，说一句，把门店里的好故事讲给对的人。MateQuill 为华为门店销售员连接自然输入、门店选题、产品知识、图文创作与可编辑海报，覆盖电脑、手机和平板生态。

由 Shuuyou 与 6TDooo 面向浙客松华为产业赛道构建。复赛采用 Next.js 16 + React 19 前端、FastAPI 工作流与 PostgreSQL 18 企业资源库，连接代理总部、门店、店员与合作企业。工程结构见 [复赛工程说明](docs/SEMIFINAL_ENGINEERING.md)，身份、迁移与部署见 [多租户运行手册](docs/TENANCY_OPERATIONS.md)，设计依据见 [多门店方案](docs/MULTITENANCY_PROPOSAL_20261021.md)。初赛作品说明见 [作品说明书](docs/MateQuill-作品说明书.pdf)。

## 复赛产品能力

[观看复赛讲解视频](docs/demo/MateQuill-semifinal.mp4) · [创作能力验收](docs/SEMIFINAL_VALIDATION.md) · [企业与门店协作验收](docs/TENANCY_VALIDATION_20261021.md)

- **拍照、语音、文字三种入口**：上传场景照片或说出任务，确认转写，只在缺少关键信息时继续追问。
- **千店千面**：保存门店人群、常见任务与表达习惯；同一推广任务形成不同切角、叙事与视觉规划，再生成各店成稿。
- **新品持续入库**：官方产品资料预览、事实确认与持久化，型号、系统、原文、来源一起保存。
- **华为生态内容**：电脑、手机与平板围绕同一个任务组织内容，参数归属到设备，协同功能关联具体操作条件。
- **图文与海报交付**：正文、分镜、配图、修改与导出；海报支持文字、字体、颜色、位置、撤销重做及 PNG / SVG / JSON。
- **可维护的工作流**：类型化 API、分离的媒体／产品／门店模块、版本化创意连接器、独立审校、持久化记录和前后端 CI。
- **企业与门店协作**：真实账号、单次邀请、多店任职和工作区切换；成稿、素材、海报与导出关联所属企业和门店。
- **总部活动到本店成稿**：版本化活动包、分发领取、差异化创作、批注审核、人工发布确认与进度回收；独立合作企业通过定向邀请接收活动。
- **持久化任务与用量**：排队、幂等提交、租约与心跳、进程恢复、企业创作额度和审计；成员权限变化在任务执行与读取时重新核验。

## 来源口径更新（2026 年 10 月 18 日）

根据用户当日转述的出题评委更新：取消“不引用 ima 扣分”的规定，允许直接使用赛事知识库 ZIP，也允许从华为官方商城等渠道补充产品事实及宣传素材。这是用户提供的现场口径，尚未独立核验公开赛事公告。应用默认采用正式本地知识库模式，ima 保留为可选接入；独立演练仍明确标识。来源切换不免除型号、系统、限定条件、画面相关性和素材使用范围的检查。

## 启动

Python 3.11 或更新版本：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Linux 使用 `.venv/bin/python`。使用下方 Next.js 前端启动步骤打开工作台。首次使用先通过 `scripts/tenant_admin.py bootstrap` 创建企业管理员；PostgreSQL 生产库先运行迁移并使用受限应用角色。模型和 ima 配置由服务端环境注入，企业用户的“团队设置”管理成员与门店，不更改全平台连接。不要将真实密钥写入命令行参数、公开仓库或录屏。

| 环境变量 | 用途 |
| --- | --- |
| `IMA_OPENAPI_CLIENTID` | 已加入共享库的账号 Client ID |
| `IMA_OPENAPI_APIKEY` | ima API Key |
| `IMA_KB_NAME` | 默认指定共享库名称 |
| `IMA_KB_ID` | 已知库 ID，可跳过库名搜索；只供服务内部使用 |
| `LLM_BASE_URL` | 默认 `https://api.deepseek.com` |
| `LLM_MODEL` | 默认 `deepseek-flash`，按实际模型服务配置 |
| `LLM_API_KEY` | 模型 API Key |
| `IMAGE_BASE_URL` | OpenAI 兼容图像服务地址，包含 `/v1` |
| `IMAGE_API_KEY` | 图像服务密钥，仅服务端使用 |
| `IMAGE_MODEL` | 服务商提供的模型 ID，本轮实测 `gpt-image-2.5` |
| `IMAGE_REVIEW_MODEL` | 可选的多模态成品审稿模型，本轮实测 `gpt-4o` |
| `ALLOW_LOCAL_SETTINGS` | 本地为 `1`；服务器必须为 `0` |
| `MATEQUILL_DATABASE_URL` | 受限 PostgreSQL 应用角色连接；未配置时 SQLite 仅用于本地开发 |
| `MATEQUILL_MIGRATION_DATABASE_URL` | 独立迁移所有者连接，只供 Alembic 使用 |
| `MATEQUILL_ALLOWED_ORIGINS` | 浏览器工作台的完整 Origin 白名单 |
| `MATEQUILL_COOKIE_SECURE` | HTTPS 入口设为 `1` |
| `MATEQUILL_DAILY_CREATIONS` | 企业每日创作额度，默认 100 次；失败释放额度 |
| `MATEQUILL_BUSINESS_TIMEZONE` | 业务日时区，默认 `Asia/Shanghai` |
| `MATEQUILL_PUBLIC_URL` | 可选的固定 HTTPS 入口，配置后 Next 将其他入口重定向到此地址 |

变量配置模板为 `.env.example`。服务当前从进程环境读取；文件模板不会自动加载，以免误将本地文件当安全秘密管理服务。你可在进程启动器、systemd 受控环境或容器秘密管理中注入。

## 工作空间

界面延续 Mingcha 的暖纸、墨色字形与角色视觉语言。创作从一个统一输入区开始，顾客需求、产品和顾客信息保持可见。产品资料页可搜索实际目录并将型号带入当前任务；生成后进入文稿工作区，按实际结果提供创作思路、客户方案和资料检查。

当前标签页的草稿与导航按用户、企业、门店隔离保存在 sessionStorage；切换工作区重新加载所属草稿。企业资源、成稿、海报、任务和审核记录在数据库持久化，浏览器仅保存体验状态。会话使用 HttpOnly cookie；连接密钥不进入浏览器存储。

## 如何使用

1. 默认使用“本地知识库＋官方资料”，也可选择“ima 可选连接”或“独立演练”。本地知识库可走正式生成及审查流程，不再因没有 ima 引用被判为演练。独立演练始终保留演练标识。
2. 输入准确产品型号、目标人群和具体任务，选择购买阶段、内容目标及发布渠道。缺项会触发追问。
3. 运行后检查场景策略、正文事实标签、图片位置、来源和核验记录。错误或冲突会阻断最终交付。
4. 修改场景、平台或补充要求后重新生成。可导出 Markdown、HTML 或 JSON；草稿保留水印，被阻断任务不能导出为营销稿。

本地知识库和独立演练资料默认读取 `research/knowledge/华为PC全板块深度知识库_v7`。该目录已被 Git 忽略，不能将赛事资料整包公开。没有私有资料时演练目录为空，系统不会伪造参数。Docker 运行本地模式需将授权私有素材挂载到 `/app/research/knowledge`；ima 模式继续从网络读取商品依据。官方补充素材应保留来源网址、获取时间、具体型号和使用条件，不能凭官网域名跳过审核。

## 核心流程

```text
任务补全和型号确认
  → 场景与平台策略
  → 本地知识库及官方补充检索，或可选 ima 检索
  → 原文事实和图片来源账本
  → DeepSeek 图文与策略生成
  → 型号、数字、引用、条件、图源、合规硬检查
  → 独立模型语义审校
  → 最多一次修复及复核
  → 成稿或明确阻断原因
```

模型最多调用 4 次；平台规则由程序锁定。API 同时运行任务数限制为 2，设超时和每客户端限流。素材读取限制域名、公共 DNS 地址、TLS、大小、类型和跳转；带鉴权信息的原始 URL 不发给前端。公共结果只包含本次引用片段，不返回原文全库、完整密钥、内部媒体 ID 或本地路径。

平台范围：公众号 2–4 图、小红书 3–6 图、朋友圈 1–3 图、海报 1 图且不超过 3 个卖点。正文策略和表达应随场景和平台改变。图片可见画面已审阅时使用同字节 SHA256 记录；未命中记录的图片明确显示画面待审阅。标题、来源域名和文件名不能单独证明图文相关性。

## 可编辑图片海报

海报默认采用矢量构图与真实文字排版：理性编辑、字体海报、几何宣言三种不同构图，生图仅作为用户明确选择的辅助背景。

选择海报后，填写投放用途、画布尺寸、视觉方向与风格。支持竖版、方形、故事竖屏、横屏、A4 比例和自定义像素；A4 比例输出尚不等同印刷交付。

海报以独立图层保存真实产品图、标题、副标题、卖点、行动引导和必要限定。产品和草稿标记锁定；文字、位置、字号、字体族、字距、行距与颜色可编辑，支持保存、撤销和版本冲突检查。成品可导出指定尺寸 PNG、安全 SVG 及包含素材的 JSON 源文档；新企业海报保存在受租户权限约束的资源库。

背景可以使用品牌色设计，或调用已配置的图像服务生成。生成背景不会重新绘制产品主体，也不会伪装为实拍；服务返回尺寸与请求不同会按实际像素处理。模型列表是服务商返回的候选，不等于每个模型都能通过生图端点调用。

配置视觉审稿模型后，对真实渲染 PNG 做一次多模态审阅，可采纳一次经几何校验的非文字布局修复。错误、超时和未完成均保留；模型审阅不能认证产品型号、版权或营销效果。独立 OCR 逐字核对尚未完成，海报保留待发布复核的草稿水印。

部署 PNG 渲染需安装 Chromium 和中文字体。Linux 可执行 `python -m playwright install --with-deps chromium` 并安装 Noto CJK 字体。图像服务凭证仅进入服务端进程，前端不保存密钥；重启后需重新注入。

## ima 接入的边界

`search_knowledge` 的命中不是全文，也不保证返回图片字段。必须继续 `get_media_info` 并携带其返回的访问头读取原文。单个文件不可读应隔离，不能当作整个账户配额耗尽；目录不可用也不能直接等同搜索不可用。对权限、配额和接口失败保留实际业务码，不凭数字猜错误类型。

在可选 ima 模式中，已知库 ID 可以减少对库名搜索的依赖，本地目录元数据仅作定位和候选消歧；该模式只把从网络实际取回的同型号 P1 原文标为 ima 依据。本地正式模式则将附件原文标为 local_archive，官方补充标为 huawei_official，两者都不冒充 ima。搜索混入竞品、营销案例或相近型号时拒绝交叉继承。只有外站图片链接时明确记录间接来源，不能伪装为已确认的 ima 直接图片。

来源标签区分 `local_archive`（赛事附件）、`huawei_official`（华为官方资料）、`ima`（共享库）和 `local_demo`（独立演练）。系统状态 `ready` 仅表示系统审查通过，不代表平台认可、素材授权完成或流量保证。

价格、补贴及库存暂不提供自动成交报价；真实授权、时效和叠加规则未补全时停止承诺。P4 只有模板，不是内部政策数据。演练及合成测试不会证明真实商业转化。

## 测试

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check web/app.js
```

测试包含幻觉、引用、限定词、串型号、OS 混淆、伪造体验、提示注入、无模型、独立审校失败、图片来源、SSRF、分页和导出安全，以及实际执行前端 JavaScript 的最小 DOM 契约检查。浏览器视觉与真实 API 的结果单列于验证记录，不与 Mock 测试混为一谈。

## Next.js 生产部署

复赛版本由 Next.js 16 前端与 FastAPI 服务组成。Node.js 22 负责编译与运行前端，Python 3.11+ 运行业务服务；Next.js 将同源 `/api/*` 转发到仅监听回环地址的 FastAPI。手机录音使用 HTTPS 或 localhost，照片与音频文件上传共用媒体处理接口。

本地开发分别启动 Python 后端与前端：

```powershell
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-proxy-headers
cd web-next
npm ci
npm run dev
```

前端访问 `http://localhost:3001`。生产构建使用 `npm run build`；构建前设置 `API_ORIGIN` 指向后端回环地址。`output: standalone` 提供独立前端运行包，将 `public` 和 `.next/static` 分别复制到 standalone 下的同名目录后，由 `scripts/run_services.py` 管理两个子进程：先检查 API 健康，再启动前端；任一进程退出会停止整个服务，便于容器或服务管理器整体重启。

### Docker

根目录 Dockerfile 采用多阶段构建，使用 `constraints-runtime.txt` 固定已通过回归的 Python 核心依赖，包含 Node.js、Python、Chromium、中文字体与经 SHA256 核验的华为官方图片。运行时向进程注入模型配置，并挂载持久数据目录：

```bash
docker build -t matequill .
docker run --name matequill --restart unless-stopped -p 7860:7860 \
  --env-file /secure/matequill.env \
  -v matequill-runtime:/app/runtime \
  -v /secure/knowledge:/app/research/knowledge:ro matequill
```

`/secure/matequill.env` 由部署环境管理，内容不进入仓库；其中提供已迁移 PostgreSQL 的应用连接、模型配置及浏览器 Origin。发布前使用独立所有者执行 Alembic；应用进程不修改数据库策略。若挂载空的 runtime 卷，先运行 `/app/.venv/bin/python scripts/fetch_official_assets.py` 初始化官方图片。业务记录在数据库，照片与录音按企业保存在私有持久目录。生产端口由 `PORT` 控制，默认 7860；`API_PORT` 默认 8000，与构建时 `API_ORIGIN` 一致。

语音识别配置使用 `ASR_BASE_URL`、`ASR_API_KEY`、`ASR_MODEL`；未单独配置时启动器复用图像服务网关，默认模型为 `gpt-4o-mini-transcribe`。公网部署设置 `ALLOW_LOCAL_SETTINGS=0`，连接配置由服务端管理；新品维护接口使用会话、角色与同源校验。

### 版本化 SSH 部署

`scripts/deploy_semifinal.py` 使用现有 SSH key，将源码白名单与官方图片打包到新建的版本目录。脚本保留旧服务，先构建并在独立端口检查 Next.js 页面及 API；不会自动替换旧服务或修改防火墙。`--previous-pid` 指向同一应用旧进程，模型配置在远端进程内存之间传递，不经本机、不写入发布目录或日志。

```powershell
python scripts/deploy_semifinal.py --host user@server --previous-dir /root/previous-release --previous-pid 12345 --port 7861 --api-port 8001
```

服务器需预装 Node.js 22，并按脚本中的 Node 路径调整；首次部署可直接采用 Docker 和平台秘密管理。检查完成后由运维入口切换至新版本。共享地址服务器需评委处于可访问网络；公开评审可采用已有 HTTPS 创空间入口。
