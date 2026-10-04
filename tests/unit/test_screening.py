from sentinel.screening import normalize_name


def test_normalize_name():
    assert normalize_name("PJSC Sberbank of Russia") == "sberbank russia"
    assert normalize_name("Nestlé S.A.") == "nestle"
    assert normalize_name("OOO 'Sber-Kapital'", strip_legal=False) == "ooo sber kapital"


def test_real_list_matches_and_variants(container):
    idx = container.screening
    idx.ensure_loaded()
    assert idx.size > 2000 and set(idx.counts()) >= {"ofac_sdn", "un_sc", "uk_sanctions", "eu_fsf", "us_congress"}
    hits = idx.screen("Sberbank of Russia", entity_type="entity")
    assert {h.source_id for h in hits} >= {"ofac_sdn", "uk_sanctions"} and hits[0].score == 1.0
    assert hits[0].list_url.startswith("https://") and hits[0].list_retrieved_at
    assert idx.screen("russia of sberbank public joint stock company", entity_type="entity")  # word order
    assert idx.screen("Apple Inc.", entity_type="entity") == []
    assert idx.screen("The Boeing Company") == []
    assert idx.screen("") == []


def test_identifier_match_and_category_filters(container):
    idx = container.screening
    hits = idx.screen("Totally Different Name", identifiers=["SABRRUMM"])
    assert hits and hits[0].method.startswith("identifier") and hits[0].score == 1.0
    assert all(h.category == "pep" for h in idx.screen("David Joyce", categories=("pep",)))
    assert idx.screen("David Joyce", categories=("sanctions",)) == []


def test_entity_type_penalty(container):
    idx = container.screening
    as_entity = idx.screen("David Joyce", entity_type="entity", categories=("pep",))
    as_person = idx.screen("David Joyce", entity_type="individual", categories=("pep",))
    assert as_person and (not as_entity or as_entity[0].score < as_person[0].score)


def test_generic_overlap_is_not_a_match_but_person_middle_name_is(container):
    """Organisation subset matches need the shared words to carry the information (IDF mass); personal names
    tolerate a missing middle name. Regression cases come from the full-list FPR evaluation (docs/evaluation.md)."""
    idx = container.screening
    sber = idx.screen("Sberbank of Russia", entity_type="entity")[0]
    # generic words added around a distinctive listed name still match (distinctive token present)
    assert any(h.entry_id == sber.entry_id for h in idx.screen("Sberbank Russia Holding", entity_type="entity"))
    # look-alike strings whose distinctive word differs are discounted below threshold
    assert not idx.screen("Sberbonk Rossiya Capital Partners", entity_type="entity", categories=("sanctions",))
    # a person listed without middle name is still surfaced for the reviewer
    assert idx.screen("Joyce David Leon", entity_type="individual", categories=("pep",))


def test_index_renormalises_names_from_older_versions(container):
    from sqlalchemy import update

    from sentinel.db.models import WatchlistName

    with container.db.session() as s:
        s.execute(update(WatchlistName).values(normalized="stale-normalisation"))
    idx = container.screening
    idx._version = None  # force reload
    assert idx.screen("Sberbank of Russia", entity_type="entity")
