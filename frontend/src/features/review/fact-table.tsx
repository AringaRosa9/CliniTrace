import type { Evidence, Fact, Review } from "./types";
import { labels, factValue } from "./types";
export function FactTable({
  review,
  selected,
  busy,
  onSelect,
  onEdit,
  onCheck,
}: {
  review: Review;
  selected?: string;
  busy: boolean;
  onSelect: (f: Fact, e: Evidence) => void;
  onEdit: (f: Fact) => void;
  onCheck: (f: Fact) => void;
}) {
  return (
    <div className="fact-table-scroll">
      <table className="fact-table">
        <caption>逐项核对 · {review.facts.length} 条事实</caption>
        <thead>
          <tr>
            <th>字段 / 实体</th>
            <th>原始值 → 当前值</th>
            <th>核对与操作</th>
          </tr>
        </thead>
        <tbody>
          {review.facts.map((f) => {
            const original = review.originals.find(
              (o) => o.fact_id === f.fact_id,
            );
            const checked = review.checked_revision_ids.includes(f.revision_id);
            return (
              <tr
                key={f.fact_id}
                id={`fact-${f.fact_id}`}
                className={selected === f.fact_id ? "selected-fact" : ""}
              >
                <td>
                  {labels[f.field_path] ?? f.field_path}
                  <small>
                    实体 {f.entity_group_id.slice(-6)} · 修订 {f.revision}
                  </small>
                  {f.excluded && (
                    <strong className="amber-label">已排除</strong>
                  )}
                </td>
                <td>
                  {original && <small>原始：{factValue(original)}</small>}
                  <strong>{factValue(f)}</strong>
                  <small>
                    {
                      { affirmed: "肯定", negated: "否定", suspected: "疑似" }[
                        f.value.assertion ?? "affirmed"
                      ]
                    }{" "}
                    ·{" "}
                    {
                      {
                        patient: "患者",
                        family: "家族",
                        other: "其他",
                        unknown: "未知",
                      }[f.value.experiencer ?? "patient"]
                    }
                  </small>
                  {f.reason && <small>理由：{f.reason}</small>}
                  {f.evidence.map((e, i) => (
                    <button
                      className="evidence-link"
                      key={e.id}
                      onClick={() => onSelect(f, e)}
                    >
                      证据 {i + 1} · 第 {e.page} 页
                    </button>
                  ))}
                </td>
                <td>
                  <button disabled={busy || checked} onClick={() => onCheck(f)}>
                    {checked ? "已核对" : "确认此项"}
                  </button>
                  <button disabled={busy} onClick={() => onEdit(f)}>
                    {f.excluded ? "查看 / 恢复" : "编辑 / 排除"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!review.facts.length && (
        <p className="empty-note">
          没有可复核事实。请核对抽取结果，或从原文补录。
        </p>
      )}
    </div>
  );
}
export function Timeline({
  review,
  onSelect,
}: {
  review: Review;
  onSelect: (f: Fact, e: Evidence) => void;
}) {
  const facts = review.facts
    .filter((f) => f.event_time && !f.excluded)
    .sort((a, b) =>
      (a.event_time?.value ?? "~").localeCompare(b.event_time?.value ?? "~"),
    );
  return (
    <div className="timeline">
      <p className="hint">仅展示有原文依据的时间；相对病程保留原始精度。</p>
      {facts.length ? (
        facts.map((f) => (
          <article key={f.fact_id}>
            <time>
              {f.event_time?.value ?? f.event_time?.original_text ?? "时间未知"}
            </time>
            <p>
              {labels[f.field_path]} · {factValue(f)}
            </p>
            <button onClick={() => onSelect(f, f.evidence[0])}>
              查看时间证据
            </button>
          </article>
        ))
      ) : (
        <p className="empty-note">当前事实没有可展示的事件时间。</p>
      )}
    </div>
  );
}
export function JsonView({ review }: { review: Review }) {
  return (
    <pre className="review-json">
      {JSON.stringify(
        {
          scope_revision: review.scope_revision,
          members: review.members,
          facts: review.facts,
          relations: review.relations,
          configurations: review.configurations,
        },
        null,
        2,
      )}
    </pre>
  );
}
