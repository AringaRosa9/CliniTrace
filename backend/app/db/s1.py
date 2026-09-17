"""S1 relational metadata. Clinical rows always carry a composite project scope."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text

from app.db.models import encounters, metadata


def scoped(name: str, *columns: object, parent: str | None = None) -> Table:
    # SQLAlchemy's heterogeneous SchemaItem constructor is intentionally dynamic here.
    items = [
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid, nullable=False),
        Column("project_id", Uuid, nullable=False),
        *columns,
        UniqueConstraint("tenant_id", "project_id", "id"),
        ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]),
    ]
    if parent:
        table, field = parent.split(":")
        items.append(
            ForeignKeyConstraint(
                ["tenant_id", "project_id", field],
                [f"{table}.tenant_id", f"{table}.project_id", f"{table}.id"],
            )
        )
    return Table(name, metadata, *items)  # type: ignore[arg-type]


users = Table(
    "users",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("issuer", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("display_name", String(200), nullable=False),
    UniqueConstraint("issuer", "subject"),
)
memberships = scoped(
    "memberships",
    Column("user_id", Uuid, nullable=False),
    Column("roles", JSON, nullable=False),
    Column("capabilities", JSON, nullable=False),
    Column("project_name", String(200), nullable=False),
    ForeignKeyConstraint(["user_id"], ["users.id"]),
    UniqueConstraint("project_id", "user_id"),
)
identities = scoped(
    "patient_identities",
    Column("patient_id", Uuid, nullable=False),
    Column("source", String(100), nullable=False),
    Column("source_key", String(200), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "source", "source_key"),
    parent="patients:patient_id",
)
encounter_details = scoped(
    "encounter_details",
    Column("encounter_id", Uuid, nullable=False),
    Column("source", String(100), nullable=False),
    Column("source_key", String(200), nullable=False),
    Column("occurred_on", Date),
    Column("department", String(100), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "source", "source_key"),
    parent="encounters:encounter_id",
)
documents = scoped(
    "documents",
    Column("patient_id", Uuid, nullable=False),
    Column("encounter_id", Uuid, nullable=False),
    Column("document_type", String(30), nullable=False),
    Column("filename", String(255), nullable=False),
    Column("processing_status", String(30), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "project_id", "patient_id"],
        ["patients.tenant_id", "patients.project_id", "patients.id"],
    ),
    parent="encounters:encounter_id",
)
versions = scoped(
    "document_versions",
    Column("document_id", Uuid, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("mime", String(100), nullable=False),
    Column("size", Integer, nullable=False),
    Column("object_key", Text, nullable=False),
    Column("source", String(200), nullable=False),
    Column("authorization_reference", String(200), nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "sha256"),
    parent="documents:document_id",
)
jobs = scoped(
    "jobs",
    Column("kind", String(30), nullable=False, server_default="parsing"),
    Column("document_version_id", Uuid, nullable=False),
    Column("status", String(30), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("progress", Integer, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("cancel_requested", Boolean, nullable=False),
    Column("lease_until", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("error", JSON),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("request_id", Uuid, nullable=False),
    parent="document_versions:document_version_id",
)
attempts = scoped(
    "job_attempts",
    Column("job_id", Uuid, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String(30), nullable=False),
    Column("error_code", String(80)),
    UniqueConstraint("tenant_id", "project_id", "job_id", "generation"),
    parent="jobs:job_id",
)
artifacts = scoped(
    "parse_artifacts",
    Column("document_version_id", Uuid, nullable=False),
    Column("job_id", Uuid, nullable=False),
    Column("parser_version", String(100), nullable=False),
    Column("object_key", Text, nullable=False),
    Column("page_count", Integer, nullable=False),
    Column("needs_ocr", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "project_id", "job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.id"]
    ),
    parent="document_versions:document_version_id",
)
audit = scoped(
    "audit_events",
    Column("actor_id", Uuid, nullable=False),
    Column("action", String(80), nullable=False),
    Column("target_id", Uuid, nullable=False),
    Column("request_id", Uuid, nullable=False),
    Column("details", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
idempotency = scoped(
    "idempotency_keys",
    Column("actor_id", Uuid, nullable=False),
    Column("operation", String(100), nullable=False),
    Column("key", String(128), nullable=False),
    Column("digest", String(64), nullable=False),
    Column("response", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "project_id", "actor_id", "operation", "key"),
)
# Routing-only table: no filenames, contents or patient identifiers. Dispatcher needs all scopes.
outbox = Table(
    "outbox_events",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("project_id", Uuid, nullable=False),
    Column("job_id", Uuid, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("delivered_at", DateTime(timezone=True)),
    Column("deliveries", Integer, nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "project_id", "job_id"], ["jobs.tenant_id", "jobs.project_id", "jobs.id"]
    ),
)
s1_tables = [
    users,
    memberships,
    identities,
    encounter_details,
    documents,
    versions,
    jobs,
    attempts,
    artifacts,
    audit,
    idempotency,
    outbox,
]

# Match the strengthened encounter relationship and query indexes from migration 0003.


encounters.append_constraint(
    UniqueConstraint("tenant_id", "project_id", "id", "patient_id", name="uq_encounter_patient")
)
documents.append_constraint(
    ForeignKeyConstraint(
        ["tenant_id", "project_id", "encounter_id", "patient_id"],
        ["encounters.tenant_id", "encounters.project_id", "encounters.id", "encounters.patient_id"],
        name="fk_document_encounter_patient",
    )
)
Index(
    "documents_listing",
    documents.c.tenant_id,
    documents.c.project_id,
    documents.c.created_at,
    documents.c.id,
)
Index("outbox_pending", outbox.c.available_at, postgresql_where=sql_text("delivered_at IS NULL"))
