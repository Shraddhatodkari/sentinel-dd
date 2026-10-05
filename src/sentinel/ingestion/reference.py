"""Reference-data ingestion (sanctions & PEP lists): fetch → lineage → parse → validate → versioned load.

Properties:
* **Idempotent** — an unchanged file (same SHA-256) does not create a new version; freshness is updated.
* **Transactional** — the new version replaces the old one atomically; on any failure the previous version
  stays current (graceful degradation) and the run is recorded as failed with the error.
* **Quality-gated** — runs whose record count drops > ``max_shrink`` vs. the current version are rejected
  (protects against truncated downloads silently removing designated parties).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update

from sentinel.db.base import Database
from sentinel.db.models import IngestionRun, Source, SourceDocument, WatchlistEntry, WatchlistName, new_id
from sentinel.observability.logging import get_logger, log_event
from sentinel.observability.metrics import METRICS
from sentinel.screening import normalize_name
from sentinel.sources.catalog import OFAC_BASE, SOURCES, WATCHLIST_SOURCES
from sentinel.sources.fetcher import SourceFetcher
from sentinel.sources.watchlists import PARSERS, parse_ofac_sdn, validate_records

log = get_logger(__name__)


@dataclass
class RunSummary:
    run_id: str
    source_id: str
    status: str
    unchanged: bool
    records_loaded: int
    records_rejected: int
    document_id: str | None
    error: str | None
    quality: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sync_source_catalog(
    db: Database, opensanctions_enabled: bool = False, sla_overrides: dict[str, int] | None = None
) -> None:
    """Upsert the source catalogue. ``sla_overrides`` maps a source kind (sanctions, pep) to a freshness SLA in
    hours (``SENTINEL_SANCTIONS_MAX_AGE_H`` / ``SENTINEL_PEP_MAX_AGE_H``)."""
    with db.session() as s:
        for sd in SOURCES.values():
            sla = (sla_overrides or {}).get(sd.kind, sd.freshness_sla_h) if sd.freshness_sla_h else None
            row = s.get(Source, sd.id)
            enabled = sd.enabled_by_default or (sd.id == "opensanctions_peps" and opensanctions_enabled)
            if row is None:
                s.add(
                    Source(
                        id=sd.id,
                        name=sd.name,
                        kind=sd.kind,
                        authority=sd.authority,
                        url=sd.url,
                        license=sd.license,
                        freshness_sla_h=sla,
                        enabled=enabled,
                    )
                )
            else:
                row.name, row.kind, row.authority, row.url = sd.name, sd.kind, sd.authority, sd.url
                row.license, row.freshness_sla_h, row.enabled = sd.license, sla, enabled


class ReferenceDataService:
    def __init__(self, db: Database, fetcher: SourceFetcher, max_shrink: float = 0.2, keep_versions: int = 3) -> None:
        self.db = db
        self.fetcher = fetcher
        self.max_shrink = max_shrink
        self.keep_versions = keep_versions

    def refresh_all(self, triggered_by: str = "system") -> list[RunSummary]:
        out = []
        with self.db.session() as s:
            enabled = [r.id for r in s.scalars(select(Source).where(Source.id.in_(WATCHLIST_SOURCES), Source.enabled))]
        for sid in enabled:
            out.append(self.refresh(sid, triggered_by))
        return out

    def refresh(self, source_id: str, triggered_by: str = "system") -> RunSummary:
        sd = SOURCES[source_id]
        with self.db.session() as s:
            run = IngestionRun(source_id=source_id, kind="reference", status="running", triggered_by=triggered_by)
            s.add(run)
            s.flush()
            run_id = run.id
        try:
            doc = self.fetcher.get(source_id, sd.url, use_cache=False)
            extra_docs: dict[str, str] = {}
            if source_id == "ofac_sdn":
                alt = self.fetcher.get(source_id, f"{OFAC_BASE}/alt.csv", use_cache=False)
                extra_docs["alt.csv"] = alt.document_id
                records = parse_ofac_sdn(doc.content, alt.content)
                version_key = doc.sha256 + alt.sha256
            else:
                records = PARSERS[source_id](doc.content)
                version_key = doc.sha256
            good, quality = validate_records(records)
            quality |= {"documents": {"main": doc.document_id, **extra_docs}, "version_key": version_key[:16]}
            with self.db.session() as s:
                current = s.execute(
                    select(WatchlistEntry.document_id, func.count())
                    .where(WatchlistEntry.source_id == source_id, WatchlistEntry.is_current.is_(True))
                    .group_by(WatchlistEntry.document_id)
                ).first()
                last_ok = s.scalars(
                    select(IngestionRun)
                    .where(
                        IngestionRun.source_id == source_id,
                        IngestionRun.status == "succeeded",
                        IngestionRun.kind == "reference",
                    )
                    .order_by(IngestionRun.finished_at.desc())
                    .limit(1)
                ).first()
                unchanged = bool(current and last_ok and (last_ok.quality or {}).get("version_key") == version_key[:16])
                if current and not unchanged and len(good) < current[1] * (1 - self.max_shrink):
                    raise ValueError(
                        f"quality gate: record count fell from {current[1]} to {len(good)} "
                        f"(> {int(self.max_shrink * 100)}% shrink) — refusing to replace current list"
                    )
                if not unchanged:
                    self._load_version(s, source_id, doc.document_id, good)
                done = s.get(IngestionRun, run_id)
                assert done is not None
                done.status, done.finished_at, done.document_id = "succeeded", datetime.now(UTC), doc.document_id
                done.records_in, done.records_loaded = quality["records_in"], 0 if unchanged else len(good)
                done.records_rejected = quality["rejected_no_name"] + quality["duplicates_dropped"]
                done.unchanged, done.quality = unchanged, quality
                summary = RunSummary(
                    run_id,
                    source_id,
                    "succeeded",
                    unchanged,
                    done.records_loaded,
                    done.records_rejected,
                    doc.document_id,
                    None,
                    quality,
                )
            METRICS.inc("reference_refresh", source=source_id, outcome="unchanged" if unchanged else "loaded")
            log_event(log, "reference_refresh", source=source_id, unchanged=unchanged, records=len(good))
            return summary
        except Exception as exc:  # recorded, surfaced, never swallowed: previous version remains current
            with self.db.session() as s:
                failed = s.get(IngestionRun, run_id)
                assert failed is not None
                failed.status, failed.finished_at, failed.error = (
                    "failed",
                    datetime.now(UTC),
                    f"{type(exc).__name__}: {exc}"[:2000],
                )
            METRICS.inc("reference_refresh", source=source_id, outcome="failed")
            log_event(log, "reference_refresh_failed", source=source_id, error=str(exc))
            return RunSummary(run_id, source_id, "failed", False, 0, 0, None, str(exc), {})

    def _load_version(self, s: Any, source_id: str, document_id: str, records: list[Any]) -> None:
        category = SOURCES[source_id].kind
        s.execute(
            update(WatchlistEntry)
            .where(WatchlistEntry.source_id == source_id, WatchlistEntry.is_current.is_(True))
            .values(is_current=False)
        )
        entry_rows, name_rows = [], []
        for r in records:
            eid = new_id()
            entry_rows.append(
                dict(
                    id=eid,
                    source_id=source_id,
                    document_id=document_id,
                    external_id=r.external_id[:64],
                    category="pep" if category == "pep" else "sanctions",
                    entity_type=r.entity_type,
                    primary_name=r.primary_name[:500],
                    programs=r.programs,
                    countries=r.countries[:20],
                    identifiers=r.identifiers[:20],
                    remarks=r.remarks,
                    listed_on=r.listed_on,
                    is_current=True,
                )
            )
            for name, kind in r.names[:60]:
                name_rows.append(
                    dict(entry_id=eid, name=name[:500], normalized=normalize_name(name)[:500], name_type=kind)
                )
        for i in range(0, len(entry_rows), 2000):
            s.execute(insert(WatchlistEntry), entry_rows[i : i + 2000])
        for i in range(0, len(name_rows), 5000):
            s.execute(insert(WatchlistName), name_rows[i : i + 5000])
        # retention: keep the last N versions for lineage, drop older ones
        versions = [
            d
            for (d,) in s.execute(
                select(WatchlistEntry.document_id)
                .join(SourceDocument, SourceDocument.id == WatchlistEntry.document_id)
                .where(WatchlistEntry.source_id == source_id)
                .group_by(WatchlistEntry.document_id)
                .order_by(func.max(SourceDocument.retrieved_at))
            ).all()
        ]
        if len(versions) > self.keep_versions:
            old_docs = [d for d in versions if d != document_id][: len(versions) - self.keep_versions]
            old_ids = select(WatchlistEntry.id).where(
                WatchlistEntry.source_id == source_id, WatchlistEntry.document_id.in_(old_docs)
            )
            s.execute(delete(WatchlistName).where(WatchlistName.entry_id.in_(old_ids)))
            s.execute(
                delete(WatchlistEntry).where(
                    WatchlistEntry.source_id == source_id, WatchlistEntry.document_id.in_(old_docs)
                )
            )


def freshness(db: Database) -> list[dict[str, Any]]:
    """Per-source freshness/health: last successful retrieval, age vs. SLA, last run status."""
    now = datetime.now(UTC)
    out = []
    with db.session() as s:
        for src in s.scalars(select(Source).order_by(Source.kind, Source.id)):
            last_ok = s.scalars(
                select(IngestionRun)
                .where(IngestionRun.source_id == src.id, IngestionRun.status == "succeeded")
                .order_by(IngestionRun.finished_at.desc())
                .limit(1)
            ).first()
            last_run = s.scalars(
                select(IngestionRun)
                .where(IngestionRun.source_id == src.id)
                .order_by(IngestionRun.started_at.desc())
                .limit(1)
            ).first()
            last_doc = s.scalars(
                select(SourceDocument)
                .where(SourceDocument.source_id == src.id)
                .order_by(SourceDocument.retrieved_at.desc())
                .limit(1)
            ).first()
            records = (
                s.scalar(
                    select(func.count())
                    .select_from(WatchlistEntry)
                    .where(WatchlistEntry.source_id == src.id, WatchlistEntry.is_current.is_(True))
                )
                or 0
            )
            retrieved = _aware(last_doc.retrieved_at) if last_doc else None
            if src.freshness_sla_h and last_ok is not None and last_ok.document_id:
                doc = s.get(SourceDocument, last_ok.document_id)
                retrieved = _aware(doc.retrieved_at) if doc else retrieved
            age_h = round((now - retrieved).total_seconds() / 3600, 2) if retrieved else None
            stale = bool(src.freshness_sla_h and (age_h is None or age_h > src.freshness_sla_h))
            out.append(
                {
                    "source_id": src.id,
                    "name": src.name,
                    "kind": src.kind,
                    "authority": src.authority,
                    "license": src.license,
                    "enabled": src.enabled,
                    "url": src.url,
                    "freshness_sla_h": src.freshness_sla_h,
                    "last_retrieved_at": retrieved.isoformat() if retrieved else None,
                    "age_hours": age_h,
                    "stale": stale if src.enabled else False,
                    "current_records": records,
                    "last_run": None
                    if last_run is None
                    else {
                        "status": last_run.status,
                        "started_at": _aware(last_run.started_at).isoformat(),
                        "unchanged": last_run.unchanged,
                        "records_loaded": last_run.records_loaded,
                        "error": last_run.error,
                        "quality": last_run.quality,
                    },
                }
            )
    return out


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
