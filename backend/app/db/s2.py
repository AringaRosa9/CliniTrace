"""Scoped extraction ledger; published results are append-only."""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)

from app.db.s1 import scoped


def reference(table: str, column: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "project_id", column],
        [f"{table}.tenant_id", f"{table}.project_id", f"{table}.id"],
    )


runs = scoped(
    "extraction_runs",
    Column("document_version_id", Uuid, nullable=False),
    Column("parse_artifact_id", Uuid, nullable=False),
    Column("job_id", Uuid, nullable=False),
    Column("document_revision", Integer, nullable=False),
    Column("configuration", JSON, nullable=False),
    Column("input_digest", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "job_id"),
    reference("jobs", "job_id"),
    reference("parse_artifacts", "parse_artifact_id"),
    parent="document_versions:document_version_id",
)
results = scoped(
    "extraction_results",
    Column("run_id", Uuid, nullable=False),
    Column("raw_object_key", Text, nullable=False),
    Column("usage", JSON, nullable=False),
    Column("issues", JSON, nullable=False),
    Column("codings", JSON, nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "run_id"),
    parent="extraction_runs:run_id",
)
facts = scoped(
    "clinical_facts",
    Column("run_id", Uuid, nullable=False),
    Column("revision_id", Uuid, nullable=False),
    Column("entity_group_id", Uuid, nullable=False),
    Column("field_path", String(200), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("tenant_id", "project_id", "revision_id"),
    parent="extraction_runs:run_id",
)
evidence = scoped(
    "evidence",
    Column("run_id", Uuid, nullable=False),
    Column("document_version_id", Uuid, nullable=False),
    Column("parse_artifact_id", Uuid, nullable=False),
    Column("payload", JSON, nullable=False),
    reference("document_versions", "document_version_id"),
    reference("parse_artifacts", "parse_artifact_id"),
    parent="extraction_runs:run_id",
)
fact_evidence = scoped(
    "fact_evidence",
    Column("fact_id", Uuid, nullable=False),
    Column("evidence_id", Uuid, nullable=False),
    reference("clinical_facts", "fact_id"),
    parent="evidence:evidence_id",
)
relations = scoped(
    "fact_relations",
    Column("run_id", Uuid, nullable=False),
    Column("source_id", Uuid, nullable=False),
    Column("target_id", Uuid, nullable=False),
    Column("kind", String(80), nullable=False),
    reference("clinical_facts", "source_id"),
    reference("clinical_facts", "target_id"),
    parent="extraction_runs:run_id",
)
review_sets = scoped(
    "review_sets",
    Column("encounter_id", Uuid, nullable=False),
    Column("scope_revision", Integer, nullable=False),
    Column("status", String(30), nullable=False),
    Column("members", JSON, nullable=False),
    Column("issues", JSON, nullable=False),
    UniqueConstraint("tenant_id", "project_id", "encounter_id"),
    parent="encounters:encounter_id",
)
active_runs = scoped(
    "active_runs",
    Column("document_id", Uuid, nullable=False),
    Column("run_id", Uuid, nullable=False),
    UniqueConstraint("tenant_id", "project_id", "document_id"),
    reference("extraction_runs", "run_id"),
    parent="documents:document_id",
)
s2_tables = [runs, results, facts, evidence, fact_evidence, relations, review_sets, active_runs]

# Every attempt is measured, including rejected responses and cancelled jobs.
measurements = scoped(
    "extraction_measurements",
    Column("run_id", Uuid, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("raw_object_key", Text),
    Column("usage", JSON),
    Column("duration_ms", Integer, nullable=False),
    Column("outcome", String(30), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "run_id", "generation"),
    parent="extraction_runs:run_id",
)


Index(
    "extraction_document",
    runs.c.tenant_id,
    runs.c.project_id,
    runs.c.document_version_id,
    runs.c.created_at,
)
Index("facts_run", facts.c.tenant_id, facts.c.project_id, facts.c.run_id)
