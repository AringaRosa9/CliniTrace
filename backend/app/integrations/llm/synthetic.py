"""Explicit synthetic fixture grammar, never a clinical extractor or quality baseline."""

import re
from typing import Any
from uuid import uuid4

from app.modules.extractions.pipeline import ExtractionFailure


def extract_synthetic(request: dict[str, Any]) -> dict[str, Any]:
    parsed = request["parsed"]
    if not parsed["text"].startswith("SYNTHETIC-S2"):
        raise ExtractionFailure("SYNTHETIC_DOCUMENT_REQUIRED")

    def item(
        raw: str,
        start: int,
        group: str,
        *,
        normalized: str | None = None,
        kind: str = "text",
        missing: str | None = None,
        assertion: str = "affirmed",
        unit: str | None = None,
        relative: bool = False,
    ) -> dict[str, Any]:
        page, block = next(
            (p, b)
            for p in parsed["pages"]
            for b in p["blocks"]
            if b["start"] <= start and start + len(raw) <= b["end"]
        )
        return {
            "entity_group_id": group,
            "value": {
                "raw": raw,
                "normalized": None if missing else normalized if normalized is not None else raw,
                "kind": kind,
                "missing_reason": missing,
                "assertion": assertion,
                "unit": unit,
                "comparator": next((x for x in ("<=", ">=", "<", ">") if raw.startswith(x)), "=")
                if kind == "decimal"
                else None,
            },
            "evidence": [
                {
                    "id": str(uuid4()),
                    "document_version_id": request["document_version_id"],
                    "parse_artifact_id": request["parse_artifact_id"],
                    "text_version": parsed["text_version"],
                    "page": page["page"],
                    "span": {"start": start, "end": start + len(raw)},
                    "quote": raw,
                    "block_id": block["id"],
                    "boxes": [],
                }
            ],
            "event_time": {"precision": "relative", "original_text": raw, "value": None}
            if relative
            else None,
        }

    output: dict[str, Any] = {
        "schema_version": request["configuration"]["template_version"].split("-")[0] + "-1.0.0"
    }
    lab = output["schema_version"].startswith("laboratory")
    output.update(
        {"observations": []}
        if lab
        else {"diagnoses": [], "history": [], "duration": [], "medications": []}
    )
    offset = 0
    for line in parsed["text"].splitlines(keepends=True):
        row = line.rstrip("\r\n")
        group = str(uuid4())
        if ":" in row:
            tag, value = row.split(":", 1)
            start = offset + len(tag) + 1
            if not lab and tag in ("diagnoses", "history", "duration"):
                assertion = (
                    "negated"
                    if value.startswith("否认")
                    else "suspected"
                    if value.startswith("疑似")
                    else "affirmed"
                )
                normalized = re.sub(r"^(否认|疑似)", "", value)
                output[tag].append(
                    item(
                        value,
                        start,
                        group,
                        normalized=normalized,
                        assertion=assertion,
                        relative=tag == "duration",
                    )
                )
            elif not lab and tag == "medication":
                parts = value.split("|")
                med = {"name": item(parts[0], start, group)}
                if len(parts) > 1:
                    med["dose"] = item(
                        parts[1],
                        start + len(parts[0]) + 1,
                        group,
                        missing="explicitly_unknown" if parts[1] == "剂量不详" else None,
                    )
                output["medications"].append(med)
            elif lab and tag == "observation":
                name, number, unit = value.split("|")
                output["observations"].append(
                    {
                        "name": item(name, start, group),
                        "result": item(
                            number + "|" + unit,
                            start + len(name) + 1,
                            group,
                            kind="decimal",
                            normalized=number.lstrip("<>= "),
                            unit=unit or None,
                        ),
                    }
                )
        offset += len(line)
    return output
