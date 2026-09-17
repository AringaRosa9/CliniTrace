# S0 工程交付与验收记录

日期：2026-09-16。执行环境：macOS arm64、Node 24.13.0、pnpm 12.4.2、Python 3.12.12、uv 0.10.6、Docker Desktop 29.4.1。

结论：FND-01～04 工程交付完成；FND-05 的本地合成开发基线完成，机构身份源、词库/模型/样本授权与付费预算尚未提供，因此不将整个 S0 的机构决策验收标记为完成。不影响继续 S1 本地合成开发。

## 交付对应

| 工单 | 交付与证据 | 状态 |
| --- | --- | --- |
| FND-01 | 原型九场景、五视口截图、键盘/导出/搜索/导入回归；prototype-baseline.md | 完成 |
| FND-02 | Next.js/FastAPI、精确依赖/锁文件、健康/统一错误、格式/lint/typecheck、CI、启动说明、Compose | 完成；CI 配置已写入，尚无远程执行记录 |
| FND-03 | ER 图、审核范围/不可变修订、缺失/证据语义、门诊/检验 Schema、四份 ADR、0001_scope 迁移 | 完成工程设计；模板尚非医学发布版本 |
| FND-04 | 六类请求/响应及错误样例、幂等/版本/空值/时间/金额规则、OpenAPI、生成 TS 客户端、显式合成 mock、双端 span 测试 | 完成；业务接口仍按 S1–S3 实现 |
| FND-05 | 合成身份模式/样本/词库版本、关闭外部调用、0 元预算、资源配置、机构接入材料与责任清单 | 开发部分完成；外部决策待确认 |

## 已执行验证

| 检查 | 结果 |
| --- | --- |
| 后端 pytest（含实际 PostgreSQL） | 19 passed；语义、模板、span、错误、mock/production 门禁、RLS/复合外键/连接池上下文 |
| 前端 Vitest | 7 passed；共享中文/emoji span、非法边界、生成客户端 |
| 原型 Playwright | 14 passed；九场景＋390/768/1024/1440/1920 |
| 新应用 Playwright | 3 passed；390/768/1440，实际 FastAPI 与 Next 生产构建，刷新/同源代理/无 JS 异常/无水平溢出 |
| Ruff lint/format、mypy | 通过 |
| ESLint、Prettier、TypeScript、Next 生产构建 | 通过 |
| 契约生成漂移检查 | 通过；runtime/v1 OpenAPI、两类 JSON Schema、TS 生成类型 |
| Alembic | upgrade head / check / downgrade base / upgrade head 通过；空的专用开发库，无临床数据 |
| Compose | PostgreSQL、Redis、MinIO 已启动；仅本地端口 |
| Redis/S3 | PING、私有 bucket 合成写入/读取/删除通过，临时对象与 bucket 已删除 |
| 依赖扫描 | pnpm audit 与 pip-audit 均报告 No known vulnerabilities found |
| peer dependencies | 无冲突 |

合计 43 项自动测试通过，另含迁移/服务连通性和静态检查。测试依赖 Starlette 发出 httpx/AnyIO API 弃用提示，不影响通过；后续框架升级时迁移，未屏蔽提示。

执行命令和重跑方式见根 README、scripts/ci/check.sh 与 CI 配置。截图保存在 `docs/testing/baseline/`，原型与新应用明确分开。

## 修复与边界

实际验证发现并修复：TypeScript 7 与 eslint 解析器不兼容、初始复合唯一键命名碰撞、原型截图采到淡入动画。已有其他项目占用常见端口，因此本项目改用 API 18000、前端 13000、PG 25432、Redis 26379、S3 59000/59001；未停止或改动其他项目服务。

FND-05 不能代替机构授权：仅使用自行构造的合成样本，正式词库编码保持空值，不调用外部 OCR/模型，预算 0。生产配置拒绝 synthetic/mock。尚无 OIDC 登录、业务上传/任务/审核/导出实现，也没有临床质量或性能声明。

原型保持原样，字号/触控/完整无障碍仍按 S3/S5 改进。实际解析页面坐标、业务越权、多用户并发、模型质量与机构部署均属于后续阶段。

当前目录不是 Git 仓库；文件及锁文件已写入工作区，没有创建 commit、PR 或运行远程 CI。浏览器冒烟启动的 API/前端随测试结束自动退出；Compose 开发基础设施保留，可按 README 停止。
