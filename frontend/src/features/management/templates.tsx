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
  pretty,
  type Props,
  type Models,
} from "./shared";
const api = createApiClient();
export function Templates({ project, csrf }: Props) {
  const path = { project_id: project.id },
    headers = { "X-CSRF-Token": csrf };
  const [selected, setSelected] = useState<Models["TemplateView"]>();
  const [kind, setKind] = useState<"outpatient" | "laboratory">("outpatient");
  const [schema, setSchema] = useState("{}");
  const [guide, setGuide] = useState("");
  const [positive, setPositive] = useState(
    '[{"schema_version":"outpatient-1.0.0","diagnoses":[],"history":[],"medications":[],"duration":[]}]',
  );
  const [negative, setNegative] = useState("[{}]");
  const [evidence, setEvidence] = useState(
    "每条事实必须有同文书同解析版本的原文证据；未知不得补全。",
  );
  const [validation, setValidation] = useState("");
  const drafts = useQuery({
    queryKey: ["project", project.id, "templates"],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/templates", {
          params: { path },
        }),
      ),
  });
  const versions = useQuery({
    queryKey: ["project", project.id, "template-versions"],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/template-versions", {
          params: { path },
        }),
      ),
  });
  const can = project.capabilities.includes("templates.manage");
  const content = () => ({
    schema_definition: JSON.parse(schema),
    guide,
    positive_examples: JSON.parse(positive),
    negative_examples: JSON.parse(negative),
    evidence_rules: evidence.split("\n").filter(Boolean),
  });
  return (
    <>
      <div className="management-heading">
        <div>
          <h1>抽取模板</h1>
          <p>维护字段约束与标注指南。发布后保留原版本，后续修改另行发布。</p>
        </div>
        <span className="badge">{versions.data?.length ?? 0} 个已发布版本</span>
      </div>
      {(drafts.error || versions.error) && (
        <p role="alert">{drafts.error?.message ?? versions.error?.message}</p>
      )}
      <div className="management-columns">
        <section>
          <h2>模板草稿</h2>
          {drafts.isPending ? (
            <p>正在加载…</p>
          ) : !drafts.data?.length ? (
            <p>暂无模板。选择文书类型，载入基础字段后创建第一个草稿。</p>
          ) : (
            drafts.data.map((d) => (
              <button
                className="record-select"
                aria-pressed={selected?.id === d.id}
                key={d.id}
                onClick={() => {
                  setSelected(d);
                  setKind(d.document_type as typeof kind);
                  setSchema(pretty(d.payload.schema_definition));
                  setGuide(d.payload.guide);
                  setPositive(pretty(d.payload.positive_examples));
                  setNegative(pretty(d.payload.negative_examples));
                  setEvidence(d.payload.evidence_rules.join("\n"));
                  setValidation("");
                }}
              >
                <strong>{d.name}</strong>
                <span>
                  修订 {d.revision} ·{" "}
                  {d.document_type === "outpatient" ? "门诊" : "检验"}
                </span>
              </button>
            ))
          )}
          <h2>发布记录</h2>
          {versions.data?.map((v) => (
            <RecordDetails key={v.id} title={v.version} value={v} />
          ))}
        </section>
        <section>
          <h2>
            {selected
              ? `${selected.name} · 修订 ${selected.revision}`
              : "新建模板"}
          </h2>
          <button
            onClick={() => {
              setSelected(undefined);
              setValidation("");
            }}
          >
            新建草稿
          </button>
          <SubmitForm
            disabled={!can}
            label={selected ? "保存新修订" : "创建草稿"}
            submit={async (form) => {
              const saved = selected
                ? await result(
                    api.PATCH("/api/v1/projects/{project_id}/templates/{tid}", {
                      params: { path: { ...path, tid: selected.id } },
                      headers,
                      body: {
                        ...content(),
                        expected_revision: selected.revision,
                      },
                    }),
                  )
                : await result(
                    api.POST("/api/v1/projects/{project_id}/templates", {
                      params: { path },
                      headers,
                      body: {
                        ...content(),
                        name: String(form.get("name")),
                        document_type: kind,
                      },
                    }),
                  );
              setSelected(saved);
              setValidation("");
            }}
          >
            {!selected && (
              <>
                <Field label="模板名称">
                  <input name="name" required maxLength={150} />
                </Field>
                <Field label="文书类型">
                  <select
                    value={kind}
                    onChange={(e) => setKind(e.target.value as typeof kind)}
                  >
                    <option value="outpatient">门诊记录</option>
                    <option value="laboratory">检验报告</option>
                  </select>
                </Field>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      setSchema(
                        pretty(
                          await result(
                            api.GET(
                              "/api/v1/projects/{project_id}/templates/base/{kind}",
                              { params: { path: { ...path, kind } } },
                            ),
                          ),
                        ),
                      );
                      setPositive(
                        pretty([
                          {
                            schema_version: `${kind}-1.0.0`,
                            ...(kind === "outpatient"
                              ? {
                                  diagnoses: [],
                                  history: [],
                                  medications: [],
                                  duration: [],
                                }
                              : { observations: [] }),
                          },
                        ]),
                      );
                    } catch (e) {
                      setValidation((e as Error).message);
                    }
                  }}
                >
                  载入基础字段
                </button>
              </>
            )}
            <Field label="指南与语义说明">
              <textarea
                required
                rows={5}
                value={guide}
                onChange={(e) => setGuide(e.target.value)}
              />
            </Field>
            <Field label="证据规则（每行一条）">
              <textarea
                required
                rows={3}
                value={evidence}
                onChange={(e) => setEvidence(e.target.value)}
              />
            </Field>
            <details>
              <summary>字段约束表单</summary>
              <SchemaFields value={schema} onChange={setSchema} />
            </details>
            <Json label="Schema JSON" value={schema} onChange={setSchema} />
            <Json
              label="正例 JSON 数组"
              value={positive}
              onChange={setPositive}
            />
            <Json
              label="反例 JSON 数组"
              value={negative}
              onChange={setNegative}
            />
          </SubmitForm>
          {selected && can && (
            <>
              <button
                onClick={async () => {
                  try {
                    const r = await result(
                      api.POST(
                        "/api/v1/projects/{project_id}/templates/{tid}/validate",
                        {
                          params: { path: { ...path, tid: selected.id } },
                          headers,
                        },
                      ),
                    );
                    setValidation(
                      r.valid
                        ? "已保存草稿通过 Schema 与兼容性校验。"
                        : r.errors.join("；"),
                    );
                  } catch (e) {
                    setValidation((e as Error).message);
                  }
                }}
              >
                校验已保存草稿
              </button>
              <p role="status">{validation}</p>
              <SubmitForm
                label="发布不可变版本"
                submit={async (form) =>
                  result(
                    api.POST(
                      "/api/v1/projects/{project_id}/templates/{tid}/publish",
                      {
                        params: { path: { ...path, tid: selected.id } },
                        headers,
                        body: {
                          expected_revision: selected.revision,
                          version: String(form.get("version")),
                        },
                      },
                    ),
                  )
                }
              >
                <Field label="新版本号">
                  <input
                    required
                    name="version"
                    placeholder={`${kind}-1.1.0`}
                  />
                </Field>
                <p className="hint">
                  发布使用最近保存的草稿。基础 1.0.0
                  版本保留；不兼容的字段类型或证据约束会阻止发布。
                </p>
              </SubmitForm>
            </>
          )}
          {!can && <p>当前账号可查看模板，编辑需要模板管理权限。</p>}
        </section>
      </div>
    </>
  );
}
function SchemaFields({
  value,
  onChange,
}: {
  value: string;
  onChange: (s: string) => void;
}) {
  let schema: {
    properties?: Record<
      string,
      {
        type?: string;
        minItems?: number;
        description?: string;
        enum?: string[];
      }
    >;
    required?: string[];
  };
  try {
    schema = JSON.parse(value);
  } catch {
    return <p>请先修正 JSON 格式。</p>;
  }
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>字段</th>
            <th>类型</th>
            <th>必填</th>
            <th>数组最少项</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(schema.properties ?? {}).map(([name, field]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{field.type ?? "引用"}</td>
              <td>
                <input
                  aria-label={`${name} 必填`}
                  type="checkbox"
                  checked={schema.required?.includes(name) ?? false}
                  onChange={(e) =>
                    onChange(
                      pretty({
                        ...schema,
                        required: e.target.checked
                          ? [...(schema.required ?? []), name]
                          : schema.required?.filter((n) => n !== name),
                      }),
                    )
                  }
                />
              </td>
              <td>
                {field.type === "array" && (
                  <input
                    aria-label={`${name} 最少项`}
                    type="number"
                    min={0}
                    max={100}
                    value={field.minItems ?? 0}
                    onChange={(e) =>
                      onChange(
                        pretty({
                          ...schema,
                          properties: {
                            ...schema.properties,
                            [name]: {
                              ...field,
                              minItems: Number(e.target.value),
                            },
                          },
                        }),
                      )
                    }
                  />
                )}
              </td>
              <td>
                <input
                  aria-label={`${name} 说明`}
                  value={field.description ?? ""}
                  onChange={(e) =>
                    onChange(
                      pretty({
                        ...schema,
                        properties: {
                          ...schema.properties,
                          [name]: { ...field, description: e.target.value },
                        },
                      }),
                    )
                  }
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
