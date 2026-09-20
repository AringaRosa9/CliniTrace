# 开发脚本

根目录执行：`bash scripts/dev/api.sh` 启动 API；`bash scripts/db/migrate.sh` 迁移；`bash scripts/ci/check.sh` 运行静态检查、测试、契约漂移与生产构建。`pnpm contracts` 调用生成器，生成物需要进入版本控制。

## S5 交付

见 [S5 运行手册](../docs/operations/s5.md)。`infra/deploy` 提供机构 Compose/配置/角色与试点门禁清单，`infra/docker` 提供镜像定义，`infra/observability` 提供私有监控与告警。`scripts/ops` 提供备份恢复、授权项目删除、初始项目配置、发布检查与兼容回滚工具。未填写的机构批准或指标会阻断试点放行。
