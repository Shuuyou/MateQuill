# 发布源码收尾审查 · 2026-10-24

本审查只读检查共享工作区，不提交、不推送、不重启、不部署、不写入生产价格。结论对应审查时的文件内容；后续修改仍需复查。

## 源码边界

建议公开源代码采用明确允许列表：`app/`、`web/`、`web-next/` 的源文件及已核实字体许可证、`prompts/`、`data/` 中产品事实/素材元数据/活动资料、`migrations/`、`tests/`、`alembic.ini`、依赖锁定与约束、Docker构建文件、README和明确选定的工程/研究文档。脚本只选运行/部署/采集/核验工具，不直接将整个个人工作目录加入版本。

不主动纳入：`.env*`实际配置、`runtime/`、`research/`、开发/录屏证据目录、会话存档、`docs/CODEX-HISTORY.md`、`docs/academic-notes.txt`、历史未核对草稿、远程数据库、媒体上传与个人用户会话。`.env.example`仅允许空值或显式非真实示例。

## 已完成检查

- 扫描app、web-next（排除node_modules、所有.next变体与证据目录）、tests、scripts、data共428个文本文件，检查provider key、ima凭证形状、JWT、私钥、硬编码凭证、粘贴聊天结构及竞品标记。没有发现真实凭证或完整私人会话；凭证字面量命中均为测试合成占位。没有打印任何匹配值。当前本地.env无可供比对的非空秘密值，因此不将“未发现”夸大为与所有历史凭证逐一比对。
- 单独检查电话号码、邮箱和认证头候选；产品JSON命中实际是哈希数字段及官方图片路径的`@`命名，演示seed脚本邮箱是显式合成演示身份，不是导出的客户通讯录。生产源码树没有数据库、HAR、私钥、日志或JSONL会话文件。
- 将本地1375个竞品公开代码文件与我方app/web-next/scripts/tests作连续12条非空代码行、至少240字符的精确窗口比对：零直接匹配。此项用于发现直接搬运，不替代许可证或法律审查。
- `git diff --check`返回0，没有实际空白错误。CRLF转LF提示是换行规范提示，不是构建失败。没有重置任何共享脏文件。

## 真实发布包核验

实际调用现有`deploy_design_release.package_source`和`publish_modelscope.stage`构造本地临时包后核对，不发起任何网络发布：SSH源码包1047个文件，Studio源目录881个文件。

逐项确认报价模块/路由、区域多样性、门店机会模块/路由、Agent runtime、publication模块、daily agent、GTM迁移、QuotationCard及其CSS/lib、CampaignOpportunities组件全部进入两个包；关键新源码无遗漏。两个包均未包含research、CODEX-HISTORY、academic-notes、.env、数据库配置或provider密钥文件。临时包由受控临时目录清理。

待维护建议（已直接通知主Agent，未越权修改部署文件）：

1. Dockerfile当前`COPY docs ./docs`，若今后把会话历史/草稿加入公开checkout，将一起进入镜像；宜改为选定文档。
2. 两个当前包都未带可选`requirements-messaging.txt`及`scripts/run_messaging.py`。网页核心不受影响；若交付独立IM网关，需要明确把依赖清单和入口加入允许列表，并进行外部联调验收。
3. `run_credential_service.py`的加密凭证字段白名单仅LLM_/IMAGE_/IMA_/ASR_和数据库URL；多样性嵌入provider字段应由明确环境变量配置，或加入精确白名单后再装入加密凭证，不可假定会自动继承该载荷。

主协调收尾更新：第1项已改为仅复制`docs/PLAN.html`；第3项已加入`DESIGN_`及`DIVERSITY_EMBEDDING_`明确前缀，并通过现有凭证隔离测试。公开源码的测试辅助脚本依赖已按本地import递归补齐，未加入录屏、历史会话和竞品研究目录。

## 报价集成验收

报价专属测试20项通过。除纯函数和临时租户数据库验证外，新增真实`app.api`已注册router的TestClient完整链：未登录401、无Origin写请求403、店长提交演示规则、另一审核者批准、店员准备报价、按ID读取、Cache-Control private/no-store、跨店404。所有价格测试仅使用明确is_demo的临时数据，未seed真实或生产价格。

QuotationCard新增用户/租户/门店和报价ID身份、活动epoch、实时sessionScopeKey的异步守卫；身份变动当次渲染即隐藏旧卡，旧focus请求不得回写，复制前后均检查当前身份。TypeScript检查通过。主Agent另负责完整前端构建和部署验收。
