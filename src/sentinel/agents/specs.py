"""Agent contracts: purpose, inputs, outputs, tools, permissions, failure modes, guardrails, metrics, fallback,
observability and escalation criteria for every node of the investigation graph. Served at ``GET /api/agents``
and rendered in the dashboard; kept next to the code so they cannot drift silently (see tests)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    name: str
    purpose: str
    inputs: list[str]
    outputs: list[str]
    tools: list[str]
    permissions: list[str]
    failure_modes: list[str]
    guardrails: list[str]
    metrics: list[str]
    fallback: str
    escalation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


READ_ONLY_NET = "read-only HTTPS GET to allow-listed authoritative sources via the lineage fetcher"

SPECS: dict[str, AgentSpec] = {
    s.name: s
    for s in [
        AgentSpec(
            "intake",
            "Validate the request and open the case",
            ["query", "identifier hints", "question"],
            ["case status=running"],
            [],
            ["case:create (checked by API)"],
            ["prompt injection in query/question"],
            ["input guardrails (injection scoring, DLP, length)"],
            ["agent_node_runs", "guardrail_blocks"],
            "request rejected with 422; audit event case.blocked",
            "n/a",
        ),
        AgentSpec(
            "resolve_entity",
            "Resolve the query to an authoritative legal entity (SEC CIK and/or GLEIF LEI)",
            ["query", "hints"],
            ["resolution {name, cik, lei, confidence, ambiguous, candidates}"],
            ["EDGAR ticker map", "EDGAR submissions", "GLEIF full-text search", "GLEIF LEI lookup"],
            [READ_ONLY_NET],
            ["no match", "ambiguous candidates", "source outage"],
            ["deterministic fuzzy scoring; no LLM"],
            ["resolution confidence"],
            "screen the raw query name; mark registry coverage unavailable",
            "confidence < 0.85 or ambiguity → policy TPRM-DAT-02 (REVIEW)",
        ),
        AgentSpec(
            "planner",
            "Produce research questions for filing/evidence RAG",
            ["resolution", "question"],
            ["plan (3–6 questions)"],
            ["LLM (structured output)"],
            ["none"],
            ["invalid structured output", "off-topic questions"],
            ["schema validation + self-repair; questions must name the entity"],
            ["llm_tokens", "llm_cost_usd"],
            "deterministic default plan",
            "n/a",
        ),
        AgentSpec(
            "corporate_registry",
            "Collect registry facts, consolidation parents/children, Exhibit 21 subsidiaries and Form 3/4 insiders",
            ["resolution"],
            ["Evidence", "Relationships"],
            ["EDGAR submissions", "EDGAR Exhibit 21", "EDGAR Forms 3/4", "GLEIF Level 1/2"],
            [READ_ONLY_NET],
            ["source outage", "missing exhibit", "XML parse errors"],
            ["facts stored verbatim with locator"],
            ["evidence_count", "source_requests_total"],
            "partial coverage recorded; case continues",
            "registry unavailable → TPRM-DAT-04",
        ),
        AgentSpec(
            "financials",
            "Extract reported annual financials (XBRL) and compute indicators + peer anomaly",
            ["resolution"],
            ["Evidence (financial facts)", "Signals (financial_stress)", "peer anomaly result"],
            ["EDGAR company facts", "peer anomaly model (IsolationForest, real SEC frames)"],
            [READ_ONLY_NET],
            ["non-SEC entity", "missing concepts"],
            ["reported values only; no imputation"],
            ["financial coverage"],
            "factor marked data-unavailable; confidence reduced",
            "n/a",
        ),
        AgentSpec(
            "adverse_media",
            "Find enforcement releases and news mentioning the entity; classify topic and the "
            "entity's role (subject / victim / mentioned)",
            ["resolution"],
            ["Evidence", "Signals (adverse_media)"],
            ["DOJ press releases API", "GDELT DOC API"],
            [READ_ONLY_NET],
            ["rate limiting (GDELT)", "false associations (homonyms, victims)"],
            ["relevance check", "subject-role classifier", "news is evidence of publication, not proof"],
            ["media items, flagged items"],
            "partial coverage recorded",
            "subject-role adverse items feed the scorecard",
        ),
        AgentSpec(
            "filings",
            "Extract annual-report sections (business, risk factors, cybersecurity, legal proceedings)",
            ["resolution"],
            ["Evidence (filing_text)"],
            ["EDGAR filing documents"],
            [READ_ONLY_NET],
            ["non-SEC entity", "non-standard filing layout"],
            ["verbatim text with section locator"],
            ["sections extracted"],
            "not_applicable / partial recorded",
            "n/a",
        ),
        AgentSpec(
            "screening",
            "Screen subject, aliases and related parties against sanctions and PEP lists",
            ["resolution", "relationships"],
            ["Evidence (list records)", "Signals (potential matches)"],
            ["screening index over current OFAC/UN/UK/EU/PEP versions"],
            ["read-only database"],
            ["stale lists", "homonyms"],
            ["matches are *potential* matches requiring human adjudication"],
            ["hits by list", "list freshness"],
            "never skipped; stale lists force REVIEW",
            "any sanctions potential match → ESCALATE (TPRM-SAN-01/02)",
        ),
        AgentSpec(
            "research",
            "Answer the plan questions from case evidence only (hybrid RAG + grounding check)",
            ["plan", "case evidence index"],
            ["research answers with citations + grounding scores"],
            ["case-scoped hybrid retriever (dense + BM25 + RRF + MMR)", "LLM"],
            ["case-scoped read"],
            ["insufficient evidence", "indirect prompt injection in documents"],
            ["context sanitisation", "mandatory citations", "abstention (INSUFFICIENT_EVIDENCE)"],
            ["rag_grounding_score", "indirect_injection_blocked"],
            "abstain",
            "low grounding → critic loop",
        ),
        AgentSpec(
            "research_refine",
            "Targeted re-research of weakly grounded answers requested by the critic",
            ["follow_ups", "case evidence index"],
            ["additional research answers"],
            ["case-scoped hybrid retriever", "LLM"],
            ["case-scoped read"],
            ["no better evidence exists"],
            ["same as research; bounded by the critic's iteration limit"],
            ["iterations"],
            "keep the original answers",
            "persistent low grounding → TPRM-AI-01",
        ),
        AgentSpec(
            "graph_analyst",
            "Build the evidence-backed relationship graph; find exposure paths to flagged parties",
            ["relationships", "screening signals"],
            ["graph summary, exposure paths, UI graph"],
            ["networkx"],
            ["database read"],
            ["large groups (truncated)"],
            ["every edge carries its evidence id"],
            ["nodes, edges, exposure paths"],
            "empty graph",
            "exposure paths surface in policy reasons",
        ),
        AgentSpec(
            "risk_assessor",
            "Compute the versioned scorecard (factors, points, evidence, confidence)",
            ["signals", "coverage", "resolution", "peer anomaly"],
            ["RiskAssessment"],
            ["scorecard"],
            ["database write"],
            ["missing data"],
            ["deterministic & reproducible; no LLM"],
            ["score, band"],
            "n/a (failure fails the case → retry)",
            "band feeds policy",
        ),
        AgentSpec(
            "report_writer",
            "Write the case memo separating FACT / SIGNAL / ASSESSMENT / POLICY layers",
            ["all of the above"],
            ["report (markdown)", "grounding score"],
            ["LLM", "case retriever"],
            ["none"],
            ["hallucination", "PII leakage"],
            ["grounding check", "output guardrails", "DLP masking"],
            ["report_grounding"],
            "extractive narrative",
            "grounding < threshold → TPRM-AI-01",
        ),
        AgentSpec(
            "critic",
            "Detect weakly-grounded research and request targeted re-research (bounded loop)",
            ["research", "report_grounding"],
            ["follow_ups"],
            ["LLM (structured)"],
            ["none"],
            ["infinite loop"],
            ["max 2 iterations"],
            ["iterations"],
            "stop looping",
            "n/a",
        ),
        AgentSpec(
            "policy",
            "Apply the versioned TPRM policy; open the human review",
            ["assessment", "coverage", "screening", "grounding"],
            ["PolicyDecision", "Review (pending)"],
            ["policy engine"],
            ["database write"],
            ["n/a"],
            ["policy engine, not LLM, decides routing"],
            ["outcomes by rule"],
            "n/a",
            "REVIEW → reviewer; ESCALATE → senior_reviewer",
        ),
        AgentSpec(
            "human_review",
            "Pause until an authorised reviewer decides (maker-checker)",
            ["review"],
            ["human decision"],
            [],
            ["review:decide / review:decide_escalated"],
            ["reviewer absence"],
            ["requester/creator cannot decide; tier-based role check"],
            ["pending reviews, time-to-decision"],
            "case remains awaiting_review",
            "n/a",
        ),
        AgentSpec(
            "finalize",
            "Close the case and write the final audit record",
            ["review or policy"],
            ["case status"],
            [],
            ["database write"],
            ["n/a"],
            ["idempotent"],
            ["cases_closed_total"],
            "n/a",
            "n/a",
        ),
    ]
}
