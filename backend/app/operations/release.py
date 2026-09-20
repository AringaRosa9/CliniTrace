"""Fail-closed pilot readiness validation; signatures are institution evidence, not fabricated."""

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

# name: (direction, unit, require denominator)
METRICS = {
    "critical_field_f1": ("min", "ratio", True),
    "mapping_accuracy": ("min", "ratio", True),
    "negation_to_positive": ("max", "count", True),
    "dose_errors": ("max", "count", True),
    "patient_link_errors": ("max", "count", True),
    "document_p95_seconds": ("max", "seconds", True),
    "queue_p95_seconds": ("max", "seconds", True),
    "failure_rate": ("max", "ratio", True),
    "api_p95_ms": ("max", "ms", True),
    "review_concurrency": ("min", "users", False),
    "export_max_facts": ("min", "facts", False),
    "review_median_seconds": ("max", "seconds", True),
    "review_p95_seconds": ("max", "seconds", True),
    "cost_per_document_cny": ("max", "CNY", True),
    "rpo_seconds": ("max", "seconds", False),
    "rto_seconds": ("max", "seconds", False),
}
EVIDENCE = (
    "environment_approval",
    "data_authorization",
    "terminology_license",
    "oidc",
    "security",
    "accessibility",
    "load",
    "quality",
    "restore",
    "deletion",
    "rollback",
    "uat",
    "training",
    "oncall",
    "retention",
)
ROLES = ("institution", "medical", "product", "operations")


def blank_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "environment": "institution",
        "release_commit": "",
        "images": {"backend": "", "frontend": "", "gateway": ""},
        "migration": "0007_quality",
        "configuration_sha256": "",
        "dataset_sha256": "",
        "versions": {
            key: ""
            for key in ("template", "guide", "prompt", "rules", "model", "terminology", "parser")
        },
        "approvals": {role: {"reviewer": "", "signed_at": "", "evidence": ""} for role in ROLES},
        "evidence": {key: {"path": "", "sha256": ""} for key in EVIDENCE},
        "metrics": {
            name: {
                "threshold": None,
                "measured": None,
                "unit": unit,
                "denominator": None,
                "evidence": "",
                "conditions": "",
            }
            for name, (_, unit, _) in METRICS.items()
        },
        "blocker_defects": None,
        "uat_passed": False,
        "rollback_rehearsed": False,
        "retention_days": {
            key: None for key in ("originals", "derived", "exports", "logs", "backups")
        },
        "pilot": {
            "project_ids": [],
            "user_count": None,
            "observe_hours": None,
            "oncall_primary": "",
            "oncall_backup": "",
            "rollback_decider": "",
        },
    }


def finite(value: Any) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def check_manifest(value: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != 1 or value.get("environment") != "institution":
        errors.append("institution_schema_required")
    if not re.fullmatch(r"[0-9a-f]{40}", str(value.get("release_commit", ""))):
        errors.append("release_commit_required")
    for name in ("backend", "frontend", "gateway"):
        if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", value.get("images", {}).get(name, "")):
            errors.append(f"image_digest_required:{name}")
    for key in ("configuration_sha256", "dataset_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(value.get(key, ""))):
            errors.append(f"digest_required:{key}")
    if value.get("migration") != "0007_quality":
        errors.append("migration_mismatch")
    for name in blank_manifest()["versions"]:
        if not value.get("versions", {}).get(name):
            errors.append(f"version_required:{name}")
    verified = set()
    for name, entry in value.get("evidence", {}).items():
        path = (root / entry.get("path", "")).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or not entry.get("path"):
            errors.append(f"evidence_missing:{name}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            errors.append(f"evidence_hash_mismatch:{name}")
        else:
            verified.add(name)
    for name in EVIDENCE:
        if name not in verified:
            errors.append(f"evidence_required:{name}")
    for role in ROLES:
        approval = value.get("approvals", {}).get(role, {})
        from datetime import datetime

        try:
            date = datetime.fromisoformat(approval.get("signed_at", ""))
            date_valid = date.tzinfo is not None and date <= datetime.now(date.tzinfo)
        except (TypeError, ValueError):
            date_valid = False
        if (
            not approval.get("reviewer")
            or not date_valid
            or approval.get("evidence") not in verified
        ):
            errors.append(f"approval_required:{role}")
    for name, (direction, unit, denominator) in METRICS.items():
        metric = value.get("metrics", {}).get(name, {})
        threshold, measured = metric.get("threshold"), metric.get("measured")
        if not finite(threshold) or not finite(measured):
            errors.append(f"metric_unmeasured:{name}")
            continue
        if unit == "ratio" and (threshold > 1 or measured > 1):
            errors.append(f"metric_ratio_invalid:{name}")
        if metric.get("unit") != unit or not metric.get("conditions"):
            errors.append(f"metric_conditions_required:{name}")
        if denominator and (
            type(metric.get("denominator")) is not int or metric["denominator"] <= 0
        ):
            errors.append(f"denominator_required:{name}")
        if unit in ("count", "users", "facts") and (
            int(threshold) != threshold or int(measured) != measured
        ):
            errors.append(f"metric_integer_required:{name}")
        if metric.get("evidence") not in verified:
            errors.append(f"metric_evidence_required:{name}")
        if (direction == "min" and measured < threshold) or (
            direction == "max" and measured > threshold
        ):
            errors.append(f"threshold_failed:{name}")
    if type(value.get("blocker_defects")) is not int or value["blocker_defects"] != 0:
        errors.append("blocker_defects_not_closed")
    for key in ("uat_passed", "rollback_rehearsed"):
        if value.get(key) is not True:
            errors.append(f"required:{key}")
    for key in blank_manifest()["retention_days"]:
        days = value.get("retention_days", {}).get(key)
        if type(days) is not int or days <= 0:
            errors.append(f"retention_required:{key}")
    pilot = value.get("pilot", {})
    from uuid import UUID

    try:
        if not pilot.get("project_ids"):
            raise ValueError()
        for project in pilot["project_ids"]:
            UUID(project)
    except (ValueError, TypeError, AttributeError):
        errors.append("pilot_projects_required")
    for key in ("user_count", "observe_hours"):
        if type(pilot.get(key)) is not int or pilot[key] <= 0:
            errors.append(f"pilot_required:{key}")
    for key in ("oncall_primary", "oncall_backup", "rollback_decider"):
        if not pilot.get(key):
            errors.append(f"pilot_required:{key}")
    return sorted(set(errors))


def evaluate(path: Path) -> dict[str, Any]:
    try:
        errors = check_manifest(json.loads(path.read_text()), path.parent)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError):
        errors = ["invalid_manifest"]
    return {
        "ready": not errors,
        "blockers": errors,
        "note": (
            "Evidence validation does not replace institution approval or verify signer identity."
        ),
    }
