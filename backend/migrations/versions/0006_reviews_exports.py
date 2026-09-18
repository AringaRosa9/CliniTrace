"""S3 human review and immutable delivery ledgers."""

from alembic import op

from migrations.s3_schema import s3_tables

revision = "0006_reviews_exports"
down_revision = "0005_measurements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in s3_tables:
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
        op.execute(
            f"CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON {table.name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_write()"
        )


def downgrade() -> None:
    for table in reversed(s3_tables):
        op.drop_table(table.name)
