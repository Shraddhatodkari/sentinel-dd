"""Build *documented excerpts* of the official watch-lists for offline tests and evaluation.

Full lists are 5–50 MB each; tests replay small, unmodified row subsets of the real files. Each excerpt is written
as a cassette for the official URL with ``excerpt: true`` plus the full file's SHA-256 and the selection rule, so
nobody can mistake an excerpt for the complete list.

    python scripts/make_watchlist_excerpts.py --raw-dir /path/to/full/downloads --out tests/fixtures/http
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import xml.etree.ElementTree as ET
from pathlib import Path

from sentinel.sources.catalog import OFAC_BASE, SOURCES
from sentinel.sources.replay import write_cassette

KEEP_TERMS = [
    "SBERBANK",
    "ROSNEFT",
    "BANK MELLI",
    "GAZPROMBANK",
    "VTB",
    "HAMAS",
    "ISLAMIC REVOLUTIONARY GUARD",
    "KIM JONG UN",
    "WAGNER",
    "AL-QAIDA",
    "AL QAIDA",
    "HIZBALLAH",
    "HEZBOLLAH",
    "ROSOBORONEXPORT",
    "AEROFLOT",
    "NORD STREAM",
    "ALROSA",
    "SOVCOMFLOT",
    "TALIBAN",
    "LUKOIL",
]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def keep(text: str) -> bool:
    up = text.upper()
    return any(t in up for t in KEEP_TERMS)


def ofac(raw: Path, out: Path, rnd: random.Random) -> None:
    sdn, alt = (raw / "sdn.csv").read_bytes(), (raw / "alt.csv").read_bytes()
    rows = list(csv.reader(io.StringIO(sdn.decode("latin-1"))))
    sel = {r[0] for r in rows if len(r) > 1 and keep(r[1] + " " + (r[11] if len(r) > 11 else ""))}
    sel |= {r[0] for r in rnd.sample(rows, 400) if r}
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([r for r in rows if r and r[0] in sel])
    body = buf.getvalue().encode("latin-1")
    alt_rows = [r for r in csv.reader(io.StringIO(alt.decode("latin-1"))) if r and r[0] in sel]
    abuf = io.StringIO()
    csv.writer(abuf, lineterminator="\n").writerows(alt_rows)
    meta = {"excerpt": True, "selection": f"rows naming {KEEP_TERMS} + 400 random rows (seed 7)"}
    write_cassette(out, f"{OFAC_BASE}/sdn.csv", body, "text/csv", full_sha256=_sha(sdn), **meta)
    write_cassette(
        out, f"{OFAC_BASE}/alt.csv", abuf.getvalue().encode("latin-1"), "text/csv", full_sha256=_sha(alt), **meta
    )


def uk(raw: Path, out: Path, rnd: random.Random) -> None:
    full = (raw / "uk.csv").read_bytes()
    text = full.decode("utf-8-sig")
    first, rest = text.split("\n", 1)
    rows = list(csv.DictReader(io.StringIO(rest)))
    ids = {r["Unique ID"] for r in rows if keep(" ".join(r.get(f"Name {i}") or "" for i in range(1, 7)))}
    ids |= set(rnd.sample(sorted({r["Unique ID"] for r in rows}), 300))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows([r for r in rows if r["Unique ID"] in ids])
    write_cassette(
        out,
        SOURCES["uk_sanctions"].url,
        (first + "\n" + buf.getvalue()).encode(),
        "text/csv",
        excerpt=True,
        full_sha256=_sha(full),
        selection="designations naming key terms + 300 random (seed 7)",
    )


def eu(raw: Path, out: Path, rnd: random.Random) -> None:
    full = (raw / "eu.csv").read_bytes()
    rows = list(csv.DictReader(io.StringIO(full.decode("utf-8-sig")), delimiter=";"))
    ids = {r["Entity_LogicalId"] for r in rows if keep(r.get("NameAlias_WholeName") or "")}
    ids |= set(rnd.sample(sorted({r["Entity_LogicalId"] for r in rows}), 300))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), delimiter=";", lineterminator="\n")
    w.writeheader()
    w.writerows([r for r in rows if r["Entity_LogicalId"] in ids])
    write_cassette(
        out,
        SOURCES["eu_fsf"].url,
        buf.getvalue().encode("utf-8"),
        "text/csv",
        excerpt=True,
        full_sha256=_sha(full),
        selection="entities naming key terms + 300 random (seed 7)",
    )


def un(raw: Path, out: Path, rnd: random.Random) -> None:
    full = (raw / "un.xml").read_bytes()
    root = ET.fromstring(full)  # noqa: S314
    for group in ("INDIVIDUALS", "ENTITIES"):
        cont = root.find(group)
        if cont is None:
            continue
        items = list(cont)
        chosen = {id(e) for e in items if keep(ET.tostring(e, encoding="unicode"))} | {
            id(e) for e in rnd.sample(items, 120)
        }
        for e in items:
            if id(e) not in chosen:
                cont.remove(e)
    write_cassette(
        out,
        SOURCES["un_sc"].url,
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
        "application/xml",
        excerpt=True,
        full_sha256=_sha(full),
        selection="records naming key terms + 120 random per group (seed 7)",
    )


def congress(raw: Path, out: Path, rnd: random.Random) -> None:
    full = (raw / "congress.json").read_bytes()
    members = json.loads(full)
    names = {"Joyce"}  # keep homonyms that appear among real corporate insiders in the recorded cases
    chosen = [m for m in members if m["name"].get("last") in names] + rnd.sample(members, 80)
    write_cassette(
        out,
        SOURCES["us_congress"].url,
        json.dumps(chosen).encode(),
        "application/json",
        excerpt=True,
        full_sha256=_sha(full),
        selection="members with last name in {'Joyce'} + 80 random (seed 7)",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--out", default="tests/fixtures/http")
    a = ap.parse_args()
    raw, out = Path(a.raw_dir), Path(a.out)
    for fn in (ofac, uk, eu, un, congress):
        fn(raw, out, random.Random(7))
        print(f"wrote {fn.__name__} excerpt")


if __name__ == "__main__":
    main()
