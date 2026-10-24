# 平台原生交付：截至 2026-10-24 的研究与实施契约

本模块解决生成之后最后一步的可用性：朋友圈拿到文字和有序照片；公众号看到的排版、复制的排版、下载的文件来自同一份内容与文件快照。不声称自动发布或已进入微信草稿箱。

## 一手来源与证据范围

- [MDN Clipboard.write](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/write)：安全上下文；一次 ClipboardItem 同时提供 text/html 和 text/plain；权限或用户激活可能拒绝。该 API 的跨新浏览器 baseline 为 2024。下载离线包是明确的可用路径，不能把复制失败显示成成功。
- [MDN ClipboardItem](https://developer.mozilla.org/en-US/docs/Web/API/ClipboardItem)：同一对象多 MIME 表示；多个系统剪贴板条目并非总可用。因此不尝试把一组照片伪装成一次多图粘贴。
- [MDN Clipboard API](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API)：浏览器权限与激活差异；复制不等于目标编辑器完整接受所有样式或图片。
- [OWASP XSS Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)：按上下文转义；不信任模型输出 HTML；CSP 不能替代正确编码。实施只接受结构化文字，以固定样式模板排版，所有用户字段转义；图片来自已授权文件读取。
- 微信官方文档入口：[新建草稿](https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html)、[永久素材](https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/Adding_Permanent_Assets.html)、[临时素材](https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/New_temporary_materials.html)。2026-10-24 本研究访问这些页面失败；不能据此声称验证了当日字段限制、权限、图片 URL 接收规则。故本轮实现可离线检验的复制排版与下载包，未实现/未宣称微信官方自动同步。待有已授权公众号账号后，另行核对当前官方接口及实际粘贴验收。

MDN/OWASP 为实时文档，无可证明的 2026-10-24 历史快照；本模块只采用早于该日已存在的稳定接口，不把网页最新修改时间当研究发布日期。

## 取舍与产品交付

朋友圈：保留可直接修改、复制的文字；新增一个“下载配图包”，文件按 01、02 排序。公众号：一个复制排版按钮，一个下载图文包按钮；包内正文 HTML、纯文字、有序原图和文件指纹清单。复制图片在目标公众号编辑器中的保留取决于其粘贴支持，图文包提供独立原图，不以浏览器成功写剪贴板冒充平台发布。

统一服务器 renderer 产生预览、剪贴板富文本和 HTML 文件。固定内联样式不依赖站点 CSS，不接收外部 HTML、脚本、外链抓取或模型 CSS。正文、事实适用说明、实际图片位置完整保留，不混入策略、内部审核、连接器或运行日志。

## 可验证工程契约

每次导出重新读取当前授权 run 和所有实际配图字节；仅 status=ready、无 error 审核的版本可进入交付。版本号、正文、图片顺序、实际文件 SHA256、renderer 版本一起决定 content_hash。用户操作携带 expected_hash；内容或文件变化返回冲突，重新获取。哈希不等于人工审批；ready 与正式渠道审批是不同状态，不额外自称已获主管批准。

每张图必须存在于该稿声明的素材与选图中，通过既有父资源授权；不通过任意 URL 取图。单图/总量限额，真实图片格式校验；缺失、变更、哈希不符、中途读取失败均不得生成“成功但少图”的包。ZIP 路径由服务器固定生成。无缓存，因此不存在跨租户缓存键混淆或空壳缓存命中。

验收包括：内容前后版本变化、图字节变化、私有图片拒绝、损坏图、无图合法文章、XSS 字段、顺序和 ZIP 每个文件哈希、不同租户授权、复制失败提示。公众号真实编辑器粘贴是单独的人工/授权联调项目，不能由离线 HTML 测试替代。
