"use client";
import { useState } from "react";
import type { Models, Review } from "./types";
export function IssuePanel({
  review,
  busy,
  onDispose,
  onLocate,
}: {
  review: Review;
  busy: boolean;
  onDispose: (
    id: string,
    body: Models["DispositionRequest"],
  ) => Promise<boolean>;
  onLocate: (revision: string) => void;
}) {
  return (
    <section className="issue-panel" aria-label="审核问题">
      <div className="panel-title">
        <h2>质量与复核</h2>
        <span>
          {
            review.issues.filter(
              (i) => i.severity === "blocking" && i.status === "open",
            ).length
          }{" "}
          项阻断
        </span>
      </div>
      {review.issues.length ? (
        review.issues.map((i) => (
          <Issue
            key={`${review.scope_revision}-${i.id}`}
            issue={i}
            review={review}
            busy={busy}
            onDispose={onDispose}
            onLocate={onLocate}
          />
        ))
      ) : (
        <p className="empty-note">当前没有规则问题，仍需逐项核对事实。</p>
      )}
    </section>
  );
}
function Issue({
  issue: i,
  review,
  busy,
  onDispose,
  onLocate,
}: {
  issue: Models["ReviewIssue"];
  review: Review;
  busy: boolean;
  onDispose: (
    id: string,
    body: Models["DispositionRequest"],
  ) => Promise<boolean>;
  onLocate: (revision: string) => void;
}) {
  const [reason, setReason] = useState("");
  const resolvable = [
    "CONFLICT",
    "DUPLICATE",
    "OCR_LOW_QUALITY",
    "MISSING_DIAGNOSIS",
  ].includes(i.rule);
  return (
    <article className="review-issue">
      <strong>
        {i.severity === "blocking"
          ? "阻断"
          : i.severity === "warning"
            ? "提示"
            : "信息"}{" "}
        ·{" "}
        {i.status === "open"
          ? "待处理"
          : i.status === "accepted_unknown"
            ? "已接受未知"
            : "已核对处理"}
      </strong>
      <p>{i.message}</p>
      {i.fact_revision_ids.map((id) => (
        <button key={id} onClick={() => onLocate(id)}>
          查看相关字段
        </button>
      ))}
      {i.status !== "open" ? (
        <p className="hint">理由：{i.reason}</p>
      ) : i.can_accept_unknown || resolvable ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void onDispose(i.id, {
              review_set_id: review.id,
              expected_scope_revision: review.scope_revision,
              status: i.can_accept_unknown ? "accepted_unknown" : "resolved",
              reason,
            });
          }}
        >
          <label className="field">
            处置理由
            <textarea
              required
              maxLength={1000}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <button disabled={busy || !reason.trim()}>
            {i.can_accept_unknown ? "接受未知并保留原文" : "已核对并说明处理"}
          </button>
        </form>
      ) : (
        <p className="hint">请修订相关事实或补充材料后重新校验。</p>
      )}
    </article>
  );
}
