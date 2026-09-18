"use client";

import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  useEffect,
  useState,
  useSyncExternalStore,
  type FormEvent,
  type ReactNode,
} from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import "./workspace.css";
import { ReviewWorkspace } from "../review/workspace";
import { ManagementWorkspace } from "../management/workspace";
import { DatasetWorkspace } from "../datasets/workspace";
type WorkspaceProps = {
  projectId?: string;
  mode?:
    | "documents"
    | "reviews"
    | "datasets"
    | "templates"
    | "terminology"
    | "quality";
  reviewSetId?: string;
};

type Models = components["schemas"];
type Me = Models["Me"];
type Doc = Models["DocumentView"];
type Patient = Models["PatientView"];
type Encounter = Models["EncounterView"];
const api = createApiClient();
class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
const messages: Record<number, string> = {
  401: "会话已过期，请重新登录。未提交的内容需要重新填写。",
  403: "当前账号没有此项操作权限。",
  404: "记录不存在，或不在当前项目的授权范围内。",
  409: "记录状态已变化、来源键已被使用，或相同文件已关联其他记录。请刷新后核对。",
  413: "文件超过 20 MiB 上限，请拆分后上传。",
  415: "文件内容与格式不符。支持 UTF-8 TXT、PDF、PNG 和 JPEG。",
  422: "请检查文件是否为空，以及必填项和关联信息是否完整。",
};
async function result<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let value;
  try {
    value = await promise;
  } catch {
    throw new Error("网络连接中断，请检查连接后重试。已保存的记录不会丢失。");
  }
  if (!value.response.ok) {
    if (value.response.status === 401)
      window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(
      value.response.status,
      messages[value.response.status] ?? "服务暂不可用，请稍后重试。",
    );
  }
  return value.data as T;
}
const statuses: Record<string, string> = {
  queued: "排队中",
  parsing: "解析中",
  parsed: "解析完成",
  needs_ocr: "待 OCR",
  parse_failed: "解析失败",
  extracting: "抽取中",
  pending_review: "待审核",
  extraction_failed: "抽取失败",
  cancelled: "已取消",
  running: "处理中",
  succeeded: "已完成",
  failed: "失败",
};
const errorLabels: Record<string, string> = {
  ENCRYPTED_PDF: "PDF 已加密，请解密后重新上传。",
  DAMAGED_PDF: "PDF 文件损坏，请重新提供原件。",
  INVALID_DOCUMENT: "文件无法解析，请检查原件。",
  PAGE_LIMIT: "超过 100 页限制，请拆分文件。",
  IMAGE_LIMIT: "图片超过 4000 万像素或含多帧。",
  PARSE_TIMEOUT: "解析超时，可重试或拆分文件。",
  WORKER_LEASE_EXPIRED: "处理进程中断，已记录本次尝试。",
  DEPENDENCY_UNAVAILABLE: "存储或处理服务暂时不可用，可稍后重试。",
};
function time(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
function Field({ title, children }: { title: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{title}</span>
      {children}
    </label>
  );
}
function Notice({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="notice">
      {children}
    </p>
  );
}

export function DocumentWorkspace({
  projectId,
  mode = "documents",
  reviewSetId,
}: WorkspaceProps) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: true },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <AppShell projectId={projectId} mode={mode} reviewSetId={reviewSetId} />
    </QueryClientProvider>
  );
}
function AppShell({
  projectId,
  mode = "documents",
  reviewSetId,
}: WorkspaceProps) {
  const cache = useQueryClient();
  const [expired, setExpired] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const me = useQuery({
    queryKey: ["me", epoch],
    queryFn: () => result(api.GET("/api/v1/me")),
    retry: false,
    refetchInterval: 30_000,
  });
  useEffect(() => {
    function clear() {
      cache.removeQueries({ queryKey: ["project"] });
      cache.removeQueries({ queryKey: ["me"] });
      setExpired(true);
    }
    window.addEventListener("session-expired", clear);
    return () => window.removeEventListener("session-expired", clear);
  }, [cache]);
  const user = expired ? undefined : me.data;
  return (
    <div className="workspace documents-workspace">
      <a className="skip" href="#main">
        跳转到主要内容
      </a>
      <aside className="sidebar">
        <Link className="brand" href="/">
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
        </Link>
        <p className="sidebar-label">临床数据研究空间</p>
        <nav aria-label="业务模块">
          <Link
            href="/documents"
            aria-current={mode === "documents" ? "page" : undefined}
          >
            文档任务 <small>已开放</small>
          </Link>
          <Link
            href={projectId ? `/projects/${projectId}/reviews` : "/reviews"}
            aria-current={mode === "reviews" ? "page" : undefined}
          >
            审核工作台
          </Link>
          {(
            [
              ["templates", "抽取模板"],
              ["terminology", "术语管理"],
            ] as const
          ).map(([key, name]) => (
            <Link
              href={projectId ? `/projects/${projectId}/${key}` : `/${key}`}
              aria-current={mode === key ? "page" : undefined}
              key={key}
            >
              {name}
            </Link>
          ))}
          <Link
            href={projectId ? `/projects/${projectId}/datasets` : "/datasets"}
            aria-current={mode === "datasets" ? "page" : undefined}
          >
            数据集
          </Link>
          <Link
            href={projectId ? `/projects/${projectId}/quality` : "/quality"}
            aria-current={mode === "quality" ? "page" : undefined}
          >
            质量评测
          </Link>
        </nav>
        <div className="sidebar-foot">准确 · 清晰 · 可追溯</div>
      </aside>
      <div className="content">
        {user ? (
          <Project
            key={`${user.id}-${epoch}`}
            user={user}
            initialProject={projectId}
            mode={mode}
            reviewSetId={reviewSetId}
            logout={async () => {
              await result(
                api.POST("/api/v1/auth/logout", {
                  headers: { "X-CSRF-Token": user.csrf_token },
                }),
              );
              cache.removeQueries({ queryKey: ["project"] });
              cache.removeQueries({ queryKey: ["me"] });
              setExpired(true);
            }}
          />
        ) : (
          <main id="main" className="login-main">
            <p className="eyebrow">CLINICAL DATA STRUCTURING PLATFORM</p>
            <h1>登录文档工作空间</h1>
            {me.isPending && !expired ? (
              <p className="intro">正在确认会话…</p>
            ) : (
              <Login
                expired={expired}
                failure={
                  me.error instanceof ApiError ? undefined : me.error?.message
                }
                onLogin={() => {
                  setExpired(false);
                  setEpoch((n) => n + 1);
                }}
              />
            )}
          </main>
        )}
      </div>
    </div>
  );
}
function Login({
  expired,
  failure,
  onLogin,
}: {
  expired: boolean;
  failure?: string;
  onLogin: () => void;
}) {
  const config = useQuery({
    queryKey: ["auth-config"],
    queryFn: () => result(api.GET("/api/v1/auth/config")),
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <>
      <p className="intro">
        登录后选择项目，建立患者与就诊记录，保存并预览原始文书。
      </p>
      {expired && <Notice>会话已结束，请重新登录。</Notice>}
      {failure && <Notice>{failure}</Notice>}
      {config.data?.provider === "oidc" ? (
        <a className="primary button-link" href="/api/v1/auth/oidc/login">
          使用机构账号登录
        </a>
      ) : config.data ? (
        <form
          className="login-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            const data = new FormData(e.currentTarget);
            try {
              await result(
                api.POST("/api/v1/auth/synthetic", {
                  body: { access_code: String(data.get("code")) },
                }),
              );
              onLogin();
            } catch (err) {
              setError(
                err instanceof ApiError && err.status === 401
                  ? "开发访问码不正确，或尚未配置。"
                  : (err as Error).message,
              );
            } finally {
              setBusy(false);
            }
          }}
        >
          <span className="badge">本地合成开发环境</span>
          <Field title="开发访问码">
            <input name="code" type="password" required autoComplete="off" />
          </Field>
          <p className="hint">
            使用本地配置的访问码。此环境仅用于合成文书验证。
          </p>
          <button className="primary" disabled={busy}>
            {busy ? "正在登录…" : "进入工作空间"}
          </button>
        </form>
      ) : (
        <Notice>{config.error?.message ?? "正在连接身份服务…"}</Notice>
      )}
      {error && <Notice>{error}</Notice>}
    </>
  );
}
function Project({
  user,
  initialProject,
  mode,
  reviewSetId,
  logout,
}: {
  user: Me;
  initialProject?: string;
  mode:
    | "documents"
    | "reviews"
    | "datasets"
    | "templates"
    | "terminology"
    | "quality";
  reviewSetId?: string;
  logout: () => Promise<void>;
}) {
  const router = useRouter();
  const [projectId, setProjectId] = useState(
    initialProject ?? user.projects[0]?.id ?? "",
  );
  const [error, setError] = useState("");
  const cache = useQueryClient();
  const project = user.projects.find((item) => item.id === projectId);
  return (
    <>
      <header>
        <label className="project-switch">
          当前项目
          <select
            aria-label="当前项目"
            value={projectId}
            onChange={(e) => {
              cache.removeQueries({ queryKey: ["project"] });
              setProjectId(e.target.value);
              router.push(`/projects/${e.target.value}/${mode}`);
            }}
          >
            {!project && <option value={projectId}>请选择获准项目</option>}
            {user.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <div className="account">
          <span>{user.display_name}</span>
          <button
            onClick={() => logout().catch((err) => setError(err.message))}
          >
            退出
          </button>
        </div>
      </header>
      {error && <Notice>{error}</Notice>}
      {project ? (
        mode === "documents" ? (
          <Documents key={projectId} project={project} csrf={user.csrf_token} />
        ) : mode === "reviews" ? (
          <ReviewWorkspace
            key={projectId}
            project={project}
            csrf={user.csrf_token}
            reviewSetId={projectId === initialProject ? reviewSetId : undefined}
          />
        ) : mode === "datasets" ? (
          <DatasetWorkspace
            key={projectId}
            project={project}
            csrf={user.csrf_token}
          />
        ) : (
          <ManagementWorkspace
            key={`${projectId}-${mode}`}
            project={project}
            csrf={user.csrf_token}
            mode={mode}
          />
        )
      ) : (
        <main id="main">
          <h1>暂无可访问的项目</h1>
          <p className="intro">请由项目负责人为账号分配项目和操作能力。</p>
        </main>
      )}
    </>
  );
}
function subscribeNetwork(listener: () => void) {
  window.addEventListener("online", listener);
  window.addEventListener("offline", listener);
  return () => {
    window.removeEventListener("online", listener);
    window.removeEventListener("offline", listener);
  };
}
function Documents({
  project,
  csrf,
}: {
  project: Me["projects"][number];
  csrf: string;
}) {
  const cache = useQueryClient();
  const params = { path: { project_id: project.id } };
  const online = useSyncExternalStore(
    subscribeNetwork,
    () => navigator.onLine,
    () => true,
  );
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [kind, setKind] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<(string | undefined)[]>([]);
  const [selected, setSelected] = useState<string>();
  const [upload, setUpload] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const list = useQuery({
    queryKey: ["project", project.id, "documents", query, filter, kind, cursor],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/documents", {
          params: {
            ...params,
            query: {
              query,
              status: (filter as "queued") || undefined,
              document_type: (kind as "outpatient") || undefined,
              cursor,
              limit: 20,
            },
          },
        }),
      ),
    refetchInterval: 2500,
  });
  const detail = useQuery({
    queryKey: ["project", project.id, "document", selected],
    enabled: !!selected,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/documents/{document_id}", {
          params: { path: { project_id: project.id, document_id: selected! } },
        }),
      ),
    refetchInterval: 2500,
  });
  const refresh = () =>
    cache.invalidateQueries({ queryKey: ["project", project.id] });
  function resetPage() {
    setCursor(undefined);
    setHistory([]);
  }
  async function act(doc: Doc, action: "cancel" | "retry") {
    setBusy(true);
    setMessage("");
    try {
      const options = {
        params: { path: { project_id: project.id, job_id: doc.job_id } },
        headers: { "X-CSRF-Token": csrf },
      };
      await result(
        action === "cancel"
          ? api.POST(
              "/api/v1/projects/{project_id}/jobs/{job_id}/cancel",
              options,
            )
          : api.POST(
              "/api/v1/projects/{project_id}/jobs/{job_id}/retry",
              options,
            ),
      );
      await refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main id="main" className="documents-main">
      {!online && (
        <Notice>
          网络已断开。已保存的记录仍在服务端，连接恢复后将自动刷新。
        </Notice>
      )}
      <div className="document-heading">
        <div>
          <p className="eyebrow">DOCUMENT WORKSPACE</p>
          <h1>文档任务</h1>
          <p className="intro">保存原始材料，核对归属，跟踪每一次处理。</p>
        </div>
        <div className="actions">
          {project.capabilities.includes("audit.read") && (
            <button onClick={() => setAuditOpen(!auditOpen)}>操作记录</button>
          )}
          {project.capabilities.includes("import") && (
            <button className="primary" onClick={() => setUpload(!upload)}>
              {upload ? "收起导入" : "+ 导入文书"}
            </button>
          )}
        </div>
      </div>
      {message && <Notice>{message}</Notice>}
      {upload && (
        <Intake
          projectId={project.id}
          csrf={csrf}
          done={async (id, duplicate) => {
            setUpload(false);
            setSelected(id);
            setMessage(
              duplicate
                ? "当前项目已保存相同文件，已打开现有记录。"
                : "原件已保存，解析任务已进入队列。",
            );
            await refresh();
          }}
        />
      )}
      {auditOpen && <Audit projectId={project.id} />}
      <div className="document-toolbar">
        <Field title="搜索文书">
          <input
            type="search"
            value={query}
            placeholder="按文件名搜索"
            onChange={(e) => {
              setQuery(e.target.value);
              resetPage();
            }}
          />
        </Field>
        <Field title="处理状态">
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              resetPage();
            }}
          >
            <option value="">全部状态</option>
            {[
              "queued",
              "parsing",
              "parsed",
              "needs_ocr",
              "parse_failed",
              "extracting",
              "pending_review",
              "extraction_failed",
              "cancelled",
            ].map((s) => (
              <option key={s} value={s}>
                {statuses[s]}
              </option>
            ))}
          </select>
        </Field>
        <Field title="文书类型">
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              resetPage();
            }}
          >
            <option value="">全部类型</option>
            <option value="outpatient">门诊记录</option>
            <option value="laboratory">检验报告</option>
          </select>
        </Field>
        <button onClick={() => void refresh()}>刷新列表</button>
      </div>
      {list.error && <Notice>{list.error.message}</Notice>}
      <div className={selected ? "document-split" : ""}>
        <section className="document-list" aria-label="文书列表">
          {list.isPending ? (
            <p className="empty-state">正在读取已保存文书…</p>
          ) : !list.data?.items.length ? (
            <div className="empty-state">
              <h2>
                {query || filter || kind
                  ? "没有符合条件的文书"
                  : "从第一份文书开始"}
              </h2>
              <p>
                {query || filter || kind
                  ? "调整搜索词或筛选条件后再试。"
                  : "点击「导入文书」，选择或建立患者与就诊记录，再上传文件。"}
              </p>
            </div>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>文书 / 导入时间</th>
                    <th>类型</th>
                    <th>处理状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((doc) => (
                    <tr
                      key={doc.id}
                      className={doc.id === selected ? "selected-row" : ""}
                    >
                      <td>
                        <button
                          className="document-name"
                          onClick={() => setSelected(doc.id)}
                        >
                          {doc.filename}
                        </button>
                        <small>
                          {time(doc.created_at)} ·{" "}
                          {(doc.size / 1024).toFixed(1)} KB
                        </small>
                      </td>
                      <td>
                        {doc.document_type === "outpatient"
                          ? "门诊记录"
                          : "检验报告"}
                      </td>
                      <td>
                        <span
                          className={`status status-${doc.processing_status}`}
                        >
                          {doc.cancel_requested && doc.job_status === "running"
                            ? "取消中"
                            : statuses[doc.processing_status]}
                        </span>
                        <small>
                          尝试 {doc.attempt} 次
                          {doc.job_status === "running"
                            ? ` · ${doc.progress}%`
                            : ""}
                        </small>
                      </td>
                      <td>
                        <button onClick={() => setSelected(doc.id)}>
                          查看详情
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="pagination">
            <span>
              第 {history.length + 1} 页 · 本页 {list.data?.items.length ?? 0}{" "}
              份
            </span>
            <div className="actions">
              <button
                disabled={!history.length}
                onClick={() => {
                  setCursor(history.at(-1));
                  setHistory(history.slice(0, -1));
                }}
              >
                上一页
              </button>
              <button
                disabled={!list.data?.next_cursor}
                onClick={() => {
                  setHistory([...history, cursor]);
                  setCursor(list.data!.next_cursor!);
                }}
              >
                下一页
              </button>
            </div>
          </div>
        </section>
        {selected && (
          <section className="document-detail" aria-label="文书详情">
            <div className="section-heading">
              <h2>文书详情</h2>
              <button onClick={() => setSelected(undefined)}>关闭详情</button>
            </div>
            {detail.error && <Notice>{detail.error.message}</Notice>}
            {detail.data ? (
              <Detail
                key={detail.data.id}
                doc={detail.data}
                projectId={project.id}
                csrf={csrf}
                capabilities={project.capabilities}
                busy={busy}
                act={act}
                refresh={refresh}
              />
            ) : (
              <p>正在加载详情…</p>
            )}
          </section>
        )}
      </div>
      <p className="footnote">
        解析与抽取结果已保存。事实进入待审核后仍需人工核对，医学审核与导出将在下一阶段开放。
      </p>
    </main>
  );
}
function Intake({
  projectId,
  csrf,
  done,
}: {
  projectId: string;
  csrf: string;
  done: (id: string, duplicate: boolean) => Promise<void>;
}) {
  const [patient, setPatient] = useState<Patient>();
  const [encounter, setEncounter] = useState<Encounter>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File>();
  const [textPreview, setTextPreview] = useState("");
  const [key, setKey] = useState(() => crypto.randomUUID());
  return (
    <section className="intake" aria-labelledby="intake-heading">
      <div className="section-heading">
        <h2 id="intake-heading">导入文书</h2>
        <span>1 关联记录　→　2 上传原件</span>
      </div>
      <AssociationPicker
        projectId={projectId}
        csrf={csrf}
        patient={patient}
        encounter={encounter}
        setPatient={(p) => {
          setPatient(p);
          setEncounter(undefined);
          setKey(crypto.randomUUID());
        }}
        setEncounter={(e) => {
          setEncounter(e);
          setKey(crypto.randomUUID());
        }}
      />
      {encounter && (
        <form
          className="upload-form"
          onChange={() => setKey(crypto.randomUUID())}
          onSubmit={async (e) => {
            e.preventDefault();
            if (!file || !patient || !encounter) return;
            setBusy(true);
            setError("");
            const form = new FormData(e.currentTarget);
            try {
              const accepted = await result(
                api.POST("/api/v1/projects/{project_id}/documents", {
                  params: {
                    path: { project_id: projectId },
                    header: { "Idempotency-Key": key },
                  },
                  headers: { "X-CSRF-Token": csrf },
                  body: {
                    patient_id: patient.id,
                    encounter_id: encounter.id,
                    document_type: String(form.get("type")) as "outpatient",
                    source: String(form.get("source")),
                    authorization_reference: String(form.get("authorization")),
                    file: file as unknown as string,
                  },
                  bodySerializer: (body) => {
                    const data = new FormData();
                    Object.entries(body).forEach(([k, v]) => data.append(k, v));
                    return data;
                  },
                }),
              );
              await done(accepted.document_id, accepted.duplicate);
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="form-grid">
            <Field title="文书类型">
              <select name="type" defaultValue={encounter.kind}>
                <option value="outpatient">门诊记录</option>
                <option value="laboratory">检验报告</option>
              </select>
            </Field>
            <Field title="文件来源">
              <input
                name="source"
                required
                maxLength={200}
                placeholder="例如：合成门诊样本集"
              />
            </Field>
            <Field title="授权记录引用">
              <input
                name="authorization"
                required
                maxLength={200}
                placeholder="填写可核对的授权记录编号"
              />
            </Field>
          </div>
          <Field title="选择原件">
            <input
              name="file"
              type="file"
              accept=".txt,.pdf,.png,.jpg,.jpeg"
              required
              onChange={async (e) => {
                const chosen = e.target.files?.[0];
                setFile(chosen);
                setTextPreview("");
                setError("");
                if (chosen && chosen.size > 20 * 1024 * 1024) {
                  setError("文件超过 20 MiB 上限。");
                  return;
                }
                if (chosen?.name.toLowerCase().endsWith(".txt"))
                  setTextPreview((await chosen.text()).slice(0, 3000));
              }}
            />
          </Field>
          <p className="hint">
            TXT（UTF-8）、PDF、PNG / JPEG；最大 20 MiB、100 页、4000
            万像素。扫描内容会标记为待 OCR。
          </p>
          {textPreview && (
            <details>
              <summary>上传前预览（前 3000 字符）</summary>
              <pre className="source-text">{textPreview}</pre>
            </details>
          )}
          <button
            className="primary"
            disabled={busy || !file || file.size > 20 * 1024 * 1024}
          >
            {busy ? "正在保存原件…" : "保存并开始解析"}
          </button>
        </form>
      )}
      {error && <Notice>{error}</Notice>}
    </section>
  );
}
function AssociationPicker({
  projectId,
  csrf,
  patient,
  encounter,
  setPatient,
  setEncounter,
}: {
  projectId: string;
  csrf: string;
  patient?: Patient;
  encounter?: Encounter;
  setPatient: (p: Patient | undefined) => void;
  setEncounter: (e: Encounter | undefined) => void;
}) {
  const cache = useQueryClient();
  const [patientQuery, setPatientQuery] = useState("");
  const [newPatient, setNewPatient] = useState(false);
  const [newEncounter, setNewEncounter] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const patientList = useQuery({
    queryKey: ["project", projectId, "patients", patientQuery],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/patients", {
          params: {
            path: { project_id: projectId },
            query: { query: patientQuery },
          },
        }),
      ),
  });
  const encounterList = useQuery({
    queryKey: ["project", projectId, "encounters", patient?.id],
    enabled: !!patient,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/encounters", {
          params: {
            path: { project_id: projectId },
            query: { patient_id: patient!.id },
          },
        }),
      ),
  });
  async function create(
    e: FormEvent<HTMLFormElement>,
    type: "patient" | "encounter",
  ) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    const source = String(form.get("source")),
      source_key = String(form.get("source_key"));
    try {
      if (type === "patient") {
        const p = await result(
          api.POST("/api/v1/projects/{project_id}/patients", {
            params: { path: { project_id: projectId } },
            headers: { "X-CSRF-Token": csrf },
            body: {
              patient_key: String(form.get("patient_key")),
              source,
              source_key,
            },
          }),
        );
        setPatient(p);
        setNewPatient(false);
      } else {
        const item = await result(
          api.POST("/api/v1/projects/{project_id}/encounters", {
            params: { path: { project_id: projectId } },
            headers: { "X-CSRF-Token": csrf },
            body: {
              patient_id: patient!.id,
              kind: String(form.get("kind")) as "outpatient",
              source,
              source_key,
              occurred_on: String(form.get("date")) || null,
              department: String(form.get("department")),
            },
          }),
        );
        setEncounter(item);
        setNewEncounter(false);
      }
      await cache.invalidateQueries({ queryKey: ["project", projectId] });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="association-picker">
      <div className="form-grid">
        <Field title="查找患者编号">
          <input
            value={patientQuery}
            onChange={(e) => setPatientQuery(e.target.value)}
            placeholder="输入项目内编号"
          />
        </Field>
        <Field title="选择患者">
          <select
            value={patient?.id ?? ""}
            onChange={(e) =>
              setPatient(patientList.data?.find((p) => p.id === e.target.value))
            }
          >
            <option value="">请选择患者</option>
            {patient && !patientList.data?.some((p) => p.id === patient.id) && (
              <option value={patient.id}>{patient.patient_key}</option>
            )}
            {patientList.data?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.patient_key}
              </option>
            ))}
          </select>
        </Field>
        <button onClick={() => setNewPatient(!newPatient)} type="button">
          {newPatient ? "收起建档" : "新建患者"}
        </button>
      </div>
      {newPatient && (
        <form
          className="record-form"
          onSubmit={(e) => void create(e, "patient")}
        >
          <h3>患者建档</h3>
          <p className="hint">按来源系统与来源标识核对，不根据姓名合并患者。</p>
          <div className="form-grid">
            <Field title="项目内患者编号">
              <input name="patient_key" required maxLength={100} />
            </Field>
            <Field title="患者来源系统">
              <input name="source" required maxLength={100} />
            </Field>
            <Field title="患者来源标识">
              <input name="source_key" required maxLength={200} />
            </Field>
          </div>
          <button disabled={busy}>保存患者</button>
        </form>
      )}
      {patient && (
        <>
          <div className="form-grid">
            <Field title="选择就诊">
              <select
                value={encounter?.id ?? ""}
                onChange={(e) =>
                  setEncounter(
                    encounterList.data?.find((p) => p.id === e.target.value),
                  )
                }
              >
                <option value="">请选择就诊记录</option>
                {encounter &&
                  !encounterList.data?.some((p) => p.id === encounter.id) && (
                    <option value={encounter.id}>
                      {encounter.occurred_on ?? "日期未填写"} ·{" "}
                      {encounter.department || encounter.kind}
                    </option>
                  )}
                {encounterList.data?.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.occurred_on ?? "日期未填写"} ·{" "}
                    {e.department ||
                      (e.kind === "outpatient" ? "门诊" : "检验")}{" "}
                    · {e.id.slice(-6)}
                  </option>
                ))}
              </select>
            </Field>
            <button
              type="button"
              onClick={() => setNewEncounter(!newEncounter)}
            >
              {newEncounter ? "收起就诊建档" : "新建就诊"}
            </button>
          </div>
          {newEncounter && (
            <form
              className="record-form"
              onSubmit={(e) => void create(e, "encounter")}
            >
              <h3>就诊建档</h3>
              <div className="form-grid">
                <Field title="就诊类型">
                  <select name="kind">
                    <option value="outpatient">门诊</option>
                    <option value="laboratory">检验</option>
                  </select>
                </Field>
                <Field title="就诊日期（可选）">
                  <input type="date" name="date" />
                </Field>
                <Field title="科室（可选）">
                  <input name="department" maxLength={100} />
                </Field>
                <Field title="就诊来源系统">
                  <input name="source" required maxLength={100} />
                </Field>
                <Field title="就诊来源标识">
                  <input name="source_key" required maxLength={200} />
                </Field>
              </div>
              <button disabled={busy}>保存就诊</button>
            </form>
          )}
        </>
      )}
      {error && <Notice>{error}</Notice>}
      {(patientList.error || encounterList.error) && (
        <Notice>{(patientList.error || encounterList.error)?.message}</Notice>
      )}
    </div>
  );
}
function Detail({
  doc,
  projectId,
  csrf,
  capabilities,
  busy,
  act,
  refresh,
}: {
  doc: Doc;
  projectId: string;
  csrf: string;
  capabilities: string[];
  busy: boolean;
  act: (doc: Doc, action: "cancel" | "retry") => Promise<void>;
  refresh: () => Promise<void>;
}) {
  const [changing, setChanging] = useState(false);
  const [evidence, setEvidence] = useState<Models["Evidence"]>();
  const [patient, setPatient] = useState<Patient>();
  const [encounter, setEncounter] = useState<Encounter>();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const job = useQuery({
    queryKey: ["project", projectId, "job", doc.job_id],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/jobs/{job_id}", {
          params: { path: { project_id: projectId, job_id: doc.job_id } },
        }),
      ),
    refetchInterval: 2500,
  });
  const canImport = capabilities.includes("import");
  return (
    <>
      <h3 className="detail-filename">{doc.filename}</h3>
      <dl className="document-meta">
        <dt>处理状态</dt>
        <dd>
          {statuses[doc.processing_status]} · 尝试 {doc.attempt} 次
        </dd>
        <dt>患者记录</dt>
        <dd>{doc.patient_key}</dd>
        <dt>就诊记录</dt>
        <dd>
          {doc.encounter_date ?? "日期未填写"} ·{" "}
          {doc.department || "科室未填写"}
        </dd>
        <dt>关联版本</dt>
        <dd>{doc.revision}</dd>
      </dl>
      {doc.error && (
        <Notice>
          {errorLabels[String(doc.error.code)] ??
            "解析失败，请核对原件后处理。"}
          <br />
          <small>
            代码：{String(doc.error.code)} · 请求：
            {String(doc.error.request_id)}
          </small>
        </Notice>
      )}
      <div className="actions">
        {canImport && ["queued", "running"].includes(doc.job_status) && (
          <button
            disabled={busy || doc.cancel_requested}
            onClick={() => void act(doc, "cancel")}
          >
            {doc.cancel_requested ? "正在取消…" : "取消任务"}
          </button>
        )}
        {canImport &&
          doc.attempt < doc.max_attempts &&
          (doc.job_status === "cancelled" ||
            (doc.job_status === "failed" && doc.error?.retryable === true)) && (
            <button disabled={busy} onClick={() => void act(doc, "retry")}>
              重试解析
            </button>
          )}
        {canImport && (
          <button onClick={() => setChanging(!changing)}>
            {changing ? "收起更正" : "更正关联"}
          </button>
        )}
        {doc.artifact_id && capabilities.includes("original.read") && (
          <a
            href={`/api/v1/projects/${projectId}/documents/${doc.id}/original`}
            className="button-link"
          >
            下载原件
          </a>
        )}
      </div>
      {changing && (
        <section className="correction">
          <h3>更正患者 / 就诊关联</h3>
          <AssociationPicker
            projectId={projectId}
            csrf={csrf}
            patient={patient}
            encounter={encounter}
            setPatient={(p) => {
              setPatient(p);
              setEncounter(undefined);
            }}
            setEncounter={setEncounter}
          />
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (!patient || !encounter) return;
              setSaving(true);
              setError("");
              try {
                await result(
                  api.PATCH(
                    "/api/v1/projects/{project_id}/documents/{document_id}/association",
                    {
                      params: {
                        path: { project_id: projectId, document_id: doc.id },
                      },
                      headers: { "X-CSRF-Token": csrf },
                      body: {
                        patient_id: patient.id,
                        encounter_id: encounter.id,
                        expected_revision: doc.revision,
                        reason: String(
                          new FormData(e.currentTarget).get("reason"),
                        ),
                      },
                    },
                  ),
                );
                setChanging(false);
                await refresh();
              } catch (err) {
                setError((err as Error).message);
              } finally {
                setSaving(false);
              }
            }}
          >
            <Field title="更正理由">
              <textarea name="reason" required maxLength={500} />
            </Field>
            <button disabled={!encounter || saving}>保存关联更正</button>
          </form>
          {error && <Notice>{error}</Notice>}
        </section>
      )}
      <details className="attempts">
        <summary>处理尝试与诊断（{job.data?.attempts.length ?? 0} 次）</summary>
        {job.error && <Notice>{job.error.message}</Notice>}
        {job.data?.attempts.map((a) => (
          <p key={a.generation}>
            {time(a.started_at)} · {statuses[a.status]}
            {a.error_code ? ` · ${a.error_code}` : ""}
          </p>
        ))}
        {job.data?.heartbeat_at && (
          <p>最近心跳：{time(job.data.heartbeat_at)}</p>
        )}
      </details>
      {doc.artifact_id && capabilities.includes("original.read") && (
        <ExtractionPanel
          key={doc.id}
          doc={doc}
          projectId={projectId}
          csrf={csrf}
          canImport={canImport}
          onEvidence={setEvidence}
        />
      )}
      {doc.artifact_id && capabilities.includes("original.read") ? (
        <Preview
          key={evidence?.id ?? doc.artifact_id}
          artifactId={evidence?.parse_artifact_id ?? doc.artifact_id}
          projectId={projectId}
          evidence={evidence}
        />
      ) : (
        <p className="preview-placeholder">
          {capabilities.includes("original.read")
            ? "解析完成后可查看文书内容。"
            : "当前账号未获准读取原件与解析内容。"}
        </p>
      )}
    </>
  );
}
function Preview({
  artifactId,
  projectId,
  evidence,
}: {
  artifactId: string;
  projectId: string;
  evidence?: Models["Evidence"];
}) {
  const [page, setPage] = useState(evidence?.page ?? 1);
  const [zoom, setZoom] = useState(100);
  const [showBoxes, setShowBoxes] = useState(false);
  const preview = useQuery({
    queryKey: ["project", projectId, "preview", artifactId],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/parse-artifacts/{artifact_id}", {
          params: { path: { project_id: projectId, artifact_id: artifactId } },
        }),
      ),
  });
  if (preview.error) return <Notice>{preview.error.message}</Notice>;
  if (!preview.data) return <p>正在加载解析预览…</p>;
  const current = preview.data.pages[page - 1];
  return (
    <section className="preview">
      <div className="preview-controls">
        <h3>原文预览</h3>
        {evidence && (
          <p className="hint">
            已定位证据：第 {evidence.page} 页 · {evidence.quote}
          </p>
        )}
        <div className="actions">
          <button
            aria-label="上一页预览"
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </button>
          <span>
            {page} / {preview.data.pages.length}
          </span>
          <button
            aria-label="下一页预览"
            disabled={page >= preview.data.pages.length}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </button>
          <select
            aria-label="预览缩放"
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
          >
            {[75, 100, 125, 150, 200].map((n) => (
              <option key={n} value={n}>
                {n}%
              </option>
            ))}
          </select>
        </div>
      </div>
      {current.needs_ocr && (
        <p className="ocr-note">
          此页需要 OCR。当前保留真实预览，尚未识别的内容不会补写。
        </p>
      )}
      {current.image ? (
        <>
          <label className="inline-check">
            <input
              type="checkbox"
              checked={showBoxes}
              onChange={(e) => setShowBoxes(e.target.checked)}
            />
            显示文字块位置
          </label>
          <div className="page-scroll">
            <div className="page-canvas" style={{ width: `${zoom}%` }}>
              {/* The authenticated API serves a persisted raster page, never active PDF content. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                alt={`文书第 ${page} 页`}
                src={`/api/v1/projects/${projectId}/parse-artifacts/${artifactId}/pages/${page}`}
              />
              {evidence?.page === page &&
                evidence.boxes?.map((box, i) => (
                  <span
                    key={i}
                    className="evidence-box"
                    aria-label="当前事实证据"
                    style={{
                      left: `${box.x0 * 100}%`,
                      top: `${box.y0 * 100}%`,
                      width: `${(box.x1 - box.x0) * 100}%`,
                      height: `${(box.y1 - box.y0) * 100}%`,
                    }}
                  />
                ))}
              {showBoxes &&
                current.blocks
                  .filter((b) => b.bbox)
                  .map((b) => (
                    <span
                      className="text-box"
                      key={b.id}
                      title={b.text}
                      style={{
                        left: `${b.bbox![0] * 100}%`,
                        top: `${b.bbox![1] * 100}%`,
                        width: `${(b.bbox![2] - b.bbox![0]) * 100}%`,
                        height: `${(b.bbox![3] - b.bbox![1]) * 100}%`,
                      }}
                    />
                  ))}
            </div>
          </div>
        </>
      ) : (
        <pre className="source-text" style={{ fontSize: `${zoom}%` }}>
          {current.blocks.map((b) => {
            if (
              !evidence ||
              evidence.page !== page ||
              b.id !== evidence.block_id
            )
              return b.text;
            const chars = Array.from(b.text);
            const start = evidence.span.start - b.start,
              end = evidence.span.end - b.start;
            return (
              <span key={b.id}>
                {chars.slice(0, start).join("")}
                <mark>{chars.slice(start, end).join("")}</mark>
                {chars.slice(end).join("")}
              </span>
            );
          })}
        </pre>
      )}
      {current.image && (
        <details>
          <summary>查看本页文字层</summary>
          <pre className="source-text">
            {current.blocks.map((b) => b.text).join("") ||
              "此页暂无可用文字层。"}
          </pre>
        </details>
      )}
    </section>
  );
}
function Audit({ projectId }: { projectId: string }) {
  const events = useQuery({
    queryKey: ["project", projectId, "audit"],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/audit-events", {
          params: { path: { project_id: projectId }, query: { limit: 50 } },
        }),
      ),
  });
  const labels: Record<string, string> = {
    "patient.create": "患者建档",
    "encounter.create": "就诊建档",
    "document.upload": "文书上传",
    "document.associate": "更正关联",
    "document.original.read": "读取原件",
    "document.preview.read": "读取预览",
    "job.cancel": "取消任务",
    "job.retry": "重试任务",
  };
  return (
    <section className="audit-panel">
      <h2>最近操作记录</h2>
      {events.error && <Notice>{events.error.message}</Notice>}
      <ol>
        {events.data?.map((e) => (
          <li key={e.id}>
            <time>{time(e.created_at)}</time> {labels[e.action] ?? e.action}
            <small>
              目标 {e.target_id} · 操作者 {e.actor_id}
            </small>
            {e.details.reason ? <p>理由：{String(e.details.reason)}</p> : null}
          </li>
        ))}
      </ol>
      {events.data?.length === 0 && <p>当前项目暂无操作记录。</p>}
    </section>
  );
}

function ExtractionPanel({
  doc,
  projectId,
  csrf,
  canImport,
  onEvidence,
}: {
  doc: Doc;
  projectId: string;
  csrf: string;
  canImport: boolean;
  onEvidence: (e: Models["Evidence"]) => void;
}) {
  const cache = useQueryClient();
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const path = { project_id: projectId, document_id: doc.id };
  const [templateVersion, setTemplateVersion] = useState("");
  const templates = useQuery({
    queryKey: ["project", projectId, "template-versions"],
    enabled: canImport,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/template-versions", {
          params: { path: { project_id: projectId } },
        }),
      ),
  });
  const config = useQuery({
    queryKey: ["project", projectId, "extraction-config"],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/extraction-config", {
          params: { path },
        }),
      ),
  });
  const history = useQuery({
    queryKey: ["project", projectId, "extractions", doc.id],
    queryFn: () =>
      result(
        api.GET(
          "/api/v1/projects/{project_id}/documents/{document_id}/extractions",
          { params: { path } },
        ),
      ),
    refetchInterval: (q) =>
      q.state.data?.some((r) => ["queued", "running"].includes(r.status))
        ? 2000
        : false,
  });
  const run = history.data?.find((r) => r.id === selected) ?? history.data?.[0];
  const scope = useQuery({
    queryKey: [
      "project",
      projectId,
      "review-set",
      doc.encounter_id,
      history.dataUpdatedAt,
    ],
    enabled: history.data?.some((r) => r.status === "succeeded") ?? false,
    queryFn: () =>
      result(
        api.GET(
          "/api/v1/projects/{project_id}/encounters/{encounter_id}/review-set",
          {
            params: {
              path: { project_id: projectId, encounter_id: doc.encounter_id },
            },
          },
        ),
      ),
  });
  const active = scope.data?.members.some(
    (m) => m.extraction_run_id === run?.id,
  );
  async function action(
    kind: "start" | "cancel" | "retry" | "activate" | "ocr",
  ) {
    setBusy(true);
    setError("");
    try {
      const headers = { "X-CSRF-Token": csrf };
      if (kind === "ocr") {
        await result(
          api.POST(
            "/api/v1/projects/{project_id}/documents/{document_id}/ocr",
            {
              params: {
                path,
                header: { "Idempotency-Key": crypto.randomUUID() },
              },
              headers,
            },
          ),
        );
      } else if (kind === "start") {
        const value = await result(
          api.POST(
            "/api/v1/projects/{project_id}/documents/{document_id}/extractions",
            {
              params: {
                path,
                header: { "Idempotency-Key": crypto.randomUUID() },
              },
              headers,
              body: {
                parse_artifact_id: doc.artifact_id!,
                template_version:
                  templateVersion || `${doc.document_type}-1.0.0`,
              },
            },
          ),
        );
        setSelected(value.run_id);
      } else if (kind === "activate" && run && scope.data) {
        await result(
          api.POST(
            "/api/v1/projects/{project_id}/documents/{document_id}/active-run",
            {
              params: { path },
              headers,
              body: {
                run_id: run.id,
                expected_scope_revision: scope.data.scope_revision,
                reason,
              },
            },
          ),
        );
        setReason("");
      } else if (run) {
        const options = {
          params: { path: { project_id: projectId, job_id: run.job_id } },
          headers,
        };
        await result(
          kind === "cancel"
            ? api.POST(
                "/api/v1/projects/{project_id}/jobs/{job_id}/cancel",
                options,
              )
            : api.POST(
                "/api/v1/projects/{project_id}/jobs/{job_id}/retry",
                options,
              ),
        );
      }
      await cache.invalidateQueries({ queryKey: ["project", projectId] });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const labels: Record<string, string> = {
    diagnoses: "诊断",
    history: "病史",
    duration: "病程",
    "medications.name": "药物",
    "medications.dose": "剂量",
    "medications.frequency": "频次",
    "observations.name": "检验项目",
    "observations.result": "检验结果",
    "observations.method": "方法",
    "observations.specimen": "标本",
  };
  const missing: Record<string, string> = {
    explicitly_unknown: "明确未知",
    not_mentioned: "未提及",
    missing: "应有而缺失",
    unreadable: "无法辨认",
    not_applicable: "不适用",
  };
  return (
    <section className="extraction-panel" aria-label="事实抽取">
      <div className="section-head">
        <h3>事实抽取</h3>
        {canImport && (
          <button
            className="primary"
            disabled={
              busy ||
              !config.data?.enabled ||
              doc.processing_status === "needs_ocr" ||
              history.data?.some((r) =>
                ["queued", "running"].includes(r.status),
              )
            }
            onClick={() => void action("start")}
          >
            {history.data?.length ? "创建新抽取版本" : "开始抽取"}
          </button>
        )}
      </div>
      {config.data?.provider === "synthetic" && (
        <p className="ocr-note">
          合成适配器验证模式，仅支持标记为 SYNTHETIC-S2 的测试文书。
        </p>
      )}
      {config.data && !config.data.enabled && (
        <p className="hint">抽取服务尚未启用，请配置获准模型服务后开始抽取。</p>
      )}
      {canImport &&
        doc.processing_status === "needs_ocr" &&
        config.data?.ocr_provider !== "disabled" && (
          <button disabled={busy} onClick={() => void action("ocr")}>
            识别扫描文字
          </button>
        )}
      {doc.processing_status === "needs_ocr" && (
        <p className="hint">文书仍有未识别页面，完成 OCR 后才能抽取。</p>
      )}
      {(error || history.error || config.error || scope.error) && (
        <Notice>
          {error ||
            history.error?.message ||
            config.error?.message ||
            scope.error?.message}
        </Notice>
      )}
      {!!history.data?.length && (
        <Field title="抽取版本">
          <select
            value={run?.id ?? ""}
            onChange={(e) => setSelected(e.target.value)}
          >
            {history.data.map((r) => (
              <option key={r.id} value={r.id}>
                {time(r.created_at)} · {statuses[r.status]} · {r.id.slice(-6)}
              </option>
            ))}
          </select>
        </Field>
      )}
      {run && (
        <>
          <p role="status">
            {statuses[run.status]}
            {run.status === "succeeded"
              ? active
                ? " · 当前审核范围"
                : " · 候选版本"
              : ""}
            {run.duration_ms != null &&
              ` · ${(run.duration_ms / 1000).toFixed(2)} 秒`}
            {run.usage &&
              ` · ${String(run.usage.cost_amount)} ${String(run.usage.cost_currency)}`}
          </p>
          {run.error && (
            <Notice>
              抽取未完成：{String(run.error.code)}。失败结果不会加入待审核事实。
            </Notice>
          )}
          {canImport && (
            <Field title="抽取模板版本">
              <select
                value={templateVersion}
                onChange={(e) => setTemplateVersion(e.target.value)}
              >
                <option value="">基础模板 {doc.document_type}-1.0.0</option>
                {templates.data
                  ?.filter((t) => t.version.startsWith(doc.document_type + "-"))
                  .map((t) => (
                    <option key={t.id} value={t.version}>
                      {t.version}
                    </option>
                  ))}
              </select>
            </Field>
          )}
          {templates.error && <Notice>{templates.error.message}</Notice>}
          <div className="actions">
            {canImport && ["queued", "running"].includes(run.status) && (
              <button disabled={busy} onClick={() => void action("cancel")}>
                取消抽取
              </button>
            )}
            {canImport &&
              (run.status === "cancelled" ||
                (run.status === "failed" && run.error?.retryable === true)) && (
                <button disabled={busy} onClick={() => void action("retry")}>
                  重试抽取
                </button>
              )}
          </div>
          {run.status === "succeeded" && (
            <>
              {scope.data && (
                <Link
                  className="button-link"
                  href={`/projects/${projectId}/reviews/${scope.data.id}`}
                >
                  进入就诊审核工作台
                </Link>
              )}
              <div className="extraction-facts">
                <table>
                  <caption>
                    结构化事实 · {run.facts.length} 条，尚未人工审核
                  </caption>
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>抽取值</th>
                      <th>语义 / 时间</th>
                      <th>证据</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.facts.map((f) => (
                      <tr key={f.fact_id}>
                        <td>
                          {labels[f.field_path] ?? f.field_path}
                          <small>实体 {f.entity_group_id.slice(-6)}</small>
                        </td>
                        <td>
                          {f.value.comparator && f.value.comparator !== "="
                            ? f.value.comparator
                            : ""}
                          {f.value.normalized ??
                            missing[f.value.missing_reason ?? "missing"]}
                          {f.value.unit && ` ${f.value.unit}`}
                          <small>原文：{f.value.raw}</small>
                        </td>
                        <td>
                          {
                            {
                              affirmed: "肯定",
                              negated: "否定",
                              suspected: "疑似",
                            }[f.value.assertion ?? "affirmed"]
                          }
                          {f.event_time?.original_text && (
                            <small>
                              {f.event_time.original_text}（
                              {f.event_time.precision === "relative"
                                ? "相对时间"
                                : f.event_time.precision}
                              ）
                            </small>
                          )}
                        </td>
                        <td>
                          {f.evidence.map((ev, i) => (
                            <button key={ev.id} onClick={() => onEvidence(ev)}>
                              定位证据 {i + 1} · 第 {ev.page} 页
                            </button>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <details open>
                <summary>
                  待复核问题（
                  {
                    (active ? (scope.data?.issues ?? run.issues) : run.issues)
                      .length
                  }
                  ）
                </summary>
                <ul className="extraction-issues">
                  {(active
                    ? (scope.data?.issues ?? run.issues)
                    : run.issues
                  ).map((i) => (
                    <li key={i.id}>
                      <strong>
                        {i.severity === "blocking"
                          ? "阻断"
                          : i.severity === "warning"
                            ? "提示"
                            : "信息"}
                      </strong>{" "}
                      · {i.message}
                      {i.can_accept_unknown && (
                        <small>可在人工审核时有理由地保留未知。</small>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
              <details>
                <summary>术语候选（待人工确认）</summary>
                {run.codings.map((c) => (
                  <p key={c.fact_id}>
                    {c.candidates
                      .map((x) => `${x.display} / ${x.code}`)
                      .join("；") || "保留待映射"}{" "}
                    · {c.reason}
                    <small>{c.version}</small>
                  </p>
                ))}
              </details>
              <details>
                <summary>版本、关系与 JSON</summary>
                <pre className="source-text">
                  {JSON.stringify(
                    {
                      configuration: run.configuration,
                      usage: run.usage,
                      relations: run.relations,
                      facts: run.facts,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              {canImport && !active && scope.data && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action("activate");
                  }}
                >
                  <p className="hint">
                    采用此版本将重新计算审核范围和问题，已有审核状态失效。
                  </p>
                  <Field title="采用新版本的理由">
                    <input
                      required
                      maxLength={500}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </Field>
                  <button disabled={busy || !reason.trim()}>
                    采用此版本并重新待审
                  </button>
                </form>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
