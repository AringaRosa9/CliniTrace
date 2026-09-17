"""Durable routing, fenced attempts and a separately bounded parser process."""

import json
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from celery import Celery  # type: ignore[import-untyped]
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.s1 import artifacts, attempts, documents, jobs, outbox, versions
from app.db.s2 import measurements, results, runs
from app.db.session import transaction
from app.integrations.storage.s3 import Storage
from app.modules.documents.service import now
from app.modules.extractions.pipeline import (
    PROMPT,
    RULES,
    TERMS,
    ExtractionFailure,
    digest,
    model_for,
)
from app.modules.extractions.service import publish

settings = get_settings()
celery: Any = Celery("clinical_documents", broker=settings.redis_url)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    task_time_limit=settings.task_timeout_seconds + 45,
    broker_transport_options={"visibility_timeout": settings.task_timeout_seconds + 90},
)


def error(code: str, request_id: UUID, retryable: bool) -> dict[str, Any]:
    return {
        "code": code,
        "message": "文书解析未完成，请查看失败代码。",
        "details": {"stage": "parsing"},
        "request_id": str(request_id),
        "retryable": retryable,
    }


def execute(tenant: str, project: str, job_id: str, config: Settings | None = None) -> None:
    cfg = config or settings
    scope = {"tenant_id": UUID(tenant), "project_id": UUID(project)}
    jid = UUID(job_id)
    tx = {"tenant": UUID(tenant), "project": UUID(project), "worker": True}
    with transaction(cfg, **tx) as conn:  # type: ignore[arg-type]
        job = (
            conn.execute(select(jobs).where(jobs.c.id == jid).with_for_update()).mappings().first()
        )
        if not job or job["status"] != "queued":
            return
        if job["attempt"] >= cfg.max_attempts:
            return
        version = dict(
            conn.execute(select(versions).where(versions.c.id == job["document_version_id"]))
            .mappings()
            .one()
        )
        extracting = job["kind"] == "extracting"
        run = (
            dict(conn.execute(select(runs).where(runs.c.job_id == jid)).mappings().one())
            if extracting
            else None
        )
        generation = job["generation"] + 1
        conn.execute(
            jobs.update()
            .where(jobs.c.id == jid)
            .values(
                status="running",
                attempt=job["attempt"] + 1,
                generation=generation,
                started_at=now(),
                heartbeat_at=now(),
                lease_until=now() + timedelta(seconds=30),
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
        conn.execute(
            documents.update()
            .where(documents.c.id == version["document_id"])
            .values(processing_status="extracting" if extracting else "parsing")
        )
    started = time.monotonic()
    storage = Storage(cfg)
    written: list[str] = []
    result: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    cancelled = False
    trace: dict[str, Any] | None = None
    measured = False
    artifact_id = uuid4()
    category = "extracted" if extracting else "parsed"
    prefix = f"{category}/{tenant}/{project}/{version['id']}/{artifact_id}"

    def heartbeat() -> bool:
        with transaction(cfg, **tx) as conn:  # type: ignore[arg-type]
            row = (
                conn.execute(select(jobs).where(jobs.c.id == jid).with_for_update())
                .mappings()
                .one()
            )
            if (
                row["generation"] != generation
                or row["status"] != "running"
                or row["cancel_requested"]
                or row["lease_until"] < now()
            ):
                return False
            conn.execute(
                jobs.update()
                .where(jobs.c.id == jid)
                .values(
                    heartbeat_at=now(),
                    lease_until=now() + timedelta(seconds=30),
                    progress=30 if extracting else min(85, 20 + int(time.monotonic() - started)),
                )
            )
            return True

    try:
        with tempfile.TemporaryDirectory(prefix="bljgh-parse-") as directory:
            path = Path(directory)
            if extracting and run:
                fixed = run["configuration"]
                if (
                    fixed["provider"] != cfg.extraction_provider
                    or fixed.get("schema_digest")
                    != digest(model_for(fixed["template_version"]).model_json_schema())
                    or fixed["authorization"] != cfg.extraction_authorization
                    or (
                        fixed["terminology_version"].startswith("synthetic")
                        and fixed["terminology_digest"] != digest(TERMS)
                    )
                    or fixed["prompt_digest"] != digest(PROMPT)
                    or fixed["rules_digest"] != digest(RULES)
                    or fixed["gateway_digest"] != digest(cfg.extraction_gateway_url)
                    or fixed["model"]
                    != (
                        cfg.extraction_model
                        if cfg.extraction_provider == "gateway"
                        else "synthetic-grammar-1.0.0"
                    )
                ):
                    raise ExtractionFailure("RUN_CONFIGURATION_CHANGED")
                with transaction(cfg, **tx) as conn:  # type: ignore[arg-type]
                    source = (
                        conn.execute(
                            select(artifacts).where(artifacts.c.id == run["parse_artifact_id"])
                        )
                        .mappings()
                        .one()
                    )
                parsed = json.loads(storage.read(source["object_key"]))
                if digest(parsed) != run["input_digest"]:
                    raise ExtractionFailure("PARSE_DIGEST_MISMATCH")
                request = {
                    "run_id": str(run["id"]),
                    "generation": generation,
                    "parsed": parsed,
                    "document_version_id": str(version["id"]),
                    "parse_artifact_id": str(run["parse_artifact_id"]),
                    "configuration": fixed,
                }
                frozen = cfg.model_copy(
                    update={
                        "extraction_max_chars": fixed["parameters"]["max_chars"],
                        "extraction_max_output_bytes": fixed["parameters"]["max_output_bytes"],
                        "extraction_max_repairs": fixed["parameters"]["max_repairs"],
                        "extraction_timeout_seconds": fixed["parameters"]["timeout_seconds"],
                    }
                )
                (path / "config.json").write_text(frozen.model_dump_json())
                (path / "input").write_text(json.dumps(request))
                command = [sys.executable, "-m", "app.workers.extract", directory]
            else:
                (path / "input").write_bytes(storage.read(version["object_key"]))
                command = [
                    sys.executable,
                    "-m",
                    "app.integrations.parsers.document",
                    directory,
                    version["mime"],
                    str(cfg.max_document_pages),
                    str(cfg.max_image_pixels),
                    cfg.ocr_provider,
                    cfg.ocr_language,
                    str(cfg.ocr_timeout_seconds),
                ]
            child = subprocess.Popen(
                command,
                start_new_session=True,
                cwd=Path(__file__).resolve().parents[2],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                while child.poll() is None:
                    if not heartbeat():
                        cancelled = True
                        break
                    if time.monotonic() - started > cfg.task_timeout_seconds:
                        failure = error(
                            "EXTRACTION_TIMEOUT" if extracting else "PARSE_TIMEOUT",
                            job["request_id"],
                            True,
                        )
                        break
                    time.sleep(0.2)
            finally:
                if child.poll() is None:
                    import os
                    import signal

                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            if not cancelled and not failure:
                if child.returncode:
                    code = "PARSER_TERMINATED"
                    if (path / "error.json").exists():
                        code = json.loads((path / "error.json").read_text())["code"]
                    retryable = code in ("PARSER_TERMINATED", "OCR_TIMEOUT")
                    if (path / "error.json").exists():
                        retryable = json.loads((path / "error.json").read_text()).get(
                            "retryable", retryable
                        )
                    failure = error(code, job["request_id"], retryable)
                else:
                    result = json.loads((path / "result.json").read_text())
            if extracting and (path / "raw.json").exists():
                trace = json.loads((path / "raw.json").read_text())
                key = f"{prefix}/raw.json"
                written.append(key)
                with (path / "raw.json").open("rb") as stream:
                    storage.put(key, stream, "application/json")
            if not cancelled and not failure:
                for file in sorted(path.iterdir()):
                    if file.name in ("input", "config.json") or (
                        extracting and file.name == "raw.json"
                    ):
                        continue
                    if not heartbeat():
                        cancelled = True
                        break
                    key = f"{prefix}/{file.name}"
                    written.append(key)
                    with file.open("rb") as stream:
                        storage.put(
                            key,
                            stream,
                            "image/png" if file.suffix == ".png" else "application/json",
                        )
    except ExtractionFailure as exc:
        failure = error(exc.code, job["request_id"], exc.retryable)
    except Exception:
        failure = error("DEPENDENCY_UNAVAILABLE", job["request_id"], True)
    if failure and extracting:
        failure["details"]["stage"] = "extracting"
        failure["message"] = "抽取未完成，请查看失败代码。"
    committed = False
    try:
        with transaction(cfg, **tx) as conn:  # type: ignore[arg-type]
            current = (
                conn.execute(select(jobs).where(jobs.c.id == jid).with_for_update())
                .mappings()
                .one()
            )
            # This is the only publish point. Old workers and cancelled calls cannot win here.
            if (
                current["generation"] != generation
                or current["status"] != "running"
                or current["lease_until"] < now()
            ):
                # A stale worker may append billing evidence, never publish clinical facts.
                if extracting and run:
                    from sqlalchemy.dialects.postgresql import insert

                    conn.execute(
                        insert(measurements)
                        .values(
                            id=uuid4(),
                            **scope,
                            run_id=run["id"],
                            generation=generation,
                            raw_object_key=f"{prefix}/raw.json" if trace else None,
                            usage=trace.get("usage") if trace else None,
                            duration_ms=int((time.monotonic() - started) * 1000),
                            outcome="superseded",
                        )
                        .on_conflict_do_nothing()
                    )
                return
            cancelled = cancelled or current["cancel_requested"]
            status = "cancelled" if cancelled else "failed" if failure else "succeeded"
            if status == "succeeded" and result and extracting and run:
                try:
                    with conn.begin_nested():
                        publish(
                            conn,
                            scope,
                            run,
                            result,
                            prefix,
                            int((time.monotonic() - started) * 1000),
                        )
                except ExtractionFailure as exc:
                    failure = error(exc.code, job["request_id"], exc.retryable)
                    failure["details"]["stage"] = "extracting"
                    status = "failed"
            if extracting and run:
                conn.execute(
                    measurements.insert().values(
                        id=uuid4(),
                        **scope,
                        run_id=run["id"],
                        generation=generation,
                        raw_object_key=f"{prefix}/raw.json" if trace else None,
                        usage=trace.get("usage") if trace else None,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        outcome=status,
                    )
                )
                measured = True
            if status == "succeeded" and result and not extracting:
                conn.execute(
                    artifacts.insert().values(
                        id=artifact_id,
                        **scope,
                        document_version_id=version["id"],
                        job_id=jid,
                        parser_version=result["parser_version"],
                        object_key=f"{prefix}/result.json",
                        page_count=len(result["pages"]),
                        needs_ocr=result["needs_ocr"],
                        created_at=now(),
                    )
                )
            conn.execute(
                jobs.update()
                .where(jobs.c.id == jid)
                .values(
                    status=status,
                    finished_at=now(),
                    lease_until=None,
                    error=None if cancelled else failure,
                    progress=100 if status == "succeeded" else current["progress"],
                )
            )
            conn.execute(
                attempts.update()
                .where(attempts.c.job_id == jid, attempts.c.generation == generation)
                .values(
                    status=status,
                    finished_at=now(),
                    error_code=failure["code"] if failure and not cancelled else None,
                )
            )
            doc_status = (
                ("needs_ocr" if result and result.get("needs_ocr") else "parsed")
                if status == "succeeded"
                else "parse_failed"
                if status == "failed"
                else "cancelled"
            )
            if extracting:
                doc_status = (
                    "pending_review"
                    if status == "succeeded"
                    else "extraction_failed"
                    if status == "failed"
                    else "cancelled"
                )
            conn.execute(
                documents.update()
                .where(documents.c.id == version["document_id"])
                .values(processing_status=doc_status)
            )
        committed = status == "succeeded"
        if measured:
            written = [key for key in written if not key.endswith("/raw.json")]
    finally:
        if not committed:
            # A lost commit acknowledgement is not proof that publication failed.
            try:
                with transaction(cfg, **tx) as conn:  # type: ignore[arg-type]
                    if (
                        extracting
                        and run
                        and conn.execute(
                            select(measurements.c.id).where(
                                measurements.c.run_id == run["id"],
                                measurements.c.generation == generation,
                            )
                        ).first()
                    ):
                        written = [key for key in written if not key.endswith("/raw.json")]
                    if conn.execute(
                        select(results.c.id).where(results.c.run_id == run["id"])
                        if extracting and run
                        else select(artifacts.c.id).where(artifacts.c.id == artifact_id)
                    ).first():
                        written = []
            except Exception:
                written = []  # sweeper will reconcile after the database recovers
            for key in written:
                try:
                    storage.delete(key)
                except Exception:
                    pass


@celery.task(name="documents.parse")  # type: ignore[untyped-decorator]
def parse_task(tenant: str, project: str, job_id: str) -> None:
    execute(tenant, project, job_id)


def dispatch_once(cfg: Settings | None = None) -> int:
    cfg = cfg or settings
    count = 0
    with transaction(cfg, worker=True) as conn:
        events = (
            conn.execute(
                select(outbox)
                .where(outbox.c.delivered_at.is_(None), outbox.c.available_at <= now())
                .order_by(outbox.c.available_at)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .all()
        )
        for event in events:
            celery.send_task(
                "documents.parse",
                args=[str(event["tenant_id"]), str(event["project_id"]), str(event["job_id"])],
                queue="parsing",
            )
            conn.execute(
                outbox.update()
                .where(outbox.c.id == event["id"])
                .values(delivered_at=now(), deliveries=event["deliveries"] + 1)
            )
            count += 1
    return count


def recover_once(cfg: Settings | None = None) -> int:
    cfg = cfg or settings
    count = 0
    # The routing table is retained for recovery; it contains only IDs and timestamps.
    with transaction(cfg, worker=True) as conn:
        routes = conn.execute(
            select(outbox.c.tenant_id, outbox.c.project_id, outbox.c.job_id).distinct()
        ).all()
    for tenant, project, jid in routes:
        with transaction(cfg, tenant=tenant, project=project, worker=True) as conn:
            job = (
                conn.execute(select(jobs).where(jobs.c.id == jid).with_for_update(skip_locked=True))
                .mappings()
                .first()
            )
            if not job:
                continue
            expired = (
                job["status"] == "running" and job["lease_until"] and job["lease_until"] < now()
            )
            # Requeue lost broker deliveries, not just expired workers.
            last = conn.execute(
                select(outbox.c.delivered_at)
                .where(outbox.c.job_id == jid)
                .order_by(outbox.c.available_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            lost = job["status"] == "queued" and last and last < now() - timedelta(seconds=60)
            if expired:
                failure = error("WORKER_LEASE_EXPIRED", job["request_id"], True)
                failure["details"]["stage"] = job["kind"]
                status = (
                    "cancelled"
                    if job["cancel_requested"]
                    else "failed"
                    if job["attempt"] >= cfg.max_attempts
                    else "queued"
                )
                conn.execute(
                    jobs.update()
                    .where(jobs.c.id == jid)
                    .values(
                        status=status,
                        generation=job["generation"] + 1,
                        lease_until=None,
                        error=failure if status == "failed" else None,
                        finished_at=now() if status != "queued" else None,
                    )
                )
                conn.execute(
                    attempts.update()
                    .where(attempts.c.job_id == jid, attempts.c.generation == job["generation"])
                    .values(
                        status="cancelled" if status == "cancelled" else "failed",
                        finished_at=now(),
                        error_code=failure["code"],
                    )
                )
                doc_id = conn.execute(
                    select(versions.c.document_id).where(
                        versions.c.id == job["document_version_id"]
                    )
                ).scalar_one()
                conn.execute(
                    documents.update()
                    .where(documents.c.id == doc_id)
                    .values(
                        processing_status=(
                            "extraction_failed" if job["kind"] == "extracting" else "parse_failed"
                        )
                        if status == "failed"
                        else status
                    )
                )
                lost = status == "queued"
                count += 1
            if lost:
                # Reset the latest event, avoiding an unbounded chain on every watchdog tick.
                event = conn.execute(
                    select(outbox.c.id)
                    .where(outbox.c.job_id == jid)
                    .order_by(outbox.c.available_at.desc())
                    .limit(1)
                ).scalar_one()
                conn.execute(
                    outbox.update()
                    .where(outbox.c.id == event)
                    .values(delivered_at=None, available_at=now())
                )
    return count


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    while True:
        try:
            recovered = recover_once()
            delivered = dispatch_once()
            if recovered or delivered:
                logging.info(
                    json.dumps(
                        {"event": "dispatcher.tick", "delivered": delivered, "recovered": recovered}
                    )
                )
        except Exception:
            logging.warning('{"event":"dispatcher.unavailable"}')
        time.sleep(3)


if __name__ == "__main__":
    main()
