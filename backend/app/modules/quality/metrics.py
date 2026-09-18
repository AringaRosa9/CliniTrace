"""Offline, deterministic multiset scoring. No network, model calls or mutable state."""

import json
from collections import Counter, defaultdict
from decimal import Decimal
from math import ceil
from typing import Any

PROTOCOL = "quality-1.0.0"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def score(tp: int, fp: int, fn: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "f1": ratio(2 * tp, 2 * tp + fp + fn),
        "gold_count": tp + fn,
        "prediction_count": tp + fp,
    }


def field_key(fact: dict[str, Any]) -> str:
    # Exact decimal strings, null semantics, assertions and units are intentionally preserved.
    value = fact["value"]
    return canonical(
        [
            fact["field_path"],
            {
                k: value.get(k)
                for k in (
                    "normalized",
                    "kind",
                    "unit",
                    "comparator",
                    "missing_reason",
                    "assertion",
                    "experiencer",
                )
            },
        ]
    )


def spans(fact: dict[str, Any]) -> list[tuple[str, str, int, int, int]]:
    return sorted(
        (
            e["document_version_id"],
            e.get("text_version", ""),
            e["page"],
            e["span"]["start"],
            e["span"]["end"],
        )
        for e in fact["evidence"]
    )


def anchor(fact: dict[str, Any]) -> str:
    return canonical([fact["field_path"], spans(fact)])


def label_digest(labels: dict[str, Any]) -> str:
    """Ignore generated IDs/order, retaining values, evidence, time and graph endpoints."""
    keys = {
        f["fact_id"]: canonical(
            [field_key(f), spans(f), f.get("event_time"), f.get("excluded", False)]
        )
        for f in labels["facts"]
    }
    return canonical(
        [
            sorted(keys.values()),
            sorted(
                canonical([r["kind"], keys[r["source_id"]], keys[r["target_id"]]])
                for r in labels["relations"]
            ),
            sorted(
                canonical([keys[c["fact_id"]], c["system"], c["version"], c["code"]])
                for c in labels["codings"]
            ),
        ]
    )


def counts(gold: list[str], predicted: list[str]) -> tuple[int, int, int]:
    g, p = Counter(gold), Counter(predicted)
    tp = sum((g & p).values())
    return tp, sum(p.values()) - tp, sum(g.values()) - tp


def evidence_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return any(
        x[0:3] == y[0:3] and max(x[3], y[3]) < min(x[4], y[4]) for x in spans(a) for y in spans(b)
    )


def overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return field_key(a) == field_key(b) and evidence_overlap(a, b)


