from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.db.s1 import attempts, documents, jobs, versions
from app.modules.documents.service import Service, enqueue, now


def job_view(service: Service, job_id: UUID) -> dict[str, Any]:
    service.require("documents.read")
    with service.tx() as conn:
        row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
        if not row:
            raise HTTPException(404)
        history = (
            conn.execute(
                select(
                    attempts.c.generation,
                    attempts.c.status,
                    attempts.c.started_at,
                    attempts.c.finished_at,
                    attempts.c.error_code,
                )
                .where(attempts.c.job_id == job_id)
                .order_by(attempts.c.generation)
            )
            .mappings()
            .all()
        )
        keys = (
            "id",
            "status",
            "attempt",
            "progress",
            "error",
            "created_at",
            "cancel_requested",
            "heartbeat_at",
        )
        return {
            **{k: row[k] for k in keys},
            "attempts": [dict(x) for x in history],
            "stage": row["kind"],
        }


def control(service: Service, job_id: UUID, action: str) -> dict[str, Any]:
    service.require("import")
    with service.tx() as conn:
        row = (
            conn.execute(select(jobs).where(jobs.c.id == job_id).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        document_id = conn.execute(
            select(versions.c.document_id).where(versions.c.id == row["document_version_id"])
        ).scalar_one()
        if action == "cancel":
            if row["status"] in ("succeeded", "failed"):
                raise HTTPException(409)
            status = "cancelled" if row["status"] in ("queued", "cancelled") else "running"
            conn.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .values(
                    cancel_requested=True,
                    status=status,
                    finished_at=now() if status == "cancelled" else None,
                )
            )
            if status == "cancelled":
                conn.execute(
                    documents.update()
                    .where(documents.c.id == document_id)
                    .values(processing_status="cancelled")
                )
        else:
            if (
                row["status"] not in ("failed", "cancelled")
                or row["attempt"] >= service.settings.max_attempts
            ):
                raise HTTPException(409)
            if row["status"] == "failed" and not (row["error"] or {}).get("retryable", False):
                raise HTTPException(409)
            conn.execute(
                jobs.update()
                .where(jobs.c.id == job_id)
                .values(
                    status="queued",
                    cancel_requested=False,
                    error=None,
                    progress=0,
                    lease_until=None,
                    finished_at=None,
                )
            )
            conn.execute(
                documents.update()
                .where(documents.c.id == document_id)
                .values(processing_status="queued")
            )
            enqueue(conn, service.scope, job_id)
        service.event(conn, f"job.{action}", job_id)
    return job_view(service, job_id)
