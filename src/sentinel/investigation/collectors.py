"""Evidence collectors (the tools used by the data-gathering agents).

Every collector:
* reads only from authoritative connectors via the lineage-preserving fetcher,
* writes *facts* as Evidence (verbatim/structured, with locator) and *derived inferences* as Signals,
* returns a ``CollectorResult`` with an explicit coverage status — ``ok``, ``partial``, ``not_applicable`` or
  ``unavailable`` — so gaps are visible to the risk engine, the policy engine and the reviewer.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sentinel.investigation.evidence import EvidenceWriter
from sentinel.investigation.resolution import Resolution
from sentinel.risk.financial import compute_indicators, financial_stress_points
from sentinel.risk.media import SEVERITY_RANK, TAXONOMY_VERSION, classify, distinctive_tokens, subject_role
from sentinel.screening import SCREENING_VERSION, ScreeningHit, ScreeningIndex
from sentinel.sources.edgar import EdgarConnector, EdgarProfile, financial_facts
from sentinel.sources.gleif import GleifConnector, LeiRecord
from sentinel.sources.http import SourceError
from sentinel.sources.media import DojConnector, GdeltConnector, MediaItem


@dataclass
class CollectorResult:
    name: str
    status: str  # ok | partial | not_applicable | unavailable
    evidence_ids: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _money(v: float | None) -> str:
    if v is None:
        return "n/a"
    sign = "-" if v < 0 else ""
    v = abs(v)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= div:
            return f"{sign}USD {v / div:,.2f}{unit}"
    return f"{sign}USD {v:,.0f}"


# ------------------------------------------------------------------------------------------- registry
def collect_registry(
    w: EvidenceWriter,
    res: Resolution,
    profile: EdgarProfile | None,
    lei: LeiRecord | None,
    edgar: EdgarConnector,
    gleif: GleifConnector,
) -> CollectorResult:
    r = CollectorResult("corporate_registry", "ok")
    subject = res.name or res.query
    if profile is not None and profile.fetched is not None:
        addr = profile.business_address
        content = (
            f"SEC EDGAR registrant record: {profile.name} (CIK {profile.cik:010d}). SIC {profile.sic} "
            f"({profile.sic_description}). State of incorporation: {profile.state_of_incorporation or 'not reported'}. "
            f"Tickers: {', '.join(profile.tickers) or 'none'}; exchanges: {', '.join(profile.exchanges) or 'none'}. "
            f"Filer category: {profile.category or 'n/a'}. Business address: {addr.get('street1') or ''} "
            f"{addr.get('city') or ''} {addr.get('stateOrCountry') or ''}. Former names: "
            + ("; ".join(f"{f['name']} (until {str(f.get('to', ''))[:10]})" for f in profile.former_names) or "none")
            + "."
        )
        ev = w.add(
            profile.fetched,
            "registry_profile",
            f"SEC EDGAR registrant profile — {profile.name}",
            content,
            structured={
                "cik": profile.cik,
                "sic": profile.sic,
                "state_of_incorporation": profile.state_of_incorporation,
                "tickers": profile.tickers,
                "exchanges": profile.exchanges,
                "ein": profile.ein,
                "former_names": profile.former_names,
                "lei": profile.lei,
            },
            locator="$ (submissions API)",
        )
        r.evidence_ids.append(ev.id)
    if lei is not None:
        lei_doc = lei.document_id
        content = (
            f"GLEIF LEI record {lei.lei}: legal name '{lei.legal_name}'. Other names: "
            f"{'; '.join(lei.other_names) or 'none'}. Jurisdiction: {lei.jurisdiction}; legal address country: "
            f"{lei.legal_country}; HQ country: {lei.hq_country}. Entity status: {lei.entity_status}; registration "
            f"status: {lei.registration_status}; corroboration: {lei.corroboration}; last update: {lei.last_update}."
        )
        ev = w.add(
            lei_doc,
            "lei_record",
            f"GLEIF LEI record — {lei.lei}",
            content,
            source_id="gleif",
            structured={
                "lei": lei.lei,
                "legal_name": lei.legal_name,
                "entity_status": lei.entity_status,
                "registration_status": lei.registration_status,
                "jurisdiction": lei.jurisdiction,
                "corroboration": lei.corroboration,
                "other_names": lei.other_names,
            },
            locator=f"lei-records/{lei.lei}",
        )
        r.evidence_ids.append(ev.id)
        r.detail["lei_status"] = {"entity": lei.entity_status, "registration": lei.registration_status}
        for kind in ("direct", "ultimate"):
            try:
                parent = gleif.parent(lei.lei, kind)
            except SourceError as exc:
                r.errors.append(f"gleif {kind}-parent: {exc}")
                r.status = "partial"
                continue
            if parent is None:
                r.detail[f"{kind}_parent"] = None
                continue
            pev = w.add(
                parent.document_id,
                "ownership",
                f"GLEIF {kind} accounting-consolidation parent of {lei.legal_name}",
                f"GLEIF Level 2 data reports '{parent.legal_name}' (LEI {parent.lei}, {parent.legal_country}) "
                f"as the {kind} accounting-consolidation parent of '{lei.legal_name}' (LEI {lei.lei}).",
                source_id="gleif",
                structured={
                    "parent_lei": parent.lei,
                    "parent_name": parent.legal_name,
                    "relationship": f"IS_{kind.upper()}LY_CONSOLIDATED_BY",
                },
                locator=f"lei-records/{lei.lei}/{kind}-parent",
            )
            r.evidence_ids.append(pev.id)
            w.relate(
                parent.legal_name,
                "company",
                f"CONSOLIDATES_{kind.upper()}",
                subject,
                "company",
                pev.id,
                lei=parent.lei,
                basis="accounting consolidation (GLEIF Level 2)",
            )
            r.detail[f"{kind}_parent"] = {"name": parent.legal_name, "lei": parent.lei}
        try:
            children, total = gleif.children(lei.lei, limit=25)
            for ch in children:
                cev = w.add(
                    ch.document_id,
                    "ownership",
                    f"GLEIF direct child of {lei.legal_name}: {ch.legal_name}",
                    f"GLEIF Level 2 data reports '{ch.legal_name}' (LEI {ch.lei}, {ch.legal_country}) as directly "
                    f"consolidated by '{lei.legal_name}'.",
                    source_id="gleif",
                    structured={"child_lei": ch.lei, "child_name": ch.legal_name, "status": ch.entity_status},
                    locator=f"lei-records/{lei.lei}/direct-children",
                )
                w.relate(
                    subject,
                    "company",
                    "CONSOLIDATES_DIRECT",
                    ch.legal_name,
                    "company",
                    cev.id,
                    lei=ch.lei,
                    basis="accounting consolidation (GLEIF Level 2)",
                    country=ch.legal_country,
                )
            r.detail["gleif_children"] = {"returned": len(children), "total": total}
        except SourceError as exc:
            r.errors.append(f"gleif children: {exc}")
            r.status = "partial"
    if profile is not None:
        filing = edgar.latest(profile)
        if filing:
            try:
                subs, ex21_doc = edgar.subsidiaries(profile.cik, filing["accession"])
                if ex21_doc is not None:
                    listing = "; ".join(f"{s['name']} ({s['jurisdiction']})" for s in subs[:80])
                    sev = w.add(
                        ex21_doc,
                        "subsidiaries",
                        f"Exhibit 21 subsidiaries — {filing['form']} filed {filing['date']}",
                        f"Exhibit 21 to the {filing['form']} filed {filing['date']} (accession "
                        f"{filing['accession']}) lists {len(subs)} subsidiaries: {listing}",
                        structured={"subsidiaries": subs, "accession": filing["accession"]},
                        locator=f"{filing['accession']}/EX-21",
                    )
                    r.evidence_ids.append(sev.id)
                    for sub in subs[:200]:
                        w.relate(
                            profile.name,
                            "company",
                            "PARENT_OF",
                            sub["name"],
                            "company",
                            sev.id,
                            jurisdiction=sub["jurisdiction"],
                            basis="SEC Exhibit 21 (significant subsidiaries)",
                        )
                    r.detail["exhibit21_subsidiaries"] = len(subs)
            except SourceError as exc:
                r.errors.append(f"exhibit 21: {exc}")
                r.status = "partial"
    if profile is None and lei is None:
        r.status = "unavailable"
        r.errors.append("no registry record could be retrieved (SEC EDGAR / GLEIF)")
    return r


# ------------------------------------------------------------------------------------------- insiders
def collect_insiders(w: EvidenceWriter, profile: EdgarProfile | None, edgar: EdgarConnector) -> CollectorResult:
    r = CollectorResult("insiders", "ok")
    if profile is None:
        r.status = "not_applicable"
        r.detail["reason"] = "not an SEC registrant — Forms 3/4 unavailable"
        return r
    insiders, errors = edgar.insiders(profile)
    r.errors += errors
    if errors and not insiders:
        r.status = "unavailable"
    elif errors:
        r.status = "partial"
    for ins in insiders:
        doc = ins.document_id
        roles = ", ".join(ins.roles) or "reporting person"
        ev = w.add(
            doc,
            "insider",
            f"Insider filing (Form 3/4): {ins.name}",
            f"SEC Form 3/4 filed {ins.filing_date} (accession {ins.accession}) identifies {ins.name} as "
            f"{roles} of {profile.name}"
            + (f" with officer title '{ins.officer_title}'." if ins.officer_title else "."),
            structured={
                "name": ins.name,
                "roles": ins.roles,
                "officer_title": ins.officer_title,
                "accession": ins.accession,
            },
            locator=f"{ins.accession}/reportingOwner",
        )
        r.evidence_ids.append(ev.id)
        w.relate(
            ins.name, "person", "INSIDER_OF", profile.name, "company", ev.id, roles=ins.roles, title=ins.officer_title
        )
    r.detail["insiders"] = [{"name": i.name, "roles": i.roles, "title": i.officer_title} for i in insiders]
    return r


# ------------------------------------------------------------------------------------------- financials
def collect_financials(w: EvidenceWriter, profile: EdgarProfile | None, edgar: EdgarConnector) -> CollectorResult:
    r = CollectorResult("financials", "ok")
    if profile is None:
        r.status = "not_applicable"
        r.detail["reason"] = "no SEC XBRL filings for this entity; financial statements must be obtained directly"
        return r
    try:
        doc = edgar.company_facts(profile.cik)
    except SourceError as exc:
        r.status = "unavailable"
        r.errors.append(str(exc))
        return r
    facts = financial_facts(doc.json())
    if not facts:
        r.status = "partial"
        r.detail["reason"] = "no annual US-GAAP USD facts reported"
        return r
    lines = []
    for key, vals in facts.items():
        v = vals[0]
        lines.append(
            f"{key.replace('_', ' ')}: {_money(v['value'])} for period ended {v['period_end']} "
            f"(us-gaap:{v['concept']}, {v['form']} accession {v['accession']})"
        )
    ev = w.add(
        doc,
        "financial_facts",
        f"SEC XBRL company facts — {profile.name}",
        "Reported annual figures from SEC XBRL company facts. " + "; ".join(lines) + ".",
        structured=facts,
        locator="facts.us-gaap.*.units.USD (FY, 10-K/20-F)",
    )
    r.evidence_ids.append(ev.id)
    ind = compute_indicators(facts)
    r.detail["indicators"] = ind.to_dict()
    for rule, pts, why in financial_stress_points(ind):
        w.signal(
            "financial_stress", why, pts, "medium" if pts >= 4 else "low", 0.95, [ev.id], "risk.financial:v1", rule=rule
        )
    if ind.missing:
        r.status = "partial" if len(ind.missing) < 4 else "unavailable"
    return r


# ------------------------------------------------------------------------------------------- media & enforcement
def collect_media(
    w: EvidenceWriter, res: Resolution, doj: DojConnector, gdelt: GdeltConnector | None
) -> CollectorResult:
    r = CollectorResult("adverse_media", "ok")
    names = [n for n in [res.name, *res.aliases] if n] or [res.query]
    search_name = display_name(res)
    toks = distinctive_tokens(search_name)
    short = " ".join(toks[:2]) if toks else search_name
    items: list[MediaItem] = []
    statuses: dict[str, str] = {}
    try:
        items += doj.search(short)
        statuses["doj_press"] = "ok"
    except (SourceError, ValueError) as exc:
        statuses["doj_press"] = "unavailable"
        r.errors.append(f"doj_press: {exc}")
    if gdelt is not None:
        try:
            # distinctive short name, as for DOJ: an exact legal-form phrase ("BOEING CO") rarely appears in news
            items += gdelt.search(short)
            statuses["gdelt"] = "ok"
        except (SourceError, ValueError) as exc:
            statuses["gdelt"] = "unavailable"
            r.errors.append(f"gdelt: {exc}")
    else:
        statuses["gdelt"] = "disabled"
    flagged = 0
    roles: dict[str, int] = {"subject": 0, "victim": 0, "mentioned": 0}
    for it in items:
        text = f"{it.title}. {it.excerpt}"
        cls = classify(text, [search_name, *names])
        if not cls.relevant:
            continue
        role = subject_role(it.title, [search_name, *names])
        roles[role] += 1
        kind = "enforcement_release" if it.source_id == "doj_press" else "media_article"
        ev = w.add(
            it.document_id,
            kind,
            it.title or "(untitled)",
            f"{it.outlet} — {it.published}: {it.title}." + (f" Excerpt: {it.excerpt}" if it.excerpt else ""),
            structured={
                "outlet": it.outlet,
                "published": it.published,
                "categories": cls.categories,
                "matched_terms": cls.matched_terms,
                "taxonomy": TAXONOMY_VERSION,
                "subject_role": role,
            },
            locator=it.url,
            source_url=it.url or None,
            source_id=it.source_id,
            confidence=0.9 if it.source_id == "doj_press" else 0.6,
        )
        r.evidence_ids.append(ev.id)
        if not cls.categories or role == "victim":
            continue  # context only: the subject is not the target of the reported conduct
        flagged += 1
        authority = "official enforcement release" if it.source_id == "doj_press" else "news report"
        if role == "subject":
            w.signal(
                "adverse_media",
                f"{', '.join(cls.categories)} — {authority}: {it.title[:160]}",
                SEVERITY_RANK[cls.severity],
                cls.severity,
                0.85 if it.source_id == "doj_press" else 0.55,
                [ev.id],
                TAXONOMY_VERSION,
                categories=cls.categories,
                source=it.source_id,
                matched_terms=cls.matched_terms,
                subject_role=role,
            )
        else:
            w.signal(
                "adverse_media_unclear_role",
                f"role unclear — {authority}: {it.title[:160]}",
                1,
                "low",
                0.3,
                [ev.id],
                TAXONOMY_VERSION,
                categories=cls.categories,
                source=it.source_id,
                matched_terms=cls.matched_terms,
                subject_role=role,
            )
    r.detail = {
        "queried": {"doj_press": short, "gdelt": short},
        "sources": statuses,
        "items_retrieved": len(items),
        "relevant_items": len(r.evidence_ids),
        "flagged": flagged,
        "subject_roles": roles,
    }
    if all(v != "ok" for v in statuses.values()):
        r.status = "unavailable"
    elif any(v == "unavailable" for v in statuses.values()):
        r.status = "partial"
    return r


def display_name(res: Resolution) -> str:
    """Prefer a Latin-script name for searching English-language sources."""
    name = res.name or res.query
    if name.isascii():
        return name
    for c in res.aliases:
        if c.isascii():
            return c
    return res.query


# ------------------------------------------------------------------------------------------- filings text (RAG)
SECTION_PATTERNS = [
    ("business", r"^item\s*1\.?\s*business\b"),
    ("risk_factors", r"^item\s*1a\.?\s*risk\s+factors\b"),
    ("cybersecurity", r"^item\s*1c\.?\s*cybersecurity\b"),
    ("legal_proceedings", r"^item\s*3\.?\s*legal\s+proceedings\b"),
]
_NEXT_ITEM = re.compile(r"^item\s*\d+[a-z]?\.?\s+\S", re.I | re.M)


def extract_sections(text: str, max_chars: int = 80_000) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, pat in SECTION_PATTERNS:
        best = ""
        for m in re.finditer(pat, text, re.I | re.M):
            nxt = _NEXT_ITEM.search(text, m.end())
            body = text[m.start() : nxt.start() if nxt else len(text)]
            if len(body) > len(best):
                best = body
        if len(best) > 200:
            out[name] = best[:max_chars]
    return out


def collect_filing_text(w: EvidenceWriter, profile: EdgarProfile | None, edgar: EdgarConnector) -> CollectorResult:
    r = CollectorResult("filings_text", "ok")
    if profile is None:
        r.status = "not_applicable"
        r.detail["reason"] = "not an SEC registrant"
        return r
    filing = edgar.latest(profile)
    if filing is None:
        r.status = "not_applicable"
        r.detail["reason"] = "no annual report (10-K/20-F/40-F) in recent filings"
        return r
    try:
        text, doc = edgar.filing_text(profile.cik, filing)
    except SourceError as exc:
        r.status = "unavailable"
        r.errors.append(str(exc))
        return r
    sections = extract_sections(text)
    for name, body in sections.items():
        ev = w.add(
            doc,
            "filing_text",
            f"{filing['form']} ({filing['date']}) — {name.replace('_', ' ').title()}",
            body,
            structured={
                "form": filing["form"],
                "accession": filing["accession"],
                "section": name,
                "filed": filing["date"],
                "chars": len(body),
            },
            locator=f"{filing['accession']}/{filing['primary_document']}#{name}",
        )
        r.evidence_ids.append(ev.id)
    r.detail = {"filing": filing, "sections": sorted(sections), "text_chars": len(text)}
    if not sections:
        r.status = "partial"
        r.errors.append("could not locate standard 10-K items in the filing text")
    return r


# ------------------------------------------------------------------------------------------- screening
def collect_screening(
    w: EvidenceWriter,
    index: ScreeningIndex,
    subject_names: list[str],
    related: list[tuple[str, str, str]],
    threshold: float,
    identifiers: list[str],
    max_related: int = 60,
) -> CollectorResult:
    """subject_names: names of the subject itself; related: (name, role, entity_type) for parents, insiders,
    subsidiaries. Every hit becomes Evidence (the list record) + a Signal (the potential match)."""
    r = CollectorResult("screening", "ok")
    hits: list[ScreeningHit] = []
    for n in dict.fromkeys(subject_names):
        hits += index.screen(n, "subject", "entity", threshold, identifiers)
    for name, role, etype in related[:max_related]:
        hits += index.screen(
            name, role, etype, threshold, categories=("sanctions", "pep") if etype == "individual" else ("sanctions",)
        )
    seen: set[tuple[str, str]] = set()
    for h in hits:
        if (h.entry_id, h.query_role) in seen:
            continue
        seen.add((h.entry_id, h.query_role))
        doc = h.document_id
        kind = "pep_record" if h.category == "pep" else "sanctions_record"
        ev = w.add(
            doc,
            kind,
            f"{h.source_id}: {h.primary_name}",
            f"List record on {h.source_id} ({', '.join(h.programs) or 'n/a'}): '{h.primary_name}', entity type "
            f"{h.entity_type}, countries {', '.join(h.countries) or 'n/a'}, listed {h.listed_on or 'n/a'}. "
            f"Remarks: {h.remarks[:400]}",
            structured={
                "entry_id": h.entry_id,
                "programs": h.programs,
                "matched_name": h.matched_name,
                "query": h.query,
                "role": h.query_role,
                "score": h.score,
                "method": h.method,
            },
            locator=f"{h.source_id}:{h.entry_id}",
            confidence=h.score,
        )
        r.evidence_ids.append(ev.id)
        sig_type = "pep_potential_match" if h.category == "pep" else "sanctions_potential_match"
        severity = (
            "critical"
            if h.category == "sanctions" and h.query_role == "subject"
            else ("high" if h.category == "sanctions" else "medium")
        )
        w.signal(
            sig_type,
            f"{h.query} ({h.query_role}) ~ {h.matched_name} [{h.source_id}]",
            h.score,
            severity,
            h.score,
            [ev.id],
            SCREENING_VERSION,
            role=h.query_role,
            source=h.source_id,
            method=h.method,
            programs=h.programs,
            query=h.query,
        )
    r.detail = {
        "subject_names": list(dict.fromkeys(subject_names)),
        "related_screened": min(len(related), max_related),
        "related_total": len(related),
        "hits": len(seen),
        "threshold": threshold,
        "lists": index.counts(),
    }
    return r


def as_json(obj: Any) -> str:
    return json.dumps(obj, default=str)
