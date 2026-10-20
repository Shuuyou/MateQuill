# MateQuill 复赛研究与技术决策

调研及代码核对日期：2026 年 10 月 20 日。

本文将论文、官方设计指南与工程文档转成可以检查的产品决策。表中的“已实现”指当前代码能力；模型论文与候选部署单独注明，评测数字不移用为 MateQuill 的效果指标。

## 决策总览

| 业务问题 | 采用的产品技术决策 | 当前实现 | 研究或官方依据 |
| --- | --- | --- | --- |
| 店员没有时间填需求 | 一句话、照片或语音起步；缺什么才问什么 | `IntakeComposer`、`MediaCapture`、`Clarification`、`useCreation` | Apple 信息层级、Material 按需揭示详情 |
| 几千家店发出同一种文案 | 先按门店任务规划选题、叙事和画面，再生成 | `store_campaigns.py` 的三轴差异化规划及标准任务输出 | DPWriter 的规划阶段分支；LaMP 的画像条件化 |
| 新品知识更新快 | 原文导入、预览确认、事实与来源入库 | `product_knowledge.py`、`LocalKnowledgeProvider` | 可更新的检索证据与明确产品范围 |
| 生态宣传容易混淆设备与功能 | 显式选择设备；具体能力保留适用条件；写作优先相关协同事实 | `TaskContextProvider`、`capability_fact`、`llm.generate` | 对任务相关证据进行预算选择，创作与验证分工 |
| 内容结果缺少产品感 | 有状态的角色、品牌字体、阅读排版和可编辑交付 | Next 组件、`product.css`、本地字体及原有状态动效 | Apple HIG 的有目的动效、可读性及品牌层级 |
| 前端迁移后接口容易冲突 | Next 自身路由优先，普通 API 使用 fallback 代理 | `next.config.ts`、明确的维护操作路由 | Next.js 官方 rewrites 与 standalone 文档 |

## 一、拍照与语音：把店员已有的信息变成输入

### 1. 当前的真实模型连接

语音已通过现有兼容网关的 `gpt-4o-mini-transcribe` 模型标识和 `/v1/audio/transcriptions` 完成真实中文样例转写。照片使用现有视觉网关配置，实际请求 `gpt-4o` 已返回场景描述、短文字和场景线索。模型名称来自请求配置与服务响应，不把兼容网关的名称进一步解释为自主训练或本地模型部署。

`app/media_intake.py` 负责实际识别，`media_routes.py` 负责有界上传和预览。照片转成去除 EXIF 的规范 JPEG，识别结果按固定结构返回；录音识别使用实际文件内容。空转写、未配置或上游失败分别保留可恢复状态。

照片的作用是带入本店环境、实物画面和可见内容。视觉模型候选名称必须出现在本次可见文字中，再由任务解析确认准确产品。`TaskContextProvider` 将这些观测存入 `user_materials`，不会自动写入产品事实。

### 2. 私有部署候选：Qwen3-ASR

