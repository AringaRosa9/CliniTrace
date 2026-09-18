# 契约产物

唯一源：后端 Pydantic 模型（`backend/app/contracts/` 与 `backend/app/modules/*/schema.py`）。根目录 `pnpm contracts` 生成，`pnpm contracts:check` 验证漂移。

- `openapi/runtime.json`：实际 S1–S3 业务与健康接口。
- `openapi/v1.json`：S1–S3 实际接口/类型（与运行时同步）。
- `json-schema/`：门诊和检验模板 1.0.0，JSON Schema 2020-12。
- `examples/`：上传、事实、证据、审核、任务、导出和统一错误的纯合成样例。

生成的前端类型位于 `frontend/src/lib/api/schema.d.ts`。API/版本/幂等/金额/时间/空值规则见 `docs/api/contract.md`。跨字段验证由 Pydantic 执行，不得只依赖 JSON Schema。
