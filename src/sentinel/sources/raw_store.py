"""Content-addressed, write-once raw document store (filesystem; swap for S3 with Object Lock in prod)."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path


class RawStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sha: str) -> Path:
        return self.root / sha[:2] / sha[2:4] / f"{sha}.gz"

    def put(self, content: bytes) -> tuple[str, str]:
        sha = hashlib.sha256(content).hexdigest()
        path = self._path(sha)
        if not path.exists():  # idempotent: identical bytes are stored once
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(content, compresslevel=6))
            tmp.replace(path)  # atomic publish
        return sha, str(path.relative_to(self.root))

    def get(self, sha: str) -> bytes:
        data = gzip.decompress(self._path(sha).read_bytes())
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f"raw document {sha} failed integrity verification")
        return data

    def exists(self, sha: str) -> bool:
        return self._path(sha).exists()
