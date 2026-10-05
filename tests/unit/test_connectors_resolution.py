from sentinel.investigation.resolution import EntityResolver
from sentinel.sources.edgar import (
    financial_facts,
    html_to_text,
    name_variants,
    norm_company,
    parse_exhibit21,
    parse_form4,
)


def test_name_normalisation_and_variants():
    assert norm_company("The Boeing Company") == norm_company("BOEING CO") == "boeing"
    assert norm_company("Nestlé S.A.") == "nestle" and norm_company("Siemens Aktiengesellschaft") == "siemens"
    assert name_variants("Siemens AG") == ["Siemens AG", "Siemens Aktiengesellschaft"]
    assert name_variants("Apple") == ["Apple"]


def test_edgar_search_profile_facts_and_filings(container):
    ed = container.edgar
    cands = ed.search("Apple Inc.")
    assert cands[0].cik == 320193 and cands[0].score == 1.0
    assert ed.search("AAPL")[0].method == "ticker" and ed.search("320193")[0].method == "cik"
    p = ed.profile(320193)
    assert p.name == "Apple Inc." and "AAPL" in p.tickers and p.fetched is not None
    latest = ed.latest(p)
    assert latest["form"] == "10-K"
    facts = financial_facts(ed.company_facts(320193).json())
    assert facts["revenue"][0]["value"] > 1e11 and facts["revenue"][0]["accession"]
    subs, doc = ed.subsidiaries(320193, latest["accession"])
    assert doc is not None and any(s["name"] == "Braeburn Capital, Inc." for s in subs)
    insiders, errors = ed.insiders(p)
    assert insiders and not errors and all(i.url.startswith("https://www.sec.gov/Archives") for i in insiders)
    text, _doc = ed.filing_text(320193, latest)
    assert "Legal Proceedings" in text


def test_parsers_edge_cases():
    """Markup variants seen across filers, built from records in Apple's recorded filings (Exhibit 21 row,
    Form 4 reporting owner): header rows skipped, duplicate rows collapsed, boolean flags as "1" or "true"."""
    rows = parse_exhibit21(
        "<table><tr><td>Name of Subsidiary</td><td>Jurisdiction of Incorporation</td></tr>"
        "<tr><td>Apple Asia Limited</td><td>Hong Kong</td></tr><tr><td>Apple Asia Limited</td><td>Hong Kong</td></tr>"
        "</table>"
    )
    assert rows == [{"name": "Apple Asia Limited", "jurisdiction": "Hong Kong"}]
    xml = (
        "<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>COOK TIMOTHY D</rptOwnerName>"
        "</reportingOwnerId><reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>true</isOfficer>"
        "<officerTitle>Executive Chair</officerTitle></reportingOwnerRelationship></reportingOwner>"
        "</ownershipDocument>"
    )
    ins = parse_form4(xml, "2026-01-01", "acc", "doc", "url")
    assert ins[0].name == "COOK TIMOTHY D"
    assert ins[0].roles == ["director", "officer"] and ins[0].officer_title == "Executive Chair"
    assert html_to_text(b"<html><style>x{}</style><p>Hi</p><div style='display:none'>secret</div></html>") == "Hi"
    assert financial_facts({}) == {}


def test_gleif_search_lookup_and_relationships(container):
    gl = container.gleif
    hits = gl.search("Sberbank")
    assert hits[0].lei == "549300WE6TAF5EEWQS81" and "Sberbank of Russia" in hits[0].other_names
    rec = gl.by_lei("HWUPKR0MPOU8FGXBT394")
    assert rec.legal_name == "Apple Inc." and rec.registration_status == "ISSUED"
    assert gl.parent("HWUPKR0MPOU8FGXBT394", "ultimate") is None  # GLEIF: no reported parent (404)
    children, total = gl.children("HWUPKR0MPOU8FGXBT394", limit=25)
    assert total >= len(children) > 0


def test_resolution_paths(container):
    r = EntityResolver(container.db, container.edgar, container.gleif)
    apple, prof, lei = r.resolve("Apple Inc.")
    assert (apple.cik, apple.lei, apple.country, apple.confidence) == (320193, "HWUPKR0MPOU8FGXBT394", "US", 1.0)
    assert prof is not None and lei is not None and "APPLE COMPUTER INC" in apple.aliases
    again, _, _ = r.resolve("Apple Inc.")
    assert again.entity_id == apple.entity_id  # identifiers re-use the persisted entity
    sber, prof2, _ = r.resolve("Sberbank")
    assert prof2 is None and sber.lei == "549300WE6TAF5EEWQS81" and sber.country == "RU" and sber.method == "gleif_name"
    unknown, _, _ = r.resolve("Zzzq Nonexistent Holdings")
    assert not unknown.resolved and unknown.confidence == 0.0 and unknown.entity_id is None
