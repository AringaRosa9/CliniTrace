"""Local OCR only. TSV cells retain row order and normalized image coordinates."""

import csv
import io
import math
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

from app.integrations.parsers.document import ParseFailure


def recognize(path: Path, language: str, timeout: int) -> tuple[list[dict[str, Any]], str]:
    try:
        version = (
            subprocess.run(["tesseract", "--version"], capture_output=True, timeout=5, check=True)
            .stdout.decode()
            .splitlines()[0]
        )
        response = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", language, "--psm", "3", "tsv"],
            capture_output=True,
            timeout=timeout,
            check=True,
        )
    except FileNotFoundError:
        raise ParseFailure("OCR_NOT_INSTALLED") from None
    except subprocess.TimeoutExpired:
        raise ParseFailure("OCR_TIMEOUT") from None
    except subprocess.CalledProcessError:
        raise ParseFailure("OCR_ENGINE_FAILED") from None
    with Image.open(path) as image:
        width, height = image.size
    rows: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for cell in csv.DictReader(io.StringIO(response.stdout.decode()), delimiter="\t"):
        value = cell.get("text", "").strip()
        if cell["level"] != "5" or not value:
            continue
        confidence = float(cell["conf"])
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            raise ParseFailure("OCR_INVALID_OUTPUT")
        x, y, w, h = [int(cell[k]) for k in ("left", "top", "width", "height")]
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
            raise ParseFailure("OCR_INVALID_OUTPUT")
        key = tuple(cell[k] for k in ("block_num", "par_num", "line_num"))
        rows.setdefault(key, []).append(
            {
                "text": value,
                "confidence": confidence / 100,
                "bbox": [x / width, y / height, (x + w) / width, (y + h) / height],
            }
        )
    return [
        {
            "cells": cells,
            "text": " ".join(c["text"] for c in cells) + "\n",
            "bbox": [
                min(c["bbox"][0] for c in cells),
                min(c["bbox"][1] for c in cells),
                max(c["bbox"][2] for c in cells),
                max(c["bbox"][3] for c in cells),
            ],
        }
        for cells in rows.values()
    ], version


def apply_ocr(result: dict[str, Any], output: Path, language: str, timeout: int) -> None:
    text = ""
    engines = set()
    for page in result["pages"]:
        if page["needs_ocr"]:
            lines, version = recognize(output / f"page-{page['page']}.png", language, timeout)
            engines.add(version)
            page["blocks"] = [
                {"id": f"p{page['page']}-ocr-{i}", "text": row["text"], "bbox": row["bbox"]}
                for i, row in enumerate(lines)
            ]
            page["tables"] = [{"kind": "ocr_rows", "rows": lines}]
            scores = [c["confidence"] for row in lines for c in row["cells"]]
            page["quality"] = {
                "source": "ocr",
                "engine_version": version,
                "language": language,
                "mean_confidence": sum(scores) / len(scores) if scores else 0,
                "low_quality": not scores or min(scores) < 0.5,
                "table_structure": "row_cells_requires_review",
            }
            page["needs_ocr"] = not bool(scores)
        for block in page["blocks"]:
            block["start"] = len(text)
            text += block["text"]
            block["end"] = len(text)
    result["text"] = text
    result["text_version"] = "unicode-ocr-v1"
    result["needs_ocr"] = any(p["needs_ocr"] for p in result["pages"])
    result["parser_version"] += "/" + "+".join(sorted(engines)) + "/" + language
