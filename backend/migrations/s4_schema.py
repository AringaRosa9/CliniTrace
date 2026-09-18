"""S4 immutable releases, independent annotations and patient-isolated evaluation."""

from sqlalchemy import JSON, Column, DateTime, Integer, String, UniqueConstraint, Uuid

from migrations.s1_schema import scoped
from migrations.s2_schema import reference


def ledger(name: str, *columns: object, parent: str | None = None):  # type: ignore[no-untyped-def]
    return scoped(
        name,
        *columns,
        Column("actor_id", Uuid, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        parent=parent,
    )


templates = ledger(
    "template_drafts",
    Column("name", String(150), nullable=False),
    Column("document_type", String(30), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
)
template_versions = ledger(
    "template_versions",
    Column("template_id", Uuid, nullable=False),
    Column("version", String(100), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("digest", String(64), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "version"),
    parent="template_drafts:template_id",
)
terminologies = ledger(
    "terminology_versions",
    Column("version", String(100), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("digest", String(64), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "version"),
)
activations = ledger(
    "terminology_activations",
    Column("terminology_id", Uuid, nullable=False),
    parent="terminology_versions:terminology_id",
)
mappings = ledger(
    "mapping_decisions",
    Column("fact_id", Uuid, nullable=False),
    Column("revision_id", Uuid, nullable=False),
    Column("terminology_id", Uuid, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("tenant_id", "project_id", "fact_id", "sequence"),
    reference("terminology_versions", "terminology_id"),
    parent="clinical_facts:fact_id",
)
samples = ledger(
    "quality_samples",
    Column("run_id", Uuid, nullable=False),
    Column("patient_id", Uuid, nullable=False),
    Column("patient_group", String(64), nullable=False),
    Column("split", String(10), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("tenant_id", "project_id", "run_id"),
    reference("patients", "patient_id"),
    parent="extraction_runs:run_id",
)
annotations = ledger(
    "quality_annotations",
    Column("sample_id", Uuid, nullable=False),
    Column("stage", String(20), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("tenant_id", "project_id", "sample_id", "stage"),
    parent="quality_samples:sample_id",
)
gold_versions = ledger(
    "gold_dataset_versions",
    Column("name", String(150), nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("digest", String(64), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "digest"),
)
evaluations = ledger(
    "evaluation_runs",
    Column("dataset_id", Uuid, nullable=False),
    Column("name", String(150), nullable=False),
    Column("payload", JSON, nullable=False),
    parent="gold_dataset_versions:dataset_id",
)
corrections = ledger(
    "correction_candidates",
    Column("revision_id", Uuid, nullable=False),
    Column("patient_id", Uuid, nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("tenant_id", "project_id", "revision_id"),
    reference("patients", "patient_id"),
    parent="fact_revisions:revision_id",
)
correction_reviews = ledger(
    "correction_reviews",
    Column("candidate_id", Uuid, nullable=False),
    Column("decision", String(20), nullable=False),
    Column("reason", String(1000), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "candidate_id"),
    parent="correction_candidates:candidate_id",
)
correction_releases = ledger(
    "correction_releases",
    Column("name", String(150), nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("digest", String(64), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "digest"),
)
s4_tables = [
    templates,
    template_versions,
    terminologies,
    activations,
    mappings,
    samples,
    annotations,
    gold_versions,
    evaluations,
    corrections,
    correction_reviews,
    correction_releases,
]
