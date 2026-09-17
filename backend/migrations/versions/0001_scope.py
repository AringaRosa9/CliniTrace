"""Initial scoped identity containers; no clinical data or demo accounts seeded."""

import sqlalchemy as sa
from alembic import op

revision = "0001_scope"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", "id"),
    )
    op.create_table(
        "patients",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("patient_key", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]),
        sa.UniqueConstraint("tenant_id", "project_id", "id"),
        sa.UniqueConstraint("tenant_id", "project_id", "patient_key"),
    )
    op.create_table(
        "encounters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "patient_id"],
            ["patients.tenant_id", "patients.project_id", "patients.id"],
        ),
        sa.UniqueConstraint("tenant_id", "project_id", "id"),
    )
    for table in ("tenants", "projects", "patients", "encounters"):
        tenant_column = "id" if table == "tenants" else "tenant_id"
        predicate = f"{tenant_column} = nullif(current_setting('app.tenant_id', true), '')::uuid"
        if table in ("patients", "encounters"):
            predicate += (
                " AND project_id = nullif(current_setting('app.project_id', true), '')::uuid"
            )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY scope_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    for table in ("encounters", "patients", "projects", "tenants"):
        op.drop_table(table)
