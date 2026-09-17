"""Enforce that a document's encounter belongs to its associated patient."""

from alembic import op

revision = "0003_association"
down_revision = "0002_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_encounter_patient", "encounters", ["tenant_id", "project_id", "id", "patient_id"]
    )
    op.create_foreign_key(
        "fk_document_encounter_patient",
        "documents",
        "encounters",
        ["tenant_id", "project_id", "encounter_id", "patient_id"],
        ["tenant_id", "project_id", "id", "patient_id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_document_encounter_patient", "documents", type_="foreignkey")
    op.drop_constraint("uq_encounter_patient", "encounters", type_="unique")
