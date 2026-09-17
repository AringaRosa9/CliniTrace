# 验证分层

- 后端 `backend/tests`：统一错误、环境门禁、语义/模板、共享 span、示例、mock；integration 验证 PostgreSQL 复合键/RLS/连接池上下文。
- 前端 `frontend/tests`：共享 Unicode span、边界拒绝与生成客户端。
- `tests/e2e/prototype.spec.ts`：PRD §12 九场景及 390/768/1024/1440/1920 截图；`pnpm test:e2e`。
- `tests/fixtures/synthetic`：纯合成文本/emoji 夹具，无真实病例。

报告见 docs/testing。S1 已加入真实业务 E2E、身份/跨项目、任务恢复和持久化验证（pnpm test:s1）；模型性能与机构安全验收仍在 S2–S5 实现；原型回归通过不代表正式业务已完成。
