from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from sentinel.config import Settings
from sentinel.db.models import Case, LLMUsage
from sentinel.domain import Chunk, Document, ScoredChunk
from sentinel.embeddings.base import HashingEmbedder, OpenAIEmbedder, build_embedder
from sentinel.ingestion.chunking import chunk_document
from sentinel.investigation.evidence import EvidenceWriter
from sentinel.llm.anthropic_client import AnthropicClient
from sentinel.llm.base import LLMClient, LLMError, LLMResponse, Message, StructuredOutputError, extract_json
from sentinel.llm.extractive import ExtractiveLLM
from sentinel.llm.metering import MeteredLLM, llm_context
from sentinel.llm.openai_client import OpenAIClient
from sentinel.llm.router import LLMRouter, build_client, build_llm
from sentinel.rag.bm25 import BM25Index, light_stem, tokenize
from sentinel.rag.grounding import GroundingChecker
from sentinel.rag.retriever import analyze_query, mmr, rrf
from sentinel.vectorstore.base import InMemoryVectorStore, matches


def _chunk(cid, text, **meta):
    return Chunk(cid, cid.split("#")[0], text, meta)


def test_chunking_embedding_and_stores():
    para = " ".join(f"Sentence {i} discusses export control risk." for i in range(60))
    chunks = chunk_document(Document("d", "Doc", f"# Intro\n\n{para}\n\n# Next\n\nShort."), 300, 60)
    assert len(chunks) > 5 and chunks[0].id == "d#0" and chunks[-1].text.startswith("Doc > Next:")
    with pytest.raises(ValueError):
        chunk_document(Document("d", "T", "x"), 100, 100)
    e = HashingEmbedder(128)
    m = e.embed(["sanctions exposure", "sanctions exposure", "chocolate cake"])
    assert np.allclose(np.linalg.norm(m, axis=1), 1, atol=1e-5) and float(m[0] @ m[1]) == pytest.approx(1.0, abs=1e-5)
    assert build_embedder("hashing", 64).dim == 64
    with pytest.raises(ValueError):
        build_embedder("nope")
    stub = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda model, input: SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0]) for _ in input])
        )
    )
    assert OpenAIEmbedder(client=stub, dim=2).embed(["a"]).shape == (1, 2)
    s = InMemoryVectorStore(128)
    cs = [_chunk("a#0", "sanctions on director", kind="x"), _chunk("b#0", "revenue growth", kind="y")]
    s.upsert(cs, e.embed([c.text for c in cs]))
    assert s.search(e.embed_one("director sanctions"), 1)[0].chunk.id == "a#0"
    assert s.search(e.embed_one("director"), 5, {"kind": "y"})[0].chunk.id == "b#0"
    assert s.delete_document("a") == 1 and s.count() == 1 and len(s.all_chunks()) == 1
    assert matches({"y": 2025}, {"y": {"$gte": 2024, "$lte": 2025}}) and not matches({"k": "a"}, {"k": {"$ne": "a"}})


def test_sparse_fusion_mmr_query_analysis_grounding():
    idx = BM25Index()
    idx.add([_chunk("a#0", "Boeing charged with fraud"), _chunk("b#0", "Apple revenue")])
    assert idx.search("Boeing fraud", 2)[0].chunk.id == "a#0" and idx.delete_document("b") == 1
    assert light_stem("certifications") == "certification" and tokenize("The Owners of it") == ["owner"]
    a, b, c = (ScoredChunk(_chunk(f"{x}#0", x), 1.0) for x in "abc")
    assert rrf([[a, b], [b, c]])[0].chunk.id == "b#0"
    vecs = np.array([[1, 0], [0.99, 0.01], [0, 1]], dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    assert mmr(np.array([1, 0.2], dtype=np.float32), [a, b, c], vecs, k=2, lam=0.5)[1] == 2
    plan = analyze_query("What legal proceedings and sanctions risks are disclosed?")
    assert "filing_text" in plan.doc_types and "sanctions_record" in plan.doc_types
    chunks = [_chunk("d#0", "Boeing agreed to pay 2.5 billion to resolve the fraud conspiracy charge.")]
    gc = GroundingChecker()
    assert (
        gc.check("Boeing agreed to pay 2.5 billion to resolve the fraud conspiracy charge. [d#0]", chunks).score == 1.0
    )
    bad = gc.check("Boeing is owned by the Moon government entirely. [d#0] It is safe [x#9].", chunks)
    assert bad.score == 0.0 and bad.invalid_citations == ["x#9"]
    assert gc.check("INSUFFICIENT_EVIDENCE: none", chunks).score == 1.0 and gc.check("ok", chunks).score == 0.0


def test_case_index_is_case_scoped(container):
    with container.db.session() as s:
        s.add_all(
            [
                Case(id="case-a", entity_query="a", created_by="alice"),
                Case(id="case-b", entity_query="b", created_by="alice"),
            ]
        )
    doc = container.fetcher.get("sec_edgar", "https://www.sec.gov/files/company_tickers.json")
    EvidenceWriter(container.db, "case-a").add(doc, "registry_profile", "A", "Alpha Corp builds rockets in Texas.")
    EvidenceWriter(container.db, "case-b").add(doc, "registry_profile", "B", "Beta Corp sells bananas in Peru.")
    assert container.case_index.build("case-a") == 1 and container.case_index.build("case-a") == 0  # idempotent
    container.case_index.build("case-b")
    hits = container.case_index.retriever("case-a").retrieve("bananas Peru", k=5)
    assert all("Beta" not in h.chunk.text for h in hits)
    ev = EvidenceWriter(container.db, "case-a").evidence()[0]
    assert container.case_index.evidence_for_citation("case-a", f"{ev.id[:12]}#0") == ev.id
    assert ev.source_url == doc.url and ev.retrieved_at is not None  # lineage taken from the source document
    with pytest.raises(ValueError, match="persisted source document"):
        EvidenceWriter(container.db, "case-a").add("missing-doc", "x", "t", "c")


class Plan(BaseModel):
    steps: list[str]
    n: int


def test_extractive_backend_and_structured_output():
    llm = ExtractiveLLM()
    out = llm.complete(
        [
            Message(
                "user",
                "QUESTION: which entity consolidates apple operations international\n\nCONTEXT:\n"
                "[d#0] GLEIF > Level 2: APPLE OPERATIONS INTERNATIONAL LIMITED is directly consolidated by Apple Inc. "
                "The record was last updated by the managing LOU.\n[d#1] Exhibit 21 lists Braeburn Capital, Inc.",
            )
        ]
    ).text
    assert "directly consolidated by Apple Inc." in out and "[d#0]" in out
    assert "managing LOU" not in out and "GLEIF > Level 2" not in out
    assert llm.complete([Message("user", "QUESTION: x\nCONTEXT:\n")]).text.startswith("INSUFFICIENT_EVIDENCE")
    assert ExtractiveLLM(scripted={"Plan": lambda m: {"steps": ["a"], "n": 2}}).complete_structured(
        [Message("user", "x")], Plan
    ) == Plan(steps=["a"], n=2)
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1} and extract_json("x [1, 2] y") == [1, 2]
    with pytest.raises(StructuredOutputError):
        extract_json("no json")

    class Broken(LLMClient):
        provider, model = "b", "m"

        def complete(self, messages, **kw):
            return LLMResponse('{"steps": "nope"}', "b", "m")

    with pytest.raises(StructuredOutputError):
        Broken().complete_structured([Message("user", "x")], Plan, repair_attempts=1)


