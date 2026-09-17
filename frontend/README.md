# 前端

Next.js App Router + React + TypeScript。根目录 `pnpm dev` 启动，`pnpm build` 构建，`pnpm --filter frontend start` 运行构建产物。

`src/app` 提供概览、`/documents` 和 `/projects/[projectId]/documents`。文档模块包含登录/项目切换、患者/就诊建档、上传、筛选分页、任务控制、关联更正、审计和真实预览；其余模块标记后续开放。颜色、字体沿用 `.impeccable.md`，样式 token 位于 `src/styles/globals.css`。

`src/lib/api/schema.d.ts` 从后端 OpenAPI 生成，不手改。`createApiClient` 通过 openapi-fetch 约束路径和参数。业务权限由 FastAPI 最终执行，页面依据返回能力显示操作。`API_INTERNAL_URL` 是服务端变量，不含公开密钥；浏览器走同源代理。应用不在 localStorage 保存医疗数据。

单元测试：`pnpm test`；lint：`pnpm lint`；类型：`pnpm typecheck`。项目根目录 Playwright 包含原型、首页和 `pnpm test:s1` 的真实业务流程。正式三栏审核工作台在 S3 迁移。
