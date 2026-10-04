"""Timezone helpers (all persisted timestamps are UTC; SQLite returns naive values)."""

from __future__ import annotations

from datetime import UTC, datetime


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def iso(dt: datetime | None) -> str | None:
    a = aware(dt)
    return a.isoformat() if a else None


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_published(raw: object) -> str:
    """Normalise source publication dates to ISO-8601 (UTC).

    DOJ's API returns Unix epoch seconds (as int or str); GDELT returns ``YYYYMMDDTHHMMSSZ``. Unrecognised values are
    returned unchanged (never guessed).
    """
    s = str(raw or "").strip()
    if s.isdigit() and 9 <= len(s) <= 11:
        return datetime.fromtimestamp(int(s), UTC).isoformat()
    if len(s) == 16 and s[8] == "T" and s.endswith("Z"):
        try:
            return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
        except ValueError:
            return s
    return s
