"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createApiClient } from "@/lib/api/client";
import { result } from "@/lib/api/result";
import { DocumentViewer } from "./document-viewer";
import { FactTable, Timeline, JsonView } from "./fact-table";
import { FactEditor } from "./fact-editor";
import { IssuePanel } from "./issue-panel";
import { HistoryDrawer } from "./history-drawer";
import type { Review, Models, Evidence, Fact } from "./types";
import { states } from "./types";
import { useActivity } from "../management/shared";
import { DatasetWorkspace } from "../datasets/workspace";
import "./workspace.css";
const api = createApiClient();
export function ReviewWorkspace({
  project,
  csrf,
  reviewSetId,
}: {
  project: Models["ProjectView"];
  csrf: string;
  reviewSetId?: string;
}) {
  if (
    !project.capabilities.includes("review") ||
    !project.capabilities.includes("original.read")
  )
    return (
      <main id="main">
        <h1>审核工作台</h1>
        <p>需要项目审核和原文读取权限，请联系项目负责人。</p>
      </main>
    );
  if (!reviewSetId)
    return <DatasetWorkspace project={project} csrf={csrf} queue />;
  return (
    <ReviewBody
      key={project.id + reviewSetId}
      project={project}
      csrf={csrf}
      sid={reviewSetId}
    />
  );
}
function ReviewBody({
  project,
  csrf,
  sid,
}: {
  project: Models["ProjectView"];
  csrf: string;
  sid: string;
}) {
  const activity = useActivity();
  const session = useRef<string | null>(null);
  const cache = useQueryClient();
  const path = { project_id: project.id, sid };
  const query = useQuery({
    queryKey: ["project", project.id, "workspace", sid],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/review-sets/{sid}", {
          params: { path },
        }),
      ),
  });
  const [selected, setSelected] = useState<Fact>();
  const [evidence, setEvidence] = useState<Evidence>();
  const [view, setView] = useState("facts");
  const [editor, setEditor] = useState<{
    fact?: Fact;
    evidence?: Evidence;
    key: string;
  }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState(false);
  async function mutate(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      session.current ??= crypto.randomUUID();
      try {
        await result(
          api.POST("/api/v1/projects/{project_id}/review-sets/{sid}/activity", {
            params: { path },
            headers: { "X-CSRF-Token": csrf },
            body: {
              session_id: session.current,
              active_seconds: activity.seconds(),
            },
          }),
        );
      } catch {
        setMessage("操作已保存，审核耗时暂未记录。后续操作会重试累计记录。");
      }
      await cache.invalidateQueries({ queryKey: ["project", project.id] });
      setMessage("已保存，审核范围和状态已更新。");
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  function locate(f: Fact, e: Evidence) {
    setSelected(f);
    setEvidence(e);
    setView("facts");
    requestAnimationFrame(() =>
      document
        .getElementById(`fact-${f.fact_id}`)
        ?.scrollIntoView({ block: "nearest" }),
    );
  }
  const w = query.data;
  const headers = { "X-CSRF-Token": csrf };
  return (
    <main
      id="main"
      className="review-main"
      onKeyDown={activity.tick}
      onPointerDown={activity.tick}
      onInput={activity.tick}
    >
      <div className="review-heading">
        <div>
          <p className="eyebrow">EVIDENCE & REVIEW</p>
          <h1>审核工作台</h1>
        </div>
        <div className="actions">
          <Link href={`/projects/${project.id}/reviews`}>返回审核队列</Link>
          <button onClick={() => void query.refetch()}>刷新审核范围</button>
          <button onClick={() => setHistory(true)}>修订历史</button>
        </div>
      </div>
      {error && (
        <p role="alert" className="notice">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="save-message">
          {message}
        </p>
      )}
      {query.error && (
        <p role="alert">
          {query.error.message}
          <button onClick={() => void query.refetch()}>重新加载工作台</button>
        </p>
      )}
      {query.isPending && <p>正在加载审核范围…</p>}
      {w && (
        <>
          <CaseContext review={w} />
          <div className="review-columns">
            <DocumentViewer
              key={evidence?.id ?? w.id}
              projectId={project.id}
              review={w}
              evidence={evidence}
              onSelect={locate}
              onEvidenceDraft={(e) =>
                setEditor({ evidence: e, key: crypto.randomUUID() })
              }
            />
            <section className="review-facts">
              <div className="panel-title">
                <h2>结构化事实</h2>
                <span>
                  {w.checked_revision_ids.length} / {w.facts.length} 已核对
                </span>
              </div>
              <div className="view-tabs" role="group" aria-label="事实视图">
                {[
                  ["facts", "抽取字段"],
                  ["timeline", "病程时间线"],
                  ["json", "JSON"],
                ].map(([id, name]) => (
                  <button
                    key={id}
                    aria-pressed={view === id}
                    onClick={() => setView(id)}
                  >
                    {name}
                  </button>
                ))}
              </div>
              {view === "facts" ? (
                <FactTable
                  review={w}
                  busy={busy}
                  selected={selected?.fact_id}
                  onSelect={locate}
                  onEdit={(f) =>
                    setEditor({ fact: f, key: crypto.randomUUID() })
                  }
                  onCheck={(f) =>
                    void mutate(() =>
                      result(
                        api.POST(
                          "/api/v1/projects/{project_id}/review-sets/{sid}/fact-checks",
                          {
                            params: { path },
                            headers,
                            body: {
                              expected_scope_revision: w.scope_revision,
                              fact_revision_id: f.revision_id,
                            },
                          },
                        ),
                      ),
                    )
                  }
                />
              ) : view === "timeline" ? (
                <Timeline review={w} onSelect={locate} />
              ) : (
                <JsonView review={w} />
              )}
              {editor && (
                <FactEditor
                  key={editor.key}
                  review={w}
                  fact={editor.fact}
                  evidence={editor.evidence}
                  busy={busy}
                  onClose={() => setEditor(undefined)}
                  onSave={(body) =>
                    mutate(() =>
                      "review_set_id" in body
                        ? result(
                            api.POST("/api/v1/projects/{project_id}/facts", {
                              params: { path: { project_id: project.id } },
                              headers,
                              body,
                            }),
                          )
                        : result(
                            api.PATCH(
                              "/api/v1/projects/{project_id}/facts/{fid}",
                              {
                                params: {
                                  path: {
                                    project_id: project.id,
                                    fid: editor.fact!.fact_id,
                                  },
                                },
                                headers,
                                body,
                              },
                            ),
                          ),
                    )
                  }
                  onExclude={(body) =>
                    mutate(() =>
                      result(
                        api.POST(
                          "/api/v1/projects/{project_id}/facts/{fid}/exclude",
                          {
                            params: {
                              path: {
                                project_id: project.id,
                                fid: editor.fact!.fact_id,
                              },
                            },
                            headers,
                            body,
                          },
                        ),
                      ),
                    )
                  }
                />
              )}
            </section>
            <IssuePanel
              review={w}
              busy={busy}
              onLocate={(rid) => {
                const f = w.facts.find((f) => f.revision_id === rid);
                if (f) locate(f, f.evidence[0]);
              }}
              onDispose={(iid, body) =>
                mutate(() =>
                  result(
                    api.POST(
                      "/api/v1/projects/{project_id}/issues/{iid}/dispositions",
                      {
                        params: { path: { project_id: project.id, iid } },
                        headers,
                        body,
                      },
                    ),
                  ),
                )
              }
            />
          </div>
          <ReviewFooter
            key={w.scope_revision}
            review={w}
            busy={busy}
            onApprove={() =>
              mutate(() =>
                result(
                  api.POST("/api/v1/projects/{project_id}/reviews", {
                    params: { path: { project_id: project.id } },
                    headers,
                    body: {
                      review_set_id: sid,
                      expected_scope_revision: w.scope_revision,
                      final_confirmation: true,
                    },
                  }),
                ),
              )
            }
            onReturn={(reason, required_material) =>
              mutate(() =>
                result(
                  api.POST(
                    "/api/v1/projects/{project_id}/review-sets/{sid}/return",
                    {
                      params: { path },
                      headers,
                      body: {
                        expected_scope_revision: w.scope_revision,
                        reason,
                        required_material,
                      },
                    },
                  ),
                ),
              )
            }
          />
        </>
      )}
      {history && (
        <HistoryDrawer
          projectId={project.id}
          sid={sid}
          onClose={() => setHistory(false)}
        />
      )}
    </main>
  );
}
export function CaseContext({ review: w }: { review: Review }) {
  return (
    <section className="case-context" aria-label="病例范围">
      <strong>患者 {w.patient_key}</strong>
      <span>就诊 {w.encounter_id.slice(-8)}</span>
      <span>{w.documents.length} 份关联文书</span>
      <span>范围 v{w.scope_revision}</span>
      <strong
        className={w.status === "approved" ? "approved-label" : "amber-label"}
      >
        {states[w.status] ?? w.status}
      </strong>
    </section>
  );
}
function ReviewFooter({
  review: w,
  busy,
  onApprove,
  onReturn,
}: {
  review: Review;
  busy: boolean;
  onApprove: () => Promise<boolean>;
  onReturn: (reason: string, material: string) => Promise<boolean>;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [reason, setReason] = useState("");
  const [material, setMaterial] = useState("");
  const blockers = w.issues.filter(
    (i) => i.severity === "blocking" && i.status === "open",
  ).length;
  const unchecked = w.facts.length - w.checked_revision_ids.length;
  const incomplete = w.documents.some((d) => !d.run_id);
  return (
    <footer className="review-footer">
      <div>
        <p>
          {blockers} 项阻断 · {unchecked} 项待核对
          {incomplete ? " · 存在未完成抽取的文书" : ""}
        </p>
        <label className="inline-check">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          我已逐项核对原文、事实、未知语义及问题处置，确认完成本次审核。
        </label>
      </div>
      <button
        className="primary"
        disabled={
          busy ||
          !confirmed ||
          !!blockers ||
          !!unchecked ||
          incomplete ||
          !w.facts.length ||
          w.status !== "pending_review"
        }
        onClick={() => void onApprove()}
      >
        {w.status === "approved" ? "已完成审核" : "完成审核并冻结快照"}
      </button>
      <details>
        <summary>退回补充材料</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void onReturn(reason, material);
          }}
        >
          <label className="field">
            退回理由
            <textarea
              required
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <label className="field">
            待补充材料
            <input
              required
              value={material}
              onChange={(e) => setMaterial(e.target.value)}
            />
          </label>
          <button disabled={busy || !reason.trim() || !material.trim()}>
            退回补料
          </button>
        </form>
      </details>
    </footer>
  );
}
