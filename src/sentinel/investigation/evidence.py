"""Evidence, relationship and signal persistence for a case.

Evidence is always tied to the ``SourceDocument`` it was extracted from (URL, retrieval timestamp, hash) and
carries a locator (JSON path / XBRL concept / section / list record id) so a reviewer can verify it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from sentinel.db.base import Database
from sentinel.db.models import Evidence, Relationship, Signal, SourceDocument
from sentinel.sources.fetcher import Fetched


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    kind: str
    title: str
    source_id: str
    source_url: str


class EvidenceWriter:
    def __init__(self, db: Database, case_id: str) -> None:
        self.db = db
        self.case_id = case_id

    def add(
        self,
        doc: Fetched | str,
        kind: str,
        title: str,
        content: str,
        *,
        structured: dict[str, Any] | None = None,
        locator: str = "",
        confidence: float = 1.0,
        quality: dict[str, Any] | None = None,
        source_url: str | None = None,
        source_id: str | None = None,
    ) -> EvidenceRef:
        """``doc`` is the SourceDocument (or its id) the evidence was extracted from. Source id, URL and retrieval
        timestamp are always taken from the persisted SourceDocument row — never supplied by the caller."""
        structured = structured or {}
        doc_id = doc if isinstance(doc, str) else doc.document_id
        h = hashlib.sha256(
            json.dumps([kind, title, content, structured, locator], sort_keys=True, default=str).encode()
        ).hexdigest()
        with self.db.session() as s:
            existing = s.scalars(
                select(Evidence).where(Evidence.case_id == self.case_id, Evidence.content_hash == h)
            ).first()
            if existing is not None:  # idempotent re-run of a node
                return EvidenceRef(existing.id, existing.kind, existing.title, existing.source_id, existing.source_url)
            src_doc = s.get(SourceDocument, doc_id)
            if src_doc is None:
                raise ValueError(f"evidence must reference a persisted source document (got {doc_id!r})")
            ev = Evidence(
                case_id=self.case_id,
                document_id=doc_id,
                source_id=source_id or src_doc.source_id,
                kind=kind,
                title=title[:500],
                content=content,
                structured=structured,
                locator=locator[:500],
                source_url=(source_url or src_doc.url)[:1000],
                retrieved_at=src_doc.retrieved_at,
                confidence=max(0.0, min(1.0, confidence)),
                quality=quality or {},
                content_hash=h,
            )
            s.add(ev)
            s.flush()
            return EvidenceRef(ev.id, kind, ev.title, ev.source_id, ev.source_url)

    def relate(
        self,
        subject: str,
        subject_type: str,
        relation: str,
        obj: str,
        object_type: str,
        evidence_id: str,
        **attributes: Any,
    ) -> None:
        with self.db.session() as s:
            s.add(
                Relationship(
                    case_id=self.case_id,
                    subject=subject[:500],
                    subject_type=subject_type,
                    relation=relation,
                    object=obj[:500],
                    object_type=object_type,
                    attributes=attributes,
                    evidence_id=evidence_id,
                )
            )

    def signal(
        self,
        signal_type: str,
        label: str,
        value: float,
        severity: str,
        confidence: float,
        evidence_ids: list[str],
        derived_by: str,
        **detail: Any,
    ) -> None:
        with self.db.session() as s:
            s.add(
                Signal(
                    case_id=self.case_id,
                    signal_type=signal_type,
                    label=label[:500],
                    value=float(value),
                    severity=severity,
                    confidence=max(0.0, min(1.0, confidence)),
                    evidence_ids=sorted(set(evidence_ids)),
                    derived_by=derived_by,
                    detail=detail,
                )
            )

    def evidence(self, kinds: tuple[str, ...] | None = None) -> list[Evidence]:
        with self.db.session() as s:
            q = select(Evidence).where(Evidence.case_id == self.case_id)
            if kinds:
                q = q.where(Evidence.kind.in_(kinds))
            return list(s.scalars(q.order_by(Evidence.retrieved_at)))

    def signals(self) -> list[Signal]:
        with self.db.session() as s:
            return list(s.scalars(select(Signal).where(Signal.case_id == self.case_id).order_by(Signal.id)))

    def relationships(self) -> list[Relationship]:
        with self.db.session() as s:
            return list(s.scalars(select(Relationship).where(Relationship.case_id == self.case_id)))
