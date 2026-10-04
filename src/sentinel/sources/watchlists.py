"""Parsers for official sanctions lists and PEP datasets → normalised ``WatchlistRecord``s.

Parsers are pure functions over the raw bytes (no network), so they are deterministic, testable against
recorded real files, and re-runnable from the raw store for lineage/replay.
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET  # types/ParseError only; parsing goes through defusedxml
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import defusedxml.ElementTree as SafeET


@dataclass
class WatchlistRecord:
    external_id: str
    category: str  # sanctions | pep
    entity_type: str  # individual | entity | vessel | aircraft | unknown
    primary_name: str
    names: list[tuple[str, str]] = field(default_factory=list)  # (name, primary|aka|weak)
    programs: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    identifiers: list[dict[str, str]] = field(default_factory=list)
    remarks: str = ""
    listed_on: str | None = None

    def add_name(self, name: str, kind: str = "aka") -> None:
        name = " ".join(name.split()).strip(" ,;")
        if name and all(n.lower() != name.lower() for n, _ in self.names):
            self.names.append((name, kind))


class ParseError(ValueError):
    pass


def _nz(v: str | None) -> str:
    v = (v or "").strip()
    return "" if v in ("-0-", "-0- ") else v


# ------------------------------------------------------------------------------------------------ OFAC SDN
_OFAC_ID_PATTERNS = [
    ("LEI", re.compile(r"Legal Entity Number ([A-Z0-9]{20})")),
    ("SWIFT/BIC", re.compile(r"SWIFT/BIC ([A-Z0-9]{8,11})")),
    ("REGISTRATION", re.compile(r"Registration (?:ID|Number) ([A-Za-z0-9\-/]+)")),
    ("TAX_ID", re.compile(r"Tax ID No\. ([A-Za-z0-9\-]+)")),
    ("IMO", re.compile(r"IMO (\d{7})")),
]
_OFAC_TYPES = {"individual": "individual", "vessel": "vessel", "aircraft": "aircraft"}


def parse_ofac_sdn(sdn: bytes, alt: bytes | None = None) -> list[WatchlistRecord]:
    """OFAC legacy CSV format: sdn.csv (+ alt.csv aliases). Files have no header row."""
    recs: OrderedDict[str, WatchlistRecord] = OrderedDict()
    for row in csv.reader(io.StringIO(sdn.decode("latin-1"))):
        if len(row) < 12 or not row[0].strip().isdigit():
            continue
        ent = row[0].strip()
        remarks = _nz(row[11])
        rec = WatchlistRecord(
            external_id=ent,
            category="sanctions",
            entity_type=_OFAC_TYPES.get(_nz(row[2]).lower(), "entity"),
            primary_name=_nz(row[1]),
            programs=[p.strip(" []") for p in _nz(row[3]).split("] [") if p.strip(" []")],
            remarks=remarks[:4000],
        )
        rec.add_name(rec.primary_name, "primary")
        for scheme, pat in _OFAC_ID_PATTERNS:
            for m in pat.finditer(remarks):
                rec.identifiers.append({"scheme": scheme, "value": m.group(1)})
        for m in re.finditer(r"a\.k\.a\. '([^']+)'", remarks):
            rec.add_name(m.group(1), "aka")
        recs[ent] = rec
    if not recs:
        raise ParseError("OFAC SDN file contained no records")
    if alt:
        for row in csv.reader(io.StringIO(alt.decode("latin-1"))):
            if len(row) >= 4 and row[0].strip() in recs:
                kind = "weak" if "weak" in _nz(row[4] if len(row) > 4 else "").lower() else "aka"
                recs[row[0].strip()].add_name(_nz(row[3]), kind)
    return list(recs.values())


def parse_ofac_addresses(add: bytes) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in csv.reader(io.StringIO(add.decode("latin-1"))):
        if len(row) >= 5 and _nz(row[4]):
            out.setdefault(row[0].strip(), []).append(_nz(row[4]))
    return out


# ------------------------------------------------------------------------------------------------ UN SC
def parse_un_consolidated(xml_bytes: bytes) -> list[WatchlistRecord]:
    root = SafeET.fromstring(xml_bytes)
    out: list[WatchlistRecord] = []

    def text(el: ET.Element, tag: str) -> str:
        child = el.find(tag)
        return (child.text or "").strip() if child is not None and child.text else ""

    for kind, group, alias_tag in (
        ("individual", "INDIVIDUALS", "INDIVIDUAL_ALIAS"),
        ("entity", "ENTITIES", "ENTITY_ALIAS"),
    ):
        container = root.find(group)
        if container is None:
            continue
        for el in container:
            parts = [text(el, t) for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
            name = " ".join(p for p in parts if p)
            ref = text(el, "REFERENCE_NUMBER") or text(el, "DATAID")
            rec = WatchlistRecord(
                external_id=ref,
                category="sanctions",
                entity_type=kind,
                primary_name=name,
                programs=[f"UN {text(el, 'UN_LIST_TYPE')}".strip()],
                remarks=text(el, "COMMENTS1")[:4000],
                listed_on=text(el, "LISTED_ON") or None,
            )
            rec.add_name(name, "primary")
            for a in el.findall(alias_tag):
                quality = text(a, "QUALITY").lower()
                rec.add_name(text(a, "ALIAS_NAME"), "weak" if quality in ("low", "a.k.a. low") else "aka")
            for nat in el.findall("NATIONALITY/VALUE"):
                if nat.text:
                    rec.countries.append(nat.text.strip())
            for addr in el.findall(f"{'INDIVIDUAL' if kind == 'individual' else 'ENTITY'}_ADDRESS"):
                if (c := text(addr, "COUNTRY")) and c not in rec.countries:
                    rec.countries.append(c)
            out.append(rec)
    if not out:
        raise ParseError("UN consolidated list contained no records")
    return out


# ------------------------------------------------------------------------------------------------ UK
_UK_TYPES = {"individual": "individual", "entity": "entity", "ship": "vessel"}


def parse_uk_sanctions(csv_bytes: bytes) -> list[WatchlistRecord]:
    text = csv_bytes.decode("utf-8-sig")
    if text.startswith("Report Date"):
        text = text.split("\n", 1)[1]
    recs: OrderedDict[str, WatchlistRecord] = OrderedDict()
    for row in csv.DictReader(io.StringIO(text)):
        uid = (row.get("Unique ID") or "").strip()
        if not uid:
            continue
        given = [(row.get(f"Name {i}") or "").strip() for i in range(1, 6)]
        last = (row.get("Name 6") or "").strip()
        name = " ".join(p for p in [*given, last] if p)
        name_type = (row.get("Name type") or "").lower()
        rec = recs.get(uid)
        if rec is None:
            rec = WatchlistRecord(
                external_id=uid,
                category="sanctions",
                entity_type=_UK_TYPES.get((row.get("Designation Type") or "").strip().lower(), "unknown"),
                primary_name=name,
                programs=[(row.get("Regime Name") or "").strip()],
                remarks=(row.get("UK Statement of Reasons") or "")[:4000],
                listed_on=(row.get("Date Designated") or "").strip() or None,
            )
            recs[uid] = rec
        if name_type.startswith("primary name") and not name_type.endswith("variation"):
            rec.primary_name = name
            rec.add_name(name, "primary")
        else:
            rec.add_name(name, "weak" if (row.get("Alias strength") or "").lower() == "weak" else "aka")
        if (c := (row.get("Address Country") or "").strip()) and c not in rec.countries:
            rec.countries.append(c)
        reg = (row.get("Business registration number (s)") or "").strip()
        ident = {"scheme": "REGISTRATION", "value": reg[:120]}
        if reg and ident not in rec.identifiers and len(rec.identifiers) < 10:
            rec.identifiers.append(ident)
    if not recs:
        raise ParseError("UK sanctions list contained no records")
    return list(recs.values())


# ------------------------------------------------------------------------------------------------ EU
_EU_TYPES = {"person": "individual", "enterprise": "entity"}


def parse_eu_fsf(csv_bytes: bytes) -> list[WatchlistRecord]:
    recs: OrderedDict[str, WatchlistRecord] = OrderedDict()
    for row in csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")), delimiter=";"):
        lid = (row.get("Entity_LogicalId") or "").strip()
        if not lid:
            continue
        rec = recs.get(lid)
        name = (row.get("NameAlias_WholeName") or "").strip()
        if rec is None:
            rec = WatchlistRecord(
                external_id=lid,
                category="sanctions",
                entity_type=_EU_TYPES.get((row.get("Entity_SubjectType_ClassificationCode") or "").strip(), "unknown"),
                primary_name=name,
                programs=[(row.get("Entity_Regulation_Programme") or "").strip()],
                remarks=(row.get("Entity_Remark") or "")[:4000],
                listed_on=(row.get("Entity_DesignationDate") or "").strip() or None,
            )
            recs[lid] = rec
        if name:
            rec.add_name(name, "primary" if name == rec.primary_name else "aka")
        if (c := (row.get("Address_CountryIso2Code") or "").strip()) and c not in rec.countries:
            rec.countries.append(c)
        num = (row.get("Identification_Number") or "").strip()
        if (
            num
            and {"scheme": (row.get("Identification_TypeCode") or "ID").upper(), "value": num} not in rec.identifiers
        ):
            rec.identifiers.append({"scheme": (row.get("Identification_TypeCode") or "ID").upper(), "value": num})
    if not recs:
        raise ParseError("EU FSF file contained no records")
    return list(recs.values())


# ------------------------------------------------------------------------------------------------ PEPs
def parse_us_congress(json_bytes: bytes) -> list[WatchlistRecord]:
    out = []
    for m in json.loads(json_bytes):
        n = m.get("name", {})
        full = n.get("official_full") or " ".join(x for x in (n.get("first"), n.get("middle"), n.get("last")) if x)
        term = (m.get("terms") or [{}])[-1]
        role = {"sen": "U.S. Senator", "rep": "U.S. Representative"}.get(term.get("type", ""), "Member of Congress")
        rec = WatchlistRecord(
            external_id=m.get("id", {}).get("bioguide", full),
            category="pep",
            entity_type="individual",
            primary_name=full,
            programs=[f"{role} ({term.get('state', '')})"],
            countries=["US"],
            remarks=f"{role}, {term.get('party', '')}; term {term.get('start', '')} to {term.get('end', '')}",
            listed_on=term.get("start"),
        )
        rec.add_name(full, "primary")
        if n.get("first") and n.get("last"):
            rec.add_name(f"{n['first']} {n['last']}", "aka")
        if n.get("nickname") and n.get("last"):
            rec.add_name(f"{n['nickname']} {n['last']}", "aka")
        out.append(rec)
    if not out:
        raise ParseError("Congress legislators file contained no records")
    return out


def parse_opensanctions_simple(csv_bytes: bytes) -> list[WatchlistRecord]:
    out = []
    for row in csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))):
        rec = WatchlistRecord(
            external_id=row["id"],
            category="pep",
            entity_type="individual" if row.get("schema") == "Person" else "entity",
            primary_name=row.get("name", ""),
            programs=[p for p in (row.get("sanctions") or row.get("dataset") or "").split(";") if p][:5],
            countries=[c for c in (row.get("countries") or "").split(";") if c],
        )
        rec.add_name(rec.primary_name, "primary")
        for a in (row.get("aliases") or "").split(";")[:20]:
            rec.add_name(a, "aka")
        out.append(rec)
    return out


PARSERS: dict[str, Callable[..., list[WatchlistRecord]]] = {
    "ofac_sdn": parse_ofac_sdn,
    "un_sc": parse_un_consolidated,
    "uk_sanctions": parse_uk_sanctions,
    "eu_fsf": parse_eu_fsf,
    "us_congress": parse_us_congress,
    "opensanctions_peps": parse_opensanctions_simple,
}


def validate_records(records: list[WatchlistRecord]) -> tuple[list[WatchlistRecord], dict[str, Any]]:
    """Data-quality gate: reject records with no usable name; report duplicates and completeness."""
    seen: set[str] = set()
    good: list[WatchlistRecord] = []
    rejected = dupes = 0
    for r in records:
        if not r.primary_name.strip() or not r.names:
            rejected += 1
            continue
        if r.external_id in seen:
            dupes += 1
            continue
        seen.add(r.external_id)
        good.append(r)
    n = max(len(good), 1)
    quality = {
        "records_in": len(records),
        "rejected_no_name": rejected,
        "duplicates_dropped": dupes,
        "pct_with_aliases": round(sum(len(r.names) > 1 for r in good) / n, 3),
        "pct_with_identifiers": round(sum(bool(r.identifiers) for r in good) / n, 3),
        "entity_types": {t: sum(r.entity_type == t for r in good) for t in {r.entity_type for r in good}},
    }
    return good, quality
