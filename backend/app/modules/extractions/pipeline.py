"""Schema validation, source grounding, relation assembly and explainable rules."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.contracts.models import (
    ExtractedItem,
    FactRevision,
    LabTemplate,
    OutpatientTemplate,
    validate_quote,
)
from app.modules.extractions.schema import Coding, Issue, Relation

ASSETS = Path(__file__).resolve().parents[4] / "ai"
RULES = json.loads((ASSETS / "rules/extraction-1.0.0.json").read_text())
PROMPT = (ASSETS / "prompts/extraction-1.0.0.txt").read_text()
TERMS = json.loads((ASSETS / "schemas/synthetic-terms-1.0.0.json").read_text())


class ExtractionFailure(Exception):
    def __init__(self, code: str, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def model_for(template: str) -> type[OutpatientTemplate] | type[LabTemplate]:
    return OutpatientTemplate if template == "outpatient-1.0.0" else LabTemplate


def candidates(raw: str, version: str) -> list[dict[str, str]]:
    if version != TERMS["version"]:
        return []
    return [
        {
            "code": term["code"],
            "display": term["display"],
            "system": TERMS["system"],
            "version": version,
            "basis": "合成词库原文精确匹配；须人工核对上下文",
        }
        for term in TERMS["terms"]
        if raw in term["aliases"]
    ]


def issue(rule: str, revisions: list[str], message: str) -> dict[str, Any]:
    severity, acceptable = RULES["rules"][rule]
    return Issue(
        id=digest([rule, sorted(revisions), message])[:24],
        rule=rule,
        severity=severity,
        can_accept_unknown=acceptable,
        fact_revision_ids=revisions,
        message=message,
    ).model_dump(mode="json")


def validate_evidence(
    item: ExtractedItem, parsed: dict[str, Any], version: str, artifact: str
) -> None:
    for ev in item.evidence:
        if str(ev.document_version_id) != version or str(ev.parse_artifact_id) != artifact:
            raise ExtractionFailure("EVIDENCE_VERSION_MISMATCH")
        try:
            validate_quote(ev, parsed["text"], parsed["text_version"])
        except ValueError:
            raise ExtractionFailure("EVIDENCE_QUOTE_MISMATCH") from None
        page = next((p for p in parsed["pages"] if p["page"] == ev.page), None)
        block = next((b for b in page["blocks"] if b["id"] == ev.block_id), None) if page else None
        if not block or not block["start"] <= ev.span.start < ev.span.end <= block["end"]:
            raise ExtractionFailure("EVIDENCE_BLOCK_MISMATCH")
        # A model cannot relocate evidence. Derive/validate geometry against the parser.
        box = block["bbox"]
        if box:
            expected = dict(zip(("x0", "y0", "x1", "y1"), box, strict=True))
            if not ev.boxes:
                from app.contracts.models import BoundingBox

                ev.boxes = [BoundingBox(**expected)]
            if len(ev.boxes) != 1 or any(
                abs(a - b) > 1e-6
                for a, b in zip(ev.boxes[0].model_dump().values(), expected.values(), strict=True)
            ):
                raise ExtractionFailure("EVIDENCE_BBOX_MISMATCH")
        elif ev.boxes:
            raise ExtractionFailure("EVIDENCE_BBOX_MISMATCH")
    quote = "\n".join(e.quote for e in item.evidence)
    if item.value.raw not in quote:
        raise ExtractionFailure("VALUE_WITHOUT_EVIDENCE")
    if (
        item.value.kind in ("text", "date")
        and item.value.normalized is not None
        and item.value.normalized not in quote
    ):
        raise ExtractionFailure("NORMALIZATION_WITHOUT_EVIDENCE")
    if item.value.unit and item.value.unit not in quote:
        raise ExtractionFailure("UNIT_WITHOUT_EVIDENCE")
    if item.value.kind == "decimal" and item.value.normalized is not None:
        from decimal import Decimal

        numbers = re.findall(r"(?<![\d.])[-+]?\d+(?:\.\d+)?(?![\d.])", item.value.raw)
        if not any(Decimal(n) == Decimal(item.value.normalized) for n in numbers):
            raise ExtractionFailure("NUMBER_WITHOUT_EVIDENCE")
        comparison = re.search(r"<=|>=|[<>≤≥]", item.value.raw)
        expected_comparator = (
            comparison.group().replace("≤", "<=").replace("≥", ">=") if comparison else "="
        )
        if (item.value.comparator or "=") != expected_comparator:
            raise ExtractionFailure("COMPARATOR_MISMATCH")
    event = item.event_time
    if event:
        if not event.original_text or event.original_text not in quote:
            raise ExtractionFailure("TIME_WITHOUT_EVIDENCE")
        if event.anchor_fact_revision_id or event.derivation:
            raise ExtractionFailure("UNSUPPORTED_TIME_DERIVATION")
        if event.precision in ("relative", "unknown") and event.value is not None:
            raise ExtractionFailure("INFERRED_DATE")
        if event.value and event.value not in event.original_text:
            raise ExtractionFailure("INFERRED_DATE")


def assemble(
    raw: Any, parsed: dict[str, Any], version: str, artifact: str, template: str, terminology: str
) -> dict[str, Any]:
    model = model_for(template).model_validate(raw)
    rows: list[FactRevision] = []
    links: list[Relation] = []
    issues: list[dict[str, Any]] = []
    mappings: list[Coding] = []
    groups: set[UUID] = set()
    evidence_ids: dict[UUID, dict[str, Any]] = {}
    internal_groups: dict[UUID, UUID] = {}
    internal_evidence: dict[UUID, UUID] = {}

    def add(item: ExtractedItem, path: str) -> FactRevision:
        validate_evidence(item, parsed, version, artifact)
        for ev in item.evidence:
            payload = ev.model_dump(mode="json")
            if ev.id in evidence_ids and evidence_ids[ev.id] != payload:
                raise ExtractionFailure("EVIDENCE_ID_COLLISION")
            evidence_ids[ev.id] = payload
            internal_evidence.setdefault(ev.id, uuid4())
        item = item.model_copy(deep=True)
        for ev in item.evidence:
            ev.id = internal_evidence[ev.id]
        fact = FactRevision(
            fact_id=uuid4(),
            revision_id=uuid4(),
            revision=1,
            entity_group_id=internal_groups.setdefault(item.entity_group_id, uuid4()),
            field_path=path,
            value=item.value,
            event_time=item.event_time,
            evidence=item.evidence,
        )
        rows.append(fact)
        return fact

    def group(items: list[tuple[str, ExtractedItem]], prefix: str) -> None:
        gid = items[0][1].entity_group_id
        if gid in groups or any(x.entity_group_id != gid for _, x in items):
            raise ExtractionFailure("ENTITY_GROUP_MISMATCH")
        groups.add(gid)
        root = add(items[0][1], prefix + "." + items[0][0])
        for field, item in items[1:]:
            child = add(item, prefix + "." + field)
            links.append(
                Relation(
                    id=uuid4(),
                    source_id=root.fact_id,
                    target_id=child.fact_id,
                    kind=prefix + "." + field,
                )
            )
        if prefix == "medications" and "dose" not in [f for f, _ in items]:
            issues.append(
                issue(
                    "MISSING_DOSE", [str(root.revision_id)], "药物剂量未提及；不得按常规用量补全。"
                )
            )

    if isinstance(model, OutpatientTemplate):
        for category in ("diagnoses", "history", "duration"):
            for item in getattr(model, category):
                if item.entity_group_id in groups:
                    raise ExtractionFailure("ENTITY_GROUP_MISMATCH")
                groups.add(item.entity_group_id)
                add(item, category)
        for med in model.medications:
            group(
                [(k, v) for k in ("name", "dose", "frequency") if (v := getattr(med, k))],
                "medications",
            )
        if not model.diagnoses:
            issues.append(
                issue("MISSING_DIAGNOSIS", [], "文书没有抽取到诊断，请核对文书类型和原文。")
            )
    else:
        for obs in model.observations:
            if obs.collected_at:
                obs.result.event_time = obs.collected_at
            group(
                [
                    (k, v)
                    for k in ("name", "result", "specimen", "method")
                    if (v := getattr(obs, k))
                ],
                "observations",
            )
    if not rows:
        issues.append(issue("EMPTY_EXTRACTION", [], "未抽取到可复核事实，请核对原文和模板。"))
    for fact in rows:
        rid = [str(fact.revision_id)]
        quote = "\n".join(e.quote for e in fact.evidence)
        if fact.value.missing_reason:
            issues.append(
                issue(
                    "MISSING_VALUE",
                    rid,
                    "值缺失："
                    + {
                        "missing": "应有而缺失",
                        "not_mentioned": "未提及",
                        "explicitly_unknown": "明确未知",
                        "not_applicable": "不适用",
                        "unreadable": "无法辨认",
                    }[fact.value.missing_reason.value]
                    + "。",
                )
            )
        if re.search(r"否认|无.*史|未见", quote) and fact.value.assertion == "affirmed":
            issues.append(issue("NEGATION_MISMATCH", rid, "证据含否定表述，需核对断言。"))
        if re.search(r"不详|未知|不清", fact.value.raw) and fact.value.missing_reason is None:
            issues.append(issue("UNKNOWN_MISMATCH", rid, "原文表示未知，不得补写为已知值。"))
        if (
            fact.field_path == "observations.result"
            and fact.value.kind == "decimal"
            and not fact.value.unit
        ):
            issues.append(issue("MISSING_UNIT", rid, "检验结果缺少原始单位，请核对。"))
        if fact.value.kind == "date" and fact.value.normalized:
            try:
                date.fromisoformat(fact.value.normalized)
            except ValueError:
                issues.append(issue("INVALID_DATE", rid, "日期格式或日历值无效。"))
        if fact.field_path in ("diagnoses", "history", "observations.name"):
            found = candidates(fact.value.normalized or fact.value.raw, terminology)
            mappings.append(
                Coding(
                    fact_id=fact.fact_id,
                    version=terminology,
                    status="pending" if found else "unmapped",
                    candidates=found,
                    reason="仅候选，尚未人工确认；检验方法和标本须核对。"
                    if found
                    else "指定词库无足够依据，保留待映射。",
                )
            )
            issues.append(issue("PENDING_MAPPING", rid, "术语编码尚未确认，保留原文。"))
    for page in parsed["pages"]:
        if (page.get("quality") or {}).get("low_quality"):
            issues.append(
                issue(
                    "OCR_LOW_QUALITY",
                    [],
                    f"第 {page['page']} 页 OCR 质量偏低，需核对页图及表格配对。",
                )
            )
    serialized = [f.model_dump(mode="json") for f in rows]
    issues.extend(compare_facts(serialized))
    return {
        "facts": serialized,
        "relations": [r.model_dump(mode="json") for r in links],
        "issues": issues,
        "codings": [m.model_dump(mode="json") for m in mappings],
    }


def compare_facts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    problems = []
    names = {
        f["entity_group_id"]: f["value"]["normalized"]
        for f in rows
        if f["field_path"].endswith(".name")
    }
    seen: dict[tuple[str, Any, str], dict[str, Any]] = {}
    for fact in rows:
        value = fact["value"]
        path = fact["field_path"]
        key = (
            "condition" if path in ("diagnoses", "history") else path,
            names.get(fact["entity_group_id"], value["normalized"]),
            value["experiencer"],
        )
        previous = seen.get(key)
        if previous:
            a, b = previous["value"], value
            conflict = any(
                a.get(k) != b.get(k)
                for k in ("normalized", "assertion", "unit", "comparator", "missing_reason")
            )
            problems.append(
                issue(
                    "CONFLICT" if conflict else "DUPLICATE",
                    [previous["revision_id"], fact["revision_id"]],
                    "同一字段存在不同来源或重复记录，保留双方事实，请结合时间核对。",
                )
            )
        seen[key] = fact
    return problems
