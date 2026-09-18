"use client";
import { useEffect, useState } from "react";
import type { Evidence, Fact, Models, Review } from "./types";
import { labels, missing } from "./types";
function template(value: unknown): string {
  return value && typeof value === "object" && "template_version" in value
    ? String(value.template_version)
    : "";
}
export function FactEditor({
  review,
  fact,
  evidence,
  busy,
  onSave,
  onExclude,
  onClose,
}: {
  review: Review;
  fact?: Fact;
  evidence?: Evidence;
  busy: boolean;
  onSave: (
    body: Models["Correction"] | Models["Supplement"],
  ) => Promise<boolean>;
  onExclude: (body: Models["Exclusion"]) => Promise<boolean>;
  onClose: () => void;
}) {
  const [value, setValue] = useState<Models["ClinicalValue"]>(
    fact?.value ?? {
      raw: evidence?.quote ?? "",
      normalized: evidence?.quote ?? "",
      kind: "text",
      unit: null,
      comparator: null,
      missing_reason: null,
      assertion: "affirmed",
      experiencer: "patient",
    },
  );
  const [reason, setReason] = useState("");
  const [field, setField] = useState(
    fact?.field_path ??
      (review.documents.find(
        (d) =>
          d.version_id === evidence?.document_version_id &&
          d.run_id &&
          template(review.configurations[d.run_id]).startsWith("laboratory"),
      )
        ? "observations.name"
        : "diagnoses"),
  );
  const [group, setGroup] = useState("");
  const [evs, setEvs] = useState<Evidence[]>(
    fact?.evidence ?? (evidence ? [evidence] : []),
  );
  const [time, setTime] = useState(fact?.event_time?.original_text ?? "");
  const [precision, setPrecision] = useState<
    Models["ClinicalTime"]["precision"]
  >(fact?.event_time?.precision ?? "relative");
  const [timeValue, setTimeValue] = useState(fact?.event_time?.value ?? "");
  const [evText, setEvText] = useState("");
  const [localError, setLocalError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [closing, setClosing] = useState(false);
  const [baseVersion] = useState(review.scope_revision);
  useEffect(() => {
    function warn(e: BeforeUnloadEvent) {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const run = review.members.find(
    (m) => m.document_version_id === evs[0]?.document_version_id,
  )?.extraction_run_id;
  return (
    <form
      className="fact-editor"
      onChange={() => setDirty(true)}
      onSubmit={async (e) => {
        e.preventDefault();
        const common = {
          expected_scope_revision: baseVersion,
          reason,
          value,
          evidence: evs,
          event_time: time
            ? {
                original_text: time,
                precision,
                value:
                  precision === "relative" || precision === "unknown"
                    ? null
                    : timeValue || null,
              }
            : null,
        };
        const ok = await onSave(
          fact
            ? { ...common, expected_revision: fact.revision }
            : {
                ...common,
                review_set_id: review.id,
                run_id: String(run ?? ""),
                field_path: field,
                entity_group_id: group || null,
              },
        );
        if (ok) {
          setDirty(false);
          onClose();
        }
      }}
    >
      <div className="panel-title">
        <h3>{fact ? "修订事实" : "补录事实"}</h3>
        <button
          type="button"
          onClick={() => (dirty ? setClosing(true) : onClose())}
        >
          关闭编辑
        </button>
      </div>
      {closing && (
        <p className="notice">
          尚有未保存修改。
          <button type="button" onClick={onClose}>
            放弃未保存修改
          </button>
          <button type="button" onClick={() => setClosing(false)}>
            继续编辑
          </button>
        </p>
      )}
      <p className="hint">保存将追加修订，并要求重新核对当前审核范围。</p>
      {baseVersion !== review.scope_revision && (
        <p className="notice">
          范围已更新，输入仍保留。请复制需要的内容，关闭并重新打开编辑，核对最新事实后提交。
        </p>
      )}
      {!fact && (
        <>
          <label className="field">
            字段
            <select value={field} onChange={(e) => setField(e.target.value)}>
              {Object.entries(labels)
                .filter(([k]) => {
                  const config = review.configurations[String(run)];
                  return template(config).startsWith("laboratory")
                    ? k.startsWith("observations.")
                    : !k.startsWith("observations.");
                })
                .map(([k, v]) => (
                  <option value={k} key={k}>
                    {v}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            所属实体
            <select value={group} onChange={(e) => setGroup(e.target.value)}>
              <option value="">新建实体</option>
              {review.facts
                .filter(
                  (f) =>
                    f.field_path.endsWith(".name") &&
                    !f.excluded &&
                    f.evidence[0].document_version_id ===
                      evs[0]?.document_version_id,
                )
                .map((f) => (
                  <option key={f.fact_id} value={f.entity_group_id}>
                    {f.value.raw} · {f.entity_group_id.slice(-6)}
                  </option>
                ))}
            </select>
          </label>
        </>
      )}
      <label className="field">
        原文值
        <input
          required
          value={value.raw}
          onChange={(e) => setValue({ ...value, raw: e.target.value })}
        />
      </label>
      <label className="field">
        缺失语义
        <select
          value={value.missing_reason ?? ""}
          onChange={(e) =>
            setValue({
              ...value,
              missing_reason: (e.target.value ||
                null) as Models["ClinicalValue"]["missing_reason"],
              normalized: e.target.value ? null : value.raw,
            })
          }
        >
          <option value="">有值</option>
          {Object.entries(missing).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        当前标准值
        <input
          required={!value.missing_reason}
          disabled={!!value.missing_reason}
          value={value.normalized ?? ""}
          onChange={(e) => setValue({ ...value, normalized: e.target.value })}
        />
      </label>
      <div className="form-pair">
        <label className="field">
          类型
          <select
            value={value.kind}
            onChange={(e) =>
              setValue({
                ...value,
                kind: e.target.value as Models["ClinicalValue"]["kind"],
              })
            }
          >
            <option value="text">文字</option>
            <option value="decimal">精确数值</option>
            <option value="date">日期</option>
          </select>
        </label>
        <label className="field">
          原始单位
          <input
            value={value.unit ?? ""}
            onChange={(e) =>
              setValue({ ...value, unit: e.target.value || null })
            }
          />
        </label>
      </div>
      <div className="form-pair">
        <label className="field">
          断言
          <select
            value={value.assertion}
            onChange={(e) =>
              setValue({
                ...value,
                assertion: e.target
                  .value as Models["ClinicalValue"]["assertion"],
              })
            }
          >
            <option value="affirmed">肯定</option>
            <option value="negated">否定</option>
            <option value="suspected">疑似</option>
          </select>
        </label>
        <label className="field">
          主体
          <select
            value={value.experiencer}
            onChange={(e) =>
              setValue({
                ...value,
                experiencer: e.target
                  .value as Models["ClinicalValue"]["experiencer"],
              })
            }
          >
            <option value="patient">患者</option>
            <option value="family">家族</option>
            <option value="other">其他</option>
            <option value="unknown">未知</option>
          </select>
        </label>
      </div>
      {value.kind === "decimal" && (
        <label className="field">
          比较符
          <select
            value={value.comparator ?? "="}
            onChange={(e) =>
              setValue({
                ...value,
                comparator: e.target
                  .value as Models["ClinicalValue"]["comparator"],
              })
            }
          >
            {["=", "<", ">", "<=", ">="].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
      )}
      <details>
        <summary>事件时间与证据</summary>
        <label className="field">
          时间原文
          <input value={time} onChange={(e) => setTime(e.target.value)} />
        </label>
        <label className="field">
          时间精度
          <select
            value={precision}
            onChange={(e) =>
              setPrecision(
                e.target.value as Models["ClinicalTime"]["precision"],
              )
            }
          >
            {["relative", "unknown", "year", "month", "day", "datetime"].map(
              (v) => (
                <option key={v}>{v}</option>
              ),
            )}
          </select>
        </label>
        {!["relative", "unknown"].includes(precision) && (
          <label className="field">
            原文中的日期
            <input
              value={timeValue}
              onChange={(e) => setTimeValue(e.target.value)}
            />
          </label>
        )}
        {evs.map((ev, i) => (
          <div key={ev.id}>
            <p>
              证据 {i + 1} · 第 {ev.page} 页：{ev.quote}
            </p>
            <button
              type="button"
              disabled={evs.length === 1}
              onClick={() => setEvs(evs.filter((_, n) => n !== i))}
            >
              移除此证据
            </button>
          </div>
        ))}
        <label className="field">
          新增证据引用
          <select
            value={evText}
            onChange={(e) => {
              setEvText(e.target.value);
              const ev = review.facts
                .flatMap((f) => f.evidence)
                .find((ev) => ev.id === e.target.value);
              if (ev && !evs.some((x) => x.id === ev.id)) setEvs([...evs, ev]);
            }}
          >
            <option value="">从当前文书已有证据选择</option>
            {Array.from(
              new Map(
                review.facts
                  .flatMap((f) => f.evidence)
                  .filter(
                    (ev) =>
                      ev.document_version_id === evs[0]?.document_version_id,
                  )
                  .map((ev) => [ev.id, ev]),
              ).values(),
            ).map((ev) => (
              <option key={ev.id} value={ev.id}>
                {ev.quote} · 第 {ev.page} 页
              </option>
            ))}
          </select>
        </label>
      </details>
      <label className="field">
        修改理由
        <textarea
          required
          maxLength={1000}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {localError && <p role="alert">{localError}</p>}
      <div className="actions">
        <button
          className="primary"
          disabled={
            busy ||
            !reason.trim() ||
            !evs.length ||
            baseVersion !== review.scope_revision
          }
        >
          {fact ? "保存修订" : "保存补录"}
        </button>
        {fact && !fact.excluded && (
          <button
            type="button"
            disabled={busy || baseVersion !== review.scope_revision}
            onClick={async () => {
              if (!reason.trim()) {
                setLocalError("排除事实前请填写理由。");
                return;
              }
              if (
                await onExclude({
                  expected_scope_revision: baseVersion,
                  expected_revision: fact.revision,
                  reason,
                })
              ) {
                setDirty(false);
                onClose();
              }
            }}
          >
            排除此事实
          </button>
        )}
      </div>
    </form>
  );
}
