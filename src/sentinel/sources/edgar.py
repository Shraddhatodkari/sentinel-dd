"""SEC EDGAR connector: entity resolution (ticker map + full-text search), company profile (submissions API),
XBRL company facts, filing documents, Exhibit 21 subsidiaries and Form 4 insiders (directors/officers/10% owners).

All requests go through :class:`SourceFetcher`, so each response is persisted with URL + timestamp + hash.
"""

from __future__ import annotations

import re
import unicodedata
import warnings
import xml.etree.ElementTree as ET  # types/ParseError only; parsing goes through defusedxml
from dataclasses import dataclass, field
from typing import Any

import defusedxml.ElementTree as SafeET
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from rapidfuzz import fuzz

from sentinel.sources.fetcher import Fetched, SourceFetcher
from sentinel.sources.http import SourceError, SourceNotFound

SRC = "sec_edgar"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

_SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|lp|llp|holdings?|group|the|"
    r"sa|s\.a|ag|aktiengesellschaft|gmbh|kgaa|se|nv|n\.v|bv|b\.v|spa|s\.p\.a|societe anonyme|ab|asa|oyj|as|"
    r"pjsc|ojsc|jsc|pao|oao|public joint stock company|joint stock company)\b\.?",
    re.I,
)


_LEGAL_EXPANSIONS = {
    "ag": "Aktiengesellschaft",
    "se": "SE",
    "plc": "Public Limited Company",
    "sa": "S.A.",
    "nv": "N.V.",
    "spa": "S.p.A.",
    "gmbh": "Gesellschaft mit beschränkter Haftung",
}


