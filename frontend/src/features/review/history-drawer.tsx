"use client";
import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { createApiClient } from "@/lib/api/client";
import { result } from "@/lib/api/result";
const api = createApiClient();
export function HistoryDrawer({
  projectId,
  sid,
  onClose,
}: {
  projectId: string;
  sid: string;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const history = useQuery({
    queryKey: ["project", projectId, "review-history", sid],
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/review-sets/{sid}/events", {
          params: { path: { project_id: projectId, sid } },
        }),
      ),
  });
  useEffect(() => {
    const before = document.activeElement as HTMLElement;
    dialog.current?.showModal();
    return () => before?.focus();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="history-drawer"
      aria-labelledby="history-heading"
      onCancel={onClose}
    >
      <div className="panel-title">
        <h2 id="history-heading">修订与审核历史</h2>
        <button onClick={onClose}>关闭历史</button>
      </div>
      {history.error && (
        <p role="alert">
          {history.error.message}
          <button onClick={() => void history.refetch()}>重新加载历史</button>
        </p>
      )}
      {history.isPending && <p>正在加载…</p>}
      <h3>不可变审核快照</h3>
      {history.data?.snapshots.length === 0 && <p>尚未完成审核。</p>}
      {history.data?.snapshots.map((s) => (
        <details key={s.id}>
          <summary>
            范围 v{s.scope_revision} ·{" "}
            {new Date(s.created_at).toLocaleString("zh-CN")}
          </summary>
          <pre>{JSON.stringify(s.payload, null, 2)}</pre>
        </details>
      ))}
      <h3>操作记录</h3>
      {history.data?.events
        .filter((e) => e.action !== "review.read")
        .map((e) => (
          <article key={e.id}>
            <strong>
              {{
                "fact.edit": "修订事实",
                "fact.exclude": "排除事实",
                "fact.supplement": "补录事实",
                "fact.check": "逐项核对",
                "review.approve": "完成审核",
                "review.return": "退回补料",
                "issue.dispose": "问题处置",
              }[e.action] ?? e.action}
            </strong>
            <p>
              {new Date(e.created_at).toLocaleString("zh-CN")} · 操作者{" "}
              {e.actor_id.slice(-8)}
            </p>
            {!!e.details.reason && <p>理由：{String(e.details.reason)}</p>}
            <details>
              <summary>查看变更详情</summary>
              <pre>{JSON.stringify(e.details, null, 2)}</pre>
            </details>
          </article>
        ))}
    </dialog>
  );
}
