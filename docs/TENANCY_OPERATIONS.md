# 多租户运行与维护

更新：2026-10-21。身份与隔离由 `app/tenancy` 统一提供，业务资源与生成历史共享 PostgreSQL 资源登记库。SQLite 用于隔离测试和轻量离线开发。

## 数据库与迁移

生产数据库使用 PostgreSQL 18。分别配置迁移所有者和应用运行角色；应用角色必须是 NOSUPERUSER、NOBYPASSRLS，且不能拥有 `mq_resources` 等业务表。数据库连接只在服务器环境中提供。

```powershell
# 用本机的凭证管理或环境注入设置以下两个变量，不提交其值。
# MATEQUILL_MIGRATION_DATABASE_URL：迁移所有者连接
# MATEQUILL_DATABASE_URL：受限应用角色连接
python -m alembic upgrade head
python scripts/tenant_admin.py bootstrap --email admin@example.com --name 企业管理员 --tenant 示例企业
```

bootstrap 密码采用隐藏输入，使用 Argon2 哈希；没有公开注册管理员入口。应用角色需要数据库 CONNECT、public schema USAGE 和业务表 SELECT/INSERT/UPDATE/DELETE；迁移所有者通过默认权限为后续新增表授予同等权限。应用角色不应获得 schema CREATE 或迁移权限。

`mq_resources`、`mq_audit_events`、`mq_usage_ledger` 启用并强制执行租户 RLS；伙伴活动授权表以发起/接收企业关系过滤，插入只允许发起企业。`mq_job_dispatch` 保存 worker 所需的受控任务定位信息；任务内容存放于受租户权限约束的资源库。

身份目录、会话和邀请表是认证服务的控制数据，不作为用户可查询的业务数据集。应用代码必须通过指定认证函数访问，不能提供任意 SQL 接口。RLS 的可信上下文来自后端身份处理，不接受浏览器自行声明的租户身份。

运行时 `get_engine()` 不执行 PostgreSQL 建表或改策略；缺少迁移、运行角色权限过大或关键表没有强制 RLS 时直接失败。请求及任务使用事务局部的 `app.tenant_id` 和 `app.user_id`，连接池复用后不保留前一租户。新增私有表需在迁移中增加相应策略。

## 会话与浏览器

- 会话 cookie 为 `matequill_session`，HttpOnly、SameSite=Lax，服务端保存随机令牌的 SHA256，不保存原始令牌。
- HTTPS 部署设置 `MATEQUILL_COOKIE_SECURE=1`，并将完整站点 Origin 加入 `MATEQUILL_ALLOWED_ORIGINS`。本地默认支持 localhost/127.0.0.1 的 3003、3001。
- 改写操作检查 Origin，匿名业务 API 返回 401。Next BFF 必须透传 Cookie、Origin 和工作区前置条件；前后端分别验证权限。
- `X-MateQuill-Workspace` 只防止多标签页切换后误写。它不建立身份或授权；与当前服务端会话不符时返回 409 `workspace_changed`，客户端刷新并让用户确认，不能自动重放改写。
- 成员权限每次从当前有效 membership 重新计算。旧任务执行及结果交付前使用 `refresh_context()`；离职或撤销任职不依赖等待 JWT 自然过期。

在内嵌第三方 iframe 中使用时，还需验证浏览器第三方 cookie 策略。正式店员入口优先使用独立顶层 HTTPS 域名。

## 成员和邀请

`admin`/`operator` 为企业级角色；`regional_manager`、`store_manager`、`salesperson`、`reviewer` 带门店范围，可通过多条 membership 跨店任职。`scoped_roles()` 读取当前门店角色，`scoped_roles(None)` 明确只读取企业范围角色。

邀请只由授权管理员创建，48 小时到期，令牌仅创建响应显示一次且可撤销。接受邀请不能覆盖已有用户密码：同邮箱已有账户必须使用原密码确认。邀请人被撤权后，未使用邀请也会失效。团队变更使用事务锁保护“至少一位企业管理员”规则。

## 业务资源接口

