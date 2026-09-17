# 开发环境操作

完整启动命令见根 README。Compose 名称 `bljgh-dev`，只有基础设施，没有业务服务镜像；固定标签用于开发，生产镜像与摘要记录在 S5 完成。

- PostgreSQL：127.0.0.1:25432 / bljgh；Redis：127.0.0.1:26379。
- MinIO：127.0.0.1:59000，控制台 59001。新建 bucket 默认私有；S1 实现分前缀、授权代理与生命周期。
- API：18000；前端：13000。`pnpm --filter frontend start` 需先 `pnpm build`。
- `docker compose -f infra/compose/compose.yaml ps` 查看；`... stop` 暂停；`... down` 删除容器但保留卷。不使用 `down -v`，除非明确打算删除本地数据。
- `uv run --project backend alembic -c backend/alembic.ini current` 查看迁移；`... upgrade head` 应用。
- `... downgrade base` 只供空的测试数据库回放，不能作为生产数据恢复方案。

S0 API liveness 无外部依赖，离线不会因此假报 ready。S1 再增加数据库/对象存储/队列 readiness 及结构化日志、任务指标；原文、文件、提示词、身份和密钥不得进入通用日志。

配置文件忽略 Git；示例密码只用于 loopback 开发服务。生产拒绝 synthetic/mock，尚未实现的 OIDC 不因配置为 oidc 而变得可用。预算强制 0，必须通过后续获准接入变更才可解锁付费调用。

恢复与生产部署、RPO/RTO、留存、备份、告警尚属 S5。当前不宣称达到生产或医学质量要求。
