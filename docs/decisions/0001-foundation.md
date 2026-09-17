# ADR-0001：模块化 API 与独立前端

状态：接受（工程基线），2026-09-16。

Next.js App Router/React/TypeScript/pnpm，FastAPI/Pydantic/uv，PostgreSQL/SQLAlchemy/Alembic；异步任务未来由同仓 Celery Worker 执行。API 是唯一业务授权和事务入口，Next.js 只代理同源 `/api/v1`。版本以 package.json、pnpm-lock.yaml、pyproject.toml、uv.lock 为准；锁文件必须进入后续版本控制。

本地实测发现 TypeScript 7 与当前 typescript-eslint 不兼容，固定为 TypeScript 5.9.3 + ESLint 9.39.4。Node 24、Python 3.12 为 CI/runtime 基线。不会把原型全局 approved 状态包装成服务。正式业务页面和 Worker 在对应 S1–S4 阶段实现。

参考：[Next.js 安装](https://nextjs.org/docs/app/getting-started/installation)、[FastAPI OpenAPI](https://fastapi.tiangolo.com/tutorial/first-steps/)。
