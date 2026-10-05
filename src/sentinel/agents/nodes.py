"""Investigation agents (LangGraph nodes). See :mod:`sentinel.agents.specs` for each agent's contract.

Layering is preserved end-to-end:
  FACT (Evidence rows) → ANALYTICAL SIGNAL (Signal rows) → RISK ASSESSMENT (scorecard) → POLICY DECISION
  (policy engine) → HUMAN DECISION (Review). LLMs only plan and summarise *retrieved* evidence; they never
  create facts, scores or decisions.
"""

from __future__ import annotations

import functools
import time
import traceback
from collections.abc import Callable
from typing import Any

from langgraph.types import interrupt
from pydantic import BaseModel, Field
from sqlalchemy import select

from sentinel.agents.state import CaseState
from sentinel.container import Container
from sentinel.db.models import Case, CaseEvent, PolicyDecisionRecord, Relationship, Review, RiskAssessment, Signal
from sentinel.governance.policy import Outcome, PolicyInput
from sentinel.graphrag import case_graph
from sentinel.ingestion.reference import freshness
from sentinel.investigation import collectors as C
from sentinel.investigation.evidence import EvidenceWriter
from sentinel.investigation.resolution import Resolution
from sentinel.llm.base import Message, StructuredOutputError
from sentinel.llm.metering import llm_context
from sentinel.observability.logging import get_logger, log_event
from sentinel.observability.metrics import METRICS
from sentinel.prompts import CRITIC_SYSTEM, GROUNDED_QA_SYSTEM, PLANNER_SYSTEM, PROMPT_VERSION, REPORT_SYSTEM
from sentinel.rag.qa import GroundedQA
from sentinel.risk import scorecard
from sentinel.risk.financial import FinancialIndicators
from sentinel.risk.jurisdictions import load_policy, tier_for
from sentinel.risk.media import TAXONOMY_VERSION
from sentinel.screening import SCREENING_VERSION
from sentinel.sources.catalog import SANCTIONS_SOURCES
from sentinel.sources.http import SourceError
from sentinel.timeutil import iso, utcnow

# node name → coverage dimension it is responsible for (used when a degradable node fails outright)
COVERAGE_KEY = {"resolve_entity": "resolution", "filings": "filings_text"}
log = get_logger(__name__)


class ResearchPlan(BaseModel):
    questions: list[str] = Field(default_factory=list, description="3-6 focused research questions")


class Critique(BaseModel):
    follow_up_questions: list[str] = Field(default_factory=list)


def default_plan(name: str) -> list[str]:
    return [
        f"What legal proceedings, government investigations or regulatory actions does {name} disclose?",
        f"What risks relating to sanctions, export controls, anti-bribery or corruption does {name} disclose?",
        f"Who owns or controls {name}, and what are its parent and subsidiary relationships?",
        f"Has {name} disclosed any material cybersecurity incidents?",
        f"Are there sanctions or politically exposed person matches associated with {name}?",
    ]


class NodeFailure(Exception):
    pass