def relaxed_count(gold: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> int:
    # Maximum bipartite matching prevents duplicate predictions or greedy ordering inflating scores.
    edges = [[j for j, p in enumerate(predicted) if overlap(g, p)] for g in gold]
    matched: dict[int, int] = {}

    def augment(i: int, seen: set[int]) -> bool:
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in matched or augment(matched[j], seen):
                matched[j] = i
                return True
        return False

    return sum(augment(i, set()) for i in range(len(gold)))


def evaluate(records: list[dict[str, Any]]) -> dict[str, Any]:
    total: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    fields: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    types: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    high = {
        k: {"count": 0, "denominator": 0}
        for k in ("negation_to_affirmed", "dose_error", "patient_linkage")
    }
    errors: list[dict[str, Any]] = []
    time_correct = time_n = mapping_correct = mapping_n = mapped = abstained = 0
    latencies, human, annotation_times = [], [], []
    costs: list[Decimal] = []
    failed = modifications = returned = 0

    def add(target: dict[str, list[int]], key: str, values: tuple[int, int, int]) -> None:
        target[key] = [a + b for a, b in zip(target[key], values, strict=True)]

    for record in records:
        gold, prediction = record["gold"], record["prediction"]
        g = [f for f in gold["facts"] if not f.get("excluded")]
        p = [f for f in prediction["facts"] if not f.get("excluded")]
        field = counts([field_key(f) for f in g], [field_key(f) for f in p])
        add(total, "field", field)
        add(types, record["document_type"], field)
        for path in sorted({f["field_path"] for f in g + p}):
            add(
                fields,
                path,
                counts(
                    [field_key(f) for f in g if f["field_path"] == path],
                    [field_key(f) for f in p if f["field_path"] == path],
                ),
            )
        add(
            total,
            "strict_span",
            counts(
                [canonical([field_key(f), spans(f)]) for f in g],
                [canonical([field_key(f), spans(f)]) for f in p],
            ),
        )
        relaxed = relaxed_count(g, p)
        add(total, "relaxed_span", (relaxed, len(p) - relaxed, len(g) - relaxed))

        def graph(labels: dict[str, Any]) -> list[str]:
            keys = {
                f["fact_id"]: canonical([field_key(f), spans(f)])
                for f in labels["facts"]
                if not f.get("excluded")
            }
            return [
                canonical([r["kind"], keys[r["source_id"]], keys[r["target_id"]]])
                for r in labels["relations"]
                if r["source_id"] in keys and r["target_id"] in keys
            ]

        relation_counts = counts(graph(gold), graph(prediction))
        add(total, "relation", relation_counts)
        available: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for f in p:
            available[anchor(f)].append(f)
        gc = {c["fact_id"]: c for c in gold["codings"]}
        pc = {c["fact_id"]: c for c in prediction["codings"]}
        sample_errors: set[str] = set()
        if relation_counts[1] or relation_counts[2]:
            sample_errors.add("relation")
        strict = counts(
            [canonical([field_key(f), spans(f)]) for f in g],
            [canonical([field_key(f), spans(f)]) for f in p],
        )
        if strict[1] or strict[2]:
            sample_errors.add("evidence")
        high["patient_linkage"]["denominator"] += 1
        if record.get("patient_id") != record.get("prediction_patient_id"):
            high["patient_linkage"]["count"] += 1
            sample_errors.add("patient_linkage")
        for f in g:
            possible = available[anchor(f)]
            if not possible:
                possible = next(
                    (
                        bucket
                        for bucket in available.values()
                        if bucket
                        and bucket[0]["field_path"] == f["field_path"]
                        and evidence_overlap(f, bucket[0])
                    ),
                    [],
                )
            matching = next((i for i, x in enumerate(possible) if field_key(x) == field_key(f)), 0)
            pred = possible.pop(matching) if possible else None
            neg = f["value"]["assertion"] == "negated"
            dose = f["field_path"] == "medications.dose"
            high["negation_to_affirmed"]["denominator"] += int(neg)
            high["dose_error"]["denominator"] += int(dose)
            if neg and pred and pred["value"]["assertion"] == "affirmed":
                high["negation_to_affirmed"]["count"] += 1
                sample_errors.add("negation_to_affirmed")
            if dose and (not pred or field_key(f) != field_key(pred)):
                high["dose_error"]["count"] += 1
                sample_errors.add("dose_error")
            if f.get("event_time") is not None:
                time_n += 1
                time_match = pred is not None and pred.get("event_time") == f["event_time"]
                time_correct += int(time_match)
                if not time_match:
                    sample_errors.add("time")
            if f["fact_id"] in gc:
                mapping_n += 1
                coding = pc.get(pred["fact_id"]) if pred else None
                is_mapped = bool(coding and coding["code"] is not None)
                mapped += int(is_mapped)
                abstained += int(not is_mapped)
                target = gc[f["fact_id"]]
                coding_match = coding is not None and all(
                    coding[k] == target[k] for k in ("system", "version", "code")
                )
                mapping_correct += int(coding_match)
                if not coding_match:
                    sample_errors.add("terminology")
        # Extra/invented dose facts are high risk too; count all unmatched predicted doses.
        extra_doses = sum(
            f["field_path"] == "medications.dose" for bucket in available.values() for f in bucket
        )
        high["dose_error"]["count"] += extra_doses
        high["dose_error"]["denominator"] += extra_doses
        if extra_doses:
            sample_errors.add("dose_error")
        if field[1]:
            sample_errors.add("false_positive")
        if field[2]:
            sample_errors.add("false_negative")
        if sample_errors:
            errors.append(
                {
                    "sample_id": record["sample_id"],
                    "strata": sorted(sample_errors),
                    "field": score(*field),
                }
            )
        if record.get("duration_ms") is not None:
            latencies.append(record["duration_ms"])
        if record.get("active_seconds") is not None:
            human.append(record["active_seconds"])
        if record.get("annotation_seconds") is not None:
            annotation_times.append(record["annotation_seconds"])
        if record.get("cost_amount") is not None:
            costs.append(Decimal(record["cost_amount"]))
        failed += int(record.get("failed", False))
        modifications += record.get("modifications", 0)
        returned += int(record.get("returned", False))

    def distribution(values: list[int]) -> dict[str, Any]:
        values = sorted(values)
        n = len(values)
        return {
            "count": n,
            "median": (values[(n - 1) // 2] + values[n // 2]) / 2 if n else None,
            "p95": values[ceil(n * 0.95) - 1] if n else None,
        }

    per_field = {k: score(*v) for k, v in sorted(fields.items())}
    macro = {
        key: (sum(values) / len(values) if values else None)
        for key in ("precision", "recall", "f1")
        for values in [[v[key] for v in per_field.values() if v[key] is not None]]
    }
    return {
        "protocol": PROTOCOL,
        "sample_count": len(records),
        "micro": score(*total["field"]),
        "macro": macro,
        "fields": per_field,
        "document_types": {k: score(*v) for k, v in sorted(types.items())},
        "strict_span": score(*total["strict_span"]),
        "relaxed_span": score(*total["relaxed_span"]),
        "relation": score(*total["relation"]),
        "time": {
            "correct": time_correct,
            "denominator": time_n,
            "accuracy": ratio(time_correct, time_n),
        },
        "terminology": {
            "correct": mapping_correct,
            "denominator": mapping_n,
            "accuracy": ratio(mapping_correct, mapping_n),
            "coverage": ratio(mapped, mapping_n),
            "abstention": ratio(abstained, mapping_n),
        },
        "high_risk": high,
        "errors": sorted(errors, key=lambda e: e["sample_id"]),
        "duration_ms": distribution(latencies),
        "human_seconds": distribution(human),
        "annotation_seconds": distribution(annotation_times),
        "cost": {
            "currency": "CNY",
            "pricing_versions": sorted(
                {r.get("configuration", {}).get("pricing_version", "unversioned") for r in records}
            ),
            "measured_samples": len(costs),
            "total": str(sum(costs, Decimal(0)))
            if len(costs) == len(records) and records
            else None,
        },
        "failure_rate": ratio(failed, len(records)),
        "modifications": modifications,
        "return_rate": ratio(returned, len(records)),
    }
