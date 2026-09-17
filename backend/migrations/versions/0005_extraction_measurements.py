"""Retain real attempt durations and responses even when validation rejects facts."""

import sqlalchemy as sa
from alembic import op

revision = "0005_measurements"
down_revision = "0004_extractions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_measurements",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("tenant_id", sa.Uuid, nullable=False),
        sa.Column("project_id", sa.Uuid, nullable=False),
        sa.Column("run_id", sa.Uuid, nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("raw_object_key", sa.Text),
        sa.Column("usage", sa.JSON),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.UniqueConstraint("tenant_id", "project_id", "id"),
        sa.UniqueConstraint("tenant_id", "project_id", "run_id", "generation"),
        sa.ForeignKeyConstraint(["tenant_id", "project_id"], ["projects.tenant_id", "projects.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "run_id"],
            ["extraction_runs.tenant_id", "extraction_runs.project_id", "extraction_runs.id"],
        ),
    )
    op.execute("ALTER TABLE extraction_measurements ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE extraction_measurements FORCE ROW LEVEL SECURITY")
    predicate = (
        "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid "
        "AND project_id = nullif(current_setting('app.project_id', true), '')::uuid"
    )
    op.execute(
        "CREATE POLICY scope_isolation ON extraction_measurements "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
    op.execute(
        "CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON extraction_measurements "
        "FOR EACH ROW EXECUTE FUNCTION reject_immutable_write()"
    )


def downgrade() -> None:
    op.drop_table("extraction_measurements")
