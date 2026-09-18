"""Append-only human revisions, review decisions and frozen export manifests."""

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, UniqueConstraint, Uuid

from app.db.s1 import scoped
from app.db.s2 import reference

revisions = scoped(
    "fact_revisions",
    Column("fact_id", Uuid, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "fact_id", "revision"),
    parent="clinical_facts:fact_id",
)
checks = scoped(
    "fact_checks",
    Column("review_set_id", Uuid, nullable=False),
    Column("scope_revision", Integer, nullable=False),
    Column("fact_revision_id", Uuid, nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "tenant_id", "project_id", "review_set_id", "scope_revision", "fact_revision_id"
    ),
    parent="review_sets:review_set_id",
)
dispositions = scoped(
    "issue_dispositions",
    Column("review_set_id", Uuid, nullable=False),
    Column("scope_revision", Integer, nullable=False),
    Column("issue_id", String(100), nullable=False),
    Column("status", String(30), nullable=False),
    Column("reason", Text, nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "review_set_id", "scope_revision", "issue_id"),
    parent="review_sets:review_set_id",
)
snapshots = scoped(
    "review_snapshots",
    Column("review_set_id", Uuid, nullable=False),
    Column("scope_revision", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "review_set_id", "scope_revision"),
    parent="review_sets:review_set_id",
)
datasets = scoped(
    "dataset_definitions",
    Column("name", String(150), nullable=False),
    Column("filters", JSON, nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
exports = scoped(
    "export_jobs",
    Column("job_id", Uuid, nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("format", String(10), nullable=False),
    Column("purpose", Text, nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "job_id"),
    parent="jobs:job_id",
)
export_files = scoped(
    "export_files",
    Column("export_id", Uuid, nullable=False),
    Column("object_key", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size", Integer, nullable=False),
    UniqueConstraint("tenant_id", "project_id", "export_id"),
    reference("export_jobs", "export_id"),
)
s3_tables = [revisions, checks, dispositions, snapshots, datasets, exports, export_files]
