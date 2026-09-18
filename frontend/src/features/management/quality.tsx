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
  useActivity,
  type Props,
  type Models,
} from "./shared";
const api = createApiClient();
type Report = {
  sample_count: number;
  micro: {
    precision: number | null;
    recall: number | null;
    f1: number | null;
    gold_count: number;
    prediction_count: number;
  };
  fields: Record<
    string,
    {
      precision: number | null;
      recall: number | null;
      f1: number | null;
      gold_count: number;
      prediction_count: number;
    }
  >;
  strict_span: {
    f1: number | null;
    gold_count: number;
    prediction_count: number;
  };
  relaxed_span: {
    f1: number | null;
    gold_count: number;
    prediction_count: number;
  };
  relation: { f1: number | null; gold_count: number; prediction_count: number };
  time: { accuracy: number | null; denominator: number };
  terminology: {
    accuracy: number | null;
    coverage: number | null;
    abstention: number | null;
    denominator: number;
  };
  high_risk: Record<string, { count: number; denominator: number }>;
};
const pct = (v: number | null | undefined) =>
  v == null ? "N/A" : `${(v * 100).toFixed(2)}%`;
const stateNames: Record<string, string> = {
  awaiting_annotation: "待标注",
  awaiting_review: "待独立二审",
  disputed: "待分歧裁决",
  accepted: "可冻结",
  pending: "待二审",
  rejected: "已拒收",
};
export function Quality(props: Props) {
  const [tab, setTab] = useState("dashboard");
  return (
    <>
      <div className="management-heading">
        <div>
          <h1>质量评测</h1>
          <p>从独立金标准到冻结评测，再到经过二审的纠错回流。</p>
        </div>
      </div>
      <nav className="management-tabs" aria-label="质量管理">
        <button
          aria-pressed={tab === "dashboard"}
          onClick={() => setTab("dashboard")}
        >
          评测与版本对比
        </button>
        <button
          aria-pressed={tab === "samples"}
          onClick={() => setTab("samples")}
        >
          金标准与裁决
        </button>
        <button
          aria-pressed={tab === "corrections"}
          onClick={() => setTab("corrections")}
        >
          纠错二审
        </button>
      </nav>
      {tab === "dashboard" ? (
        <Dashboard {...props} />
      ) : tab === "samples" ? (
        <Samples {...props} />
      ) : (
        <Corrections {...props} />
      )}
    </>
  );
}
function Dashboard({ project, csrf }: Props) {
  const path = { project_id: project.id },
    headers = { "X-CSRF-Token": csrf };
  const [dataset, setDataset] = useState("");
  const [predictions, setPredictions] = useState("{}");
  const [chosen, setChosen] = useState("");
  const [baseline, setBaseline] = useState("");
  const [details, setDetails] = useState<unknown>();
  const [error, setError] = useState("");
  const canRead = project.capabilities.includes("quality.read");
  const runs = useQuery({
    queryKey: ["project", project.id, "evaluations"],
    enabled: canRead,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/quality/evaluations", {
          params: { path },
        }),
      ),
  });
  const datasets = useQuery({
    queryKey: ["project", project.id, "gold-datasets"],
    enabled: canRead,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/quality/datasets", {
          params: { path },
        }),
      ),
  });
  const run = runs.data?.find((r) => r.id === chosen) ?? runs.data?.[0];
  const report = run?.payload.report as Report | undefined;
  const base = runs.data?.find((r) => r.id === baseline);
  const baseReport = base?.payload.report as Report | undefined;
  if (!canRead) return <p>当前账号没有质量报告查看权限。</p>;
  return (
    <>
      {(runs.error || datasets.error) && (
        <p role="alert">{runs.error?.message ?? datasets.error?.message}</p>
      )}
      {runs.isPending ? (
        <p>正在加载评测记录…</p>
      ) : !runs.data?.length ? (
        <div className="quality-empty">
          <h2>尚无评测结果</h2>
          <p>先完成样本标注和独立复核，冻结金标准后选择预测运行进行评测。</p>
          <p className="hint">暂无实测数据；此处不会显示演示指标。</p>
        </div>
      ) : (
        <>
          <Field label="当前评测运行">
            <select
              value={run?.id}
              onChange={(e) => {
                setChosen(e.target.value);
                setBaseline("");
                setDetails(undefined);
              }}
            >
              {runs.data.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} · {r.created_at}
                </option>
              ))}
            </select>
          </Field>
          <p>
            <span className="badge">
              {run?.payload.synthetic ? "合成工程验证" : "获准数据评测"}
            </span>{" "}
            样本量 {report?.sample_count} · 金标准事实{" "}
            {report?.micro.gold_count} · 预测事实{" "}
            {report?.micro.prediction_count}
          </p>
          <p className="hint">
            数据集 {run?.dataset_id} · hash{" "}
            {String(run?.payload.dataset_digest)} · 口径{" "}
            {String(run?.payload.protocol)}
          </p>
          <Field label="对比基线（同一冻结数据集）">
            <select
              value={baseline}
              onChange={(e) => setBaseline(e.target.value)}
            >
              <option value="">不对比</option>
              {runs.data
                .filter(
                  (r) => r.id !== run?.id && r.dataset_id === run?.dataset_id,
                )
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
            </select>
          </Field>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>指标</th>
                  <th>当前运行</th>
                  {base && <th>基线 / 变化</th>}
                </tr>
              </thead>
              <tbody>
                {(
                  [
                    ["精确率", "precision"],
                    ["召回率", "recall"],
                    ["F1", "f1"],
                  ] as const
                ).map(([label, k]) => (
                  <tr key={k}>
                    <td>字段 micro {label}</td>
                    <td>{pct(report?.micro[k])}</td>
                    {base && (
                      <td>
                        {pct(baseReport?.micro[k])} /{" "}
                        {report?.micro[k] != null &&
                        baseReport?.micro[k] != null
                          ? `${((report.micro[k]! - baseReport.micro[k]!) * 100).toFixed(2)} 个百分点`
                          : "N/A"}
                      </td>
                    )}
                  </tr>
                ))}
                {Object.entries(report?.high_risk ?? {}).map(([key, v]) => (
                  <tr key={key}>
                    <td>
                      {{
                        negation_to_affirmed: "否定变肯定",
                        dose_error: "剂量错误",
                        patient_linkage: "患者串联",
                      }[key] ?? key}
                    </td>
                    <td>
                      {v.count} / {v.denominator || "N/A"}
                    </td>
                    {base && (
                      <td>
                        {baseReport?.high_risk[key]?.count} /{" "}
                        {baseReport?.high_risk[key]?.denominator || "N/A"}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report && (
            <>
              <h2>关键字段与证据质量</h2>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>金标准 / 预测数</th>
                      <th>精确率</th>
                      <th>召回率</th>
                      <th>F1</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(report.fields ?? {}).map(([name, m]) => (
                      <tr key={name}>
                        <td>{name}</td>
                        <td>
                          {m.gold_count} / {m.prediction_count}
                        </td>
                        <td>{pct(m.precision)}</td>
                        <td>{pct(m.recall)}</td>
                        <td>{pct(m.f1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>指标</th>
                      <th>结果</th>
                      <th>分母</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(
                      [
                        ["严格 Span F1", report.strict_span],
                        ["宽松 Span F1", report.relaxed_span],
                        ["关系 F1", report.relation],
                      ] as const
                    ).map(([name, m]) => (
                      <tr key={name}>
                        <td>{name}</td>
                        <td>{pct(m?.f1)}</td>
                        <td>
                          金标准 {m?.gold_count} / 预测 {m?.prediction_count}
                        </td>
                      </tr>
                    ))}
                    <tr>
                      <td>时间准确率</td>
                      <td>{pct(report.time?.accuracy)}</td>
                      <td>{report.time?.denominator}</td>
                    </tr>
                    <tr>
                      <td>术语正确率 / 覆盖率 / 拒答率</td>
                      <td>
                        {pct(report.terminology?.accuracy)} /{" "}
                        {pct(report.terminology?.coverage)} /{" "}
                        {pct(report.terminology?.abstention)}
                      </td>
                      <td>{report.terminology?.denominator}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </>
          )}
          <RecordDetails
            title="字段分层、Span、关系、时间、术语、费用与耗时"
            value={run?.payload}
          />
          {project.capabilities.includes("quality.errors.read") && (
            <button
              onClick={async () => {
                try {
                  setError("");
                  const value = await result(
                    api.GET(
                      "/api/v1/projects/{project_id}/quality/evaluations/{eid}/errors",
                      { params: { path: { ...path, eid: run!.id } } },
                    ),
                  );
                  setDetails(value);
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            >
              查看错误样本与重放输入
            </button>
          )}
          {error && <p role="alert">{error}</p>}
          {details !== undefined && (
            <RecordDetails title="获准错误样本及预测快照" value={details} />
          )}
        </>
      )}
      <details>
        <summary>运行冻结集评测</summary>
        <SubmitForm
          disabled={!project.capabilities.includes("quality.manage")}
          label="运行评测并保存报告"
          submit={async (form) => {
            const r = await result(
              api.POST("/api/v1/projects/{project_id}/quality/evaluations", {
                params: { path },
                headers,
                body: {
                  dataset_id: dataset,
                  name: String(form.get("name")),
                  predictions: JSON.parse(predictions),
                },
              }),
            );
            setChosen(r.id);
          }}
        >
          <Field label="评测名称">
            <input name="name" required />
          </Field>
          <Field label="冻结数据集">
            <select
              required
              value={dataset}
              onChange={(e) => setDataset(e.target.value)}
            >
              <option value="">请选择</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} · {String(d.manifest.sample_count)} 份 ·{" "}
                  {String(d.manifest.split)}
                </option>
              ))}
            </select>
          </Field>
          <button
            type="button"
            disabled={!dataset}
            onClick={async () => {
              try {
                const d = await result(
                  api.GET(
                    "/api/v1/projects/{project_id}/quality/datasets/{gid}/manifest",
                    { params: { path: { ...path, gid: dataset } } },
                  ),
                );
                const members = d.manifest.members as { sample_id: string }[];
                setPredictions(
                  pretty(
                    Object.fromEntries(
                      members.map((m) => [
                        m.sample_id,
                        "填入此文书的预测运行 ID",
                      ]),
                    ),
                  ),
                );
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            载入冻结样本清单
          </button>
          <Json
            label="样本 ID → 预测运行 ID"
            value={predictions}
            onChange={setPredictions}
          />
          <p className="hint">
            只评测原始模型输出。人工修订不作为预测；失败运行按空输出计入。费用无完整测量时显示
            N/A。
          </p>
        </SubmitForm>
      </details>
      <h2>冻结数据集</h2>
      {datasets.data?.map((d) => (
        <RecordDetails
          key={d.id}
          title={`${d.name} · ${String(d.manifest.sample_count)} 份`}
          value={d}
        />
      ))}
    </>
  );
}
function Samples({ project, csrf }: Props) {
  const path = { project_id: project.id },
    headers = { "X-CSRF-Token": csrf };
  const [selected, setSelected] = useState("");
  const [freezeIds, setFreezeIds] = useState<string[]>([]);
  const [labels, setLabels] = useState(
    '{"facts":[],"relations":[],"codings":[]}',
  );
  const activity = useActivity();
  const can =
    project.capabilities.includes("quality.errors.read") &&
    project.capabilities.includes("original.read");
  const samples = useQuery({
    queryKey: ["project", project.id, "quality-samples"],
    enabled: can,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/quality/samples", {
          params: { path },
        }),
      ),
  });
  const templates = useQuery({
    queryKey: ["project", project.id, "template-versions"],
    enabled: can,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/template-versions", {
          params: { path },
        }),
      ),
  });
  const sample = samples.data?.find((s) => s.id === selected);
  const source = useQuery({
    queryKey: ["project", project.id, "annotation-source", sample?.run_id],
    enabled: !!sample,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/extractions/{run_id}", {
          params: { path: { ...path, run_id: sample!.run_id } },
        }),
      ),
  });
  const parsed = useQuery({
    queryKey: [
      "project",
      project.id,
      "annotation-parsed",
      sample?.payload.parse_artifact_id,
    ],
    enabled: !!sample,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/parse-artifacts/{artifact_id}", {
          params: {
            path: {
              ...path,
              artifact_id: String(sample!.payload.parse_artifact_id),
            },
          },
        }),
      ),
  });
  const stage =
    sample?.state === "awaiting_annotation"
      ? "annotation"
      : sample?.state === "awaiting_review"
        ? "review"
        : "adjudication";
  if (!can) return <p>样本内容需要质量样本和原件查看权限。</p>;
  return (
    <>
      <p>
        按受控患者组隔离 train / dev /
        test；同一患者的所有文书必须处于同一分组。二审与裁决须由不同人员完成。
      </p>
      {(samples.error || templates.error || source.error) && (
        <p role="alert">
          {samples.error?.message ??
            templates.error?.message ??
            source.error?.message}
        </p>
      )}
      <details>
        <summary>登记获准样本</summary>
        <SubmitForm
          disabled={!project.capabilities.includes("quality.annotate")}
          label="登记样本"
          submit={async (form) => {
            const s = await result(
              api.POST("/api/v1/projects/{project_id}/quality/samples", {
                params: { path },
                headers,
                body: {
                  run_id: String(form.get("run")),
                  patient_group: String(form.get("group")),
                  split: String(
                    form.get("split"),
                  ) as Models["SampleCreate"]["split"],
                  source: String(form.get("source")),
                  authorization_reference: String(form.get("authorization")),
                  synthetic: form.has("synthetic"),
                  difficulty_tags: String(form.get("tags"))
                    .split(/[,，]/)
                    .map((s) => s.trim())
                    .filter(Boolean),
                  template_version_id: String(form.get("template")),
                },
              }),
            );
            setSelected(s.id);
          }}
        >
          <Field label="抽取运行 ID">
            <input required name="run" />
          </Field>
          <Field label="受控患者组标识（跨来源同人使用相同标识）">
            <input required name="group" />
          </Field>
          <Field label="数据用途">
            <select name="split">
              <option value="test">独立测试</option>
              <option value="dev">开发验证</option>
              <option value="train">训练候选</option>
              <option value="external">机构外 / 模板外测试</option>
            </select>
          </Field>
          <Field label="样本来源">
            <input required name="source" />
          </Field>
          <Field label="授权记录引用">
            <input required name="authorization" />
          </Field>
          <Field label="难例标签（逗号分隔）">
            <input required name="tags" placeholder="否定,未知剂量,跨页表格" />
          </Field>
          <Field label="标注模板与指南版本">
            <select required name="template">
              <option value="">请选择已发布版本</option>
              {templates.data?.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.version}
                </option>
              ))}
            </select>
          </Field>
          <label>
            <input type="checkbox" name="synthetic" />
            合成样本（仅工程验证）
          </label>
        </SubmitForm>
      </details>
      <div className="management-columns">
        <section>
          <h2>样本台账</h2>
          {samples.isPending ? (
            <p>正在加载…</p>
          ) : !samples.data?.length ? (
            <p>暂无样本。登记来源、授权与患者组后开始标注。</p>
          ) : (
            samples.data.map((s) => (
              <div key={s.id}>
                <button
                  className="record-select"
                  aria-pressed={selected === s.id}
                  onClick={() => {
                    setSelected(s.id);
                    setLabels('{"facts":[],"relations":[],"codings":[]}');
                    activity.reset();
                  }}
                >
                  <strong>
                    {s.payload.document_type === "outpatient" ? "门诊" : "检验"}{" "}
                    · {s.id.slice(0, 8)}
                  </strong>
                  <span>
                    {stateNames[s.state]} · {s.split}
                  </span>
                </button>
                {s.state === "accepted" && (
                  <label>
                    <input
                      type="checkbox"
                      checked={freezeIds.includes(s.id)}
                      onChange={(e) =>
                        setFreezeIds(
                          e.target.checked
                            ? [...freezeIds, s.id]
                            : freezeIds.filter((x) => x !== s.id),
                        )
                      }
                    />
                    加入冻结清单
                  </label>
                )}
              </div>
            ))
          )}
          <SubmitForm
            disabled={
              !project.capabilities.includes("quality.manage") ||
              !freezeIds.length
            }
            label="冻结金标准版本"
            submit={async (form) =>
              result(
                api.POST("/api/v1/projects/{project_id}/quality/datasets", {
                  params: { path },
                  headers,
                  body: {
                    name: String(form.get("name")),
                    sample_ids: freezeIds,
                  },
                }),
              )
            }
          >
            <Field label="冻结版本名称">
              <input name="name" required />
            </Field>
            <p>已选 {freezeIds.length} 份；每个版本只能包含同一种数据用途。</p>
          </SubmitForm>
        </section>
        <section>
          {sample ? (
            <>
              <h2>
                {stateNames[sample.state]} · {sample.id.slice(0, 8)}
              </h2>
              <RecordDetails
                title="来源、授权与患者隔离记录"
                value={sample.payload}
              />
              <RecordDetails
                title="固定标注指南"
                value={
                  templates.data?.find(
                    (t) => t.id === sample.payload.template_version_id,
                  )?.payload
                }
              />
              {parsed.error && <p role="alert">{parsed.error.message}</p>}
              {parsed.data && (
                <details open>
                  <summary>原文全文与证据坐标</summary>
                  <blockquote>{parsed.data.text}</blockquote>
                  <RecordDetails
                    title="逐页解析块、页码、span 与 bbox"
                    value={parsed.data}
                  />
                </details>
              )}
              <RecordDetails
                title="文书证据与原始抽取（辅助定位，不代表金标准）"
                value={source.data}
              />
              {sample.state !== "accepted" && (
                <div
                  onKeyDown={activity.tick}
                  onPointerDown={activity.tick}
                  onInput={activity.tick}
                >
                  <SubmitForm
                    disabled={
                      !project.capabilities.includes(
                        {
                          annotation: "quality.annotate",
                          review: "quality.review",
                          adjudication: "quality.adjudicate",
                        }[stage],
                      )
                    }
                    label={
                      {
                        annotation: "提交独立标注",
                        review: "提交独立二审",
                        adjudication: "提交分歧裁决",
                      }[stage]
                    }
                    submit={async (form) => {
                      await result(
                        api.POST(
                          "/api/v1/projects/{project_id}/quality/samples/{sid}/annotations",
                          {
                            params: { path: { ...path, sid: sample.id } },
                            headers,
                            body: {
                              stage,
                              labels: JSON.parse(labels),
                              reason: String(form.get("reason")),
                              active_seconds: activity.seconds(),
                            },
                          },
                        ),
                      );
                      activity.reset();
                    }}
                  >
                    <p className="hint">
                      facts 沿用事实契约，需完整原文证据；relations 指向事实
                      ID；codings 记录固定词库版本。超过 60
                      秒无操作的区间不计入活动耗时。
                    </p>
                    <Json
                      label="独立标注结果 JSON"
                      value={labels}
                      onChange={setLabels}
                    />
                    <Field label="依据或分歧处理理由">
                      <textarea required name="reason" rows={3} />
                    </Field>
                  </SubmitForm>
                </div>
              )}
              <h3>标注与复核记录</h3>
              {sample.annotations.map((a) => (
                <RecordDetails
                  key={a.id}
                  title={`${String(a.payload.stage)} · ${a.actor_id.slice(0, 8)}`}
                  value={a}
                />
              ))}
            </>
          ) : (
            <p>选择样本查看指南、标注与裁决记录。</p>
          )}
        </section>
      </div>
    </>
  );
}
function Corrections({ project, csrf }: Props) {
  const path = { project_id: project.id },
    headers = { "X-CSRF-Token": csrf };
  const [selected, setSelected] = useState<string[]>([]);
  const allowed =
    project.capabilities.includes("quality.errors.read") &&
    project.capabilities.includes("original.read");
  const candidates = useQuery({
    queryKey: ["project", project.id, "correction-candidates"],
    enabled: allowed,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/quality/corrections", {
          params: { path },
        }),
      ),
  });
  const releases = useQuery({
    queryKey: ["project", project.id, "correction-releases"],
    enabled: allowed,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/quality/correction-releases", {
          params: { path },
        }),
      ),
  });
  if (!allowed) return <p>纠错样本需要质量样本和原件查看权限。</p>;
  return (
    <>
      <p>
        事实纠错自动进入候选池。独立二审通过后才能发布训练候选版本；测试及机构外测试患者禁止回流。
      </p>
      {(candidates.error || releases.error) && (
        <p role="alert">
          {candidates.error?.message ?? releases.error?.message}
        </p>
      )}
      {candidates.isPending ? (
        <p>正在加载…</p>
      ) : !candidates.data?.length ? (
        <div className="quality-empty">
          尚无纠错候选。工作台保存事实修订后会自动出现在这里。
        </div>
      ) : (
        candidates.data.map((c) => (
          <details key={c.id}>
            <summary>
              {c.id.slice(0, 8)} · {stateNames[c.decision] ?? "已接收"}
            </summary>
            <RecordDetails title="修订事实、证据与二审记录" value={c} />
            {c.decision === "pending" ? (
              <SubmitForm
                disabled={!project.capabilities.includes("quality.review")}
                label="保存独立二审决定"
                submit={async (form) =>
                  result(
                    api.POST(
                      "/api/v1/projects/{project_id}/quality/corrections/{cid}/review",
                      {
                        params: { path: { ...path, cid: c.id } },
                        headers,
                        body: {
                          decision: String(form.get("decision")) as
                            "accepted" | "rejected",
                          reason: String(form.get("reason")),
                        },
                      },
                    ),
                  )
                }
              >
                <Field label="二审结果">
                  <select name="decision">
                    <option value="rejected">拒收</option>
                    <option value="accepted">接收</option>
                  </select>
                </Field>
                <Field label="接收或拒收理由">
                  <textarea required name="reason" rows={3} />
                </Field>
              </SubmitForm>
            ) : (
              c.decision === "accepted" && (
                <label>
                  <input
                    type="checkbox"
                    checked={selected.includes(c.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, c.id]
                          : selected.filter((x) => x !== c.id),
                      )
                    }
                  />
                  加入发布清单
                </label>
              )
            )}
          </details>
        ))
      )}
      <SubmitForm
        disabled={
          !selected.length || !project.capabilities.includes("quality.manage")
        }
        label="检查污染并发布纠错版本"
        submit={async (form) =>
          result(
            api.POST(
              "/api/v1/projects/{project_id}/quality/correction-releases",
              {
                params: { path },
                headers,
                body: {
                  name: String(form.get("name")),
                  candidate_ids: selected,
                },
              },
            ),
          )
        }
      >
        <Field label="纠错版本名称">
          <input required name="name" />
        </Field>
        <p>
          已选择 {selected.length}{" "}
          条。发布仅形成训练候选清单，不会自动加入训练或评测集。
        </p>
      </SubmitForm>
      <h2>发布记录</h2>
      {releases.data?.map((r) => (
        <RecordDetails key={r.id} title={r.name} value={r} />
      ))}
    </>
  );
}
