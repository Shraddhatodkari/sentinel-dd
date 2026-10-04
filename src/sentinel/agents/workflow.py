"""LangGraph orchestration of the investigation agents.

    intake → resolve_entity → planner
        → ┌ corporate_registry ┐
          │ financials         │  (parallel fan-out, barrier join)
          │ adverse_media      │
          └ filings            ┘
        → screening
        → ┌ research      ┐ (parallel)
          └ graph_analyst ┘
        → risk_assessor → report_writer → critic ─(weak grounding, bounded)→ research_refine → report_writer
        → policy ─(REVIEW/ESCALATE)→ human_review [interrupt] → finalize
                 └(AUTO_CLEAR, if enabled)────────────────────→ finalize

State is checkpointed (SQLite or PostgreSQL) so the human-review pause survives restarts.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from sentinel.agents.nodes import Agents
from sentinel.agents.state import CaseState
from sentinel.container import Container

GATHER = ("corporate_registry", "financials", "adverse_media", "filings")


def build_workflow(c: Container) -> Any:
    a = Agents(c)
    g = StateGraph(CaseState)
    for name in (
        "intake",
        "resolve_entity",
        "planner",
        *GATHER,
        "screening",
        "research",
        "graph_analyst",
        "risk_assessor",
        "report_writer",
        "critic",
        "research_refine",
        "policy",
        "human_review",
        "finalize",
    ):
        g.add_node(name, getattr(a, name))
    g.add_edge(START, "intake")
    g.add_edge("intake", "resolve_entity")
    g.add_edge("resolve_entity", "planner")
    for n in GATHER:
        g.add_edge("planner", n)
    g.add_edge(list(GATHER), "screening")
    g.add_edge("screening", "research")
    g.add_edge("screening", "graph_analyst")
    g.add_edge(["research", "graph_analyst"], "risk_assessor")
    g.add_edge("risk_assessor", "report_writer")
    g.add_edge("report_writer", "critic")
    g.add_conditional_edges("critic", a.route_after_critic, {"research_refine": "research_refine", "policy": "policy"})
    g.add_edge("research_refine", "report_writer")
    g.add_conditional_edges("policy", a.route_after_policy, {"human_review": "human_review", "finalize": "finalize"})
    g.add_edge("human_review", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=c.checkpointer)
