"""Offline project erasure with a durable intent receipt and retry-safe object removal.

Dedicated DBA only. Runtime roles cannot disable immutable triggers. An institution
must approve the retention decision, exceptions, and backup expiry before applying.
"""

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, func, select, text

from app.core.config import Settings
from app.db import s1, s2, s3, s4  # noqa: F401
from app.db.models import metadata, projects
from app.integrations.storage.s3 import Storage


def save_receipt(path: Path, value: dict[str, Any]) -> None:
    # Persist intent before irreversible object deletion, and replace atomically on retry.
    descriptor, temporary = tempfile.mkstemp(prefix=".deletion-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def erase_project(
    cfg: Settings,
    tenant: UUID,
    project: UUID,
    receipt: Path,
    authorization: str,
    *,
    apply: bool = False,
    backup_expiry: str = "",
    legal_hold: bool = True,
) -> dict[str, Any]:
    if not authorization.strip():
        raise ValueError("Institution deletion authorization required")
    if apply:
        expiry = datetime.fromisoformat(backup_expiry)
        if legal_hold or expiry.tzinfo is None or expiry <= datetime.now(UTC):
            raise ValueError("Resolve holds and declare the latest backup expiry first")
    engine, storage = create_engine(cfg.database_url), Storage(cfg)
    scope_hash = hashlib.sha256(f"{tenant}/{project}".encode()).hexdigest()
    tables = [
        table
        for table in reversed(metadata.sorted_tables)
        if "project_id" in table.c and "tenant_id" in table.c
    ]
    prefixes = [
        f"{category}/{tenant}/{project}/"
        for category in ("quarantine", "parsed", "extracted", "exports")
    ]
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            if apply:
                names = [conn.dialect.identifier_preparer.quote(t.name) for t in tables]
                conn.execute(text("LOCK TABLE " + ",".join(names) + " IN EXCLUSIVE MODE"))
            if not conn.execute(
                select(projects.c.id).where(
                    projects.c.id == project, projects.c.tenant_id == tenant
                )
            ).first():
                raise ValueError("Project not found in approved scope")
            if conn.execute(
                select(s1.jobs.c.id).where(
                    s1.jobs.c.project_id == project,
                    s1.jobs.c.tenant_id == tenant,
                    s1.jobs.c.status == "running",
                )
            ).first():
                raise ValueError("Drain running jobs first")
            counts = {
                t.name: conn.execute(
                    select(func.count())
                    .select_from(t)
                    .where(t.c.tenant_id == tenant, t.c.project_id == project)
                ).scalar_one()
                for t in tables
            }
            report: dict[str, Any] = {
                "scope_sha256": scope_hash,
                "authorization_sha256": hashlib.sha256(authorization.encode()).hexdigest(),
                "counts": counts,
                "objects_deleted": 0,
                "backup_expiry": backup_expiry,
                "status": "planned",
            }
            if not apply:
                return report
            if receipt.exists():
                old = json.loads(receipt.read_text())
                if old.get("scope_sha256") != scope_hash or old.get("status") == "complete":
                    raise ValueError("Receipt belongs to another scope or completed erasure")
            receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            report["status"] = "in_progress"
            save_receipt(receipt, report)
            # Remove all versions and delete markers, not merely the current object version.
            for prefix in prefixes:
                for page in storage.client.get_paginator("list_object_versions").paginate(
                    Bucket=storage.bucket, Prefix=prefix
                ):
                    for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
                        storage.client.delete_object(
                            Bucket=storage.bucket, Key=item["Key"], VersionId=item["VersionId"]
                        )
                        report["objects_deleted"] += 1
            # Only the immutable guard is disabled; foreign keys and RLS remain in effect.
            for table in tables:
                quoted = conn.dialect.identifier_preparer.quote(table.name)
                guard = conn.execute(
                    text(
                        "SELECT 1 FROM pg_trigger WHERE tgrelid = "
                        "to_regclass(:name) AND tgname = 'immutable_rows'"
                    ),
                    {"name": table.name},
                ).first()
                if guard:
                    conn.execute(text(f"ALTER TABLE {quoted} DISABLE TRIGGER immutable_rows"))
                conn.execute(
                    table.delete().where(table.c.tenant_id == tenant, table.c.project_id == project)
                )
                if guard:
                    conn.execute(text(f"ALTER TABLE {quoted} ENABLE TRIGGER immutable_rows"))
        # The project shell remains to support safe retries, without clinical data or members.
        report["status"] = "complete"
        report["completed_at"] = datetime.now(UTC).isoformat()
        save_receipt(receipt, report)
        return report
    finally:
        engine.dispose()
