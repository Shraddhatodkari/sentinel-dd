"""Record real source responses for the offline test/evaluation corpus.

Runs real investigations with a recording HTTP transport (no cache), so every SEC EDGAR, GLEIF, DOJ and GDELT
response used by the tests is a genuine capture with URL + retrieval timestamp + SHA-256.

    SENTINEL_SEC_USER_AGENT="Your Org compliance you@yourorg.com" \
    python scripts/record_fixtures.py --out tests/fixtures/http "Apple Inc." "The Boeing Company" "Sberbank"
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from sentinel.config import Settings
from sentinel.container import Container
from sentinel.ml.train import run as train_peer
from sentinel.sources.replay import RecordingTransport


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/fixtures/http")
    ap.add_argument("--peer-year", type=int, default=2024)
    ap.add_argument("entities", nargs="+")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        s = Settings(
            _env_file=None,
            data_dir=Path(tmp),
            database_url=f"sqlite:///{tmp}/rec.db",
            checkpoint_url=f"{tmp}/ck.sqlite",
            runner_mode="inline",
            source_cache_ttl_s=0,
            sec_user_agent=os.environ["SENTINEL_SEC_USER_AGENT"],
        )
        c = Container.build(s, transport=RecordingTransport(a.out))
        print(train_peer(c.fetcher, c.registry, a.peer_year).to_dict())
        from sentinel.agents.service import CaseService
        from sentinel.security.auth import Principal, Role

        svc = CaseService(c)
        p = Principal("recorder", frozenset({Role.ANALYST}))
        for e in a.entities:
            d = svc.create(p, e)
            print(e, d["status"], d.get("risk_band"), d.get("error"))
        svc.shutdown()
        c.close()


if __name__ == "__main__":
    main()
