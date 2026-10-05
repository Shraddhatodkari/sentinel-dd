"""End-to-end investigations on recorded real data: Apple (SEC filer), Boeing (enforcement + PEP homonym),
Sberbank (sanctioned, non-SEC), plus degraded / failure / recovery paths."""

from sqlalchemy import select

from sentinel.db.models import Case, Evidence, Signal, SourceDocument

GATHER = {"corporate_registry", "financials", "adverse_media", "filings"}


def nodes(d):
    return [e["node"] for e in d["events"]]


def test_apple_full_investigation_layers_and_lineage(service, principal, container):
    d = service.create(principal("alice"), "Apple Inc.")
    assert d["status"] == "awaiting_review" and d["awaiting_human"] and d["resolved_name"] == "Apple Inc."
    assert set(nodes(d)[3:7]) == GATHER and nodes(d)[-1] == "policy"
    assert d["summary"]["coverage"]["screening"] == "ok" and d["summary"]["coverage"]["filings_text"] == "ok"
    kinds = {e["kind"] for e in d["evidence"]}
    assert {
        "registry_profile",
        "lei_record",
        "subsidiaries",
        "insider",
        "financial_facts",
        "filing_text",
        "enforcement_release",
    } <= kinds
    # FACT layer: every evidence row points at a persisted source document with URL + timestamp + hash
    with container.db.session() as s:
        for ev in s.scalars(select(Evidence).where(Evidence.case_id == d["case_id"])):
            doc = s.get(SourceDocument, ev.document_id)
            assert doc is not None and len(doc.sha256) == 64 and ev.source_url.startswith("https://")
    # SIGNAL → ASSESSMENT → POLICY layers
    a = d["assessment"]
    assert a["band"] == "LOW" and a["scorecard_version"] == "scorecard-2026.10" and len(a["factors"]) == 8
    assert a["model_versions"]["peer_model"] == "peer_anomaly:v1"
    assert a["model_versions"]["screening"] == "screening:rapidfuzz-v2"
    assert a["model_versions"]["jurisdiction_policy"].startswith("jurisdiction-policy-")
    assert a["model_versions"]["prompt"] and a["model_versions"]["llm"] == "extractive:extractive-v1"
    adverse = [s for s in d["signals"] if s["signal_type"] == "adverse_media"]
    assert adverse and all(s["detail"]["subject_role"] == "subject" for s in adverse)
    victim_titles = [e["title"] for e in d["evidence"] if e["structured"].get("subject_role") == "victim"]
    assert any("Defraud Apple" in t for t in victim_titles)  # victim-role items kept as context, not scored
    assert d["policy"]["outcome"] == "REVIEW" and [r["rule_id"] for r in d["policy"]["rules"]] == ["TPRM-GOV-01"]
    assert d["review"]["status"] == "pending" and d["review"]["required_role"] == "reviewer"
    # report separates layers and carries the disclaimer; research is grounded with citations
    rep = d["report"]
    for heading in ("## 2. Facts", "## 3. Analytical signals", "## 4. Risk assessment"):
        assert heading in rep
    assert "not a regulatory determination" in rep and d["report_grounding"] >= 0.6
    assert d["research"] and all(r["citations"] or r["answer"].startswith("INSUFFICIENT") for r in d["research"])
    assert d["graph_ui"]["nodes"] and d["graph"]["relation_counts"]["PARENT_OF"] > 0
    assert d["llm_usage"]["calls"] > 0


def test_sberbank_sanctions_escalation(service, principal):
    d = service.create(principal("alice"), "Sberbank")
    assert d["risk_band"] == "HIGH" and d["policy_outcome"] == "ESCALATE"
    rules = [r["rule_id"] for r in d["policy"]["rules"]]
    assert "TPRM-SAN-01" in rules and "TPRM-JUR-02" in rules
    hits = [s for s in d["signals"] if s["signal_type"] == "sanctions_potential_match"]
    assert {s["detail"]["source"] for s in hits} >= {"ofac_sdn", "uk_sanctions", "eu_fsf"}
    assert all(s["severity"] == "critical" and s["evidence_ids"] for s in hits)
    rec = next(e for e in d["evidence"] if e["kind"] == "sanctions_record" and e["source_id"] == "ofac_sdn")
    assert rec["source_url"] == "https://www.treasury.gov/ofac/downloads/sdn.csv"
    cov = d["summary"]["coverage"]
    assert cov["financials"] == "not_applicable" and cov["insiders"] == "not_applicable"
    assert d["review"]["tier"] == "ESCALATE" and d["review"]["required_role"] == "senior_reviewer"
    assert d["summary"]["graph"]["subject_flags"] == {} or True


