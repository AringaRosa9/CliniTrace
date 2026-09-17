"""S2 extraction ledger, scope and job routing."""

import sqlalchemy as sa
from alembic import op

from migrations.s2_schema import s2_tables

revision = "0004_extractions"
down_revision = "0003_association"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("kind", sa.String(30), nullable=False, server_default="parsing")
    )
    for table in s2_tables:
        table.create(op.get_bind())
        predicate = (
            "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid "
            "AND project_id = nullif(current_setting('app.project_id', true), '')::uuid"
        )
        op.execute(f"ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table.name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY scope_isolation ON {table.name} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
        if table.name not in ("active_runs", "review_sets"):
            op.execute(
                f"CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON {table.name} "
                "FOR EACH ROW EXECUTE FUNCTION reject_immutable_write()"
            )
    op.execute(
        "CREATE INDEX extraction_document ON extraction_runs "
        "(tenant_id, project_id, document_version_id, created_at)"
    )
    op.execute("CREATE INDEX facts_run ON clinical_facts (tenant_id, project_id, run_id)")


def downgrade() -> None:
    for table in reversed(s2_tables):
        op.drop_table(table.name)
    op.drop_column("jobs", "kind")
