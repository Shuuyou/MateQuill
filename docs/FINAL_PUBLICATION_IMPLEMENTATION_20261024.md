# 平台交付实现与验收（2026-10-24）

## 已交付

- 统一授权快照 + 固定转义 renderer：公众号预览、复制的 HTML、ZIP 中 article.html 来自同一生成函数，正文适用条件和实际配图位置保留；不会混入内部策略或审核文案。
- 朋友圈保留现有复制/局部修改流程，增加有序配图包；正在本地修改但尚未保存的文字不会与旧版本文件包混用。
- 公众号显示“复制公众号排版”和“下载图文包”；离线包包括 article.html、text.txt、有序原图及逐文件 SHA256 清单。无脚本/外链抓取，无自动平台发布。
- ready 状态与无 error 审核是准入，绝不将其重命名为“主管审批”。每次读取当前授权资源，内容版本/正文/图片顺序/实际文件字节与 renderer 版本共同绑定 content_hash。旧 hash 冲突、丢图、破损图、声明 hash 不符均明确失败。

## 集成接口

挂载 `app.publication_routes.router`，GET `/api/runs/{id}/publication` 取平台、正文、rich_html、content_hash、有序图清单；可带 expected_hash 强制版本一致。GET `/api/runs/{id}/publication/preview?expected_hash=...` 返回同一 HTML/CSP。GET `/api/runs/{id}/publication/package.zip?expected_hash=...` 下载文件包。各端点执行 run 的 export 授权和素材父资源授权，不信任前端传来的正文或任意图片 URL。

前端 `PublicationActions.tsx` 使用当前 workspace 请求，身份变化或卸载中断准备；复制时再次验证 hash；一次 ClipboardItem 传 text/html 与 text/plain；下载检查返回 MIME 与绑定指纹。ResultsWorkbench 公众号成品用沙箱 iframe 承载统一 renderer。MomentsDraft 仅新增下载操作，现有复制后的 adoption 逻辑保留。

## 验证证据

21 个专项测试通过：XSS 转义、预览/复制/离线 HTML 一致、逐文件 hash、图排序、缺图/损坏/格式错误/字节变化、非 ready/事实 error 拒绝、已保存新版本使旧 hash 返回409。真实账号与数据库测试验证跨企业稿件404、伪造引用另一企业照片404、合法上传规范化原图的文件字节与 SHA256 一致。读者价值相关与交付专项合计87 passed；Next typecheck通过。未重启或部署生产。

## 真实公众号编辑器的边界

复制成功只代表浏览器完成剪贴板写入，不代表公众号已保存草稿或接受了全部图片/样式。使用自包含 data 图片的原因是它们可随离线 HTML 打开，不依赖当前登录会话、会过期的私有资源 URL 或外部源；公众号编辑器可能过滤 data 图片或部分内联样式，因此包内同时保留独立原图。当前未得到已授权公众号账号，不做虚构“平台发布成功”或“已入草稿箱”的提示。后续真实粘贴验收应检查标题/段落/适用条件、原图完整性、编辑器保存重开；若需官方自动草稿，必须另行接入被授权的微信素材上传与草稿接口，不能重用本地私有 URL 冒充微信素材。

Clipboard API 需要 HTTPS/可信本地环境；不支持或拒绝时正确提示，并保留下载路径。这里不使用过时的 execCommand 来暗示所有 HTTP 部署都能原样复制富文本。上述工程边界只记在本文和研究文档，成品正文中不塞入免责声明或审核术语。