class _Fail(LLMClient):
    provider, model = "fail", "m"

    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kw):
        self.calls += 1
        raise LLMError("down")


def test_router_fallback_breaker_and_factory():
    bad = _Fail()
    r = LLMRouter([bad, ExtractiveLLM()], breaker_threshold=2, reset_after_s=60)
    for _ in range(3):
        assert r.complete([Message("user", "QUESTION: a\nCONTEXT:\n[x#0] a b c")]).provider == "extractive"
    assert bad.calls == 2
    with pytest.raises(LLMError, match="All LLM providers failed"):
        LLMRouter([_Fail()]).complete([Message("user", "x")])
    s = Settings(_env_file=None, llm_provider="extractive")
    assert isinstance(build_llm(s), ExtractiveLLM)
    for prov in ("anthropic", "openai", "vertex"):
        with pytest.raises(LLMError):
            build_client(prov, s)
    with pytest.raises(ValueError):
        build_client("nope", s)
    r2 = build_llm(
        Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="k", llm_fallbacks=["extractive"])
    )
    assert [c.provider for c in r2.clients] == ["anthropic", "extractive"]
    # the extractive backend is appended as last resort even when no fallback is configured
    r3 = build_llm(Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="k"))
    assert [c.provider for c in r3.clients] == ["anthropic", "extractive"]
    from sentinel.observability.metrics import METRICS

    LLMRouter([_Fail(), ExtractiveLLM()]).complete([Message("user", "QUESTION: a\nCONTEXT:\n[x#0] a b c")])
    assert "llm_errors_total" in METRICS.render_prometheus() and "llm_fallbacks_total" in METRICS.render_prometheus()


def test_anthropic_and_openai_clients_with_stubs(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    class Overloaded(Exception):
        status_code = 529

    def create(**kw):
        calls["n"] += 1
        assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
        if calls["n"] == 1:
            raise Overloaded("busy")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
            stop_reason="end_turn",
        )

    c = AnthropicClient("k", "claude-x", client=SimpleNamespace(messages=SimpleNamespace(create=create)), max_retries=2)
    assert c.complete([Message("user", "hi")], system="sys").usage.total == 7 and calls["n"] == 2

    class BadReq(Exception):
        status_code = 400

    def bad(**kw):
        raise BadReq("x")

    with pytest.raises(LLMError):
        AnthropicClient("k", "m", client=SimpleNamespace(messages=SimpleNamespace(create=bad))).complete(
            [Message("user", "x")]
        )

    def oc(**kw):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
        )

    o = OpenAIClient("k", "gpt", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=oc))))
    assert o.complete([Message("user", "x")], system="s").text == "ok"


def test_metering_records_usage_and_cost(container):
    class Priced(LLMClient):
        provider, model = "anthropic", "claude-sonnet-4-5"

        def complete(self, messages, **kw):
            from sentinel.llm.base import Usage

            return LLMResponse("x", self.provider, self.model, Usage(1000, 500))

    m = MeteredLLM(Priced(), container.db, {"claude-sonnet-4-5": (3.0, 15.0)})
    with llm_context("case-x", "unit-test"):
        m.complete([Message("user", "hi")])
    with container.db.session() as s:
        row = s.scalars(select(LLMUsage).where(LLMUsage.case_id == "case-x")).one()
    assert row.purpose == "unit-test" and row.cost_usd == pytest.approx(0.0105)
