"""Identity, immutable documents, durable jobs, outbox and audit."""

from alembic import op

from migrations.s1_schema import s1_tables

revision = "0002_documents"
down_revision = "0001_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in s1_tables:
        table.create(op.get_bind())
        if table.name == "outbox_events":
            continue
        if table.name == "users":
            predicate = (
                "issuer = current_setting('app.issuer', true) "
                "AND subject = current_setting('app.subject', true)"
            )
        elif table.name == "memberships":
            predicate = "user_id = nullif(current_setting('app.user_id', true), '')::uuid"
        else:
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
        "CREATE INDEX documents_listing ON documents (tenant_id, project_id, created_at, id)"
    )
    op.execute(
        "CREATE INDEX outbox_pending ON outbox_events (available_at) WHERE delivered_at IS NULL"
    )
    # Protect immutable rows even if an application accidentally issues UPDATE/DELETE.
    op.execute("""CREATE FUNCTION reject_immutable_write() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'immutable row'; END; $$""")
    for name in ("document_versions", "parse_artifacts", "audit_events"):
        op.execute(
            f"CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON {name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_write()"
        )


def downgrade() -> None:
    for table in reversed(s1_tables):
        op.drop_table(table.name)
    op.execute("DROP FUNCTION reject_immutable_write()")
