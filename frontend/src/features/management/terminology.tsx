"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createApiClient } from "@/lib/api/client";
import { result } from "@/lib/api/result";
import {
  Field,
  Json,
  SubmitForm,
  RecordDetails,
  type Props,
  type Models,
} from "./shared";
const api = createApiClient();
export function Terminology({ project, csrf }: Props) {
  const path = { project_id: project.id },
    headers = { "X-CSRF-Token": csrf };
  const [selected, setSelected] = useState("");
  const [termsText, setTermsText] = useState("[]");
  const [query, setQuery] = useState("");
  const [reviewId, setReviewId] = useState("");
  const [factId, setFactId] = useState("");
  const versions = useQuery({
    queryKey: ["project", project.id, "terminologies"],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/terminology/versions", {
          params: { path },
        }),
      ),
  });
  const tid =
    selected ||
    versions.data?.find((t) => t.active)?.id ||
    versions.data?.[0]?.id ||
    "";
  const current = versions.data?.find((t) => t.id === tid);
  const search = useQuery({
    queryKey: ["project", project.id, "terms-search", tid, query],
    enabled: !!tid && !!query,
    queryFn: () =>
      result(
        api.GET(
          "/api/v1/projects/{project_id}/terminology/versions/{tid}/search",
          { params: { path: { ...path, tid }, query: { q: query } } },
        ),
      ),
  });
  const queue = useQuery({
    queryKey: ["project", project.id, "mapping-queue"],
    enabled: project.capabilities.includes("review"),
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/review-sets", {
          params: { path, query: { limit: 100 } },
        }),
      ),
  });
  const workspace = useQuery({
    queryKey: ["project", project.id, "mapping-review", reviewId],
    enabled: !!reviewId,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/review-sets/{sid}", {
          params: { path: { ...path, sid: reviewId } },
        }),
      ),
  });
  const facts =
    workspace.data?.facts.filter(
      (f) =>
        !f.excluded &&
        ["diagnoses", "history", "observations.name"].includes(f.field_path),
    ) ?? [];
  const fact = facts.find((f) => f.fact_id === factId);
  const history = useQuery({
    queryKey: ["project", project.id, "mapping-history", factId],
    enabled: !!fact,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/facts/{fid}/mappings", {
          params: { path: { ...path, fid: factId } },
        }),
      ),
  });
  return (
    <>
      <div className="management-heading">
        <div>
          <h1>术语管理</h1>
          <p>固定词库版本、核对候选依据，保留每次映射决定。</p>
        </div>
      </div>
      {[
        versions.error,
        search.error,
        queue.error,
        workspace.error,
        history.error,
      ]
        .filter(Boolean)
        .map((e, i) => (
          <p role="alert" key={i}>
            {e?.message}
          </p>
        ))}
      <div className="management-columns">
        <section>
          <h2>词库版本</h2>
          {versions.isPending ? (
            <p>正在加载…</p>
          ) : !versions.data?.length ? (
            <p>暂无词库。导入获准版本后可检索；未提供授权记录不能激活。</p>
          ) : (
            versions.data.map((v) => (
              <button
                className="record-select"
                key={v.id}
                aria-pressed={tid === v.id}
                onClick={() => setSelected(v.id)}
              >
                <strong>{v.version}</strong>
                <span>
                  {v.active ? "已激活" : "未激活"} ·{" "}
                  {v.payload.synthetic ? "合成词库" : "授权词库"} ·{" "}
                  {v.payload.terms.length} 条
                </span>
              </button>
            ))
          )}
          {current && (
            <>
              <RecordDetails title="版本与授权记录" value={current} />
              <SubmitForm
                disabled={
                  !project.capabilities.includes("terminology.manage") ||
                  current.active
                }
                label="激活此版本"
                submit={async () =>
                  result(
                    api.POST(
                      "/api/v1/projects/{project_id}/terminology/versions/{tid}/activate",
                      { params: { path: { ...path, tid } }, headers },
                    ),
                  )
                }
              >
                <p>仅影响新建抽取运行；历史运行继续使用原版本。</p>
              </SubmitForm>
            </>
          )}
          <details>
            <summary>导入词库新版本</summary>
            <SubmitForm
              disabled={!project.capabilities.includes("terminology.manage")}
              label="导入版本"
              submit={async (form) => {
                const v = await result(
                  api.POST(
                    "/api/v1/projects/{project_id}/terminology/versions",
                    {
                      params: { path },
                      headers,
                      body: {
                        version: String(form.get("version")),
                        system: String(form.get("system")),
                        authorization_reference: String(
                          form.get("authorization"),
                        ),
                        synthetic: form.has("synthetic"),
                        terms: JSON.parse(termsText),
                      },
                    },
                  ),
                );
                setSelected(v.id);
              }}
            >
              <Field label="版本标识">
                <input
                  required
                  name="version"
                  placeholder="research-terms-1.0.0"
                />
              </Field>
              <Field label="编码体系">
                <input required name="system" />
              </Field>
              <Field label="授权记录引用">
                <input name="authorization" />
              </Field>
              <label>
                <input type="checkbox" name="synthetic" />
                合成开发词库
              </label>
              <p className="hint">
                每条术语包含 code、display、aliases 数组和
                context（标本、方法或其他消歧依据）。
              </p>
              <Json
                label="术语 JSON 数组"
                value={termsText}
                onChange={setTermsText}
              />
            </SubmitForm>
          </details>
        </section>
        <section>
          <h2>检索与人工映射</h2>
          <Field label="术语、别名或编码">
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </Field>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>编码</th>
                  <th>标准名称</th>
                  <th>候选依据</th>
                </tr>
              </thead>
              <tbody>
                {search.data?.map((t) => (
                  <tr key={t.code}>
                    <td>{t.code}</td>
                    <td>{t.display}</td>
                    <td>{t.basis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {query && !search.isFetching && !search.data?.length && (
            <p>此版本未找到候选，可保留待映射并记录原因。</p>
          )}
          <Field label="选择审核病例">
            <select
              value={reviewId}
              onChange={(e) => {
                setReviewId(e.target.value);
                setFactId("");
              }}
            >
              <option value="">请选择</option>
              {queue.data?.items.map((r) => (
                <option key={r.review_set_id} value={r.review_set_id}>
                  {r.patient_key} · {r.review_set_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="选择待映射事实">
            <select
              value={factId}
              onChange={(e) => {
                setFactId(e.target.value);
                setQuery(
                  facts.find((f) => f.fact_id === e.target.value)?.value.raw ??
                    "",
                );
              }}
            >
              <option value="">请选择</option>
              {facts.map((f) => (
                <option key={f.fact_id} value={f.fact_id}>
                  {f.value.raw} · {f.field_path}
                </option>
              ))}
            </select>
          </Field>
          {fact && (
            <>
              <blockquote>
                {fact.evidence.map((e) => e.quote).join("\n")}
              </blockquote>
              <SubmitForm
                key={`${factId}-${tid}`}
                disabled={
                  !project.capabilities.includes("terminology.map") ||
                  !tid ||
                  history.isPending ||
                  !!history.error
                }
                label="保存映射决定"
                submit={async (form) =>
                  result(
                    api.POST(
                      "/api/v1/projects/{project_id}/facts/{fid}/mapping",
                      {
                        params: { path: { ...path, fid: factId } },
                        headers,
                        body: {
                          revision_id: fact.revision_id,
                          terminology_id: tid,
                          expected_sequence:
                            history.data?.at(-1)?.sequence ?? 0,
                          status: String(
                            form.get("status"),
                          ) as Models["MappingRequest"]["status"],
                          code:
                            String(form.get("status")) === "confirmed"
                              ? String(form.get("code"))
                              : null,
                          reason: String(form.get("reason")),
                        },
                      },
                    ),
                  )
                }
              >
                <Field label="处理结果">
                  <select name="status">
                    <option value="pending">保留待映射</option>
                    <option value="confirmed">确认编码</option>
                    <option value="rejected">拒绝候选</option>
                  </select>
                </Field>
                <Field label="确认编码">
                  <select name="code">
                    <option value="">请选择检索候选</option>
                    {search.data?.map((t) => (
                      <option key={t.code} value={t.code}>
                        {t.code} · {t.display}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="上下文依据与理由">
                  <textarea required name="reason" rows={3} />
                </Field>
                <p className="hint">
                  保存会使当前审核失效，需要重新核对；已有审核快照保留。
                </p>
              </SubmitForm>
              <h3>映射历史</h3>
              {history.data?.map((h) => (
                <RecordDetails
                  key={h.id}
                  title={`决定 ${h.sequence} · ${h.payload.status}`}
                  value={h}
                />
              ))}
            </>
          )}
        </section>
      </div>
    </>
  );
}
