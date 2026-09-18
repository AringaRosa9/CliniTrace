# API 与数据基础

Python 3.12 + uv；运行 `uv sync --frozen` 后 `uv run uvicorn app.main:app --host 127.0.0.1 --port 18000 --reload`。配置从当前目录 `.env` 加载。

S1 已实现身份/项目、患者/就诊、上传/预览、异步任务及审计接口；统一 request_id/error/no-store 中间件已就位。`app/api/s1.py`、`s2.py`、`s3.py` 挂载实际文档、抽取、审核和导出路由；`app/contracts/api.py` 发布已实现的 S1–S3 契约。

`uv run alembic upgrade head` 执行 S0 基础表与 S1–S2 持久化迁移；`uv run alembic check` 检测 metadata 差异。完整 ER/后续迁移设计见 `docs/architecture/data-model.md`。

`uv run pytest` 默认单元/契约测试；`RUN_DB_TESTS=1 uv run pytest tests/integration` 连接专用 PostgreSQL 验证复合外键/RLS/事务上下文。`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy app` 为静态检查。

运行 Worker/投递器、显式合成身份初始化、OIDC 接入与能力配置见 [S1 运行手册](../docs/operations/s1.md)。synthetic 不能在 production 启用。OCR/模型关闭、预算 0；机构接入条件仍见 ADR-0004。

S2 OCR、获准网关协议、合成模式和版本切换见 [S2 运行手册](../docs/operations/s2.md)。
