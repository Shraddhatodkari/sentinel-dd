"""Multimodal document loaders: Markdown/text (with front-matter), JSON, CSV tables, PDF, images (OCR).

Every loader produces normalised :class:`~sentinel.domain.Document` objects so downstream
processing (DLP, chunking, embedding, graph extraction) is modality-agnostic.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sentinel.domain import Document

SUPPORTED = {".md", ".txt", ".json", ".csv", ".pdf", ".png", ".jpg", ".jpeg"}


def _doc_id(path: Path, text: str) -> str:
    return path.stem.lower().replace(" ", "_") or hashlib.sha256(text.encode()).hexdigest()[:10]


def parse_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    """Parse a minimal ``---\\nkey: value\\n---`` header (no YAML dependency)."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    meta: dict[str, Any] = {}
    for line in raw[3:end].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            if v.lower() in ("true", "false"):
                meta[k.strip()] = v.lower() == "true"
            elif v.lstrip("-").isdigit():
                meta[k.strip()] = int(v)
            else:
                meta[k.strip()] = v.strip("\"'")
    return meta, raw[end + 4 :].lstrip("\n")


def load_text(path: Path) -> Document:
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(raw)
    title = str(meta.pop("title", path.stem.replace("_", " ").title()))
    meta.setdefault("source", path.name)
    meta.setdefault("modality", "text")
    return Document(str(meta.pop("id", _doc_id(path, body))), title, body, meta)


def load_json(path: Path) -> list[Document]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    return [
        Document(
            str(it.get("id", f"{path.stem}_{i}")),
            str(it.get("title", path.stem)),
            str(it["text"]),
            {**it.get("metadata", {}), "source": path.name, "modality": "text"},
        )
        for i, it in enumerate(items)
    ]


def load_csv(path: Path) -> Document:
    """Tables are linearised row-wise ("col: val; col: val") which embeds/retrieves far better
    than raw CSV while staying human-readable in citations."""
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    lines = ["; ".join(f"{k}: {v}" for k, v in r.items()) + "." for r in rows]
    return Document(
        _doc_id(path, ""),
        path.stem.replace("_", " ").title(),
        "\n\n".join(lines),
        {"source": path.name, "modality": "table", "rows": len(rows)},
    )


def load_pdf(path: Path) -> Document:  # pragma: no cover - optional dependency
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [f"## Page {i + 1}\n\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages)]
    return Document(
        _doc_id(path, ""), path.stem, "\n\n".join(pages), {"source": path.name, "modality": "pdf", "pages": len(pages)}
    )


def load_image(path: Path, captioner: Any | None = None) -> Document:  # pragma: no cover - optional
    """OCR text + optional vision-LLM caption (e.g. Claude vision) for scanned certificates/IDs."""
    from PIL import Image

    text = ""
    try:
        import pytesseract

        text = pytesseract.image_to_string(Image.open(path))
    except Exception:  # noqa: S110 - OCR engine optional
        pass
    caption = captioner(path) if captioner else ""
    body = "\n\n".join(x for x in (caption, text) if x)
    return Document(
        _doc_id(path, body), path.stem, body or "(no text extracted)", {"source": path.name, "modality": "image"}
    )


def load_path(path: str | Path) -> list[Document]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".md", ".txt"):
        return [load_text(p)]
    if suffix == ".json":
        return load_json(p)
    if suffix == ".csv":
        return [load_csv(p)]
    if suffix == ".pdf":  # pragma: no cover
        return [load_pdf(p)]
    if suffix in (".png", ".jpg", ".jpeg"):  # pragma: no cover
        return [load_image(p)]
    raise ValueError(f"unsupported file type: {suffix}")


def load_directory(directory: str | Path) -> Iterable[Document]:
    for p in sorted(Path(directory).rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            yield from load_path(p)
