"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createApiClient } from "@/lib/api/client";
import { result } from "@/lib/api/result";
import type { Evidence, Fact, Review } from "./types";
import { labels } from "./types";
const api = createApiClient();

export function DocumentViewer({
  projectId,
  review,
  evidence,
  onSelect,
  onEvidenceDraft,
}: {
  projectId: string;
  review: Review;
  evidence?: Evidence;
  onSelect: (f: Fact, e: Evidence) => void;
  onEvidenceDraft: (e: Evidence) => void;
}) {
  const [selectedDoc, setDoc] = useState("");
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [overlaps, setOverlaps] = useState<Fact[]>([]);
  const doc =
    review.documents.find((d) => d.id === selectedDoc) ?? review.documents[0];
  // Evidence selection remounts this component with the evidence document/page as defaults.
  const activeDoc = selectedDoc
    ? doc
    : (review.documents.find(
        (d) => d.version_id === evidence?.document_version_id,
      ) ?? doc);
  const currentPage =
    page === 1 && !selectedDoc && evidence ? evidence.page : page;
  const parsed = useQuery({
    queryKey: ["project", projectId, "parse", activeDoc?.parse_artifact_id],
    enabled: !!activeDoc?.parse_artifact_id,
    queryFn: () =>
      result(
        api.GET("/api/v1/projects/{project_id}/parse-artifacts/{artifact_id}", {
          params: {
            path: {
              project_id: projectId,
              artifact_id: activeDoc!.parse_artifact_id!,
            },
          },
        }),
      ),
  });
  const p = parsed.data?.pages.find((p) => p.page === currentPage);
  function selectBlock(blockId: string) {
    const matches = review.facts.filter((f) =>
      f.evidence.some(
        (e) =>
          e.document_version_id === activeDoc?.version_id &&
          e.page === currentPage &&
          e.block_id === blockId,
      ),
    );
    setOverlaps(matches);
    if (matches.length === 1)
      onSelect(
        matches[0],
        matches[0].evidence.find(
          (e) =>
            e.block_id === blockId &&
            e.document_version_id === activeDoc?.version_id,
        )!,
      );
  }
  return (
    <section className="review-source" aria-label="原文预览">
      <div className="panel-title">
        <h2>原始文书</h2>
        <span>{review.documents.length} 份</span>
      </div>
      <label className="field">
        <span>关联文书</span>
        <select
          value={activeDoc?.id ?? ""}
          onChange={(e) => {
            setDoc(e.target.value);
            setPage(1);
            setOverlaps([]);
          }}
        >
          {review.documents.map((d) => (
            <option key={d.id} value={d.id}>
              {d.filename}
            </option>
          ))}
        </select>
      </label>
      {!activeDoc?.parse_artifact_id ? (
        <p className="empty-note">此文书尚未完成抽取，请到文档任务补齐处理。</p>
      ) : parsed.isPending ? (
        <p role="status">正在读取原文…</p>
      ) : parsed.error ? (
        <p role="alert">
          {parsed.error.message}
          <button onClick={() => void parsed.refetch()}>重新加载原文</button>
        </p>
      ) : (
        p && (
          <>
            <div className="viewer-toolbar">
              <button
                aria-label="上一页"
                disabled={currentPage <= 1}
                onClick={() => {
                  setDoc(activeDoc!.id);
                  setPage(currentPage - 1);
                }}
              >
                上一页
              </button>
              <span>
                {currentPage} / {parsed.data!.pages.length}
              </span>
              <button
                aria-label="下一页"
                disabled={currentPage >= parsed.data!.pages.length}
                onClick={() => {
                  setDoc(activeDoc!.id);
                  setPage(currentPage + 1);
                }}
              >
                下一页
              </button>
              <select
                aria-label="原文缩放"
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
            {overlaps.length > 1 && (
              <div
                className="overlap-choice"
                role="group"
                aria-label="重叠证据关联字段"
              >
                <p>此处关联多个字段，请选择：</p>
                {overlaps.map((f) => (
                  <button
                    key={f.fact_id}
                    onClick={() =>
                      onSelect(
                        f,
                        f.evidence.find(
                          (e) =>
                            e.document_version_id === activeDoc?.version_id &&
                            e.page === currentPage,
                        )!,
                      )
                    }
                  >
                    {labels[f.field_path]} · {f.value.raw}
                  </button>
                ))}
              </div>
            )}
            {p.image && (
              <div className="page-scroll">
                <div className="page-canvas" style={{ width: `${zoom}%` }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    alt={`文书第 ${currentPage} 页`}
                    src={`/api/v1/projects/${projectId}/parse-artifacts/${activeDoc!.parse_artifact_id}/pages/${currentPage}`}
                    onError={(e) => {
                      e.currentTarget.alt = "页图加载失败，请点击重新加载原文";
                    }}
                  />
                  {p.blocks
                    .filter((b) => b.bbox)
                    .map((b) => (
                      <button
                        className={`source-region ${evidence?.block_id === b.id && evidence.document_version_id === activeDoc?.version_id ? "active" : ""}`}
                        key={b.id}
                        aria-label={`定位字段：${b.text}`}
                        onClick={() => selectBlock(b.id)}
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
            )}
            <div className="source-blocks" style={{ fontSize: `${zoom}%` }}>
              {p.blocks.map((b) => {
                const match =
                  evidence?.block_id === b.id &&
                  evidence.document_version_id === activeDoc?.version_id &&
                  evidence.page === currentPage;
                const chars = Array.from(b.text);
                return (
                  <div key={b.id} className={match ? "selected-block" : ""}>
                    <button
                      className="source-block"
                      onClick={() => selectBlock(b.id)}
                    >
                      {match ? (
                        <>
                          {chars
                            .slice(0, evidence.span.start - b.start)
                            .join("")}
                          <mark>
                            {chars
                              .slice(
                                evidence.span.start - b.start,
                                evidence.span.end - b.start,
                              )
                              .join("")}
                          </mark>
                          {chars.slice(evidence.span.end - b.start).join("")}
                        </>
                      ) : (
                        b.text
                      )}
                    </button>
                    <button
                      className="use-evidence"
                      onClick={() =>
                        onEvidenceDraft({
                          id: crypto.randomUUID(),
                          document_version_id: activeDoc!.version_id,
                          parse_artifact_id: activeDoc!.parse_artifact_id!,
                          text_version: parsed.data!.text_version,
                          page: currentPage,
                          span: { start: b.start, end: b.end },
                          quote: b.text,
                          block_id: b.id,
                          boxes: b.bbox
                            ? [
                                {
                                  x0: b.bbox[0],
                                  y0: b.bbox[1],
                                  x1: b.bbox[2],
                                  y1: b.bbox[3],
                                },
                              ]
                            : [],
                        })
                      }
                    >
                      用作补录证据
                    </button>
                  </div>
                );
              })}
            </div>
            <button onClick={() => void parsed.refetch()}>重新加载原文</button>
          </>
        )
      )}
    </section>
  );
}