def norm_company(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace("&", " ")
    n = _SUFFIX.sub(" ", n)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", n).split())


def name_variants(name: str) -> list[str]:
    """Legal-form expansions used for registry search (e.g. 'Siemens AG' → 'Siemens Aktiengesellschaft')."""
    out = [name]
    toks = name.replace(".", "").split()
    if len(toks) >= 2 and toks[-1].lower() in _LEGAL_EXPANSIONS:
        out.append(" ".join([*name.split()[:-1], _LEGAL_EXPANSIONS[toks[-1].lower()]]))
    return out


@dataclass
class EdgarCandidate:
    cik: int
    name: str
    ticker: str | None
    score: float
    method: str
    document_id: str


@dataclass
class Insider:
    name: str
    roles: list[str]
    officer_title: str | None
    filing_date: str
    accession: str
    document_id: str
    url: str


@dataclass
class EdgarProfile:
    cik: int
    name: str
    sic: str
    sic_description: str
    state_of_incorporation: str
    tickers: list[str]
    exchanges: list[str]
    ein: str | None
    lei: str | None
    category: str
    fiscal_year_end: str
    business_address: dict[str, Any]
    former_names: list[dict[str, Any]]
    filings: list[dict[str, str]] = field(default_factory=list)
    fetched: Fetched | None = None


class EdgarConnector:
    def __init__(self, fetcher: SourceFetcher) -> None:
        self.f = fetcher

    # ---------------------------------------------------------------- resolution
    def search(self, query: str, limit: int = 5) -> list[EdgarCandidate]:
        doc = self.f.get(SRC, TICKERS_URL)
        rows = list(doc.json().values())
        q = query.strip()
        out: list[EdgarCandidate] = []
        if q.isdigit():
            out += [
                EdgarCandidate(int(r["cik_str"]), r["title"], r["ticker"], 1.0, "cik", doc.document_id)
                for r in rows
                if int(r["cik_str"]) == int(q)
            ][:1]
        tick = [r for r in rows if r["ticker"].upper() == q.upper()]
        out += [
            EdgarCandidate(int(r["cik_str"]), r["title"], r["ticker"], 1.0, "ticker", doc.document_id) for r in tick
        ]
        nq = norm_company(q)
        if nq:
            scored = []
            for r in rows:
                s = fuzz.token_sort_ratio(nq, norm_company(r["title"])) / 100
                if s >= 0.80:
                    scored.append(
                        EdgarCandidate(int(r["cik_str"]), r["title"], r["ticker"], round(s, 3), "name", doc.document_id)
                    )
            scored.sort(key=lambda c: -c.score)
            out += scored
        dedup: dict[int, EdgarCandidate] = {}
        for c in out:
            if c.cik not in dedup or c.score > dedup[c.cik].score:
                dedup[c.cik] = c
        return sorted(dedup.values(), key=lambda c: -c.score)[:limit]

    # ---------------------------------------------------------------- profile & filings
    def profile(self, cik: int) -> EdgarProfile:
        doc = self.f.get(SRC, SUBMISSIONS_URL.format(cik=cik))
        d = doc.json()
        recent = d.get("filings", {}).get("recent", {})
        filings = [
            {
                "form": recent["form"][i],
                "accession": recent["accessionNumber"][i],
                "date": recent["filingDate"][i],
                "primary_document": recent["primaryDocument"][i],
            }
            for i in range(min(len(recent.get("form", [])), 400))
        ]
        return EdgarProfile(
            cik=int(d["cik"]),
            name=d.get("name", ""),
            sic=d.get("sic", ""),
            sic_description=d.get("sicDescription", ""),
            state_of_incorporation=d.get("stateOfIncorporation") or "",
            tickers=d.get("tickers") or [],
            exchanges=d.get("exchanges") or [],
            ein=d.get("ein"),
            lei=d.get("lei"),
            category=d.get("category") or "",
            fiscal_year_end=d.get("fiscalYearEnd") or "",
            business_address=(d.get("addresses") or {}).get("business") or {},
            former_names=d.get("formerNames") or [],
            filings=filings,
            fetched=doc,
        )

    def company_facts(self, cik: int) -> Fetched:
        return self.f.get(SRC, FACTS_URL.format(cik=cik))

    @staticmethod
    def latest(profile: EdgarProfile, forms: tuple[str, ...] = ("10-K", "20-F", "40-F")) -> dict[str, str] | None:
        return next((f for f in profile.filings if f["form"] in forms), None)

    def document(self, cik: int, accession: str, name: str) -> Fetched:
        return self.f.get(SRC, ARCHIVE_URL.format(cik=cik, acc=accession.replace("-", ""), doc=name))

    def filing_files(self, cik: int, accession: str) -> list[str]:
        doc = self.f.get(SRC, INDEX_URL.format(cik=cik, acc=accession.replace("-", "")))
        return [i["name"] for i in doc.json().get("directory", {}).get("item", [])]

    # ---------------------------------------------------------------- Exhibit 21 (subsidiaries)
    def subsidiaries(self, cik: int, accession: str) -> tuple[list[dict[str, str]], Fetched | None]:
        files = self.filing_files(cik, accession)
        ex21 = next(
            (
                n
                for n in files
                if re.search(r"ex[-_]?21|exhibit21", n, re.I) and n.lower().endswith((".htm", ".html", ".txt"))
            ),
            None,
        )
        if not ex21:
            return [], None
        doc = self.document(cik, accession, ex21)
        return parse_exhibit21(doc.text), doc

    # ---------------------------------------------------------------- Form 4 insiders
    def insiders(self, profile: EdgarProfile, max_filings: int = 12) -> tuple[list[Insider], list[str]]:
        forms = [f for f in profile.filings if f["form"] in ("4", "3")][:max_filings]
        found: dict[str, Insider] = {}
        errors: list[str] = []
        for f in forms:
            xml_name = f["primary_document"].split("/")[-1]  # strip the xsl rendering prefix → raw XML
            try:
                doc = self.document(profile.cik, f["accession"], xml_name)
                for ins in parse_form4(doc.text, f["date"], f["accession"], doc.document_id, doc.url):
                    found.setdefault(ins.name.lower(), ins)
            except (SourceError, ET.ParseError) as exc:
                errors.append(f"{f['accession']}: {exc}")
        return list(found.values()), errors

    # ---------------------------------------------------------------- filing text for RAG
    def filing_text(self, cik: int, filing: dict[str, str]) -> tuple[str, Fetched]:
        doc = self.document(cik, filing["accession"], filing["primary_document"])
        return html_to_text(doc.content), doc


def html_to_text(content: bytes) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)  # inline XBRL is XHTML; HTML parsing is intended
        soup = BeautifulSoup(content, "lxml")
    for t in soup(["script", "style", "ix:header"]):
        t.decompose()
    for hidden in soup.select('[style*="display:none"]'):
        hidden.decompose()
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text


