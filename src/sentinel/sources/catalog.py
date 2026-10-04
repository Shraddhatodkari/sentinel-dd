"""Catalog of external sources: authority, endpoint, licence and freshness SLA.

Only official/authoritative or clearly-licensed public sources are enabled by default. Sources whose licence
restricts commercial use are present as adapters but disabled unless explicitly licensed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDef:
    id: str
    name: str
    kind: str  # sanctions | pep | registry | filings | lei | media | enforcement
    authority: str
    url: str
    license: str
    freshness_sla_h: int | None = None
    enabled_by_default: bool = True


OFAC_BASE = "https://www.treasury.gov/ofac/downloads"

SOURCES: dict[str, SourceDef] = {
    s.id: s
    for s in [
        SourceDef(
            "ofac_sdn",
            "OFAC Specially Designated Nationals and Blocked Persons List (SDN)",
            "sanctions",
            "U.S. Department of the Treasury, Office of Foreign Assets Control",
            f"{OFAC_BASE}/sdn.csv",
            "U.S. Government work (public domain)",
            36,
        ),
        SourceDef(
            "un_sc",
            "United Nations Security Council Consolidated List",
            "sanctions",
            "United Nations Security Council",
            "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
            "United Nations public information",
            36,
        ),
        SourceDef(
            "uk_sanctions",
            "UK Sanctions List",
            "sanctions",
            "UK Foreign, Commonwealth & Development Office",
            "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv",
            "Open Government Licence v3.0",
            36,
        ),
        SourceDef(
            "eu_fsf",
            "EU Financial Sanctions Files (consolidated list)",
            "sanctions",
            "European Commission (DG FISMA)",
            "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
            "European Commission reuse policy (Decision 2011/833/EU)",
            36,
        ),
        SourceDef(
            "us_congress",
            "Current members of the U.S. Congress (domestic PEPs)",
            "pep",
            "@unitedstates/congress-legislators (compiled from official congressional sources)",
            "https://unitedstates.github.io/congress-legislators/legislators-current.json",
            "CC0 1.0 (public domain dedication)",
            24 * 14,
        ),
        SourceDef(
            "opensanctions_peps",
            "OpenSanctions Politically Exposed Persons",
            "pep",
            "OpenSanctions (aggregated from official sources)",
            "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv",
            "CC BY-NC 4.0 — commercial use requires an OpenSanctions licence",
            24 * 7,
            enabled_by_default=False,
        ),
        SourceDef(
            "sec_edgar",
            "SEC EDGAR (submissions, XBRL company facts, filings)",
            "filings",
            "U.S. Securities and Exchange Commission",
            "https://www.sec.gov/edgar",
            "U.S. Government work (public domain); SEC fair-access policy applies",
        ),
        SourceDef(
            "gleif",
            "GLEIF Global LEI Index (Level 1 & Level 2 relationship data)",
            "lei",
            "Global Legal Entity Identifier Foundation",
            "https://api.gleif.org/api/v1",
            "CC0 1.0",
        ),
        SourceDef(
            "doj_press",
            "U.S. Department of Justice press releases",
            "enforcement",
            "U.S. Department of Justice",
            "https://www.justice.gov/api/v1/press_releases.json",
            "U.S. Government work (public domain)",
        ),
        SourceDef(
            "gdelt",
            "GDELT DOC 2.0 global news index",
            "media",
            "The GDELT Project",
            "https://api.gdeltproject.org/api/v2/doc/doc",
            "Open access with attribution; article content belongs to publishers (only metadata is stored)",
        ),
    ]
}

WATCHLIST_SOURCES = ("ofac_sdn", "un_sc", "uk_sanctions", "eu_fsf", "us_congress", "opensanctions_peps")
SANCTIONS_SOURCES = ("ofac_sdn", "un_sc", "uk_sanctions", "eu_fsf")
