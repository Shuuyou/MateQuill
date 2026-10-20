# MateQuill Web（Next.js 16）

「场景有据」的前端，正在从原生 HTML/CSS/JS 全量迁移到 Next.js App Router。
旧前端仍保留在仓库根目录的 `web/`，8000 端口可随时回退。

## 技术栈

- Next.js 16.4.2（App Router / Turbopack）
- React 19.3.1 + TypeScript 5.9
- Tailwind CSS 4（设计 token 写在 `app/globals.css` 的 `@theme`）
- 字体：得意黑（标题）+ Noto Sans SC（界面）+ Noto Serif SC（正文），
  沿用 `public/fonts/mingcha-ui.css`，由 `app/layout.tsx` 以 `<link>` 引入。

## 目录

```text
app/
  layout.tsx          全局壳与字体
  globals.css         Tailwind + 设计 token
  page.tsx            首页（Hero + 创作入口 + 特性）
  catalog/page.tsx    产品资料（Server Component，SSR 演示）
  settings/page.tsx   连接设置
  api/settings/route.ts  设置写入的 BFF 转发（验证同源与 loopback）
components/
  SiteHeader.tsx      导航
  IntakeComposer.tsx  输入 → 澄清 → 生成 → 结果
  SettingsForm.tsx    连接设置表单
lib/
  api.ts              后端调用封装
  types.ts            与 FastAPI 共享的契约类型
```

## 与后端的关系

后端 `app/`（FastAPI，8000）保持不动，Next 只做前端与 BFF：

- `next.config.ts` 把 `/api/:path*` rewrite 到 `http://127.0.0.1:8000`；
- `/api/settings` 的 Route Handler 验证本机来源、同源 Origin 和转发地址，再调用 FastAPI；线上凭证通过部署环境注入；
- 图片仍走 `/api/assets/{id}` 受控代理，不用 Next Image 优化器（会绕过 SHA256 校验）。

## 命令

```bash
npm run dev        # http://localhost:3001
npm run build
npm run start
npm run typecheck
```

后端：`python -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-proxy-headers`

## 迁移方式

保留 Mingcha 的暖纸、墨色、青绿、字体组合与真实状态角色动效，交互按照门店创作任务拆成 React 组件。
`app/product.css` 是商业工作空间样式，`public/legacy/motion.css` 保留角色动效。旧版其余 CSS 作为迁移参考，不再全局注入。
`hooks/useCreation.ts` 管理需求、澄清、生成、修改与会话恢复；`lib/api.ts` 和 `lib/types.ts` 集中维护后端契约。
`MediaCapture`、`Clarification`、`ResultWorkbench`、`StoreCampaign`、`ProductLibrary` 分别负责素材、追问、结果、门店策划和新品生态资料。

## 迁移状态

| 模块                                                                                  | 状态        |
| ------------------------------------------------------------------------------------- | ----------- |
| 工程骨架 / Tailwind / 字体 / API 代理                                                 | ✅          |
| 站点壳、单一自然输入、必要信息澄清、真实状态角色动效                                  | ✅          |
| 拍照 / 文件上传、录音 / 回听、转写确认、失败重试                                      | ✅          |
| 产品资料分类、新品资料预览确认、华为生态多选创作                                      | ✅          |
| 门店画像建立、同产品多门店策划比较、选方案生成                                        | ✅          |
| 连接设置                                                                              | ✅ 基础表单 |
| 结果工作台（正文 / 图文分镜 / 产品依据 / 修改 / 复制 / 导出）                         | ✅          |
| 海报工作台（真实矢量画布 / 图层文字与位置 / 字体颜色 / 撤销重做 / PNG-SVG-JSON 导出） | ✅          |
| 产品选择带入任务、需求与创作记录会话恢复                                              | ✅          |
| 与旧版功能对账、切换部署                                                              | ⬜          |

## 约定

- 文案语气：短句、具体、不写「赋能 / 颠覆 / 极致」。
- 颜色只用 token：`canvas / paper / ink / secondary / line / soft / accent / selected`。
- 交互必须可用键盘，焦点环沿用 `--color-accent`。
- 所有产品事实来自后端，前端不硬编码任何参数或价格。

## 交互依据

- [GOV.UK Question pages](https://design-system.service.gov.uk/patterns/question-pages/)：追问围绕影响结果的信息，保留原始输入供修改；选择后由明确按钮继续，避免点击选项就意外提交。
- [W3C 表单标签](https://www.w3.org/WAI/tutorials/forms/labels/)：自然输入、上传、录音和资料确认都有可访问名称；字段依赖 label、fieldset 与 legend。
- [Apple HIG Motion](https://developer.apple.com/design/human-interface-guidelines/motion)：角色动效与真实待命、专注、理解、等待、完成状态对应；隐藏页面暂停，减少动态效果时静态呈现。
- [W3C Media Capture and Streams](https://w3c.github.io/mediacapture-main/getusermedia.html)：麦克风由用户主动触发、结束录音释放 tracks；本机与 HTTPS 支持录音，其他页面可选择录音文件。

## 验证

Windows 环境缺少 npm 命令时，可直接运行 `node node_modules/typescript/bin/tsc --noEmit` 与
`node node_modules/next/dist/bin/next build --webpack`。当前运行时 Turbopack 的 Node pooled process 存在 Windows 启动权限问题，webpack 构建已通过。
生产预览使用 `node node_modules/next/dist/bin/next start -p 3003`，避免占用迁移开发端口 3001。
