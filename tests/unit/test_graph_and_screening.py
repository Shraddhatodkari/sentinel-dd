import pytest

from sentinel.domain import Chunk
from sentinel.graphrag.extractor import (
    CompositeExtractor,
    Extraction,
    LLMExtractor,
    PatternExtractor,
    Triple,
    clean,
)
from sentinel.graphrag.graph import KnowledgeGraph, normalize
from sentinel.llm.fake import FakeLLM
from sentinel.screening import Screener, WatchlistEntry, similarity
from sentinel.structured import EntityRegistry

TEXT = (
    "Alpha Trading FZE is incorporated in United Arab Emirates. Beta Holdings Ltd holds 70% of Alpha Trading FZE. "
    "Beta Holdings Ltd is incorporated in British Virgin Islands. Beta Holdings Ltd is a wholly owned subsidiary "
    "of Gamma Nominees Ltd. Gamma Nominees Ltd is incorporated in Panama. John Doe holds 100% of Gamma Nominees Ltd. "
    "Jane Roe serves as director of Alpha Trading FZE. John Doe is listed on the Global Consolidated sanctions list. "
    "Jane Roe is a politically exposed person. Alpha Trading FZE has a supply contract with Delta Inc. "
    "Kappa Ltd is a subsidiary of Beta Holdings Ltd."
)


@pytest.fixture
def kg():
    g = KnowledgeGraph()
    g.add(PatternExtractor().extract(Chunk("doc#0", "doc", "Title > H: " + TEXT)))
    return g


def test_pattern_extraction(kg):
    rels = {(u, d["relation"], v) for u, v, d in kg.g.edges(data=True)}
    assert ("Beta Holdings Ltd", "OWNS", "Alpha Trading FZE") in rels
    assert ("Gamma Nominees Ltd", "OWNS", "Beta Holdings Ltd") in rels
    assert ("Jane Roe", "OFFICER_OF", "Alpha Trading FZE") in rels
    assert ("Alpha Trading FZE", "PARTNER_OF", "Delta Inc") in rels
    assert ("Beta Holdings Ltd", "OWNS", "Kappa Ltd") in rels
    assert kg.g.nodes["John Doe"]["sanctioned"] and kg.g.nodes["Jane Roe"]["pep"]
    assert kg.g.nodes["Jane Roe"]["kind"] == "person"
    assert kg.stats()["nodes"] >= 9


def test_ubo_exposure_offshore_communities(kg):
    ubo = kg.ultimate_owners("Alpha Trading FZE")
    assert ubo[0]["owner"] == "John Doe" and ubo[0]["effective_pct"] == 70.0
    exp = kg.exposure("Alpha Trading FZE", "sanctioned")
    assert exp[0].target == "John Doe" and exp[0].hops == 3 and exp[0].reason == "Global Consolidated"
    assert kg.exposure("Alpha Trading FZE", "sanctioned", max_hops=2) == []
    assert kg.offshore_layers("Alpha Trading FZE") == 2
    comms = kg.communities()
    assert any("Alpha Trading FZE" in c for c in comms)
    summary = kg.community_summary(next(c for c in comms if "John Doe" in c))
    assert "Flagged parties" in summary
    facts = kg.facts_for("Alpha Trading FZE")
    assert any("SANCTIONED" in f for f in facts) is False  # John Doe is 3 hops away (hops=2)
    assert any("SANCTIONED" in f for f in kg.facts_for("Alpha Trading FZE", hops=3))
    sub = kg.subgraph_json("Alpha Trading FZE", 1)
    assert {"nodes", "edges"} <= sub.keys() and sub["edges"]
    assert "John Doe" in kg.neighbors_within("Alpha Trading FZE", 3, relations=("OWNS",))
    assert "Delta Inc" not in kg.neighbors_within("Alpha Trading FZE", 3, relations=("OWNS", "OFFICER_OF"))


def test_unknown_entity_queries_are_empty(kg):
    assert kg.ultimate_owners("Nobody") == [] and kg.exposure("Nobody", "pep") == []
    assert kg.offshore_layers("Nobody") == 0 and kg.facts_for("Nobody") == []
    assert kg.subgraph_json("Nobody") == {"nodes": [], "edges": []} and kg.neighbors_within("Nobody") == []
    assert KnowledgeGraph().communities() == [] and kg.owners_of("Nobody") == []


def test_entity_resolution_and_cycles():
    g = KnowledgeGraph()
    assert g.resolve("Acme Holdings Pvt. Ltd.") == g.resolve("ACME Holdings Private Limited")
    assert g.resolve("Acme Holding Ltd") == "Acme Holdings Pvt. Ltd."  # normalised form match
    assert g.resolve("", create=True) is None and g.resolve("Zeta", create=False) is None
    g.add(
        Extraction(
            triples=[
                Triple("A Co", "OWNS", "B Co", {"pct": 50}),
                Triple("B Co", "OWNS", "A Co", {"pct": 50}),
                Triple("A Co", "OWNS", "A Co", {}),
            ]
        )
    )
    assert isinstance(g.ultimate_owners("A Co"), list)  # circular ownership terminates
    g.set_flag("A Co", sanctioned=True)
    assert g.g.nodes["A Co"]["sanctioned"]
    assert normalize("Foo-Bar Inc.") == "foo bar"


def test_clean_and_llm_extractor():
    assert clean("The Acme Corp and") == "Acme Corp"
    scripted = FakeLLM(
        scripted={
            "_LLMExtraction": lambda m: {
                "triples": [
                    {"subject": "X", "relation": "OWNS", "object": "Y", "pct": 10},
                    {"subject": "X", "relation": "LIKES", "object": "Z"},
                ],
                "sanctioned": ["Bad Guy"],
                "peps": ["Pol Itician"],
            }
        }
    )
    ex = CompositeExtractor(PatternExtractor(), LLMExtractor(scripted)).extract(Chunk("c#0", "c", "text"))
    assert [(t.subject, t.relation, t.obj) for t in ex.triples] == [("X", "OWNS", "Y")]
    assert {f.name for f in ex.facts} == {"Bad Guy", "Pol Itician"}

    class Bad(FakeLLM):
        def complete(self, messages, **kw):
            from sentinel.llm.base import LLMResponse

            return LLMResponse("not json", "bad", "m")

    assert LLMExtractor(Bad()).extract(Chunk("c#0", "c", "t")).triples == []


def test_screening_fuzzy_aliases():
    s = Screener([WatchlistEntry("Viktor Rask", "GC", "sanctions", ["Rask, Viktor"], reference="R1")])
    assert s.screen("Rask Viktor")[0].score == 1.0
    assert s.screen("Viktor Rasks")  # typo tolerance
    assert s.screen("Victoria Ross") == []
    assert len(s.screen_many(["Viktor Rask", "Viktor Rask", "Nobody"])) == 1
    assert similarity("", "a") == 0.0


def test_entity_registry_lookup():
    reg = EntityRegistry([{"name": "Helios Trade FZE", "country": "AE"}])
    assert reg.lookup("helios trade")["country"] == "AE"
    assert reg.lookup("Helios Trad FZE")["country"] == "AE"
    assert reg.lookup("Totally Different") is None
    reg.upsert({"name": "New Co", "country": "US"})
    assert set(reg.names()) == {"Helios Trade FZE", "New Co"}
