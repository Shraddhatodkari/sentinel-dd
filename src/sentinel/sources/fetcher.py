"""Lineage-preserving fetcher: every external response becomes a ``SourceDocument`` (raw bytes in the
content-addressed store + URL, retrieval timestamp, SHA-256, HTTP status) before anything is parsed.

Responses fetched within ``cache_ttl_s`` are re-used from the raw store (idempotent, fair to upstream).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select

from sentinel.db.base import Database
from sentinel.db.models import SourceDocument
from sentinel.sources.http import ResilientHttpClient
from sentinel.sources.raw_store import RawStore
from sentinel.sources.replay import canonical_url


@dataclass(frozen=True)
class Fetched:
    document_id: str
    source_id: str
    url: str
    retrieved_at: datetime
    sha256: str
    content: bytes
    content_type: str
    from_cache: bool = False

    def json(self) -> Any:
        import json

        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8-sig", errors="replace")


class SourceFetcher:
    def __init__(self, db: Database, http: ResilientHttpClient, raw: RawStore, cache_ttl_s: int = 6 * 3600) -> None:
        self.db = db
        self.http = http
        self.raw = raw
        self.cache_ttl_s = cache_ttl_s

    def get(
        self,
        source_id: str,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        use_cache: bool = True,
        headers: dict[str, str] | None = None,
    ) -> Fetched:
        full = canonical_url(url + ("?" + urlencode(params) if params else ""))
        if use_cache and self.cache_ttl_s > 0:
            cutoff = datetime.now(UTC) - timedelta(seconds=self.cache_ttl_s)
            with self.db.session() as s:
                doc = s.scalars(
                    select(SourceDocument)
                    .where(
                        SourceDocument.source_id == source_id,
                        SourceDocument.url == full,
                        SourceDocument.retrieved_at >= cutoff,
                    )
                    .order_by(SourceDocument.retrieved_at.desc())
                    .limit(1)
                ).first()
            if doc is not None and self.raw.exists(doc.sha256):
                return Fetched(
                    doc.id,
                    source_id,
                    full,
                    _aware(doc.retrieved_at),
                    doc.sha256,
                    self.raw.get(doc.sha256),
                    doc.content_type,
                    from_cache=True,
                )
        res = self.http.get(full, headers=headers)
        return self.record(
            source_id,
            full,
            res.content,
            res.content_type,
            res.retrieved_at,
            res.status,
            {"elapsed_ms": res.elapsed_ms, "final_url": res.url},
        )

    def record(
        self,
        source_id: str,
        url: str,
        content: bytes,
        content_type: str,
        retrieved_at: datetime,
        status: int = 200,
        meta: dict[str, Any] | None = None,
    ) -> Fetched:
        sha, path = self.raw.put(content)
        with self.db.session() as s:
            existing = s.scalars(
                select(SourceDocument).where(
                    SourceDocument.source_id == source_id, SourceDocument.url == url, SourceDocument.sha256 == sha
                )
            ).first()
            if existing is None:
                existing = SourceDocument(
                    source_id=source_id,
                    url=url,
                    retrieved_at=retrieved_at,
                    sha256=sha,
                    content_type=content_type,
                    size_bytes=len(content),
                    storage_path=path,
                    http_status=status,
                    meta=meta or {},
                )
                s.add(existing)
                s.flush()
            else:  # same bytes re-retrieved: refresh the retrieval timestamp (freshness), keep one row
                existing.retrieved_at = retrieved_at
            doc_id = existing.id
        return Fetched(doc_id, source_id, url, retrieved_at, sha, content, content_type)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
