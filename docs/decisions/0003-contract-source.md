# ADR-0003：契约单一生成源

状态：接受。

Pydantic 输出 OpenAPI 与两类模板 JSON Schema；openapi-typescript 生成客户端类型，openapi-fetch 调用。当前运行时契约和未来设计契约分别发布，未来路由绝不混入已实现服务的 API 文档。

合成 mock 显式 opt-in，默认关闭，无网络模型调用。生产禁止 synthetic 身份和抽取器。CI 校验生成物漂移、共享 span、语义失败路径及样例一致性。

后果：前端不维护另一套手写 DTO；跨字段业务约束除 JSON Schema 外仍须由 Pydantic/服务层执行；S1–S3 每实现一项接口才从设计契约迁入 runtime。
