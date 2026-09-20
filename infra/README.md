# 基础设施

`compose/compose.yaml` 提供固定版本 PostgreSQL、Redis、MinIO，本地 loopback 端口分别 25432、26379、59000/59001。根目录 `docker compose -f infra/compose/compose.yaml up -d --wait` 启动；凭据仅开发使用。

生产镜像、发布、密钥管理、备份和监控按 S5 实现，开发配置不能直接用于机构试点。详见 `docs/operations/development.md`。

## S5 交付

见 [S5 运行手册](../docs/operations/s5.md)。`infra/deploy` 提供机构 Compose/配置/角色与试点门禁清单，`infra/docker` 提供镜像定义，`infra/observability` 提供私有监控与告警。`scripts/ops` 提供备份恢复、授权项目删除、初始项目配置、发布检查与兼容回滚工具。未填写的机构批准或指标会阻断试点放行。
