import { RefreshStatus } from "@/components/ui/refresh-status";
import { createApiClient } from "@/lib/api/client";
export const dynamic = "force-dynamic";

export default async function Home() {
  let available = false;
  try {
    const { data } = await createApiClient(
      process.env.API_INTERNAL_URL ?? "http://127.0.0.1:18000",
    ).GET("/api/v1/health", { signal: AbortSignal.timeout(2500) });
    available = data?.status === "ok" && data.service === "clinical-data-api";
  } catch {
    /* The workspace remains readable when the API is offline. */
  }
  const modules = [
    ["01", "文档任务", "导入门诊记录与检验报告，关联患者和就诊。", "S1"],
    ["02", "审核工作台", "对照原文证据，核对事实并保留修改依据。", "S3"],
    ["03", "抽取模板", "固定字段、缺失语义和证据要求。", "S4"],
    ["04", "术语管理", "按指定词库版本确认映射，保留待映射项。", "S4"],
    ["05", "数据集", "基于已审核快照交付可追溯的数据。", "S3"],
    ["06", "质量评测", "在冻结样本集上记录质量、耗时与成本。", "S4"],
  ];
  return (
    <div className="workspace">
      <a className="skip" href="#main">
        跳转到主要内容
      </a>
      <aside className="sidebar">
        <div className="brand">
          <svg viewBox="0 0 40 40" aria-hidden="true">
            <path d="M16 29H4V4h13l6 6v8M17 4v7h6M8 15h10M8 20h7M8 25h4M15 29h8m-3-3 3 3-3 3" />
            <rect x="26" y="21" width="11" height="15" rx=".7" />
            <path d="M26 26h11M26 31h11M31.5 26v10" />
          </svg>
          <span>
            临床数据
            <br />
            结构化平台
          </span>
        </div>
        <p className="sidebar-label">临床数据研究空间</p>
        <nav aria-label="业务模块">
          {modules.map(([n, title, , stage]) => (
            <a
              key={n}
              href={
                n === "01"
                  ? "/documents"
                  : n === "02"
                    ? "/reviews"
                    : n === "05"
                      ? "/datasets"
                      : `#module-${n}`
              }
            >
              <span>{title}</span>
              <small>{stage}</small>
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">准确 · 清晰 · 可追溯</div>
      </aside>
      <div className="content">
        <header>
          <span>工作空间 / 概览</span>
          <span className="badge">开发环境 · 审核与数据交付</span>
        </header>
        <main id="main">
          <p className="eyebrow">CLINICAL DATA STRUCTURING PLATFORM</p>
          <h1>从原始文书，到有据可循的数据。</h1>
          <p className="intro">
            文档、审核和数据集工作空间已开放。导入文书后核对原文证据，完成审核并导出可追溯的数据。
          </p>
          <section className="connection" aria-label="服务状态">
            <div>
              <span
                className={available ? "indicator online" : "indicator"}
                aria-hidden="true"
              />
              <strong>{available ? "API 进程可用" : "API 暂未连接"}</strong>
              <p>
                {available
                  ? "基础健康检查通过。进入文档任务可登录并处理文书。"
                  : "请检查服务启动状态后刷新页面。"}
              </p>
            </div>
            <RefreshStatus />
          </section>
          <section aria-labelledby="modules-heading">
            <div className="section-heading">
              <h2 id="modules-heading">工作流程</h2>
              <span>功能开放计划</span>
            </div>
            <ol className="modules">
              {modules.map(([n, title, description, stage]) => (
                <li id={`module-${n}`} key={n}>
                  <span className="step-number">{n}</span>
                  <div>
                    <h3>{title}</h3>
                    <p>{description}</p>
                  </div>
                  <span className="stage">
                    {n === "01" ? (
                      <a href="/documents">进入文档任务 →</a>
                    ) : n === "02" ? (
                      <a href="/reviews">进入审核工作台 →</a>
                    ) : n === "05" ? (
                      <a href="/datasets">进入数据集 →</a>
                    ) : (
                      `${stage} · 待开放`
                    )}
                  </span>
                </li>
              ))}
            </ol>
          </section>
          <p className="footnote">
            数据按登录项目隔离。OCR 与抽取需显式启用获准服务；质量评测在 S4
            开放。
          </p>
        </main>
        <footer>
          临床数据结构化平台 <span>基础工程 v0.1.0</span>
        </footer>
      </div>
    </div>
  );
}