[Qwen3-ASR 官方实现](https://github.com/QwenLM/Qwen3-ASR)提供 0.6B 与 1.7B 模型、中文及方言支持，并介绍离线、流式和 vLLM 服务接口。它适合实验室算力条件下进一步评估门店噪声、口音和中英文产品名混说。官方当前文档也给出了 OpenAI 兼容转写调用方式。

产品决策是保持 `ASR_BASE_URL`、`ASR_API_KEY`、`ASR_MODEL` 接口独立。现在使用已经跑通的服务完成交付；需要私有部署时可以替换该服务连接，不改变照片入口、任务结构和生成流程。Qwen3-ASR 是已调研的部署候选，当前工程没有加载它的权重，也没有对其做微调。

评估新 ASR 时，优先采集得到使用许可的短录音：门店背景声、华为产品名、自然停顿、方言口音和自我纠正。衡量字符错误、关键型号识别、响应耗时和店员修改量；论文的通用测试成绩用于选型参考，产品验收以这组实际任务为准。

### 3. 视觉候选：Qwen3-VL 与 PaddleOCR-VL

[Qwen3-VL 技术报告](https://arxiv.org/abs/2511.21631)覆盖图文理解、多图及长上下文推理；[PaddleOCR-VL-1.5](https://arxiv.org/abs/2601.21957)面向复杂真实文档的结构化解析。前者适合作为门店场景理解候选，后者适合价签、参数页和新品资料的文字结构提取候选。

这两个方向承担的任务不同：场景理解回答“这张画面可以讲什么”，产品资料提取回答“原文具体写了什么”。当前先使用现有视觉服务完成前一条路径，并对官方规格及用户提供原文采用既有提取和确认流程；未将候选 OCR 模型列为已部署组件。

## 二、千店千面：在规划阶段产生实质区别

### 1. DPWriter 对产品的启发

[DPWriter](https://arxiv.org/abs/2601.09609)研究创意写作中输出多样性下降的问题，提出在显式规划阶段引入分支，并结合训练中的多样性奖励。对 MateQuill 最有用的启发是：内容差异应在选题与组织方式阶段形成，单纯调高温度不能替代规划。

当前 `store_campaigns.py` 使用可解释的三个编辑轴：选题切角、叙事方式和视觉方向。方向选择结合门店主要顾客及关键任务，并考虑同次计划中已经使用的方向。计划输出每家店的具体标题、理由、咨询引导和标准 `TaskInput`，随后复用事实生成与审查。

例如，界面中的标题会绑定顾客任务，形成“入学选电脑，先留好这份清单”或“从会议资料整理到完成，一步步讲清”这样的具体内容方向。系统保留选题轴编码，便于维护规划逻辑与统计方向覆盖。

我们采用的是规划启发和确定性编排，没有执行 DPWriter 的强化学习训练，也没有采用其奖励模型。当前 `diversity` 度量是规划字段差异，包括方向数量及选题、叙事、视觉、人群和任务的变化。

### 2. LaMP 对门店画像的启发

[LaMP（ACL 2024）](https://aclanthology.org/2024.acl-long.399/)把用户画像及相关历史条目用于个性化语言任务，研究如何通过检索增强改善个性化输出。这说明个性化需要有具体的用户条件，而不只是要求模型“写得有个性”。

MateQuill 当前把门店主要顾客、关键任务、语气、位置和素材描述存成结构化画像，再传入规划及写作。每个字段的用途清楚：顾客与任务决定内容问题，语气约束表达，实际上传素材影响画面。填写的地址不自动推断附近学校或客流，素材描述也不自动等于已有实拍。

门店历史优质内容检索、店员主动选择的文风样例，是后续候选。当前已实现画像持久化和条件化生成，没有训练个性化参数或自动学习门店历史。

### 3. 如何评估真正的差异化

[Modifying Large Language Model Post-Training for Diverse Creative Writing](https://arxiv.org/abs/2503.17126)进一步研究创意质量与多样性的关系。这类研究提示我们应同时观察质量和范围，不能把文字不同直接等同于内容更好。

当前规划统计先回答“是否给了不同的编辑方向”。下一阶段评测应固定产品事实，让同一批顾客任务分别通过通用提示和门店规划生成，再由业务人员盲评：是否贴近顾客、是否愿意发布、修改是否省事。重复标题、开头、段落结构和语义相似度可以作为辅助指标。实际阅读、咨询和发布数据需要在真实投放后统计，不能由模型预估成既有成绩。

## 三、新品与华为生态：把事实预算用在顾客任务上

新品导入的关键是缩短知识更新到可用内容的路径。`product_knowledge.py` 接收资料链接或原文，生成带有事实、来源及哈希的预览；确认后的记录进入 `LocalKnowledgeProvider`，与既有产品使用相同的事实对象。

生态任务明确列出主设备与伴随设备。每条事实仍归属于自身产品；跨设备能力来自具体适用范围和条件。生成审查同时检查产品范围、引用、数字和限定条件，型号名字中的数字与真正参数分别处理。

写作输入由平台事实预算约束。`llm.generate` 对当前任务相关事实排序，生态组合优先单独来源的 `生态协同/…` 事实，避免把有限预算消耗在通用应用清单。`_writer_context` 给写作模型精确属性值及原文条件摘录，独立验证仍获取对应原始证据跨度。

这一设计把表达自由放在选题、结构、画面和语言上；参数、功能归属与使用条件保持可追溯。原文条件可以在邻近正文的脚注中呈现，保持文章阅读连贯，同时保留其原义。

回归测试 `tests/test_llm_fact_selection.py` 直接检查送入写作模型的结构化输入：朋友圈只有一个事实预算时，相关生态能力应优先于通用应用清单；其条件逐字保留，原始知识对象和应用脚注不被改写。

## 四、UI/UX：简洁入口与有设计的产品界面

### 1. 动效表达状态

[Apple HIG Motion](https://developer.apple.com/design/human-interface-guidelines/motion)强调有目的的动效、状态反馈及减少动态效果的选择。MateQuill 的 `Character.tsx` 使用 `idle`、`focus`、`thinking`、`waiting`、`done` 和 `error` 状态；页面不可见时暂停角色动画。`product.css` 提供进入过渡，并响应 `prefers-reduced-motion`。

实际交互上，输入聚焦对应关注状态，理解或生成对应思考状态，澄清阶段对应等待，完成时转换表情。动效与文字、按钮状态配合，不用虚构进度百分比。后续动效调整应继续绑定这些实际阶段，而不是增加独立的装饰计时器。

### 2. 字体、层级和阅读排版

[Apple HIG Typography](https://developer.apple.com/design/human-interface-guidelines/typography)、[Branding](https://developer.apple.com/design/human-interface-guidelines/branding)和[Layout](https://developer.apple.com/design/human-interface-guidelines/layout)分别提供文字可读性、品牌字体使用和信息层级参考。Apple 的平台字号单位与 Web CSS 不同，这里借鉴层级及阅读原则，不将 iOS 字号机械转换成网页标准。

当前通过本地字体资源建立三种职责：`Scene Display` 表达品牌标题，`Noto Sans SC Variable` 承担界面及控件，`Noto Serif SC Variable` 用于较长的内容阅读。`product.css` 把导航、控制区和正文分开组织，内容主区使用统一对齐、行高及留白。界面保留明确标签和键盘焦点，主要操作使用稳定位置。

### 3. 渐进展开的具体落点

[Material Design 的数据可视化交互指南](https://m2.material.io/design/communication/data-visualization.html)以按需查看数据详情说明 progressive disclosure；[Apple Layout](https://developer.apple.com/design/human-interface-guidelines/layout)也明确讨论按需揭示隐藏内容。我们将这一原则应用到创作工具，是产品设计上的推导，而不是声称指南规定了某种 LLM 表单。

`IntakeComposer` 初始提供一个自然输入框，以及照片和语音动作；主推产品和发布平台放在“补充条件”中。`Clarification` 只渲染接口返回的待确认问题，已有答案不重复收集。`ResultWorkbench` 把文章与创作思路分开，来源详情按需展开，阅读区保持成稿优先。

中文输入同时处理 `isComposing`：输入法尚在选字时，Enter 不会错误提交。转写后的内容回到可编辑输入框，照片显示实际预览，店员可以先看清系统收到了什么。

## 五、Next.js 16：保留业务边界的工程迁移

[Next.js rewrites 官方文档](https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites)说明 `fallback` 在页面、静态资源和动态路由之后处理。当前 `next.config.ts` 将普通 `/api/:path*` 通过 fallback 转发给 Python 服务，使 Next 已定义的维护路由先处理；前端使用相同站点路径调用后端，组件不拼接业务服务器地址。

`lib/server/editorProxy.ts` 对少量明确维护操作使用固定路由集合。输入组件、澄清、门店计划、产品资料、结果阅读分别位于独立组件；`lib/types.ts` 定义交互数据，`lib/api.ts` 集中请求，`useCreation` 管理创建流程与会话恢复。业务模型调用继续由 Python 完成，避免在前端组件中扩散凭证和推理逻辑。

[Next.js output 官方文档](https://nextjs.org/docs/app/api-reference/config/next-config-js/output)说明 standalone 追踪必要运行文件并生成最小服务入口。当前配置已启用 `output: "standalone"`。生产打包还需要包含 `public` 和 `.next/static`；字体、角色 CSS、图片预览和 multipart 上传应在实际打包服务上分别验证。Windows 构建的符号链接回退可能带绝对路径，跨平台发布应在目标兼容环境构建和验证。

## 六、复赛演示应展示的技术成果

1. 同一自然输入入口接收照片与语音，显示实际观测和真实转写。
2. 面对确实有歧义的输入，只出现相应的澄清选择。
3. 同一款产品、三个明确画像，展示不同的选题、叙事、画面及实际生成文稿。
4. 新资料从预览确认进入产品目录，再在实际生成内容中使用其事实。
5. 生态场景展示明确设备、具体任务和相应协同能力，来源及条件能够展开查看。
6. 编辑、复制和导出真实完成，重新打开记录仍可恢复结果。

视频脚本见 `docs/SEMIFINAL_VIDEO.md`，工程模块和存储说明见 `docs/SEMIFINAL_ENGINEERING.md`。技术介绍重点放在这些可以当场操作的机制及成果，论文作为设计依据，具体模型与实现状态保持对应。
