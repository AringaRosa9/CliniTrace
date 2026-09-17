import copy
import shutil
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw, ImageFont
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.llm.synthetic import extract_synthetic
from app.integrations.ocr.tesseract import apply_ocr
from app.integrations.parsers.document import parse
from app.modules.extractions.pipeline import ExtractionFailure, assemble, compare_facts


@pytest.fixture
def request_data(tmp_path):
    source = (
        "SYNTHETIC-S2 😀\ndiagnoses:2型糖尿病\nhistory:否认冠心病\n"
        "duration:5年\nmedication:二甲双胍|剂量不详\nmedication:阿卡波糖\n"
    )
    return {
        "parsed": parse(source.encode(), "text/plain", tmp_path, 10, 10000000),
        "document_version_id": str(uuid4()),
        "parse_artifact_id": str(uuid4()),
        "configuration": {"template_version": "outpatient-1.0.0"},
    }


def assemble_request(req, raw=None):
    return assemble(
        raw if raw is not None else extract_synthetic(req),
        req["parsed"],
        req["document_version_id"],
        req["parse_artifact_id"],
        req["configuration"]["template_version"],
        "synthetic-terms-1.0.0",
    )


def test_semantics_relations_unicode_and_no_dose_invention(request_data):
    value = assemble_request(request_data)
    facts = value["facts"]
    assert next(f for f in facts if f["field_path"] == "history")["value"]["assertion"] == "negated"
    assert next(f for f in facts if f["field_path"] == "duration")["event_time"]["value"] is None
    assert (
        next(f for f in facts if f["field_path"] == "medications.dose")["value"]["missing_reason"]
        == "explicitly_unknown"
    )
    assert len(value["relations"]) == 1
    link = value["relations"][0]
    source = next(f for f in facts if f["fact_id"] == link["source_id"])
    target = next(f for f in facts if f["fact_id"] == link["target_id"])
    assert source["entity_group_id"] == target["entity_group_id"]
    assert {i["rule"] for i in value["issues"]} >= {
        "MISSING_DOSE",
        "MISSING_VALUE",
        "PENDING_MAPPING",
    }
    for fact in facts:
        for ev in fact["evidence"]:
            assert (
                request_data["parsed"]["text"][ev["span"]["start"] : ev["span"]["end"]]
                == ev["quote"]
            )
    assert value["codings"][0]["candidates"][0]["system"] == "LOCAL-SYNTHETIC"


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda e: e.update(quote="fabricated"), "EVIDENCE_QUOTE_MISMATCH"),
        (lambda e: e.update(parse_artifact_id=str(uuid4())), "EVIDENCE_VERSION_MISMATCH"),
        (lambda e: e.update(page=2), "EVIDENCE_BLOCK_MISMATCH"),
        (lambda e: e.update(block_id="unknown"), "EVIDENCE_BLOCK_MISMATCH"),
        (
            lambda e: e.update(boxes=[{"x0": 0, "y0": 0, "x1": 1, "y1": 1}]),
            "EVIDENCE_BBOX_MISMATCH",
        ),
    ],
)
def test_evidence_rejections(request_data, change, code):
    raw = extract_synthetic(request_data)
    change(raw["diagnoses"][0]["evidence"][0])
    with pytest.raises(ExtractionFailure, match=code):
        assemble_request(request_data, raw)


def test_missing_evidence_and_fabrication(request_data):
    raw = extract_synthetic(request_data)
    raw["diagnoses"][0]["evidence"] = []
    with pytest.raises(ValidationError):
        assemble_request(request_data, raw)
    raw = extract_synthetic(request_data)
    raw["duration"][0]["event_time"]["value"] = "2021-09-17"
    with pytest.raises(ExtractionFailure, match="INFERRED_DATE"):
        assemble_request(request_data, raw)
    raw = extract_synthetic(request_data)
    raw["medications"][0]["dose"]["value"].update(normalized="500mg", missing_reason=None)
    with pytest.raises(ExtractionFailure, match="NORMALIZATION_WITHOUT_EVIDENCE"):
        assemble_request(request_data, raw)


def test_lab_precision_conflicts_multiple_evidence(request_data, tmp_path):
    request_data["configuration"]["template_version"] = "laboratory-1.0.0"
    request_data["parsed"] = parse(
        b"SYNTHETIC-S2\nobservation:FPG|<7.00|mmol/L\nobservation:HbA1c|6.80|%\n",
        "text/plain",
        tmp_path,
        10,
        10000000,
    )
    raw = extract_synthetic(request_data)
    ev = copy.deepcopy(raw["observations"][0]["result"]["evidence"][0])
    ev["id"] = str(uuid4())
    raw["observations"][0]["result"]["evidence"].append(ev)
    value = assemble_request(request_data, raw)
    assert len(value["relations"]) == 2
    result = next(f for f in value["facts"] if f["value"]["normalized"] == "7.00")
    assert result["value"]["comparator"] == "<"
    assert len(result["evidence"]) == 2
    changed = copy.deepcopy(value["facts"])
    for f in changed:
        f["revision_id"] = str(uuid4())
        if f["value"]["normalized"] == "7.00":
            f["value"]["normalized"] = "8.00"
    assert "CONFLICT" in [x["rule"] for x in compare_facts(value["facts"] + changed)]
    raw["observations"][0]["result"]["value"]["normalized"] = "100"
    with pytest.raises(ExtractionFailure, match="NUMBER_WITHOUT_EVIDENCE"):
        assemble_request(request_data, raw)


def test_group_pairing_and_duplicate_evidence_ids(request_data):
    raw = extract_synthetic(request_data)
    raw["medications"][0]["dose"]["entity_group_id"] = str(uuid4())
    with pytest.raises(ExtractionFailure, match="ENTITY_GROUP_MISMATCH"):
        assemble_request(request_data, raw)
    raw = extract_synthetic(request_data)
    raw["history"][0]["evidence"][0]["id"] = raw["diagnoses"][0]["evidence"][0]["id"]
    with pytest.raises(ExtractionFailure, match="EVIDENCE_ID_COLLISION"):
        assemble_request(request_data, raw)


def test_gateway_requires_explicit_approval_and_fixed_model():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, extraction_provider="gateway")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            extraction_provider="gateway",
            extraction_authorization="ref",
            extraction_model="fixed",
            extraction_gateway_url="http://external.invalid/extract",
        )


@pytest.mark.skipif(not shutil.which("tesseract"), reason="local OCR executable unavailable")
def test_real_local_ocr_retains_row_cells_and_spans(tmp_path):
    image = Image.new("RGB", (1600, 500), "white")
    font = (
        ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 48)
        if __import__("sys").platform == "darwin"
        else ImageFont.truetype("DejaVuSans.ttf", 48)
    )
    ImageDraw.Draw(image).text(
        (60, 80),
        "SYNTHETIC REPORT\nGlucose  7.00  mmol/L\nHbA1c  6.80  percent",
        fill="black",
        font=font,
        spacing=25,
    )
    image.save(tmp_path / "page-1.png")
    parsed = {
        "text": "",
        "parser_version": "test",
        "pages": [{"page": 1, "needs_ocr": True, "blocks": []}],
    }
    apply_ocr(parsed, tmp_path, "eng", 15)
    assert "Glucose" in parsed["text"]
    assert "7.00" in parsed["text"]
    assert not parsed["needs_ocr"]
    assert parsed["pages"][0]["tables"][0]["rows"]
    for block in parsed["pages"][0]["blocks"]:
        assert parsed["text"][block["start"] : block["end"]] == block["text"]
        assert all(0 <= n <= 1 for n in block["bbox"])
