"""Entity & relation extraction for GraphRAG.

Two complementary extractors feed the knowledge graph:
* :class:`PatternExtractor` — high-precision linguistic patterns for corporate-registry language
  (ownership, officers, incorporation, sanctions, PEP). Deterministic & auditable.
* :class:`LLMExtractor` — schema-constrained LLM extraction (Claude / GPT) for open-ended text.
Results are merged; every triple keeps its source chunk id for citation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from sentinel.domain import Chunk
from sentinel.llm.base import LLMClient, Message, StructuredOutputError

_E = r"(?P<{n}>\b[A-Z][A-Za-z0-9&\-]*(?:\s+(?:[A-Z][A-Za-z0-9&\-]*|of|and)){{0,5}})"
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def E(name: str) -> str:
    return _E.format(n=name)


@dataclass
class Triple:
    subject: str
    relation: str  # OWNS | OFFICER_OF | INCORPORATED_IN | PARTNER_OF
    obj: str
    attrs: dict[str, object] = field(default_factory=dict)
    source: str = ""


@dataclass
class EntityFact:
    name: str
    kind: str  # company | person | country | list
    flags: dict[str, object] = field(default_factory=dict)
    source: str = ""


@dataclass
class Extraction:
    triples: list[Triple] = field(default_factory=list)
    facts: list[EntityFact] = field(default_factory=list)


_COUNTRY = r"(?P<country>India|United States|United Kingdom|United Arab Emirates|British Virgin Islands|Cayman Islands|Panama|Seychelles|Singapore|Cyprus|Germany|Delaware|Mauritius)"  # noqa: E501
_ROLE = (
    r"(?P<role>director|CEO|chief executive officer|CFO|chairman|chairperson|managing director|board member|founder)"
)

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(E("sub") + r",?\s+is a wholly[- ]owned subsidiary of\s+" + E("obj")), "subsidiary100"),
    (re.compile(E("sub") + r",?\s+is a subsidiary of\s+" + E("obj")), "subsidiary"),
    (
        re.compile(
            E("sub") + r"\s+(?:holds|owns)\s+(?P<pct>\d{1,3}(?:\.\d+)?)%\s+(?:of the shares\s+)?(?:of|in)\s+" + E("obj")
        ),
        "owns_pct",
    ),
    (
        re.compile(
            E("sub")
            + r"\s+(?:serves as|is|was appointed(?: as)?)\s+(?:a |the )?"
            + _ROLE
            + r"\s+(?:of|at)\s+"
            + E("obj")
        ),
        "officer",
    ),
    (
        re.compile(E("sub") + r",?\s+(?:is|was)\s+(?:incorporated|registered|domiciled)\s+in\s+(?:the\s+)?" + _COUNTRY),
        "incorporated",
    ),
    (
        re.compile(
            E("sub") + r"\s+(?:is|was)\s+(?:listed|designated)\s+on\s+the\s+(?P<list>[A-Z][\w\- ]{1,40}?)\s+"
            r"sanctions list"
        ),
        "sanctioned",
    ),
    (re.compile(E("sub") + r",?\s+(?:is|was)\s+a\s+politically exposed person"), "pep"),
    (
        re.compile(
            E("sub") + r"\s+has\s+(?:a|an)\s+(?:supply|distribution|service|joint venture)\s+(?:contract|agreement)"
            r"\s+with\s+" + E("obj")
        ),
        "partner",
    ),
]

_TRAILING_NOISE = re.compile(r"\s+(?:and|of)$")
_LEADING_NOISE = re.compile(r"^(?:The|In|On|Since|As|According|Records|Filings|Per)\s+")


def clean(name: str) -> str:
    name = _TRAILING_NOISE.sub("", name.strip().rstrip(".,;"))
    prev = None
    while prev != name:
        prev = name
        name = _LEADING_NOISE.sub("", name)
    return name


class PatternExtractor:
    def extract(self, chunk: Chunk) -> Extraction:
        out = Extraction()
        text = chunk.text.split(": ", 1)[-1]  # drop "Title > Heading:" prefix
        for sentence in _SENTENCE.split(text):
            self._extract_sentence(sentence, chunk.id, out)
        return out

    def _extract_sentence(self, text: str, source: str, out: Extraction) -> None:
        for pat, kind in PATTERNS:
            for m in pat.finditer(text):
                g = m.groupdict()
                sub = clean(g["sub"])
                if kind in ("subsidiary100", "subsidiary"):
                    pct = 100.0 if kind == "subsidiary100" else 51.0
                    out.triples.append(Triple(clean(g["obj"]), "OWNS", sub, {"pct": pct}, source))
                elif kind == "owns_pct":
                    out.triples.append(Triple(sub, "OWNS", clean(g["obj"]), {"pct": float(g["pct"])}, source))
                elif kind == "officer":
                    out.facts.append(EntityFact(sub, "person", {}, source))
                    out.triples.append(Triple(sub, "OFFICER_OF", clean(g["obj"]), {"role": g["role"]}, source))
                elif kind == "incorporated":
                    out.triples.append(Triple(sub, "INCORPORATED_IN", g["country"], {}, source))
                    out.facts.append(EntityFact(g["country"], "country", {}, source))
                elif kind == "sanctioned":
                    out.facts.append(
                        EntityFact(sub, "unknown", {"sanctioned": True, "sanctions_list": g["list"].strip()}, source)
                    )
                elif kind == "pep":
                    out.facts.append(EntityFact(sub, "person", {"pep": True}, source))
                elif kind == "partner":
                    out.triples.append(Triple(sub, "PARTNER_OF", clean(g["obj"]), {}, source))


class _LLMTriple(BaseModel):
    subject: str
    relation: str = Field(description="OWNS | OFFICER_OF | INCORPORATED_IN | PARTNER_OF")
    object: str
    pct: float | None = None


class _LLMExtraction(BaseModel):
    triples: list[_LLMTriple] = Field(default_factory=list)
    sanctioned: list[str] = Field(default_factory=list)
    peps: list[str] = Field(default_factory=list)


class LLMExtractor:
    ALLOWED = {"OWNS", "OFFICER_OF", "INCORPORATED_IN", "PARTNER_OF"}

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def extract(self, chunk: Chunk) -> Extraction:
        try:
            res = self.llm.complete_structured(
                [Message("user", f"Extract corporate relationships from:\n{chunk.text}")],
                _LLMExtraction,
                system="You extract knowledge-graph triples for corporate due diligence.",
            )
        except StructuredOutputError:
            return Extraction()
        out = Extraction()
        for t in res.triples:
            if t.relation in self.ALLOWED and t.subject and t.object:
                attrs: dict[str, object] = {"pct": t.pct} if t.pct is not None else {}
                out.triples.append(Triple(t.subject, t.relation, t.object, attrs, chunk.id))
        out.facts += [EntityFact(n, "unknown", {"sanctioned": True}, chunk.id) for n in res.sanctioned]
        out.facts += [EntityFact(n, "person", {"pep": True}, chunk.id) for n in res.peps]
        return out


class CompositeExtractor:
    def __init__(self, *extractors: PatternExtractor | LLMExtractor) -> None:
        self.extractors = extractors

    def extract(self, chunk: Chunk) -> Extraction:
        merged = Extraction()
        for ex in self.extractors:
            r = ex.extract(chunk)
            merged.triples += r.triples
            merged.facts += r.facts
        return merged
