"""Deterministic parser, executed in a bounded child process by the worker."""

import io
import json
import math
import sys
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageOps

PARSER_VERSION = f"text-pdf-image-1/pymupdf-{pymupdf.VersionBind}"


class ParseFailure(Exception):
    pass


def detect(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if data.startswith(b"%PDF-") and suffix == ".pdf":
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n") and suffix == ".png":
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") and suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".txt":
        try:
            text = data.decode("utf-8-sig")
            if text.strip() and not any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
                return "text/plain"
        except UnicodeError:
            pass
    raise ParseFailure("UNSUPPORTED_FORMAT")


def parse(data: bytes, mime: str, output: Path, max_pages: int, max_pixels: int) -> dict[str, Any]:
    text = ""
    pages: list[dict[str, Any]] = []

    def block(value: str, box: list[float] | None, page: int, order: int) -> dict[str, Any]:
        nonlocal text
        start = len(text)
        text += value
        return {
            "id": f"p{page}-b{order}",
            "text": value,
            "start": start,
            "end": len(text),
            "bbox": box,
        }

    def page_info(number: int, width: float, height: float, rotation: int = 0) -> dict[str, Any]:
        return {
            "page": number,
            "width": width,
            "height": height,
            "rotation": rotation,
            "transform": [1, 0, 0, 1, 0, 0],
            "needs_ocr": False,
            "blocks": [],
            "image": False,
        }

    if mime == "text/plain":
        # Decode only: preserve original Unicode and line endings, so offsets remain reproducible.
        value = data.decode("utf-8-sig")
        page = page_info(1, 0, 0)
        page["blocks"] = [block(value, None, 1, 0)]
        pages.append(page)
    elif mime.startswith("image/"):
        Image.MAX_IMAGE_PIXELS = max_pixels
        with Image.open(io.BytesIO(data)) as raw:
            if raw.width * raw.height > max_pixels or getattr(raw, "n_frames", 1) != 1:
                raise ParseFailure("IMAGE_LIMIT")
            raw.verify()
        with Image.open(io.BytesIO(data)) as raw:
            img = ImageOps.exif_transpose(raw).convert("RGB")
            page = page_info(1, img.width, img.height)
            img.thumbnail((1800, 1800))
            img.save(output / "page-1.png")
        page.update(needs_ocr=True, image=True)
        pages.append(page)
    else:
        with pymupdf.open(stream=data, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
            if doc.needs_pass:
                raise ParseFailure("ENCRYPTED_PDF")
            if doc.is_repaired:
                raise ParseFailure("DAMAGED_PDF")
            if not 0 < len(doc) <= max_pages:
                raise ParseFailure("PAGE_LIMIT")
            for index, source in enumerate(doc):
                rect = source.rect
                if not all(math.isfinite(x) and 0 < x < 100_000 for x in (rect.width, rect.height)):
                    raise ParseFailure("PAGE_SIZE_LIMIT")
                page = page_info(index + 1, rect.width, rect.height, source.rotation)
                page["transform"] = list(source.rotation_matrix)
                for order, item in enumerate(source.get_text("blocks", sort=True)):
                    if item[6] != 0 or not item[4].strip():
                        continue
                    box = pymupdf.Rect(item[:4]) * source.rotation_matrix  # type: ignore[no-untyped-call]
                    coords = [
                        box.x0 / rect.width,
                        box.y0 / rect.height,
                        box.x1 / rect.width,
                        box.y1 / rect.height,
                    ]
                    coords = [max(0.0, min(1.0, c)) for c in coords]
                    page["blocks"].append(block(item[4], coords, index + 1, order))
                content = "".join(b["text"] for b in page["blocks"])
                # Conservative quality signal: sparse text or replacement characters need OCR.
                page["needs_ocr"] = (
                    len(content.strip()) < 12 or content.count("\ufffd") > len(content) * 0.05
                )
                image_area = sum(
                    max(0, info["bbox"][2] - info["bbox"][0])
                    * max(0, info["bbox"][3] - info["bbox"][1])
                    for info in source.get_image_info()
                )
                if image_area > rect.width * rect.height * 0.3 and len(content.strip()) < 80:
                    page["needs_ocr"] = True
                scale = min(
                    1.5,
                    1800 / max(rect.width, rect.height),
                    math.sqrt(max_pixels / (rect.width * rect.height)),
                )
                pix = source.get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale),  # type: ignore[no-untyped-call]
                    alpha=False,
                    annots=False,
                )
                pix.save(output / f"page-{index + 1}.png")
                page["image"] = True
                pages.append(page)
    return {
        "parser_version": PARSER_VERSION,
        "text_version": "unicode-source-v1",
        "text": text,
        "pages": pages,
        "needs_ocr": any(p["needs_ocr"] for p in pages),
    }


def main() -> None:
    import resource
    import socket

    # No adapters or network calls are available in this process.
    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Parser network disabled")

    socket.socket = no_network  # type: ignore[assignment,misc]
    resource.setrlimit(resource.RLIMIT_CPU, (170, 175))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    path, mime, limit, pixels = sys.argv[1:5]
    output = Path(path)
    try:
        result = parse((output / "input").read_bytes(), mime, output, int(limit), int(pixels))
        if len(sys.argv) > 5 and sys.argv[5] == "tesseract" and result["needs_ocr"]:
            from app.integrations.ocr.tesseract import apply_ocr

            apply_ocr(result, output, sys.argv[6], int(sys.argv[7]))
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        code = str(exc) if isinstance(exc, ParseFailure) else "INVALID_DOCUMENT"
        (output / "error.json").write_text(json.dumps({"code": code}))
        sys.exit(1)


if __name__ == "__main__":
    main()
