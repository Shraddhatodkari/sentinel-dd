"""Record / replay transports.

``RecordingTransport`` captures real responses (with URL and retrieval timestamp) so that tests and offline
evaluation run against *real* source data snapshots. ``ReplayTransport`` serves them back and fails loudly
on any un-recorded request (no silent fabrication).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, query, ""))


def _key(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:24]


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, directory: str | Path, inner: httpx.BaseTransport | None = None) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.inner = inner or httpx.HTTPTransport()
        self._lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self.inner.handle_request(request)
        body = resp.read()
        key = _key(str(request.url))
        meta = {
            "url": canonical_url(str(request.url)),
            "status": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        with self._lock:
            (self.dir / f"{key}.body.gz").write_bytes(gzip.compress(body, 9))
            (self.dir / f"{key}.json").write_text(json.dumps(meta, indent=1))
        headers = {"content-type": meta["content_type"]}
        if loc := resp.headers.get("location"):
            headers["location"] = loc
        return httpx.Response(resp.status_code, headers=headers, content=body, request=request)


class UnrecordedRequest(Exception):
    pass


class ReplayTransport(httpx.BaseTransport):
    def __init__(self, directory: str | Path, strict: bool = True) -> None:
        self.dir = Path(directory)
        self.strict = strict
        self.requests: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(canonical_url(url))
        key = _key(url)
        meta_p, body_p = self.dir / f"{key}.json", self.dir / f"{key}.body.gz"
        if not meta_p.exists():
            if self.strict:
                raise UnrecordedRequest(f"no recorded response for {canonical_url(url)}")
            return httpx.Response(503, content=b"unrecorded", request=request)
        meta = json.loads(meta_p.read_text())
        headers = {"content-type": meta.get("content_type", "")}
        return httpx.Response(
            meta["status"], headers=headers, content=gzip.decompress(body_p.read_bytes()), request=request
        )


def write_cassette(directory: str | Path, url: str, body: bytes, content_type: str, **extra: object) -> None:
    """Add a response to a cassette directory (used for documented excerpts of very large reference files)."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    key = _key(url)
    meta = {
        "url": canonical_url(url),
        "status": 200,
        "content_type": content_type,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sha256": hashlib.sha256(body).hexdigest(),
        **extra,
    }
    (d / f"{key}.body.gz").write_bytes(gzip.compress(body, 9))
    (d / f"{key}.json").write_text(json.dumps(meta, indent=1))
