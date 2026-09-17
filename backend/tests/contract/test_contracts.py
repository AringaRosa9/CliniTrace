import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.contracts.models import (
    ClinicalValue,
    ErrorResponse,
    Evidence,
    ExportAccepted,
    ExportRequest,
    FactPatch,
    FactRevision,
    JobResponse,
    LabTemplate,
    OutpatientTemplate,
    ReviewRequest,
    ReviewSnapshot,
    TextSpan,
    UploadAccepted,
    validate_quote,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "name,request_model,response",
    [
        ("upload", None, UploadAccepted),
        ("fact", FactPatch, FactRevision),
        ("evidence", None, Evidence),
        ("review", ReviewRequest, ReviewSnapshot),
        ("job", None, JobResponse),
        ("export", ExportRequest, ExportAccepted),
        ("error", None, ErrorResponse),
    ],
)
def test_examples(name, request_model, response):
    sample = json.loads((ROOT / f"packages/contracts/examples/{name}.json").read_text())
    response.model_validate(sample["response"])
    if request_model:
        request_model.model_validate(sample["request"])


@pytest.mark.parametrize(
    "name,model", [("outpatient", OutpatientTemplate), ("laboratory", LabTemplate)]
)
def test_templates(name, model):
    schema = json.loads((ROOT / f"packages/contracts/json-schema/{name}-1.0.0.json").read_text())
    sample = json.loads((ROOT / f"evaluation/datasets/synthetic/{name}.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(sample)
    model.model_validate(sample)


def test_shared_unicode_spans():
    sample = json.loads((ROOT / "packages/contracts/examples/evidence.json").read_text())[
        "response"
    ]
    for case in json.loads((ROOT / "tests/fixtures/synthetic/spans.json").read_text()):
        evidence = Evidence.model_validate(
            {**sample, "span": {"start": case["start"], "end": case["end"]}, "quote": case["quote"]}
        )
        validate_quote(evidence, case["text"], case["text_version"])
        with pytest.raises(ValueError):
            validate_quote(evidence, case["text"], "wrong-version")
        with pytest.raises(ValueError):
            validate_quote(evidence, "wrong-text", case["text_version"])


@pytest.mark.parametrize("start,end", [(-1, 2), (1, 1), (5, 2)])
def test_invalid_span(start, end):
    with pytest.raises(ValidationError):
        TextSpan(start=start, end=end)


def test_unknown_and_negation_are_independent():
    value = ClinicalValue(
        raw="剂量不详", normalized=None, kind="text", missing_reason="explicitly_unknown"
    )
    assert value.missing_reason == "explicitly_unknown"
    negation = ClinicalValue(
        raw="否认冠心病史", normalized="冠心病", kind="text", assertion="negated"
    )
    assert negation.missing_reason is None
    for update in ({"missing_reason": None}, {"normalized": "500"}):
        with pytest.raises(ValidationError):
            ClinicalValue.model_validate({**value.model_dump(), **update})


def test_review_confirmation_and_drafts():
    sample = json.loads((ROOT / "packages/contracts/examples/review.json").read_text())["request"]
    with pytest.raises(ValidationError):
        ReviewRequest.model_validate({**sample, "final_confirmation": False})
    sample = json.loads((ROOT / "packages/contracts/examples/export.json").read_text())["request"]
    with pytest.raises(ValidationError):
        ExportRequest.model_validate(
            {**sample, "draft_fact_revision_ids": sample["review_snapshot_ids"]}
        )
