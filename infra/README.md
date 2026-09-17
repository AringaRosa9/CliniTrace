# 基础设施

`compose/compose.yaml` 提供固定版本 PostgreSQL、Redis、MinIO，本地 loopback 端口分别 25432、26379、59000/59001。根目录 `docker compose -f infra/compose/compose.yaml up -d --wait` 启动；凭据仅开发使用。

生产镜像、发布、密钥管理、备份和监控按 S5 实现，开发配置不能直接用于机构试点。详见 `docs/operations/development.md`。