`register_resource` 建立 tenant/store/owner/visibility；`save_body` 要求创建者或相应管理范围；`load_body`、`authorize_resource`、`list_resources` 始终校验当前租户与门店。

`private` 为本人及相应管理范围；`store` 为获授权门店成员；`tenant` 为企业共享内容。企业共享活动不自动绑定管理员当前选中的门店。

`service_update_body` 专用于已完成业务身份与状态校验的流程迁移，不是通用公开编辑 API。活动、领取、送审、审核与发布使用 `transaction_context()` 合并多项更新；更新携带版本可防止并发覆盖。该事务不允许中途改变租户。

## 验证

```powershell
python -m pytest -q
# 在受限角色的独立测试数据库运行真实 PostgreSQL 测试：
# 设置 MATEQUILL_TEST_POSTGRES_URL 后执行
python -m pytest tests/test_tenancy_postgres.py -q
```

旧创作单元测试仅在 `tests/conftest.py` 中注入测试身份，`real_tenancy` 标记测试使用真实认证与权限；产品代码没有环境开关式匿名绕过。测试库和旧工作流记录由 tmp_path 隔离。

本机真实 PostgreSQL 已验证跨企业读写、无上下文默认拒绝、连接池上下文重置及应用账号无法禁用 RLS。伙伴活动流程也已实际验证指定接收、第三企业不可见、私有稿件独立和撤回生效。运行结果由主验收记录统一维护。

## 持久化生成任务

`POST /api/jobs` 接受任务和客户端幂等键。浏览器在首次提交前保存键，刷新后继续查询同一任务；同一个键不能替换任务内容。任务正文存放于企业资源库，调度表只保存定位和租约信息。worker 按企业轮转领取，同一企业同时运行一项任务，以租约、心跳和 claim token 避免过期 worker 提交结果。

保存成稿、提交任务结果检查点和完成用量记账位于同一事务。任务恢复时优先使用已经提交的结果，因此不会在结果已保存后再次调用模型或扣除额度。尚未提交结果的外部调用可能在恢复时重试。执行前、保存前和交付前均重新检查成员与活动权限。

`MATEQUILL_BUSINESS_TIMEZONE` 默认为 `Asia/Shanghai`，每日用量按该时区结算。额度同时计入正在执行的预留量；失败释放预留。创作历史只查询获授权记录的标题和状态，不把海报中的内嵌栅格图片载入列表接口。

## HTTPS 入口与发布分离

Next 服务可设置 `MATEQUILL_PUBLIC_URL` 为固定 HTTPS 入口，将其他入口重定向到该地址；认证后端同时配置相同的 Origin 和 Secure cookie。入口必须来自运营配置，不能从任意请求头推导。

2026-10-21 本轮发布仅覆盖本地、GitHub 与阿里云。魔搭创空间保留既有稳定演示版本，独立于新版发布，不执行上传、配置更新或重启。

## 主机服务与凭据

阿里云新版由 `matequill-channel.service` 管理，启用开机启动和失败重启；Python API 仅监听 loopback，Next 入口通过独立的 Tailscale HTTPS Serve 转发。该 HTTPS 地址供同一 tailnet 内的设备访问，HTTP 入口重定向至规范 HTTPS 地址。

入口为 `https://mingcha-aliyun.tailcdb222.ts.net:8443/`，代理到 Next 7860，后端仅监听 `127.0.0.1:8002`。迁移所有者凭据独立保存，不交给应用运行。

服务使用 `LoadCredentialEncrypted` 载入主机密钥加密的 provider 配置，由 `scripts/run_credential_service.py` 在进程内解析受限字段后启动服务。API Key 不写入 systemd 单元、源码或命令参数。加密文件与主机绑定，备份和迁移时须通过安全凭据流程重新配置。

部署使用独立 PostgreSQL 数据库及受限应用角色，保留上一版 release。服务重启后应等待 `/health` 与外部 HTTPS 登录均就绪，再验收历史图片、导出、语音和新创作；`systemctl is-active` 仅代表服务进程已启动。
