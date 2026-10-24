# 活人感与网感：质量诊断、审校和盲评研究

调研截至2026-10-24。研究发生在本模块实施之前；只使用原论文和作者/厂商工程资料。研究对象不全是中文门店营销，因此不把论文结果当成我方转化率或模型排名。

## 研究证据

1. Doshi / Hauser，[Generative artificial intelligence enhances creativity but reduces the diversity of novel content](https://arxiv.org/abs/2312.00506)，2023-12首发、2024-03修订，关联Science Advances 2024论文。实验发现个体写作获益可伴随集体内容趋同。这里借鉴的是不要让所有门店都围绕相同建议、相同解决式结尾创作，不据此断言所有AI文本必然差。
2. Zheng等，[Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685)，2023-06。指出位置、冗长、自增强等偏差。我们的人工对照不暴露模型、版本及“新版”标签，随机A/B位置，不以更长、更工整判断更好。
3. Tapwal等，[Faithful or Fabricated?](https://arxiv.org/abs/2605.23970)，2026-05-13预印本。研究与文本内容无关的提示标签如何引发评判及理由的锚定，提出先锁证据再偏好的办法。这里采用先读实际标题/首屏、再看正文和任务的顺序；不把生成者对作品的解释视为读者实际理解。
4. Tutone等，[The Limits of Automatic Evaluation of Creativity in Large Language Models](https://arxiv.org/abs/2608.23705)，2026-08-24首发、08-27修订。创意短篇的自动指标、模型裁判与人类评价存在明显错位；模型偏好AI文本的某些风格。工程含义是保留人工盲评，不新增一个“AI味分数”然后当作人类偏好。
5. Bagaria等，[Judging LLM-as-a-Judge: Concerning Rubric Artifacts](https://arxiv.org/abs/2609.02942)，2026-08-31首发。仅rubric文本能部分预测裁判输出，反向条件下裁判也可能不跟随内容变化。继续堆更多理想品质词可能强化模板；本次只添加与真实稿件表面绑定的阅读检查。
6. Chae等，[Self- and Other-Labels Induce Bidirectional Bias in LLM Judges](https://arxiv.org/abs/2608.18091v3)，2026-06-06首发、10-16修订，页面标注EMNLP Findings 2026。作者/来源标签本身会改变判断。盲评页不展示baseline、新版、模型和生成条件；研究者映射另存，不嵌入页面。
7. Anthropic，[Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)，2026-01-09。区分真实轨迹、最终产物与grader，强调校准评价。真实生成、人工选择、模型判断和合成单元测试分别标记，不能互相代替。

## 当前真实问题

已有实际局部改稿把标题改为“一天换三个地方，电脑是真沉”，正文随后推广约1.33kg产品。读者可能将生活负担直接归到推广产品，模型却将“沉”解释成重量话题的兴趣点，两次真实独立判断均通过。它不一定是可证的事实错误，也不能由程序把负面表达一律判错；问题是普通阅读的指向与模型事后解释可能不同。

现有`reader_value`已按意图区分教程/取舍/体验/审美/观察，也有逐字引用和局部改稿约束。缺口是评判顺序仍易被正文参数与创作意图锚定，普通读者第一眼并没有优先被呈现；`creative_effectiveness`也不等于店员真实愿意发送的偏好。

## 本次实施

- 在原有单次独立营销审校中加入`voice_review_context`：准确发布身份、当前平台关系、原样首屏字段和“先按普通语义读，再核对正文与身份”的检查顺序。没有额外模型调用。
- `first_screen`现有字段承担普通读者的首读解释。判断评价主体、评价方向及是否就近消歧；不许把“沉”自动解释为“有分量/有价值”，也不预设所有读者都选最负面的解释。
- 缺陷要定位到公开文字与实际冲突关系；明确针对该产品的真实负面体验、合理痛点、幽默、双关、留白和不购买的结尾仍可通过。审美偏好只作可选建议，不为“活人感”发明新的硬门槛。
- 新增人工图文成对盲评工具：同任务、相同展示尺寸、随机A/B、不显示新旧/模型/来源标签，实际图片嵌入页面，评审先选择再填写具体原因。支持“都可以/都不愿意发/无法判断”，无默认偏好、无分数、无自动投票。
- 工具仅打包已有结果，不生成内容、访问平台、抓取远程图片或上报反馈。来源映射独立保存，不能随盲评页一起发给评审；随机顺序仅减轻位置影响，不保证消除全部偏差。

## 验收边界

单元测试验证实际首屏、身份边界、独立调用预算、随机映射、XSS防护及图片字节，不证明内容已经获得人类喜爱。真实模型A/B由主协调进行；没有人工填写的结果不计为人类偏好。至少分别请一线店员与目标读者做盲评，观察身份自然度、读后所得、图文可信性以及发送/停留意愿；不会把点击选择直接换算为销售转化率。

## 实现与检查结果

- `app/voice_quality.py`与`app/marketing_review.py`：原单次审校接入，`headline_first`只呈现真实标题和导语，随后`first_glance`给首段；不复制内部创作解释，不推断顾客购买阶段，不修改最终裁判结论。
- `scripts/build_living_content_review.py`：从同任务两个已有版本打包离线页面。`review_id`绑定实际可见内容及图片，A/B来源在页面外的研究者key中，不嵌入版本标签。实际图片须位于显式asset root且通过PNG/JPEG/WEBP格式检查，支持导出已填写反馈。
- 102项相关测试通过，含服务端实际发布身份与审校身份保持一致：明确可信context优先，历史通用store默认可回退到服务端已生成的editorial_voice，不接受正文/任务自述身份。Playwright实际打开离线页验证空选择不导出、明确选择后导出、页面无脚本错误；此项为明确标识的合成UI操作，真实人类票数为0，不可统计为偏好证据。
- 给不同评审生成不同`--seed`页面以平衡位置；每个反馈保留对应`review_id`，按该页面的研究者key解码。不要将一个页面的A/B映射套到另一个页面。

调用示例：`python scripts/build_living_content_review.py actual-pairs.json --output output/living-review --asset-root output/actual-assets --key output/researcher-only/living-key.json --seed 17`。研究者key不要随页面发给评审。输入图像是已有实际文件；该命令不生成图片，也不拉取远程图片。
