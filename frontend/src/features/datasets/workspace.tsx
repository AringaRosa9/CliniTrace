"use client";
import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createApiClient } from "@/lib/api/client";
import { result } from "@/lib/api/result";
import type { Models } from "../review/types";
import { states } from "../review/types";
import "../review/workspace.css";
const api = createApiClient();
export function DatasetWorkspace({
  project,
  csrf,
  queue = false,
}: {
  project: Models["ProjectView"];
  csrf: string;
  queue?: boolean;
}) {
  const cache = useQueryClient();
  const path = { project_id: project.id };
  const headers = { "X-CSRF-Token": csrf };
  const [patient, setPatient] = useState("");
  const [document, setDocument] = useState("");
  const [encounter, setEncounter] = useState("");
  const [status, setStatus] = useState(queue ? "pending_review" : "approved");
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<Models["DatasetRecord"][]>([]);
  const [draft, setDraft] = useState(false);
  const [format, setFormat] = useState<"json" | "csv">("json");
  const [purpose, setPurpose] = useState("");
  const [name, setName] = useState("");
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const allowed =
    project.capabilities.includes("review") ||
    project.capabilities.includes("export.reviewed");
  const filters = {
    patient,
    document,
    encounter_id: encounter || undefined,
    status: (status || undefined) as Models["DatasetFilters"]["status"],
  };
  const records = useQuery({
    queryKey: ["project", project.id, "records", filters, cursor, saved],
    enabled: allowed,
    queryFn: () =>
      saved
        ? result(
            api.GET("/api/v1/projects/{project_id}/datasets/{did}/records", {
              params: {
                path: { ...path, did: saved },
                query: { cursor, limit: 20 },
              },
            }),
          )
        : result(
            api.GET("/api/v1/projects/{project_id}/review-sets", {
              params: {
                path,
                query: {
                  ...filters,
                  status: status || undefined,
                  cursor,
                  limit: 20,
                },
              },
            }),
          ),
  });
  const datasets = useQuery({
    queryKey: ["project", project.id, "datasets"],
    enabled: allowed && !queue,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/datasets", { params: { path } }),
      ),
  });
  const exports = useQuery({
    queryKey: ["project", project.id, "exports"],
    enabled: project.capabilities.includes("export.reviewed") && !queue,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/exports", { params: { path } }),
      ),
    refetchInterval: (q) =>
      q.state.data?.some((e) => ["queued", "running"].includes(e.status))
        ? 2000
        : false,
  });
  function reset() {
    setCursor(undefined);
    setSelected([]);
    setSaved("");
  }
  async function action(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      await cache.invalidateQueries({ queryKey: ["project", project.id] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function download(e: Models["ExportView"]) {
    await action(async () => {
      const response = await fetch(
        `/api/v1/projects/${project.id}/exports/${e.id}/download`,
        { cache: "no-store" },
      );
      await result(Promise.resolve({ response, data: true }));
      const url = URL.createObjectURL(await response.blob());
      const a = window.document.createElement("a");
      a.href = url;
      a.download = `clinical-${e.id}.${e.format}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  return (
    <main id="main" tabIndex={-1} className="datasets-main">
      <div className="review-heading">
        <div>
          <p className="eyebrow">{queue ? "REVIEW QUEUE" : "RESEARCH DATA"}</p>
          <h1>{queue ? "审核队列" : "数据集与导出"}</h1>
          <p className="intro">
            {queue
              ? "按就诊核对全部关联文书，完成逐项确认后提交审核。"
              : "筛选研究范围，从已审核快照生成可追溯的数据文件。"}
          </p>
        </div>
        <button onClick={() => void records.refetch()}>刷新列表</button>
      </div>
      {!allowed ? (
        <p>当前账号没有审核或导出权限。</p>
      ) : (
        <>
          <div className="dataset-filters">
            <label className="field">
              患者键
              <input
                value={patient}
                onChange={(e) => {
                  setPatient(e.target.value);
                  reset();
                }}
              />
            </label>
            <label className="field">
              就诊 ID
              <input
                value={encounter}
                onChange={(e) => {
                  setEncounter(e.target.value);
                  reset();
                }}
                placeholder="完整就诊 ID"
              />
            </label>
            <label className="field">
              文书名称
              <input
                value={document}
                onChange={(e) => {
                  setDocument(e.target.value);
                  reset();
                }}
              />
            </label>
            <label className="field">
              审核状态
              <select
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  reset();
                }}
              >
                <option value="">全部状态</option>
                <option value="approved">已审核</option>
                <option value="pending_review">待审核</option>
                <option value="returned">已退回</option>
              </select>
            </label>
          </div>
          {!queue && (
            <div className="saved-datasets">
              <label className="field">
                已保存的数据集
                <select
                  value={saved}
                  onChange={(e) => {
                    setSaved(e.target.value);
                    setCursor(undefined);
                    setSelected([]);
                  }}
                >
                  <option value="">当前筛选</option>
                  {datasets.data?.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void action(async () => {
                    const d = await result(
                      api.POST("/api/v1/projects/{project_id}/datasets", {
                        params: { path },
                        headers,
                        body: { name, filters },
                      }),
                    );
                    setSaved(d.id);
                    setNotice("筛选定义已保存。");
                  });
                }}
              >
                <label className="field">
                  数据集名称
                  <input
                    required
                    maxLength={150}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <button disabled={busy || !name.trim()}>保存筛选定义</button>
              </form>
            </div>
          )}
          {(error || records.error || datasets.error || exports.error) && (
            <p className="notice" role="alert">
              {error ||
                records.error?.message ||
                datasets.error?.message ||
                exports.error?.message}
              <button
                onClick={() => {
                  void records.refetch();
                  void exports.refetch();
                }}
              >
                重试加载
              </button>
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          <p className="dataset-counts">
            {records.data
              ? `共 ${records.data.total} 次就诊 · 已审核 ${records.data.approved_count} 次 · ${records.data.document_count} 份文书 · ${records.data.fact_count} 条候选事实`
              : "正在读取服务端统计…"}
          </p>
          <div className="dataset-table-scroll">
            <table>
              <caption>{queue ? "待办与历史审核" : "当前筛选记录"}</caption>
              <thead>
                <tr>
                  {!queue && <th>选择</th>}
                  <th>患者 / 就诊</th>
                  <th>审核状态</th>
                  <th>文书 / 事实</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {records.data?.items.map((r) => (
                  <tr key={r.review_set_id}>
                    {!queue && (
                      <td>
                        <input
                          aria-label={`选择患者 ${r.patient_key}`}
                          type="checkbox"
                          checked={selected.some(
                            (s) => s.review_set_id === r.review_set_id,
                          )}
                          onChange={(e) =>
                            setSelected(
                              e.target.checked
                                ? [...selected, r]
                                : selected.filter(
                                    (s) => s.review_set_id !== r.review_set_id,
                                  ),
                            )
                          }
                        />
                      </td>
                    )}
                    <td>
                      {r.patient_key}
                      <small>就诊 {r.encounter_id}</small>
                    </td>
                    <td>
                      {states[r.status] ?? r.status}
                      <small>范围 v{r.scope_revision}</small>
                    </td>
                    <td>
                      {r.document_count} 份 / {r.fact_count} 条
                      <small>已核对 {r.checked_count} 条</small>
                    </td>
                    <td>
                      {project.capabilities.includes("review") ? (
                        <Link
                          href={`/projects/${project.id}/reviews/${r.review_set_id}`}
                        >
                          进入审核
                        </Link>
                      ) : (
                        "受控数据范围"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {records.data?.total === 0 && (
            <p className="empty-note">
              没有符合筛选条件的记录。请调整筛选，或在文档任务中完成抽取。
            </p>
          )}
          <div className="actions">
            <button
              disabled={!cursor}
              onClick={() => {
                setCursor(undefined);
                setSelected([]);
              }}
            >
              返回首页
            </button>
            <button
              disabled={!records.data?.next_cursor}
              onClick={() => {
                setCursor(records.data!.next_cursor!);
                setSelected([]);
              }}
            >
              下一页
            </button>
          </div>
          {!queue && project.capabilities.includes("export.reviewed") && (
            <>
              <form
                className="export-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void action(async () => {
                    await result(
                      api.POST("/api/v1/projects/{project_id}/exports", {
                        params: {
                          path,
                          header: { "Idempotency-Key": crypto.randomUUID() },
                        },
                        headers,
                        body: {
                          selections: selected.map((s) => ({
                            review_set_id: s.review_set_id,
                            expected_scope_revision: s.scope_revision,
                          })),
                          reviewed_only: !draft,
                          format,
                          purpose,
                        },
                      }),
                    );
                    setNotice("导出任务已创建，文件内容已按当前版本冻结。");
                    setSelected([]);
                  });
                }}
              >
                <div>
                  <h2>创建导出</h2>
                  <p>已选择 {selected.length} 次就诊 · 默认仅允许已审核快照</p>
                </div>
                <label className="field">
                  导出格式
                  <select
                    value={format}
                    onChange={(e) =>
                      setFormat(e.target.value as "json" | "csv")
                    }
                  >
                    <option value="json">JSON · 完整结构与证据</option>
                    <option value="csv">CSV · 一行一事实修订</option>
                  </select>
                </label>
                <label className="field">
                  数据用途
                  <textarea
                    required
                    maxLength={1000}
                    value={purpose}
                    onChange={(e) => setPurpose(e.target.value)}
                  />
                </label>
                {project.capabilities.includes("export.draft") && (
                  <label className="inline-check">
                    <input
                      type="checkbox"
                      checked={draft}
                      onChange={(e) => setDraft(e.target.checked)}
                    />
                    显式包含草稿（逐条标记，未经审核）
                  </label>
                )}
                <button
                  className="primary"
                  disabled={
                    busy ||
                    !selected.length ||
                    !purpose.trim() ||
                    (!draft && selected.some((s) => s.status !== "approved"))
                  }
                >
                  生成导出文件
                </button>
              </form>
              <section className="export-history">
                <h2>我的导出任务</h2>
                <p className="hint">
                  文件保留 24
                  小时。下载时再次检查权限，操作记录保留用途与文件哈希。
                </p>
                {exports.data?.length === 0 && (
                  <p className="empty-note">尚未创建导出任务。</p>
                )}
                {exports.data?.map((e) => (
                  <article key={e.id}>
                    <div>
                      <strong>
                        {e.format.toUpperCase()} ·{" "}
                        {states[e.status] ?? e.status}
                        {e.reviewed_only ? " · 已审核快照" : " · 包含草稿"}
                      </strong>
                      <p>{e.purpose}</p>
                      <small>
                        有效期至{" "}
                        {new Date(e.expires_at).toLocaleString("zh-CN")}
                      </small>
                      {e.sha256 && (
                        <small className="file-hash">SHA-256 {e.sha256}</small>
                      )}
                      {e.error && <p role="alert">{String(e.error.message)}</p>}
                    </div>
                    {e.status === "succeeded" ? (
                      <button disabled={busy} onClick={() => void download(e)}>
                        下载 {e.format.toUpperCase()}
                      </button>
                    ) : (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void action(() =>
                            result(
                              api.POST(
                                e.status === "failed" ||
                                  e.status === "cancelled"
                                  ? "/api/v1/projects/{project_id}/jobs/{job_id}/retry"
                                  : "/api/v1/projects/{project_id}/jobs/{job_id}/cancel",
                                {
                                  params: {
                                    path: { ...path, job_id: e.job_id },
                                  },
                                  headers,
                                },
                              ),
                            ),
                          )
                        }
                      >
                        {e.status === "failed" || e.status === "cancelled"
                          ? "重试导出"
                          : "取消导出"}
                      </button>
                    )}
                  </article>
                ))}
              </section>
            </>
          )}
        </>
      )}
    </main>
  );
}
