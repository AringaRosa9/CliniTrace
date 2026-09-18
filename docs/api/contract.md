# API 契约 v1（S1–S3）

生成命令 `pnpm contracts`；`pnpm contracts:check` 检测模型、OpenAPI、模板 JSON Schema、TypeScript 的漂移。模型源位于 `backend/app/contracts/models.py` 、`backend/app/modules/documents/schema.py` 和 `backend/app/modules/extractions/schema.py`。客户端由 openapi-typescript 生成 DTO，openapi-fetch 提供受路径和请求/响应类型约束的调用。

`openapi/runtime.json` 是实际服务，包含 S1 身份、项目、患者/就诊、文书、任务、预览和审计，以及 S2 OCR/抽取/候选/待审核范围接口。`openapi/v1.json` 与运行时同步，S3 审核与导出接口已挂载；S0 的未实现设计请求已由 S3 正式契约取代。证据通过事实与快照内的完整 Evidence 返回，独立 evidence 样例仍供只读 mock 使用。健康检查仅声明进程存活，不代表数据库/队列/存储就绪。

| 类别 | 方法及项目下路径 | 成功 | 示例 |
| --- | --- | --- | --- |
| 上传 | POST documents，multipart 文件及关联/来源/授权引用 | 202，文书/版本/job/status_url | examples/upload.json |
| 事实 | PATCH facts/{fact_id}，expected_revision + expected_scope_revision + reason + value + evidence | 200，新修订 | examples/fact.json |
| 证据 | GET review-sets/{sid}（事实内 evidence） | 200，版本/页码/span/quote/bbox | examples/evidence.json |
| 审核 | POST reviews，范围版本/最终声明；逐项确认先独立持久化 | 201，不可变快照 | examples/review.json |
| 任务 | GET jobs/{job_id} | 200，阶段/attempt/进度/错误/耗时/费用 | examples/job.json |
| 导出 | POST exports，审核范围及期望版本/用途/格式/草稿开关 | 202，job/export/清单版本 | examples/export.json |

项目业务前缀 `/api/v1/projects/{project_id}`。tenant 来源于验证过的身份与成员映射。`/me`、`/projects` 返回当前获准项目/能力；成员配置使用有审计的管理 CLI，管理员无默认原件特权。S1 服务端执行能力与 RLS 检查，详见 [运行手册](../operations/s1.md)。

## 统一约定

- 金额和精确检验值：十进制字符串；货币显式 CNY/USD。时间：操作时间必须带 UTC offset，输出 UTC；临床时间保留 precision，不伪造时间点。空值显式 null；未知进度/费用为 null，不能标 0。
- 修改传 expected_revision；审核传 expected_scope_revision。冲突 409，前端重新读取后由用户解决差异，不能静默覆盖。
- 上传、抽取、导出使用 `Idempotency-Key`（8–128 字符）。数据库唯一作用域为 tenant+project+actor+operation+key，保留至少 24 小时；执行中的键不因 TTL 删除。相同键与请求摘要返回原状态/资源，相同键不同摘要 409。JSON 摘要按规范化键顺序 UTF-8 SHA-256；上传包含文件 SHA-256、关联、来源和授权引用；不得仅按文件名。键和请求摘要不含病历正文。事务占位与资源创建原子提交，TTL 后重提会形成新操作。
- 列表接口 S1 采用不透明游标、limit 默认 50 最大 200，默认 created_at + id 稳定顺序；next_cursor=null 为结束。筛选与排序白名单；游标不得绕过鉴权。
- 400 格式、401 身份、403 能力、404 不存在或不可见、409 版本/幂等冲突、413 大小、415 MIME、422 业务语义、429 频率。统一 `{code,message,details,request_id,retryable}`。details 只包含安全字段路径/版本，不回显输入、文件内容、堆栈或密钥。只有临时限流/服务不可用标记 retryable。
- request_id 使用 UUID；非法入站值重建；响应头和错误体一致。后台失败放 Job.error；不把异步失败伪装为初始 HTTP 失败。
- 所有业务和敏感响应 no-store。S1 已执行 HttpOnly Cookie、CSRF/Origin、OIDC 校验、能力检查和事务 RLS。机构身份源仍需真实联调。

## 合成 mock

静态六类样例可用于任意客户端 fixture。另提供只读 HTTP mock（仅 evidence/job）：

```sh
cd backend
ALLOW_SYNTHETIC_MOCK=true uv run uvicorn app.contracts.mock:create_mock_app --factory --host 127.0.0.1 --port 8001
```

只有示例 project/resource ID 可访问；其余 404。没有上传、修改或审核成功的伪行为。默认不启用，不与前端自动连接，production 启动时拒绝 synthetic/mock。示例中的 approved 仅是契约样本，不是医学审核结果。

## 兼容性

v1 增加可选字段需更新生成物和样例；删除字段、改变类型/空值/枚举/语义为不兼容，须新版本或迁移窗口。CI 检测生成物漂移与样例验证，不把“生成一致”宣称为完整语义兼容分析；PR 必须记录旧客户端影响。历史快照保留生成时 schema/model/template/terminology 版本。


