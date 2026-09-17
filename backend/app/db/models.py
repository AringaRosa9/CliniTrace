"""S0 migration metadata: foundational scope only; clinical entities follow in S1-S3."""

from sqlalchemy import Column, ForeignKeyConstraint, MetaData, String, Table, UniqueConstraint, Uuid

metadata = MetaData(
    naming_convention={
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ix": "ix_%(table_name)s_%(column_0_name)s",
    }
)
tenants = Table(
    "tenants",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(200), nullable=False),
)
projects = Table(
    "projects",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("name", String(200), nullable=False),
    ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    UniqueConstraint("tenant_id", "id"),
)
patients = Table(
    "patients",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("project_id", Uuid, nullable=False),
    Column("patient_key", String(100), nullable=False),
    ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]),
    UniqueConstraint("tenant_id", "project_id", "id"),
    UniqueConstraint("tenant_id", "project_id", "patient_key"),
)
encounters = Table(
    "encounters",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("project_id", Uuid, nullable=False),
    Column("patient_id", Uuid, nullable=False),
    Column("kind", String(30), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "project_id", "patient_id"],
        ["patients.tenant_id", "patients.project_id", "patients.id"],
    ),
    UniqueConstraint("tenant_id", "project_id", "id"),
)