def traced(
    node: str, degrade: bool = False
) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Persist a CaseEvent per node run (duration, status, detail, error) and export metrics.

    ``degrade=True`` (data-gathering agents): unexpected errors are recorded as an *unavailable* coverage gap and
    the investigation continues — the gap then forces human review via policy. Core agents re-raise.
    """

    def deco(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        @functools.wraps(fn)
        def wrapper(self: Agents, state: CaseState) -> dict[str, Any]:
            started, t0 = utcnow(), time.perf_counter()
            status, error, out = "completed", None, {}
            try:
                with llm_context(state["case_id"], node):
                    out = fn(self, state)
                if any(v == "unavailable" for v in (out.get("coverage") or {}).values()):
                    status = "degraded"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                log_event(
                    log,
                    "agent_node_failed",
                    node=node,
                    case_id=state["case_id"],
                    error=error,
                    trace=traceback.format_exc(limit=5),
                )
                if not degrade:
                    self._event(state["case_id"], node, "failed", started, t0, {}, error)
                    METRICS.inc("agent_node_runs", node=node, status="failed")
                    raise NodeFailure(f"{node}: {error}") from exc
                key = COVERAGE_KEY.get(node, node)
                status, out = "degraded", {"coverage": {key: "unavailable"}, "errors": [f"{node}: {error}"]}
            detail = out.pop("_event", {})
            self._event(state["case_id"], node, status, started, t0, detail, error)
            METRICS.inc("agent_node_runs", node=node, status=status)
            METRICS.observe("agent_node_seconds", time.perf_counter() - t0, node=node)
            return out

        return wrapper

    return deco


class Agents:
    def __init__(self, c: Container, max_iterations: int = 2) -> None:
        self.c = c
        self.max_iterations = max_iterations

    # ------------------------------------------------------------------ helpers
    def _event(
        self, case_id: str, node: str, status: str, started: Any, t0: float, detail: dict[str, Any], error: str | None
    ) -> None:
        with self.c.db.session() as s:
            s.add(
                CaseEvent(
                    case_id=case_id,
                    node=node,
                    status=status,
                    started_at=started,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    detail=detail,
                    error=error,
                )
            )

    def _w(self, state: CaseState) -> EvidenceWriter:
        return EvidenceWriter(self.c.db, state["case_id"])

    @staticmethod
    def _res(state: CaseState) -> Resolution:
        r = dict(state.get("resolution") or {})
        return Resolution(**r) if r else Resolution(query=state["query"], name=None)

    def _profile(self, res: Resolution) -> Any:
        return self.c.edgar.profile(res.cik) if res.cik else None

    def _lei(self, res: Resolution) -> Any:
        return self.c.gleif.by_lei(res.lei) if res.lei else None

    # ------------------------------------------------------------------ intake
    @traced("intake")
    def intake(self, state: CaseState) -> dict[str, Any]:
        with self.c.db.session() as s:
            case = s.get(Case, state["case_id"])
            assert case is not None
            case.status, case.started_at, case.attempts = "running", utcnow(), case.attempts + 1
        self.c.audit.append(
            state["requested_by"],
            "case.started",
            "case",
            state["case_id"],
            {"query": state["query"]},
            tenant=state.get("tenant", "default"),
        )
        return {"status": "running", "iterations": 0, "_event": {"query": state["query"]}}

    # ------------------------------------------------------------------ resolution
    @traced("resolve_entity", degrade=True)
    def resolve_entity(self, state: CaseState) -> dict[str, Any]:
        hints = state.get("hints") or {}
        res, _profile, _lei = self.c.resolver.resolve(
            state["query"], cik=hints.get("cik"), lei=hints.get("lei"), ticker=hints.get("ticker")
        )
        with self.c.db.session() as s:
            case = s.get(Case, state["case_id"])
            assert case is not None
            case.entity_id, case.resolved_name = res.entity_id, res.name
        cov = "ok" if res.resolved and not res.errors else ("partial" if res.resolved else "unavailable")
        return {
            "resolution": res.to_dict(),
            "coverage": {"resolution": cov},
            "errors": res.errors,
            "_event": {
                "name": res.name,
                "cik": res.cik,
                "lei": res.lei,
                "confidence": res.confidence,
                "method": res.method,
                "ambiguous": res.ambiguous,
            },
        }

    # ------------------------------------------------------------------ planner
    @traced("planner", degrade=True)
    def planner(self, state: CaseState) -> dict[str, Any]:
        res = self._res(state)
        name = C.display_name(res)
        prior = self.prior_cases(state)
        try:
            plan = self.c.llm.complete_structured(
                [Message("user", f"Target entity: {name}\nAnalyst request: {state.get('question', '')}")],
                ResearchPlan,
                system=PLANNER_SYSTEM,
            )
            key = (name.split() or [""])[0].lower()
            questions = [q for q in plan.questions if key and key in q.lower()][:6]
        except StructuredOutputError:
            questions = []
        if len(questions) < 3:
            questions = default_plan(name)
        if state.get("question"):
            questions.append(state["question"])
        return {
            "plan": questions,
            "details": {"prior_cases": prior},
            "_event": {"questions": len(questions), "prior_cases": len(prior)},
        }

    def prior_cases(self, state: CaseState) -> list[dict[str, Any]]:
        res = state.get("resolution") or {}
        if not res.get("entity_id"):
            return []
        with self.c.db.session() as s:
            rows = s.scalars(
                select(Case)
                .where(
                    Case.entity_id == res["entity_id"],
                    Case.id != state["case_id"],
                    Case.tenant == state.get("tenant", "default"),
                )
                .order_by(Case.created_at.desc())
                .limit(5)
            ).all()
            return [
                {"case_id": r.id, "status": r.status, "risk_band": r.risk_band, "created_at": iso(r.created_at)}
                for r in rows
            ]

    # ------------------------------------------------------------------ data-gathering agents (parallel)
    @traced("corporate_registry", degrade=True)
    def corporate_registry(self, state: CaseState) -> dict[str, Any]:
        res, w = self._res(state), self._w(state)
        profile, lei = self._profile(res), self._lei(res)
        reg = C.collect_registry(w, res, profile, lei, self.c.edgar, self.c.gleif)
        ins = C.collect_insiders(w, profile, self.c.edgar)
        return {
            "coverage": {"corporate_registry": reg.status, "insiders": ins.status},
            "details": {"corporate_registry": reg.detail, "insiders": ins.detail},
            "errors": reg.errors + ins.errors[:5],
            "_event": {
                "evidence": len(reg.evidence_ids) + len(ins.evidence_ids),
                "registry": reg.status,
                "insiders": ins.status,
            },
        }

    @traced("financials", degrade=True)
    def financials(self, state: CaseState) -> dict[str, Any]:
        res, w = self._res(state), self._w(state)
        r = C.collect_financials(w, self._profile(res), self.c.edgar)
        peer: dict[str, Any] = {"available": False, "reason": "no SEC financial data"}
        if r.status in ("ok", "partial") and r.detail.get("indicators"):
            if self.c.peer_model is None:
                peer = {"available": False, "reason": "peer model not trained (run `sentinel ml train`)"}
            else:
                ind = FinancialIndicators(**r.detail["indicators"])
                peer = self.c.peer_model.score(ind) | {
                    "model_version": self.c.peer_model_version,
                    "evidence_ids": r.evidence_ids,
                }
                if peer.get("available"):
                    w.signal(
                        "peer_anomaly",
                        "financial profile is anomalous vs. SEC filer peers"
                        if peer["anomaly"]
                        else "financial profile within peer norms",
                        peer["score"],
                        "low" if peer["anomaly"] else "info",
                        0.6,
                        r.evidence_ids,
                        self.c.peer_model_version or "",
                        percentiles=peer["percentiles"],
                        extreme_features=peer["extreme_features"],
                    )
        return {
            "coverage": {"financials": r.status},
            "details": {"financials": r.detail},
            "peer": peer,
            "errors": r.errors,
            "_event": {"status": r.status, "peer_anomaly": peer.get("anomaly")},
        }

    @traced("adverse_media", degrade=True)
    def adverse_media(self, state: CaseState) -> dict[str, Any]:
        res = self._res(state)
        r = C.collect_media(self._w(state), res, self.c.doj, self.c.gdelt)
        return {
            "coverage": {"adverse_media": r.status},
            "details": {"adverse_media": r.detail},
            "errors": r.errors,
            "_event": {"status": r.status, **{k: r.detail.get(k) for k in ("relevant_items", "flagged")}},
        }

    @traced("filings", degrade=True)
    def filings(self, state: CaseState) -> dict[str, Any]:
        res = self._res(state)
        r = C.collect_filing_text(self._w(state), self._profile(res), self.c.edgar)
        return {
            "coverage": {"filings_text": r.status},
            "details": {"filings": r.detail},
            "errors": r.errors,
            "_event": {"status": r.status, "sections": r.detail.get("sections")},
        }

    # ------------------------------------------------------------------ screening
    @traced("screening")
    def screening(self, state: CaseState) -> dict[str, Any]:
        res, w = self._res(state), self._w(state)
        subject_names = [n for n in [state["query"], res.name, *res.aliases] if n]
        subject = res.name or state["query"]
        related: list[tuple[str, str, str]] = []
        for rel in w.relationships():
            if rel.relation == "INSIDER_OF":
                related.append((rel.subject, "insider", "individual"))
            elif rel.relation.startswith("CONSOLIDATES") and rel.object == subject:
                related.append((rel.subject, "consolidation_parent", "entity"))
            elif rel.relation.startswith("CONSOLIDATES"):
                related.append((rel.object, "consolidated_child", "entity"))
            elif rel.relation == "PARENT_OF":
                related.append((rel.object, "subsidiary", "entity"))
        order = {"consolidation_parent": 0, "insider": 1, "consolidated_child": 2, "subsidiary": 3}
        related = sorted(dict.fromkeys(related), key=lambda x: order.get(x[1], 9))
        r = C.collect_screening(
            w,
            self.c.screening,
            subject_names,
            related,
            self.c.settings.screening_review_threshold,
            [res.lei] if res.lei else [],
            self.c.settings.screening_max_related_parties,
        )
        fresh = {f["source_id"]: f for f in freshness(self.c.db)}
        stale = [
            sid
            for sid in SANCTIONS_SOURCES
            if fresh.get(sid, {}).get("enabled") and (fresh[sid]["stale"] or not fresh[sid]["current_records"])
        ]
        status = "ok" if not stale else "partial"
        if all(not fresh.get(sid, {}).get("current_records") for sid in SANCTIONS_SOURCES):
            status = "unavailable"
        r.detail["stale_sources"] = stale
        r.detail["list_freshness"] = {
            k: {"age_hours": v["age_hours"], "records": v["current_records"], "stale": v["stale"]}
            for k, v in fresh.items()
            if v["kind"] in ("sanctions", "pep")
        }
        return {
            "coverage": {"screening": status},
            "details": {"screening": r.detail},
            "_event": {
                "hits": r.detail["hits"],
                "related_screened": r.detail["related_screened"],
                "stale_sources": stale,
            },
        }

    # ------------------------------------------------------------------ research (RAG)
    def _qa(self, state: CaseState, questions: list[str]) -> list[dict[str, Any]]:
        self.c.case_index.build(state["case_id"])
        qa = GroundedQA(self.c.case_index.retriever(state["case_id"]), self.c.llm, self.c.guardrails, self.c.grounding)
        out = []
        for q in questions:
            ev = qa.answer(q, k=self.c.settings.retrieval_top_k)
            out.append(
                {
                    "question": q,
                    "answer": ev.answer,
                    "citations": ev.citations,
                    "grounding_score": round(ev.grounding_score, 3),
                    "flagged_injection": ev.flagged_injection,
                    "evidence_ids": sorted({h.chunk.doc_id for h in ev.chunks}),
                }
            )
        return out

    @traced("research", degrade=True)
    def research(self, state: CaseState) -> dict[str, Any]:
        out = self._qa(state, state.get("plan") or [])
        return {
            "research": out,
            "_event": {"answered": len(out), "injection_blocked": sum(r["flagged_injection"] for r in out)},
        }

    @traced("research_refine", degrade=True)
    def research_refine(self, state: CaseState) -> dict[str, Any]:
        extra = self._qa(state, state.get("follow_ups") or [])
        return {
            "research": (state.get("research") or []) + extra,
            "iterations": state.get("iterations", 0) + 1,
            "_event": {"follow_ups": len(extra)},
        }

    # ------------------------------------------------------------------ graph
    @traced("graph_analyst", degrade=True)
    def graph_analyst(self, state: CaseState) -> dict[str, Any]:
        res = self._res(state)
        subject = res.name or state["query"]
        with self.c.db.session() as s:
            rels = [
                case_graph.RelRow(
                    r.subject, r.subject_type, r.relation, r.object, r.object_type, r.evidence_id, r.attributes
                )
                for r in s.scalars(select(Relationship).where(Relationship.case_id == state["case_id"]))
            ]
            sigs = list(
                s.scalars(
                    select(Signal).where(
                        Signal.case_id == state["case_id"],
                        Signal.signal_type.in_(("sanctions_potential_match", "pep_potential_match")),
                    )
                )
            )
        flags: dict[str, dict[str, Any]] = {}
        for sg in sigs:
            name = subject if sg.detail.get("role") == "subject" else sg.detail.get("query", "")
            f = flags.setdefault(name, {"evidence_ids": []})
            f["sanctioned" if sg.signal_type.startswith("sanctions") else "pep"] = True
            f["evidence_ids"] = sorted(set(f["evidence_ids"]) | set(sg.evidence_ids))
        g = case_graph.build_graph(subject, rels, flags)
        analysis = case_graph.analyse(g, subject)
        return {
            "graph": {"analysis": analysis, "facts": case_graph.facts(g), "ui": case_graph.to_json(g, subject)},
            "_event": {
                "nodes": analysis["nodes"],
                "edges": analysis["edges"],
                "exposure_paths": len(analysis["exposure_paths"]),
            },
        }

    # ------------------------------------------------------------------ risk
    @traced("risk_assessor")
    def risk_assessor(self, state: CaseState) -> dict[str, Any]:
        with self.c.db.session() as s:
            sigs = [
                {
                    "signal_type": x.signal_type,
                    "value": x.value,
                    "severity": x.severity,
                    "confidence": x.confidence,
                    "evidence_ids": x.evidence_ids,
                    "detail": x.detail,
                    "label": x.label,
                }
                for x in s.scalars(select(Signal).where(Signal.case_id == state["case_id"]))
            ]
        details = state.get("details") or {}
        a = scorecard.assess(
            sigs,
            dict(state.get("coverage") or {}),
            state.get("resolution") or {},
            details.get("corporate_registry") or {},
            state.get("peer"),
        )
        ad = a.to_dict()
        with self.c.db.session() as s:
            existing = s.scalars(select(RiskAssessment).where(RiskAssessment.case_id == state["case_id"])).first()
            row = existing or RiskAssessment(case_id=state["case_id"])
            row.scorecard_version, row.score, row.band, row.confidence = a.version, a.score, a.band, a.confidence
            row.factors, row.features = ad["factors"], ad["features"]
            row.model_versions = {
                "scorecard": a.version,
                "peer_model": self.c.peer_model_version,
                "media_taxonomy": TAXONOMY_VERSION,
                "screening": SCREENING_VERSION,
                "jurisdiction_policy": load_policy()["version"],
                "prompt": PROMPT_VERSION,
                "llm": f"{self.c.llm.provider}:{self.c.llm.model}",
            }
            if existing is None:
                s.add(row)
            case = s.get(Case, state["case_id"])
            assert case is not None
            case.risk_band, case.risk_score = a.band, a.score
        return {"assessment": ad, "_event": {"score": a.score, "band": a.band, "confidence": a.confidence}}

    # ------------------------------------------------------------------ report
    @traced("report_writer")
    def report_writer(self, state: CaseState) -> dict[str, Any]:
        res = self._res(state)
        name = C.display_name(res)
        self.c.case_index.build(state["case_id"])
        retriever = self.c.case_index.retriever(state["case_id"])
        hits = retriever.retrieve(f"{name} sanctions enforcement litigation ownership risk factors financial", k=8)
        ctx = "\n".join(f"[{h.chunk.id}] {self.c.guardrails.sanitize_context(h.chunk.text)[0]}" for h in hits)
        summary = self.c.llm.complete(
            [
                Message(
                    "user",
                    f"QUESTION: Summarise the documented due-diligence findings (ownership, sanctions, "
                    f"enforcement, litigation, financial condition) for {name}\n\nCONTEXT:\n{ctx}",
                )
            ],
            system=GROUNDED_QA_SYSTEM + "\n" + REPORT_SYSTEM,
            max_tokens=800,
        ).text.strip()
        grounding = self.c.grounding.check(summary, [h.chunk for h in hits])
        out_check = self.c.guardrails.check_output(summary)
        report = self._render(state, name, summary, grounding.score)
        report = self.c.dlp.redact(report, "mask").text
        research_scores = [
            r["grounding_score"]
            for r in state.get("research") or []
            if not r["answer"].startswith("INSUFFICIENT_EVIDENCE")
        ]
        overall = round(min([grounding.score, *research_scores]) if research_scores else grounding.score, 3)
        with self.c.db.session() as s:
            case = s.get(Case, state["case_id"])
            assert case is not None
            case.report, case.report_grounding = report, overall
        return {
            "report": report,
            "report_grounding": overall,
            "guardrail_violations": out_check.violations,
            "_event": {
                "summary_grounding": round(grounding.score, 3),
                "overall_grounding": overall,
                "unsupported_claims": len(grounding.unsupported),
                "violations": out_check.violations,
            },
        }

    def _render(self, state: CaseState, name: str, summary: str, summary_grounding: float) -> str:
        res, a = state.get("resolution") or {}, state.get("assessment") or {}
        cov, det = state.get("coverage") or {}, state.get("details") or {}
        with self.c.db.session() as s:
            sigs = list(s.scalars(select(Signal).where(Signal.case_id == state["case_id"]).order_by(Signal.id)))
        L = [
            f"# Due-diligence memo — {name}",
            "",
            "> Sentinel output is an analytical assessment to support a human decision. It is not a regulatory "
            "determination. Screening hits are *potential* matches requiring adjudication.",
            "",
            "## 1. Entity",
            f"- Query: `{state['query']}` → resolved: **{res.get('name') or 'UNRESOLVED'}** "
            f"(method {res.get('method')}, confidence {res.get('confidence', 0):.2f}"
            f"{', AMBIGUOUS' if res.get('ambiguous') else ''})",
            f"- Identifiers: CIK {res.get('cik') or '—'} · LEI {res.get('lei') or '—'} · tickers "
            f"{', '.join(res.get('tickers') or []) or '—'} · jurisdiction {res.get('jurisdiction') or '—'}",
            "- Data coverage: " + ", ".join(f"{k}={v}" for k, v in sorted(cov.items())),
            "",
            "## 2. Facts (from sources)",
            summary or "INSUFFICIENT_EVIDENCE",
            f"_Narrative grounding: {summary_grounding:.2f}_",
            "",
        ]
        fin = (det.get("financials") or {}).get("indicators")
        if fin:
            L += [
                "**Reported financials (SEC XBRL, period ended " + str(fin.get("period_end")) + "):** "
                f"revenue {C._money(fin.get('revenue'))}, net income {C._money(fin.get('net_income'))}, "
                f"total assets {C._money(fin.get('total_assets'))}, liabilities/assets "
                f"{fin.get('liabilities_to_assets')}, current ratio {fin.get('current_ratio')}.",
                "",
            ]
        graph = (state.get("graph") or {}).get("analysis") or {}
        if graph:
            L += [
                f"**Relationships:** {graph.get('nodes')} nodes / {graph.get('edges')} evidence-backed edges; "
                f"ultimate consolidation parent: {graph.get('ultimate_parent') or 'none reported'}; "
                f"insiders on file: {len(graph.get('insiders') or [])}.",
                "",
            ]
        L += ["## 3. Analytical signals (derived by Sentinel)"]
        L += [
            f"- [{sg.severity.upper()}] {sg.signal_type}: {sg.label} (confidence {sg.confidence:.2f}; evidence "
            f"{', '.join(e[:8] for e in sg.evidence_ids[:3])})"
            for sg in sigs
            if sg.severity != "info"
        ] or ["- No adverse signals derived."]
        for p in graph.get("exposure_paths") or []:
            L.append(f"- Exposure path ({p['hops']} hop(s), {p['flag']}): {' → '.join(p['path'])}")
        L += [
            "",
            f"## 4. Risk assessment ({a.get('version')})",
            f"**Score {a.get('score')} / 100 → band {a.get('band')}** · confidence {a.get('confidence')}",
            "",
            "| Factor | Input | Points | Cap |",
            "|---|---|---:|---:|",
        ]
        for f in a.get("factors") or []:
            L.append(f"| {f['name']} | {str(f['value'])[:80]} | {f['points']} | {f['cap']} |")
        L += ["", "## 5. Research Q&A (case evidence only)"]
        for r in state.get("research") or []:
            L.append(f"**Q:** {r['question']}  \n**A:** {r['answer']}  \n_grounding {r['grounding_score']:.2f}_")
        return "\n".join(L)

    # ------------------------------------------------------------------ critic
    @traced("critic", degrade=True)
    def critic(self, state: CaseState) -> dict[str, Any]:
        thr = self.c.settings.grounding_threshold
        weak = [
            r
            for r in state.get("research") or []
            if r["grounding_score"] < thr and not r["answer"].startswith("INSUFFICIENT_EVIDENCE")
        ]
        loop = (state.get("report_grounding", 1.0) < thr or bool(weak)) and state.get(
            "iterations", 0
        ) < self.max_iterations
        follow: list[str] = []
        if loop:
            try:
                crit = self.c.llm.complete_structured(
                    [Message("user", "Weak answers:\n" + "\n".join(f"- {r['question']}: {r['answer']}" for r in weak))],
                    Critique,
                    system=CRITIC_SYSTEM,
                )
                follow = crit.follow_up_questions[:3]
            except StructuredOutputError:
                follow = []
            follow = (
                follow
                or [f"{r['question']} Quote the specific disclosure." for r in weak][:3]
                or [f"What documented facts exist about {C.display_name(self._res(state))}?"]
            )
        return {"follow_ups": follow, "_event": {"loop": bool(follow), "weak": len(weak)}}

    @staticmethod
    def route_after_critic(state: CaseState) -> str:
        return "research_refine" if state.get("follow_ups") else "policy"

    # ------------------------------------------------------------------ policy
    @traced("policy")
    def policy(self, state: CaseState) -> dict[str, Any]:
        with self.c.db.session() as s:
            sigs = list(s.scalars(select(Signal).where(Signal.case_id == state["case_id"])))
        pack = lambda xs: [{"label": x.label, "evidence_ids": x.evidence_ids, "detail": x.detail} for x in xs]  # noqa: E731
        sanc = [x for x in sigs if x.signal_type == "sanctions_potential_match"]
        a, res = state.get("assessment") or {}, state.get("resolution") or {}
        scr = (state.get("details") or {}).get("screening") or {}
        tier, _ = tier_for(res.get("country") or res.get("jurisdiction"))
        result = self.c.policy.evaluate(
            PolicyInput(
                band=a.get("band", "HIGH"),
                score=float(a.get("score", 100)),
                confidence=float(a.get("confidence", 0)),
                sanctions_subject=pack([x for x in sanc if x.detail.get("role") == "subject"]),
                sanctions_related=pack([x for x in sanc if x.detail.get("role") != "subject"]),
                pep_matches=pack([x for x in sigs if x.signal_type == "pep_potential_match"]),
                jurisdiction_tier=tier,
                resolution_confidence=float(res.get("confidence") or 0),
                ambiguous=bool(res.get("ambiguous")),
                resolved=bool(res.get("name")),
                coverage=dict(state.get("coverage") or {}),
                stale_sources=scr.get("stale_sources") or [],
                report_grounding=float(state.get("report_grounding", 0)),
                guardrail_violations=state.get("guardrail_violations") or [],
            )
        )
        pd = result.to_dict()
        with self.c.db.session() as s:
            if (
                s.scalars(select(PolicyDecisionRecord).where(PolicyDecisionRecord.case_id == state["case_id"])).first()
                is None
            ):
                s.add(
                    PolicyDecisionRecord(
                        case_id=state["case_id"],
                        policy_version=result.version,
                        outcome=result.outcome.value,
                        required_role=result.required_role,
                        rules=pd["rules"],
                    )
                )
            case = s.get(Case, state["case_id"])
            assert case is not None
            case.policy_outcome = result.outcome.value
            if result.outcome != Outcome.AUTO_CLEAR:
                if s.scalars(select(Review).where(Review.case_id == state["case_id"])).first() is None:
                    s.add(
                        Review(
                            case_id=state["case_id"],
                            tenant=case.tenant,
                            tier=result.outcome.value,
                            required_role=result.required_role or "reviewer",
                            requested_by=case.created_by,
                            reasons=[{"rule_id": r["rule_id"], "description": r["description"]} for r in pd["rules"]],
                        )
                    )
                case.status = "awaiting_review"
            case.summary = {
                "coverage": dict(state.get("coverage") or {}),
                "resolution": res,
                "graph": ((state.get("graph") or {}).get("analysis") or {}),
                "details": {k: v for k, v in (state.get("details") or {}).items()},
                "peer": state.get("peer") or {},
                "research": state.get("research") or [],
                "errors": (state.get("errors") or [])[:50],
            }
        self.c.audit.append(
            "sentinel:policy",
            f"policy.{result.outcome.value.lower()}",
            "case",
            state["case_id"],
            {
                "version": result.version,
                "rules": [r["rule_id"] for r in pd["rules"]],
                "required_role": result.required_role,
            },
            tenant=state.get("tenant", "default"),
        )
        status = "awaiting_review" if result.outcome != Outcome.AUTO_CLEAR else "cleared"
        return {
            "policy": pd,
            "status": status,
            "_event": {"outcome": result.outcome.value, "rules": [r["rule_id"] for r in pd["rules"]]},
        }

    @staticmethod
    def route_after_policy(state: CaseState) -> str:
        return "human_review" if state.get("status") == "awaiting_review" else "finalize"

    # ------------------------------------------------------------------ human in the loop
    def human_review(self, state: CaseState) -> dict[str, Any]:
        decision = interrupt({"case_id": state["case_id"], "policy": state.get("policy", {}).get("outcome")})
        # the decision was validated and persisted by CaseService.decide (maker-checker, role tier)
        return {"review": decision, "status": "approved" if decision.get("approve") else "rejected"}

    # ------------------------------------------------------------------ finalize
    @traced("finalize")
    def finalize(self, state: CaseState) -> dict[str, Any]:
        with self.c.db.session() as s:
            case = s.get(Case, state["case_id"])
            assert case is not None
            final = state.get("status")
            if case.status in ("running", "awaiting_review") and final in ("approved", "rejected", "cleared"):
                case.status = str(final)
            case.completed_at = case.completed_at or utcnow()
            status = case.status
        self.c.audit.append(
            "sentinel:workflow",
            "case.finalized",
            "case",
            state["case_id"],
            {"status": status, "risk_band": (state.get("assessment") or {}).get("band")},
            tenant=state.get("tenant", "default"),
        )
        METRICS.inc("cases_closed", status=status)
        return {"status": status, "_event": {"status": status}}


__all__ = ["Agents", "NodeFailure", "SourceError", "default_plan"]
