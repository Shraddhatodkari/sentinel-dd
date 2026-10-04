"""Browser end-to-end test of the analyst dashboard (React build served by the FastAPI app).

Drives a real Chromium through the full workflow against recorded real source data: analyst opens investigations,
analysts cannot decide reviews, reviewer approves, senior reviewer rejects an escalated case, auditor verifies the
audit chain. Fails on any browser console error (including CSP violations).

Skipped when the dashboard has not been built (``npm --prefix dashboard run build``) or Playwright/Chromium is absent.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from sentinel.api.main import create_app

DIST = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
PW = "Correct-Horse-9!x"

playwright_sync = pytest.importorskip("playwright.sync_api")
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not (DIST / "index.html").exists(), reason="dashboard not built"),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def server(container) -> Iterator[str]:
    import uvicorn

    port = _free_port()
    app = create_app(container, dashboard_dir=DIST)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 20
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=10)


@pytest.fixture
def browser():
    exe = os.environ.get("SENTINEL_CHROMIUM") or None
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment without a browser
            pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


class Session:
    def __init__(self, browser, base: str) -> None:
        self.ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(60_000)
        self.base = base
        self.errors: list[str] = []
        self.page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def login(self, user: str) -> None:
        self.page.goto(self.base + "/login")
        self.page.get_by_label("Username").fill(user)
        self.page.get_by_label("Password").fill(PW)
        self.page.get_by_role("button", name="Sign in").click()
        self.page.get_by_role("heading", name="Executive risk overview").wait_for()

    def shot(self, name: str) -> None:
        """Optional screenshots for documentation/review: set SENTINEL_E2E_SCREENSHOTS=<dir>."""
        out = os.environ.get("SENTINEL_E2E_SCREENSHOTS")
        if out:
            Path(out).mkdir(parents=True, exist_ok=True)
            self.page.wait_for_timeout(300)
            self.page.screenshot(path=str(Path(out) / f"{name}.png"), full_page=True)

    def close(self) -> None:
        # 401/403 responses are expected in some steps and logged by the browser as resource errors; anything else
        # (script errors, CSP violations) fails the test.
        real = [e for e in self.errors if "status of 40" not in e]
        self.ctx.close()
        assert not real, real


def _open_case(s: Session, query: str) -> str:
    p = s.page
    p.get_by_label("Main").get_by_role("link", name="New investigation").click()
    p.get_by_label("Legal entity name").fill(query)
    p.get_by_role("button", name="Start investigation").click()
    p.wait_for_url("**/cases/case-*")
    p.get_by_test_id("risk-score").wait_for()
    return p.url.rsplit("/", 1)[-1]


def test_dashboard_end_to_end(server, browser):
    # --- analyst: two real-data investigations; analysts cannot decide --------------------------------------------
    a = Session(browser, server)
    a.login("alice")
    sber = _open_case(a, "Sberbank")
    p = a.page
    assert "Сбербанк" in p.get_by_test_id("case-title").inner_text()  # GLEIF legal name (Russian)
    assert "Sberbank" in p.get_by_test_id("case-query").inner_text()
    chain = p.get_by_test_id("decision-chain").inner_text()
    assert "HIGH" in chain and "ESCALATE" in chain
    a.shot("case-overview-sberbank")
    assert "cannot record review decisions" in p.get_by_test_id("review-blocked").inner_text()
    p.get_by_role("tab", name="Sanctions & PEP").click()
    p.get_by_text("Sanctions potential matches").wait_for()
    assert "ofac_sdn" in p.locator("body").inner_text()
    a.shot("case-screening-sberbank")
    p.get_by_role("tab", name="Evidence").click()
    assert p.get_by_test_id("evidence-item").count() > 0
    p.get_by_test_id("evidence-item").first.locator("summary").click()
    p.get_by_role("button", name="Lineage & integrity").first.click()
    p.get_by_text("raw copy verified").first.wait_for()
    a.shot("case-evidence-lineage")
    p.get_by_role("tab", name="Report").click()
    assert "Sberbank" in p.get_by_test_id("report").inner_text()
    a.shot("case-report")
    p.get_by_role("tab", name="Agent trace").click()
    assert "Resolve Entity" in p.get_by_test_id("trace").inner_text()
    a.shot("case-agent-trace")

    apple = _open_case(a, "Apple Inc.")
    p.get_by_role("tab", name="Ownership & UBO").click()
    assert p.locator(".graph svg circle").count() > 1
    a.shot("case-ownership-graph")
    p.get_by_role("tab", name="Risk scorecard").click()
    assert p.get_by_test_id("factors").locator("tbody tr").count() >= 5
    a.shot("case-risk-scorecard")
    p.get_by_label("Main").get_by_role("link", name="Case explorer").click()
    p.get_by_text(sber).wait_for()
    p.get_by_text(apple).wait_for()
    a.shot("case-explorer")
    p.get_by_label("Main").get_by_role("link", name="Overview").click()
    p.get_by_text("Risk band distribution").wait_for()
    a.shot("overview")
    a.close()

    # --- reviewer: can approve the REVIEW-tier case, cannot decide the ESCALATE-tier one ----------------------------
    b = Session(browser, server)
    b.login("bob")
    p = b.page
    p.get_by_label("Main").get_by_role("link", name="Review queue").click()
    table = p.get_by_test_id("review-table")
    table.wait_for()
    assert "not eligible" in table.locator("tr", has_text=sber).inner_text()
    b.shot("review-queue")
    table.locator("tr", has_text=apple).click()
    p.get_by_label("Decision rationale").fill("Reviewed SEC filings and screening; no confirmed adverse findings.")
    p.get_by_role("button", name="Approve relationship").click()
    rec = p.get_by_test_id("decision-record")
    rec.wait_for()
    assert "APPROVED" in rec.inner_text() and "bob" in rec.inner_text()
    b.close()

    # --- senior reviewer: rejects the escalated case -----------------------------------------------------------------
    s = Session(browser, server)
    s.login("sara")
    p = s.page
    p.goto(f"{server}/cases/{sber}")
    p.get_by_label("Decision rationale").fill(
        "Subject is designated on OFAC SDN and EU lists; relationship prohibited."
    )
    p.get_by_role("button", name="Reject relationship").click()
    rec = p.get_by_test_id("decision-record")
    rec.wait_for()
    assert "REJECTED" in rec.inner_text() and "senior_reviewer" in rec.inner_text()
    s.close()

    # --- auditor: sees the decisions in the audit trail and verifies the hash chain ----------------------------------
    au = Session(browser, server)
    au.login("carol")
    p = au.page
    p.get_by_label("Main").get_by_role("link", name="Audit trail").click()
    audit = p.get_by_test_id("audit-table").inner_text()
    assert "review.approved" in audit and "review.rejected" in audit
    p.get_by_role("button", name="Verify chain integrity").click()
    assert "Chain intact" in p.get_by_test_id("verify-result").inner_text()
    au.shot("audit-trail")
    p.get_by_label("Main").get_by_role("link", name="Governance").click()
    assert "tprm-policy" in p.get_by_test_id("versions").inner_text()
    au.shot("governance")
    p.get_by_label("Main").get_by_role("link", name="Data sources").click()
    assert "OFAC" in p.get_by_test_id("sources-table").inner_text()
    au.shot("data-sources")
    au.close()
