"""Provision an approved tenant/project and OIDC memberships with the DBA account."""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from sqlalchemy import create_engine, select

from app.core.config import Settings
from app.db.models import projects, tenants
from app.db.s1 import audit, memberships, users
from app.modules.documents.service import now

CAPABILITIES = {
    "import",
    "documents.read",
    "original.read",
    "audit.read",
    "review",
    "export.reviewed",
    "export.draft",
    "templates.manage",
    "terminology.manage",
    "terminology.map",
    "quality.read",
    "quality.errors.read",
    "quality.annotate",
    "quality.review",
    "quality.adjudicate",
    "quality.manage",
}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("manifest", type=Path)
args = parser.parse_args()
value = json.loads(args.manifest.read_text())
cfg = Settings()
if value.get("approved") is not True or not value.get("authorization", "").strip():
    parser.error("Institution provisioning approval required")
if cfg.identity_provider != "oidc" or value["issuer"] != cfg.oidc_issuer:
    parser.error("Issuer must match configured institution OIDC")
tenant, project, operator = (UUID(value[k]) for k in ("tenant_id", "project_id", "operator_id"))
if not value["members"] or operator not in [UUID(m["id"]) for m in value["members"]]:
    parser.error("Approved operator must be included in the initial mappings")
for member in value["members"]:
    if not member["subject"] or not set(member["capabilities"]) <= CAPABILITIES:
        parser.error("Invalid subject or capabilities")
with create_engine(cfg.database_url).begin() as conn:
    # New projects fail on conflicts; existing changes use provision_member.
    if not conn.execute(select(tenants.c.id).where(tenants.c.id == tenant)).first():
        conn.execute(tenants.insert().values(id=tenant, name=value["tenant_name"]))
    conn.execute(projects.insert().values(id=project, tenant_id=tenant, name=value["project_name"]))
    for member in value["members"]:
        uid = UUID(member["id"])
        existing = (
            conn.execute(
                select(users).where(
                    users.c.issuer == cfg.oidc_issuer,
                    users.c.subject == member["subject"],
                )
            )
            .mappings()
            .first()
        )
        if existing and existing["id"] != uid:
            raise ValueError("Existing identity differs from approved mapping")
        if not existing:
            conn.execute(
                users.insert().values(
                    id=uid,
                    issuer=cfg.oidc_issuer,
                    subject=member["subject"],
                    display_name=member["display_name"],
                )
            )
        conn.execute(
            memberships.insert().values(
                id=uuid4(),
                tenant_id=tenant,
                project_id=project,
                user_id=uid,
                project_name=value["project_name"],
                roles=member["roles"],
                capabilities=member["capabilities"],
            )
        )
        conn.execute(
            audit.insert().values(
                id=uuid4(),
                tenant_id=tenant,
                project_id=project,
                actor_id=operator,
                action="membership.provision",
                target_id=uid,
                request_id=uuid4(),
                created_at=now(),
                details={
                    "reason": value["authorization"],
                    "before": [],
                    "after": member["capabilities"],
                },
            )
        )
print("Approved project and OIDC memberships created; audit appended.")