## S1 运行时补充

- `GET /auth/config`、`POST /auth/synthetic`、`GET /auth/oidc/login`、`GET /auth/oidc/callback`、`POST /auth/logout`。
- `GET /me`、`GET /projects`，项目内 `GET/POST /patients`、`GET/POST /encounters`。
- `GET/POST /documents`、`GET /documents/{id}`、`PATCH /documents/{id}/association`。
- `GET /documents/{id}/original`、`GET /parse-artifacts/{id}`、`GET /parse-artifacts/{id}/pages/{page}`。
- `GET /jobs/{id}`、`POST /jobs/{id}/cancel`、`POST /jobs/{id}/retry`、`GET /audit-events`。

上传兼容原资源标识并增加 `duplicate` 标记。processing_status 增加解析阶段终点 `parsed` 和 `needs_ocr`，不提前进入 ready_for_review。Job 增加取消标记、心跳和尝试历史；费用在本地解析阶段不伪造为模型费用。文档列表稳定游标分页，默认 50、最大 200；患者选择使用编号搜索（默认 50、最大 200），就诊选择限定单个患者。审计返回最近 50 条，可指定最大 200。

写操作携带 `X-CSRF-Token` 和同源 Origin；上传另需 `Idempotency-Key`。浏览器客户端从 Pydantic OpenAPI 重新生成，不手写第二套 DTO。原件只在解析产物提交后开放；读取和下载追加审计。错误无原文、堆栈或密钥；异步失败代码在 Job.error，语义失败不可通过无条件重试绕过。


## S2 运行时补充

- `GET /extraction-config`：当前启用模式与可用性，不返回凭据。
- `POST /documents/{id}/ocr`：显式重解析；`GET/POST /documents/{id}/extractions`：最近 20 个抽取运行 / 创建固定版本运行。两类 POST 均要求 Idempotency-Key。
- `GET /extractions/{id}`：固定配置、事实/证据/关系/问题/候选、逐尝试度量与总用量；未知用量不以 0 代替。
- `POST /documents/{id}/active-run`：理由和 expected_scope_revision 必填，事务切换并重算。
- `GET /encounters/{id}/review-set`：当前待审核范围、版本及问题；`GET /terminology/candidates`：指定已配置词库版本检索。

抽取内容读取需 `original.read`，创建/取消/重试/切换另需 `import`；所有查询仍限制当前项目。运行状态复用 jobs，stage 增加 extracting。有效抽取使文书进入 pending_review；重抽取不自动覆盖当前审核范围。事实编辑、逐项复核、问题处置和审核快照在 S3 已实现。详细网关与 OCR 契约见 [S2 运行手册](../operations/s2.md)。


## S3 运行时补充

- `GET /review-sets`：患者键、就诊 UUID、文书名、审核状态筛选；服务端汇总；UUID 稳定游标，默认 20、上限 100。`GET /review-sets/{sid}`：完整工作台（原始/当前事实、证据、关联、核对、问题、配置）。
- `PATCH /facts/{fid}`、`POST /facts/{fid}/exclude`、`POST /facts`：纠错/排除/补录。完整值与 Evidence 重新校验；所有写操作带理由与期望范围版本，编辑/排除还带期望事实修订号。
- `POST /review-sets/{sid}/fact-checks`、`POST /issues/{iid}/dispositions`：绑定范围及修订；未知豁免按规则白名单。已审核范围的问题处置不能直接原位修改。
- `POST /reviews`：`Approval`（review_set_id、expected_scope_revision、final_confirmation=true），逐项确认从持久化核对台账读取；不是信任请求临时提交的 fact_checks。相同范围版本重复审核幂等。
- `POST /review-sets/{sid}/return`、`GET /review-sets/{sid}/events`：补料要求/审核历史与不可变快照。
- `GET/POST /datasets`、`GET /datasets/{did}/records`：保存筛选、读取动态成员与真实统计。
- `POST /exports`：`ExportCreate.selections` 指定审核集合和期望范围版本；默认 reviewed_only=true，冻结事务查找相应快照。草稿显式 false 并要求 export.draft。导出 ID 与状态地址返回 202。
- `GET /exports`（本人最近 50 个）、`GET /exports/{eid}`、`GET /exports/{eid}/download`：状态/哈希/大小/有效期、受控下载；已过期返回 410。Job.stage 增加 exporting，取消/重试沿用 jobs 接口。

S3 工作台需要 review + original.read；导出需要 export.reviewed，包含草稿再需 export.draft。角色均叠加项目成员资格。前端登录会话失效时清理项目缓存。

S0 仅供设计的 FactPatch、ReviewRequest、ExportRequest 不再代表正式操作请求；`examples/fact.json`、`review.json`、`export.json` 已升级到 Correction、Approval/SnapshotView、ExportCreate。不存在已部署 S3 客户端的原位迁移；当前客户端随 OpenAPI 同步生成。历史 S1/S2 响应兼容，范围版本现在在上传新文书时即递增。
