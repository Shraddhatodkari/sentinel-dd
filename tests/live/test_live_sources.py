"""Live smoke tests against the real upstream sources (opt-in: ``pytest -m live``; scheduled in CI).

They detect upstream format or access changes that the offline (recorded) suite cannot. They assert structure and
plausible magnitudes, never specific list contents, because the lists change daily.
"""

from __future__ import annotations

import os

import pytest

from sentinel.config import Settings
from sentinel.container import Container

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    d = tmp_path_factory.mktemp("live")
    ua = os.environ.get("SENTINEL_SEC_USER_AGENT")
    if not ua:  # SEC rejects requests without a real contact in the User-Agent (fair-access policy)
        pytest.skip("set SENTINEL_SEC_USER_AGENT to a real contact to run live tests")
    c = Container.build(
        Settings(
            _env_file=None,
            env="test",
            data_dir=d,
            database_url=f"sqlite:///{d}/s.db",
            checkpoint_url=f"{d}/c.sqlite",
            runner_mode="inline",
            sec_user_agent=ua,
        )
    )
    yield c
    c.close()


def test_official_watchlists_download_and_parse(live):
    for source_id, minimum in (("ofac_sdn", 5000), ("un_sc", 500), ("uk_sanctions", 2000), ("eu_fsf", 2000)):
        run = live.reference.refresh(source_id, "live-test")
        assert run.status == "succeeded", (source_id, run.error)
        assert run.records_loaded >= minimum, (source_id, run.records_loaded)


def test_edgar_and_gleif_resolution(live):
    res, profile, lei = live.resolver.resolve("Apple Inc.")
    assert res.cik == 320193 and res.lei == "HWUPKR0MPOU8FGXBT394"
    assert profile is not None and lei is not None


def test_doj_press_release_search(live):
    items = live.doj.search("Boeing")
    assert items and all(i.url.startswith("https://www.justice.gov/") for i in items)
    assert all(i.published[:2] in ("19", "20") for i in items if i.published)