def parse_exhibit21(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, str]] = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c and c not in ("*", "%")]
        if len(cells) >= 2 and len(cells[0]) > 2 and not re.search(r"jurisdiction|name of subsidiar", cells[0], re.I):
            out.append({"name": cells[0], "jurisdiction": cells[1]})
    seen: set[str] = set()
    unique = []
    for r in out:
        if r["name"].lower() not in seen:
            seen.add(r["name"].lower())
            unique.append(r)
    return unique[:500]


def parse_form4(xml_text: str, date: str, accession: str, document_id: str, url: str) -> list[Insider]:
    root = SafeET.fromstring(xml_text.encode())
    out = []
    for ro in root.findall("reportingOwner"):
        name = (ro.findtext("reportingOwnerId/rptOwnerName") or "").strip()
        rel = ro.find("reportingOwnerRelationship")
        roles = []
        title = None
        if rel is not None:
            for tag, label in (
                ("isDirector", "director"),
                ("isOfficer", "officer"),
                ("isTenPercentOwner", "10% owner"),
                ("isOther", "other"),
            ):
                if (rel.findtext(tag) or "").strip().lower() in ("1", "true"):
                    roles.append(label)
            title = (rel.findtext("officerTitle") or "").strip() or None
        if name:
            out.append(Insider(name, roles, title, date, accession, document_id, url))
    return out


def financial_facts(facts_json: dict[str, Any]) -> dict[str, Any]:
    """Latest two annual (FY, 10-K/20-F) values for core concepts, with accession + period for citation."""
    concepts = {
        "revenue": [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ],
        "net_income": ["NetIncomeLoss", "ProfitLoss"],
        "operating_income": ["OperatingIncomeLoss"],
        "total_assets": ["Assets"],
        "total_liabilities": ["Liabilities"],
        "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
        "current_assets": ["AssetsCurrent"],
        "current_liabilities": ["LiabilitiesCurrent"],
        "cash": ["CashAndCashEquivalentsAtCarryingValue"],
        "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    }
    gaap = facts_json.get("facts", {}).get("us-gaap", {})
    out: dict[str, Any] = {}
    for key, names in concepts.items():
        best: list[dict[str, Any]] = []
        for concept in names:
            units = gaap.get(concept, {}).get("units", {}).get("USD", [])
            annual = [
                u
                for u in units
                if u.get("form") in ("10-K", "20-F", "40-F")
                and u.get("fp") == "FY"
                and (("start" not in u) or _days(u["start"], u["end"]) > 300)
            ]
            if annual:
                by_end: dict[str, dict[str, Any]] = {}
                for u in annual:
                    by_end[u["end"]] = u | {"concept": concept}  # later filings restate → keep last seen
                cand = sorted(by_end.values(), key=lambda u: u["end"], reverse=True)[:2]
                if not best or cand[0]["end"] > best[0]["end"]:
                    best = cand
        if best:
            out[key] = [
                {
                    "value": b["val"],
                    "period_end": b["end"],
                    "concept": b["concept"],
                    "accession": b["accn"],
                    "form": b["form"],
                    "filed": b["filed"],
                }
                for b in best
            ]
    return out


def _days(start: str, end: str) -> int:
    from datetime import date

    return (date.fromisoformat(end) - date.fromisoformat(start)).days


__all__ = ["EdgarConnector", "SourceNotFound", "financial_facts", "html_to_text", "norm_company", "parse_exhibit21"]
