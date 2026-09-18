"""Export worker: bounded frozen manifests, fenced attempts, private immutable files."""

import io
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.config import Settings
from app.db.s1 import attempts, jobs
from app.db.s3 import exports
from app.db.session import transaction
from app.integrations.storage.s3 import Storage
from app.modules.documents.service import now
from app.modules.exports.service import publish_file, render


def execute_export(tenant: UUID, project: UUID, jid: UUID, cfg: Settings) -> None:
    scope = {"tenant_id": tenant, "project_id": project}
    with transaction(cfg, tenant=tenant, project=project, worker=True) as conn:
        job = (
            conn.execute(select(jobs).where(jobs.c.id == jid).with_for_update()).mappings().first()
        )
        if not job or job["status"] != "queued" or job["attempt"] >= cfg.max_attempts:
            return
        row = conn.execute(select(exports).where(exports.c.job_id == jid)).mappings().one()
        generation = job["generation"] + 1
        conn.execute(
            jobs.update()
            .where(jobs.c.id == jid)
            .values(
                status="running",
                generation=generation,
                attempt=job["attempt"] + 1,
                started_at=now(),
                heartbeat_at=now(),
                lease_until=now() + timedelta(seconds=cfg.task_timeout_seconds),
                progress=10,
            )
        )
        conn.execute(
            attempts.insert().values(
                id=uuid4(),
                **scope,
                job_id=jid,
                generation=generation,
                status="running",
                started_at=now(),
            )
        )
    key = f"exports/{tenant}/{project}/{row['id']}/{generation}.{row['format']}"
    storage = Storage(cfg)
    failure = None
    data = b""
    try:
        data = render(row["manifest"], row["format"])
        storage.put(
            key, io.BytesIO(data), "application/json" if row["format"] == "json" else "text/csv"
        )
    except Exception:
        failure = {
            "code": "EXPORT_STORAGE_UNAVAILABLE",
            "message": "导出文件未生成，请重试。",
            "request_id": str(job["request_id"]),
            "retryable": True,
            "details": {"stage": "exporting"},
        }
    published = False
    try:
        with transaction(cfg, tenant=tenant, project=project, worker=True) as conn:
            current = (
                conn.execute(select(jobs).where(jobs.c.id == jid).with_for_update())
                .mappings()
                .one()
            )
            if (
                current["generation"] != generation
                or current["status"] != "running"
                or current["lease_until"] < now()
            ):
                return
            status = (
                "cancelled" if current["cancel_requested"] else "failed" if failure else "succeeded"
            )
            if status == "succeeded":
                publish_file(conn, scope, row["id"], key, data)
                published = True
            conn.execute(
                jobs.update()
                .where(jobs.c.id == jid)
                .values(
                    status=status,
                    progress=100 if published else 0,
                    error=failure,
                    finished_at=now(),
                    lease_until=None,
                )
            )
            conn.execute(
                attempts.update()
                .where(attempts.c.job_id == jid, attempts.c.generation == generation)
                .values(
                    status=status,
                    finished_at=now(),
                    error_code=failure["code"] if failure else None,
                )
            )
    finally:
        if not published:
            try:
                storage.delete(key)
            except Exception:
                pass
