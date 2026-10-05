# Data sources

Sentinel uses only public, authoritative or clearly licensed sources. No evidence is fabricated: if a source is
unavailable the case records the gap (`partial` / `unavailable` coverage and an error message) and the policy engine
sends the case to review.

| ID | Source | Publisher | Access | Licence / terms | Refresh | Used for |
|---|---|---|---|---|---|---|
| `ofac_sdn` | SDN List (`sdn.csv` + `alt.csv`) | US Treasury – OFAC | bulk download | US Government work | scheduled (SLA 36 h) | sanctions screening |
| `un_sc` | UN Security Council Consolidated List (XML) | United Nations | bulk download | UN public information | scheduled (SLA 36 h) | sanctions screening |
| `uk_sanctions` | UK Sanctions List (CSV) | FCDO | bulk download | Open Government Licence v3.0 | scheduled (SLA 36 h) | sanctions screening |
| `eu_fsf` | EU Financial Sanctions Files (CSV) | European Commission | bulk download (public token URL) | EC reuse policy (Decision 2011/833/EU) | scheduled (SLA 36 h) | sanctions screening |
| `us_congress` | Current members of Congress | @unitedstates/congress-legislators | JSON | CC0 1.0 | scheduled (SLA 14 d) | domestic PEP screening |
| `opensanctions_peps` | OpenSanctions PEPs | OpenSanctions | CSV | **CC BY-NC 4.0** | disabled by default | global PEP screening (needs a commercial licence) |
| `sec_edgar` | EDGAR company index, submissions, XBRL company facts & frames, 10-K, Exhibit 21, Forms 3/4 | US SEC | HTTPS API/archives | US Government work; [fair-access policy](https://www.sec.gov/os/accessing-edgar-data) (≤ 10 req/s, declared User-Agent) | per case (cache 6 h) | resolution, filings text, subsidiaries, insiders, financials, peer model |
| `gleif` | LEI records, Level 2 parents/children | GLEIF | REST API | CC0 1.0 | per case | resolution, ownership (consolidation) |
| `doj_press` | Press releases API | US Department of Justice | REST API | US Government work | per case | enforcement history |
| `gdelt` | DOC 2.0 article list | The GDELT Project | REST API | open with attribution; article text belongs to publishers — only metadata (title, URL, domain, date) is stored | per case (window `SENTINEL_GDELT_TIMESPAN`) | news mentions |

## What each record carries

Every downloaded response becomes a `source_documents` row (URL, HTTP status, content type, size, SHA-256,
retrieval timestamp) and its bytes are stored in the raw store under the hash. Evidence rows reference the document
and add: evidence id, kind, title, extracted text/structure, locator (e.g. 10-K section, XBRL concept and period,
list entry id), confidence and the case. See [data_lineage.md](data_lineage.md).

## Known limitations of the sources

* **Ownership is not UBO.** GLEIF Level 2 reports *accounting consolidation* parents; Exhibit 21 lists *significant*
  subsidiaries; Forms 3/4 list officers, directors and >10 % holders of US-listed issuers. None of these is a
  beneficial-ownership register of natural persons. Many private and non-US companies have no Level 2 data
  (reporting exceptions). Sentinel shows this explicitly and never infers owners.
* **SEC coverage** applies to SEC registrants only. Foreign or private companies get registry (GLEIF) and screening
  coverage but no filings/financials (`not_applicable`).
* **PEP coverage** by default is US Congress only. Global PEP screening needs a licensed dataset (adapter present,
  disabled).
* **GDELT** is queried with the entity's distinctive short name (as DOJ is): exact legal-form phrases such as
  `"BOEING CO"` returned no results in testing, and 3-month windows returned an empty `{}` where a 1-week window
  returned articles, so the default window is `SENTINEL_GDELT_TIMESPAN=1week`. GDELT answers "no results" and some
  internal errors with the same `{}`, so an empty GDELT result is not proof of absence. GDELT enforces roughly one request per 5 seconds per client and frequently returns HTTP 429 or times out from
  shared egress IPs. During the release runs it was unavailable for most cases; those cases show adverse-media
  coverage `partial` with the error, and the DOJ source still contributed. Plan a commercial news provider for
  production adverse-media coverage.
* **DOJ press releases** cover US federal enforcement only, matched by keyword search on the entity's short name;
  releases about similarly named entities can appear and are classified as `mentioned` or `victim` rather than
  `subject` where the text shows it.
* **EU FSF** is downloaded from the public URL with the token published by the Commission; if the Commission changes
  it, the refresh fails visibly (last run `failed`, list goes stale, cases get TPRM-DAT-03).
* **List contents change daily**; screening results are tied to the list version used (document id and retrieval time
  are stored with every hit).

## Offline test data

Tests and the offline evaluation replay **recorded real responses** (`tests/fixtures/http`, recorded on
2026-10-04 with `scripts/record_fixtures.py`): EDGAR, GLEIF, DOJ and GDELT responses for the evaluation entities, and
**documented excerpts** of the five lists (`scripts/make_watchlist_excerpts.py`): unmodified rows selected by a
documented rule (named entities used in tests plus a seeded random sample), each cassette marked `excerpt: true` with
the full file's SHA-256. Excerpts are never presented as complete lists; full-list metrics come from live runs
(see [evaluation.md](evaluation.md)).

GDELT could not be recorded through the recorder (throttled); one successful real `artlist` response (query
`boeing`, 1 week) was captured with curl on 2026-10-05 and is stored as a cassette marked `recorded_with: curl`. In
replayed investigations GDELT has no recording and is therefore reported as unavailable, exactly as in the live runs.

The fictional companies of v1 (Helios, Northwind, BlueOrbit) and their documents have been removed from the
repository.
