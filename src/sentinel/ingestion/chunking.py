"""Structure-aware recursive chunker.

Splits on headings -> paragraphs -> sentences, packs pieces up to ``chunk_size`` characters with
``overlap`` characters of trailing context, and prefixes each chunk with its nearest heading so
chunks remain self-describing (improves retrieval precision and citation readability).
"""

from __future__ import annotations

import re

from sentinel.domain import Chunk, Document

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.M)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _sections(text: str) -> list[tuple[str, str]]:
    """Return [(heading, body)] preserving order."""
    parts: list[tuple[str, str]] = []
    last_end, heading = 0, ""
    for m in _HEADING.finditer(text):
        body = text[last_end : m.start()].strip()
        if body:
            parts.append((heading, body))
        heading = m.group(2).strip()
        last_end = m.end()
    tail = text[last_end:].strip()
    if tail:
        parts.append((heading, tail))
    return parts


def _pieces(body: str, size: int) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = " ".join(para.split())
        if not para:
            continue
        if len(para) <= size:
            out.append(para)
            continue
        for sent in _SENT.split(para):
            while len(sent) > size:  # hard-wrap pathological sentences
                out.append(sent[:size])
                sent = sent[size:]
            if sent:
                out.append(sent)
    return out


def chunk_document(doc: Document, chunk_size: int = 800, overlap: int = 120) -> list[Chunk]:
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    chunks: list[Chunk] = []
    for heading, body in _sections(doc.text) or [("", doc.text)]:
        buf = ""
        for piece in _pieces(body, chunk_size):
            if buf and len(buf) + 1 + len(piece) > chunk_size:
                chunks.append(_make(doc, len(chunks), heading, buf))
                tail = buf[-overlap:]
                # start overlap at a word boundary
                buf = (tail[tail.find(" ") + 1 :] if " " in tail else tail) + " " + piece
            else:
                buf = f"{buf} {piece}".strip()
        if buf:
            chunks.append(_make(doc, len(chunks), heading, buf))
    return chunks


def _make(doc: Document, n: int, heading: str, text: str) -> Chunk:
    prefix = f"{doc.title} > {heading}: " if heading else f"{doc.title}: "
    meta = {**doc.metadata, "title": doc.title, "heading": heading, "position": n}
    return Chunk(id=f"{doc.id}#{n}", doc_id=doc.id, text=prefix + text, metadata=meta)
