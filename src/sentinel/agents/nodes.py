"""Specialist agents (graph nodes). Each is a small, single-responsibility unit that reads the
shared :class:`CaseState` and returns a partial update.

Agents:
  planner        – decomposes the investigation (LLM structured output + safe default)
  researcher     – grounded RAG per sub-question (hybrid retrieval, injection-sanitised, cited)
  graph_analyst  – GraphRAG: UBO discovery, sanctions/PEP exposure paths, offshore layering
  screener       – fuzzy watch-list screening of the entity, owners and officers
  risk_assessor  – ML risk score + anomaly + segment + adverse-media NLP, with explanations
  report_writer  – composes a cited memo; output guardrails + grounding verification
  critic         – reflection: detects weak grounding and requests targeted re-research
  compliance     – policy engine decides ALLOW / REQUIRE_APPROVAL / DENY; files HITL request
  human_review   – pauses the graph (``interrupt``) until a reviewer decides
  finalize       – closes the case, writes long-term memory and the audit record
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from sentinel.agents.state import CaseState
from sentinel.container import Container
from sentinel.governance.policy import ActionContext, Decision
from sentinel.llm.base import Message, StructuredOutputError
from sentinel.observability.metrics import METRICS
from sentinel.prompts import CRITIC_SYSTEM, GROUNDED_QA_SYSTEM, PLANNER_SYSTEM
from sentinel.rag.grounding import GroundingChecker


class ResearchPlan(BaseModel):
    questions: list[str] = Field(default_factory=list, description="3-6 focused research questions")


class Critique(BaseModel):
    follow_up_questions: list[str] = Field(default_factory=list)


def default_plan(entity: str) -> list[str]:
    return [
        f"Who owns {entity} and where is it incorporated?",
        f"What are the revenue growth, debt to equity and audit opinion of {entity}?",
        f"Is {entity} subject to adverse media, fraud investigation or litigation?",
        f"Are any owners or directors of {entity} sanctioned or politically exposed persons?",
        f"Which certifications does {entity} hold and has it had any data breach or security incident?",
    ]


def _trace(node: str, **info: Any) -> list[dict[str, Any]]:
    METRICS.inc("agent_node_runs", node=node)
    return [{"node": node, "ts": round(time.time(), 3), **info}]


class Agents:
    def __init__(self, c: Container, max_iterations: int = 2) -> None:
        self.c = c
        self.max_iterations = max_iterations
        self.grounding = GroundingChecker()

    # ------------------------------------------------------------------ planner
    def planner(self, state: CaseState) -> dict[str, Any]:
        entity = state["entity"]
        prior = [
            {"case_id": r.metadata.get("case_id"), "summary": r.text[:300], "score": round(s, 3)}
            for r, s in self.c.memory.recall(entity, k=3, entity=entity)
        ]
        questions: list[str] = []
        try:
            plan = self.c.llm.complete_structured(
                [Message("user", f"Target entity: {entity}\nAnalyst request: {state.get('question', '')}")],
                ResearchPlan,
                system=PLANNER_SYSTEM,
            )
            questions = [q for q in plan.questions if entity.split()[0].lower() in q.lower()][:6]
        except StructuredOutputError as exc:
            return {
                "plan": default_plan(entity),
                "prior_cases": prior,
                "errors": [f"planner: {exc}"],
                "trace": _trace("planner", fallback=True),
            }
        if len(questions) < 3:
            questions = default_plan(entity)
        if state.get("question"):
            questions.append(state["question"])
        return {
            "plan": questions,
            "prior_cases": prior,
            "status": "researching",
            "trace": _trace("planner", n_questions=len(questions), prior_cases=len(prior)),
        }

    # ------------------------------------------------------------------ researcher
    def _research(self, entity: str, questions: list[str]) -> list[dict[str, Any]]:
        out = []
        for q in questions:
            ev = self.c.qa.answer(q, k=self.c.settings.retrieval_top_k, flt={"entity": entity})
            if not ev.chunks:  # entity-scoped search found nothing -> widen to global corpus
                ev = self.c.qa.answer(q, k=self.c.settings.retrieval_top_k)
            out.append(
                {
                    "question": ev.question,
                    "answer": ev.answer,
                    "citations": ev.citations,
                    "grounding_score": round(ev.grounding_score, 3),
                    "flagged_injection": ev.flagged_injection,
                    "chunk_ids": [h.chunk.id for h in ev.chunks],
                }
            )
        return out

    def researcher(self, state: CaseState) -> dict[str, Any]:
        ev = self._research(state["entity"], state.get("plan", []))
        flagged = sum(e["flagged_injection"] for e in ev)
        return {"evidence": ev, "trace": _trace("researcher", answered=len(ev), injection_blocked=flagged)}

    def research_refine(self, state: CaseState) -> dict[str, Any]:
        extra = self._research(state["entity"], state.get("follow_ups", []))
        return {
            "evidence": state.get("evidence", []) + extra,
            "iterations": state.get("iterations", 0) + 1,
            "trace": _trace("research_refine", follow_ups=len(extra)),
        }

    # ------------------------------------------------------------------ graph analyst
    def graph_analyst(self, state: CaseState) -> dict[str, Any]:
        g, entity = self.c.graph, state["entity"]
        sanc = g.exposure(entity, "sanctioned", max_hops=3)
        pep = g.exposure(entity, "pep", max_hops=2)
        findings: dict[str, Any] = {
            "ultimate_owners": g.ultimate_owners(entity),
            "sanctions_exposure": [asdict(x) for x in sanc],
            "pep_exposure": [asdict(x) for x in pep],
            "offshore_layers": g.offshore_layers(entity),
            "facts": g.facts_for(entity, hops=2),
            "related_parties": g.neighbors_within(entity, 2, ("person", "company", "unknown")),
        }
        return {
            "graph_findings": findings,
            "trace": _trace(
                "graph_analyst",
                ubos=len(findings["ultimate_owners"]),
                sanctions_paths=len(sanc),
                offshore=findings["offshore_layers"],
            ),
        }

    # ------------------------------------------------------------------ screener
    def screener(self, state: CaseState) -> dict[str, Any]:
        entity = state["entity"]
        # screen the entity plus its control network (owners / officers up to 3 hops), not mere counterparties
        names = [
            entity,
            *self.c.graph.neighbors_within(
                entity, 3, ("person", "company", "unknown"), relations=("OWNS", "OFFICER_OF")
            ),
        ]
        hits = [asdict(h) for h in self.c.screener.screen_many(names)]
        return {"screening": hits, "trace": _trace("screener", screened=len(set(names)), hits=len(hits))}

    # ------------------------------------------------------------------ risk assessor
    def risk_assessor(self, state: CaseState) -> dict[str, Any]:
        entity = state["entity"]
        profile = self.c.entities.lookup(entity)
        news = [
            c
            for c in self.c.store.all_chunks()
            if c.metadata.get("entity") == entity and c.metadata.get("doc_type") == "news"
        ]
        media_hits = self.c.risk.classify_media(
            [(c.id, self.c.guardrails.sanitize_context(c.text.split(": ", 1)[-1])[0]) for c in news]
        )
        gfeats = self.c.risk.graph_features(self.c.graph, entity)
        feats = self.c.risk.build_features(profile, gfeats, len(media_hits))
        result = self.c.risk.score(entity, feats, media_hits)
        errs = [] if profile else [f"risk_assessor: no structured profile for {entity}; conservative defaults used"]
        return {
            "profile": profile,
            "risk": result.to_dict(),
            "errors": errs,
            "trace": _trace("risk_assessor", probability=result.probability, band=result.band, anomaly=result.anomaly),
        }

    # ------------------------------------------------------------------ report writer
    def report_writer(self, state: CaseState) -> dict[str, Any]:
        entity, risk = state["entity"], state.get("risk", {})
        gf, ev = state.get("graph_findings", {}), state.get("evidence", [])
        chunk_ids = {cid for e in ev for cid in e["chunk_ids"]}
        chunks = [c for c in self.c.store.all_chunks() if c.id in chunk_ids]
        ctx = "\n".join(f"[{c.id}] {self.c.guardrails.sanitize_context(c.text)[0]}" for c in chunks)
        summary = self.c.llm.complete(
            [
                Message(
                    "user",
                    f"QUESTION: Summarise ownership, financial, adverse media, sanctions and security "
                    f"risks of {entity}\n\nCONTEXT:\n{ctx}",
                )
            ],
            system=GROUNDED_QA_SYSTEM,
            max_tokens=700,
        ).text.strip()
        grounding = self.grounding.check(summary, chunks)

        lines = [
            f"# Due Diligence Memo: {entity}",
            "",
            f"**Risk:** {risk.get('band', 'N/A')} (p={risk.get('probability', 0):.2f}, "
            f"model {risk.get('model_version', '?')}, segment: {risk.get('segment', '?')}"
            f"{', ANOMALOUS PROFILE' if risk.get('anomaly') else ''})",
            "",
            "## Executive Summary",
            summary,
            "",
            "## Ownership & Control (GraphRAG)",
        ]
        for u in gf.get("ultimate_owners", []):
            lines.append(f"- UBO **{u['owner']}** — effective {u['effective_pct']}% via {' -> '.join(u['chains'][0])}")
        lines.append(f"- Offshore holding layers: {gf.get('offshore_layers', 0)}")
        lines += [f"- {f}" for f in gf.get("facts", [])[:12]]
        lines += ["", "## Sanctions & PEP Screening"]
        for h in state.get("screening", []):
            lines.append(
                f"- {h['category'].upper()} match: {h['query']} ~ {h['matched']} ({h['list_name']}, "
                f"score {h['score']}, ref {h['reference']})"
            )
        for x in gf.get("sanctions_exposure", []):
            lines.append(f"- Sanctions exposure {x['hops']} hop(s): {' -> '.join(x['path'])}")
        if not state.get("screening") and not gf.get("sanctions_exposure"):
            lines.append("- No watch-list matches.")
        lines += ["", "## Adverse Media (NLP)"]
        lines += [f"- [{m['label']}] {m['sentence']} [{m['citation']}]" for m in risk.get("media_hits", [])] or [
            "- None identified."
        ]
        lines += ["", "## Key Risk Drivers (model explanation)"]
        lines += [f"- {d['feature']} = {d['value']:.2f} (Δp {d['contribution']:+.3f})" for d in risk.get("drivers", [])]
        lines += ["", "## Research Findings"]
        for e in ev:
            lines.append(f"**Q:** {e['question']}  \n**A:** {e['answer']} _(grounding {e['grounding_score']:.2f})_")
        report = "\n".join(lines)
        out_check = self.c.guardrails.check_output(summary)
        report = self.c.dlp.redact(report, "mask").text  # never leak PII in reports
        return {
            "report": report,
            "report_grounding": round(grounding.score, 3),
            "guardrail_violations": out_check.violations,
            "trace": _trace(
                "report_writer",
                grounding=round(grounding.score, 3),
                unsupported=len(grounding.unsupported),
                violations=out_check.violations,
            ),
        }

    # ------------------------------------------------------------------ critic (reflection)
    def critic(self, state: CaseState) -> dict[str, Any]:
        weak = [
            e
            for e in state.get("evidence", [])
            if e["grounding_score"] < self.c.settings.grounding_threshold
            and not e["answer"].startswith("INSUFFICIENT_EVIDENCE")
        ]
        needs_more = (state.get("report_grounding", 1.0) < self.c.settings.grounding_threshold or weak) and state.get(
            "iterations", 0
        ) < self.max_iterations
        follow: list[str] = []
        if needs_more:
            try:
                crit = self.c.llm.complete_structured(
                    [Message("user", "Weak answers:\n" + "\n".join(f"- {e['question']}: {e['answer']}" for e in weak))],
                    Critique,
                    system=CRITIC_SYSTEM,
                )
                follow = crit.follow_up_questions[:3]
            except StructuredOutputError:
                follow = []
            follow = (
                follow
                or [f"{e['question']} Provide specific documented facts." for e in weak][:3]
                or [f"What documented risk facts exist for {state['entity']}?"]
            )
        return {"follow_ups": follow, "trace": _trace("critic", loop=bool(follow), weak=len(weak))}

    @staticmethod
    def route_after_critic(state: CaseState) -> str:
        return "research_refine" if state.get("follow_ups") else "compliance"

    # ------------------------------------------------------------------ compliance gate
    def compliance(self, state: CaseState) -> dict[str, Any]:
        risk, gf = state.get("risk", {}), state.get("graph_findings", {})
        screening = state.get("screening", [])
        sanctions_hit = any(h["category"] == "sanctions" for h in screening) or any(
            x["hops"] <= 2 for x in gf.get("sanctions_exposure", [])
        )
        pep_hit = any(h["category"] == "pep" for h in screening) or any(
            x["hops"] <= 1 for x in gf.get("pep_exposure", [])
        )
        profile = state.get("profile") or {}
        ctx = ActionContext(
            action="recommend_onboard",
            risk_score=float(risk.get("probability", 1.0)),
            sanctions_hit=sanctions_hit,
            pep_hit=pep_hit,
            anomaly=bool(risk.get("anomaly")),
            grounding_score=float(state.get("report_grounding", 0.0)),
            jurisdiction=profile.get("country"),
        )
        if any(v.startswith("prohibited") for v in state.get("guardrail_violations", [])):
            ctx.grounding_score = 0.0
        res = self.c.policy.evaluate(ctx)
        update: dict[str, Any] = {
            "policy": {
                "decision": res.decision.value,
                "reasons": res.reasons,
                "sanctions_hit": sanctions_hit,
                "pep_hit": pep_hit,
            }
        }
        if res.decision == Decision.REQUIRE_APPROVAL:
            req = self.c.approvals.submit(
                state["case_id"],
                state.get("requested_by", "agent"),
                res.reasons,
                {"entity": state["entity"], "risk": risk.get("probability"), "band": risk.get("band")},
            )
            update |= {"approval_id": req.id, "status": "pending_review"}
        elif res.decision == Decision.DENY:
            update["status"] = "rejected"
        else:
            update["status"] = "approved"
        self.c.audit.append(
            "agent:compliance",
            f"policy.{res.decision.value}",
            state["case_id"],
            {"reasons": res.reasons, "risk": risk.get("probability")},
        )
        return update | {"trace": _trace("compliance", decision=res.decision.value)}

    @staticmethod
    def route_after_compliance(state: CaseState) -> str:
        return "human_review" if state.get("status") == "pending_review" else "finalize"

    # ------------------------------------------------------------------ human in the loop
    def human_review(self, state: CaseState) -> dict[str, Any]:
        decision = interrupt(
            {
                "approval_id": state.get("approval_id"),
                "case_id": state["case_id"],
                "entity": state["entity"],
                "reasons": state.get("policy", {}).get("reasons", []),
                "risk": state.get("risk", {}).get("band"),
            }
        )
        approved = bool(decision.get("approved"))
        return {
            "review": decision,
            "status": "approved" if approved else "rejected",
            "trace": _trace("human_review", reviewer=decision.get("reviewer"), approved=approved),
        }

    # ------------------------------------------------------------------ finalize
    def finalize(self, state: CaseState) -> dict[str, Any]:
        risk = state.get("risk", {})
        status = state.get("status", "approved")
        self.c.memory.remember(
            state["case_id"],
            f"Case {state['case_id']} on {state['entity']}: risk {risk.get('band')} "
            f"(p={risk.get('probability')}), outcome {status}.",
            entity=state["entity"],
            case_id=state["case_id"],
        )
        self.c.audit.append(
            state.get("requested_by", "agent"),
            "case.closed",
            state["case_id"],
            {"status": status, "risk_band": risk.get("band")},
        )
        METRICS.inc("cases_closed", status=status)
        return {"status": status, "trace": _trace("finalize", status=status)}
