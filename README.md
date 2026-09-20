# 临床数据结构化平台

面向门诊记录与检验报告的事实抽取、原文证据核对和可追溯数据交付。

## 当前状态

S5 交付工程已实现：机构部署模板、非 root 镜像定义、受保护监控/告警、维护模式、一致性备份/恢复、授权项目删除、试点项目初始化、发布门禁、压力与无障碍验证、UAT/培训/灰度与回滚材料。见 [S5 验收记录](docs/testing/s5-report.md) 与 [运行手册](docs/operations/s5.md)。本地恢复/删除演练通过；镜像完整构建因外部 registry 连接失败待验证。机构授权、实测门槛签署、现场 UAT 和正式灰度尚未完成，不能宣称试点已放行。

S4 管理与质量闭环已实现：模板/指南不可变发布、词库授权与映射历史、独立标注/二审/裁决、患者隔离金标准冻结、离线评测/版本对比、纠错二审及测试集防污染。管理入口为 `/templates`、`/terminology`、`/quality`。见 [S4 验收记录](docs/testing/s4-report.md)、[运行手册](docs/operations/s4.md) 和 [200 份样本计划](docs/data/s4-sampling-plan.md)。当前为本地合成工程验收，真实医学样本与正式词库仍待机构提供。

S1 文档与持久化已实现：登录与项目能力、患者/就诊建档、真实文件上传、私有对象存储、Outbox/Worker 任务、解析预览、取消/重试/恢复、关联更正及审计。文档页面位于 `/documents` 和 `/projects/[projectId]/documents`。S2 已实现本地 OCR 适配、版本化 Schema 抽取、证据校验、事实关系、术语候选、规则问题及待审核集合。S3 已实现三栏审核工作台、事实修订/补录/排除、逐项核对、问题处置、审核快照、数据集筛选与异步 JSON/CSV 导出。见 [S3 验收记录](docs/testing/s3-report.md) 和 [S3 运行手册](docs/operations/s3.md)。详见 [S2 验收记录](docs/testing/s2-report.md) 和 [S2 运行手册](docs/operations/s2.md)。详见 [S1 验收记录](docs/testing/s1-report.md) 和 [运行手册](docs/operations/s1.md)。

FND-05 已固定本地合成开发基线，机构 OIDC、正式词库授权、OCR/模型获准环境、真实样本与付费预算仍待机构提供。参见 [接入决策](docs/decisions/0004-environment.md)。

## 本地启动

需要 Node.js 24、pnpm 12.4.2、Python 3.12、uv 0.10.6、Docker Compose。依赖使用锁文件：

```sh
corepack enable
pnpm install --frozen-lockfile
uv sync --project backend --frozen
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
docker compose -f infra/compose/compose.yaml up -d --wait
bash scripts/db/migrate.sh
uv run --project backend python scripts/db/bootstrap_s1.py
```

在 backend/.env 中设置自选的 `SYNTHETIC_ACCESS_CODE`，然后分别在四个终端启动：

```sh
bash scripts/dev/api.sh
```

```sh
pnpm dev
```

```sh
bash scripts/dev/worker.sh
```

```sh
bash scripts/dev/dispatcher.sh
```

访问 [应用](http://127.0.0.1:13000)、[API 健康检查](http://127.0.0.1:18000/api/v1/health)、[API 文档](http://127.0.0.1:18000/docs)。健康检查只报告 API 进程，不代表业务或依赖服务就绪。前端同源代理 `/api/v1` 到 API，不直接访问数据库。

本项目开发设施绑定 loopback：PostgreSQL 25432、Redis 26379、对象存储 59000、存储控制台 59001。固定版本镜像及本地专用凭据见 Compose；不用于生产。启动镜像需网络可达，端口冲突时同时调整 Compose 与 `.env`。

显式 bootstrap 只创建一个合成开发身份/项目及私有桶；患者和文书由用户操作建立。独立 Worker 默认只做本地解析；OCR/抽取须显式配置启用，不会自动连接外部服务。数据库迁移与初始化可重复执行。配置方式、OIDC 接入、受限角色和故障恢复见 S1 运行手册。

## 验证与契约

```sh
bash scripts/ci/check.sh
pnpm exec playwright install chromium
pnpm test:e2e
pnpm test:app
pnpm test:s1
pnpm test:s2
pnpm test:s3
pnpm test:s4
pnpm test:s5
RUN_DB_TESTS=1 uv run --project backend pytest backend/tests/integration
pnpm contracts:check
pnpm audit --audit-level high
uv run --project backend pip-audit --local
```

完整检查涵盖 lint、格式、类型、单元/契约测试、生成物一致性与前端生产构建。数据库测试必须指向已迁移的本地/CI 专用数据库，默认跳过，需要建立角色权限；集成测试写入独立合成项目，保留不可变台账供审计验证。

修改 Pydantic 契约后执行 `pnpm contracts`，将 OpenAPI、JSON Schema、生成的 TypeScript 一起纳入版本控制。`v1.json` 与 `runtime.json` 均包含当前 S1–S4 已实现接口。合成只读 mock 的启动见 [接口文档](docs/api/contract.md)。

## 原型与交付资料

`index.html` 保持原样，可直接打开。也可 `python3 -m http.server 8765 --bind 127.0.0.1` 后访问 [原型](http://127.0.0.1:8765/index.html)。只使用合成文书；状态只保存在浏览器内存，本地导入仅预览。

- [产品需求](PRD.md) · [开发计划](DEVELOPMENT_PLAN.md)
- [S0 验收记录](docs/testing/s0-report.md) · [原型九场景基线](docs/testing/prototype-baseline.md)
- [ER 图、审核范围与迁移路线](docs/architecture/data-model.md)
- [API、幂等、版本、错误及 mock](docs/api/contract.md)
- [架构决策](docs/decisions/0001-foundation.md) · [开发运维](docs/operations/development.md)

验证记录来自本地执行；远程 CI 结果以实际工作流运行为准。