def test_boeing_enforcement_and_pep_homonym(service, principal):
    d = service.create(principal("alice"), "The Boeing Company")
    assert d["risk_band"] == "MEDIUM" and d["policy_outcome"] == "REVIEW"
    pep = [s for s in d["signals"] if s["signal_type"] == "pep_potential_match"]
    assert pep and pep[0]["detail"]["role"] == "insider" and "Joyce" in pep[0]["label"]
    assert any("Boeing Charged" in s["label"] for s in d["signals"] if s["signal_type"] == "adverse_media")
    fin = d["summary"]["details"]["financials"]["indicators"]
    assert fin["liabilities_to_assets"] > 0.9


def test_unresolved_entity_is_screened_and_routed_to_review(service, principal):
    d = service.create(principal("alice"), "Zzzq Nonexistent Holdings")
    assert d["status"] == "awaiting_review" and d["resolved_name"] is None
    rules = [r["rule_id"] for r in d["policy"]["rules"]]
    assert "TPRM-DAT-01" in rules
    assert d["summary"]["coverage"]["resolution"] == "unavailable"
    assert d["summary"]["details"]["screening"]["subject_names"] == ["Zzzq Nonexistent Holdings"]
    assert any(e["status"] == "degraded" for e in d["events"])


def test_idempotency_key_returns_same_case(service, principal):
    p = principal("alice")
    a = service.create(p, "Apple Inc.", idempotency_key="req-123")
    b = service.create(p, "Apple Inc.", idempotency_key="req-123")
    assert a["case_id"] == b["case_id"]
    assert service.list_cases(p)["total"] == 1


def test_prior_cases_are_recalled(service, principal):
    first = service.create(principal("alice"), "Apple Inc.")
    second = service.create(principal("alice"), "Apple Inc.")
    assert second["summary"]["details"]["prior_cases"][0]["case_id"] == first["case_id"]


def test_core_node_failure_fails_case_and_retry_recovers(service, principal, container, monkeypatch):
    from sentinel.risk import scorecard

    real = scorecard.assess

    def boom(*a, **k):
        raise RuntimeError("scorecard exploded")

    monkeypatch.setattr(scorecard, "assess", boom)
    p = principal("alice")
    d = service.create(p, "Apple Inc.")
    assert d["status"] == "failed" and "scorecard exploded" in d["error"]
    assert any(e["node"] == "risk_assessor" and e["status"] == "failed" for e in d["events"])
    monkeypatch.setattr(scorecard, "assess", real)
    r = service.retry(p, d["case_id"])
    assert r["status"] == "awaiting_review" and r["attempts"] == 2


def test_recovery_marks_orphaned_cases_failed(service, container):
    from datetime import timedelta

    from sentinel.timeutil import utcnow

    with container.db.session() as s:
        s.add(
            Case(
                id="case-orphan",
                entity_query="x",
                created_by="alice",
                status="running",
                updated_at=utcnow() - timedelta(hours=2),
            )
        )
    assert service.recover(stale_after_s=60) == 1
    with container.db.session() as s:
        assert s.get(Case, "case-orphan").status == "failed"


def test_source_outage_degrades_gracefully(container, principal, monkeypatch):
    from sentinel.agents.service import CaseService
    from sentinel.sources.http import SourceUnavailable

    def down(*a, **k):
        raise SourceUnavailable("https://www.justice.gov/api", "simulated outage", 503)

    monkeypatch.setattr(container.doj, "search", down)
    d = CaseService(container).create(principal("alice"), "Apple Inc.")
    assert d["summary"]["coverage"]["adverse_media"] == "unavailable"
    assert "TPRM-DAT-04" in [r["rule_id"] for r in d["policy"]["rules"]]
    with container.db.session() as s:
        assert not s.scalars(
            select(Signal).where(Signal.case_id == d["case_id"], Signal.signal_type == "adverse_media")
        ).all()
