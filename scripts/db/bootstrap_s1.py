"""Explicit local setup. No clinical records. Run with the migration account."""

import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.config import Settings
from app.db.models import projects, tenants
from app.db.s1 import memberships, users
from app.integrations.storage.s3 import Storage
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert

cfg = Settings(_env_file=Path(__file__).resolve().parents[2] / "backend" / ".env")
if cfg.app_env == "production":
    raise SystemExit("Use institution provisioning in production")
engine = create_engine(cfg.database_url)
with engine.begin() as conn:
    for role in ("bljgh_api", "bljgh_worker"):
        if not conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": role}
        ).first():
            conn.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text(f"GRANT {role} TO CURRENT_USER"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    conn.execute(
        text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO bljgh_api, bljgh_worker")
    )
    conn.execute(text("REVOKE SELECT ON outbox_events FROM bljgh_api"))
    conn.execute(
        text(
            "GRANT INSERT ON patients, patient_identities, encounters, encounter_details, documents, document_versions, jobs, idempotency_keys, audit_events, outbox_events TO bljgh_api"
        )
    )
    conn.execute(text("GRANT UPDATE ON documents, jobs TO bljgh_api"))
    conn.execute(
        text(
            "GRANT INSERT ON job_attempts, parse_artifacts, outbox_events TO bljgh_worker"
        )
    )
    conn.execute(
        text(
            "GRANT UPDATE ON documents, jobs, job_attempts, outbox_events TO bljgh_worker"
        )
    )
    conn.execute(text("GRANT INSERT ON extraction_runs TO bljgh_api"))
    conn.execute(text("GRANT SELECT, INSERT, UPDATE ON active_runs, review_sets TO bljgh_api, bljgh_worker"))
    conn.execute(text("GRANT INSERT ON extraction_measurements, extraction_results, clinical_facts, evidence, fact_evidence, fact_relations TO bljgh_worker"))
    tenant, project, user = [
        UUID(f"10000000-0000-4000-8000-{n:012}") for n in (1, 2, 3)
    ]
    for table, values in [
        (tenants, {"id": tenant, "name": "本地合成开发"}),
        (projects, {"id": project, "tenant_id": tenant, "name": "合成文书验证项目"}),
        (
            users,
            {
                "id": user,
                "issuer": "synthetic",
                "subject": "operator",
                "display_name": "合成数据操作员",
            },
        ),
        (
            memberships,
            {
                "id": UUID("10000000-0000-4000-8000-000000000004"),
                "tenant_id": tenant,
                "project_id": project,
                "user_id": user,
                "project_name": "合成文书验证项目",
                "roles": ["operator"],
                "capabilities": [
                    "import",
                    "documents.read",
                    "original.read",
                    "audit.read",
                ],
            },
        ),
    ]:
        conn.execute(insert(table).values(**values).on_conflict_do_nothing())
storage = Storage(cfg)
if not any(
    b["Name"] == cfg.s3_bucket for b in storage.client.list_buckets()["Buckets"]
):
    storage.client.create_bucket(Bucket=cfg.s3_bucket)
# No bucket policy is installed: all access goes through the authenticated API.
print("S1 local roles, synthetic membership and private bucket ready.")
