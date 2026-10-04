"""LangGraph orchestration of the due-diligence agents.

                    ┌──────────────┐
                    │   planner    │
                    └──────┬───────┘
       ┌───────────────────┼───────────────────┐        (parallel fan-out)
┌──────▼─────┐      ┌──────▼──────┐     ┌──────▼─────┐
│ researcher │      │graph_analyst│     │  screener  │
└──────┬─────┘      └──────┬──────┘     └──────┬─────┘
       └───────────────────┼───────────────────┘        (join)
                    ┌──────▼───────┐
                    │risk_assessor │
                    └──────┬───────┘
                    ┌──────▼───────┐  ◄────────────┐
                    │report_writer │               │
                    └──────┬───────┘     ┌─────────┴──────┐
                    ┌──────▼───────┐ weak│research_refine │
                    │    critic    ├────►└────────────────┘
                    └──────┬───────┘
                    ┌──────▼───────┐
                    │  compliance  │── allow/deny ──┐
                    └──────┬───────┘                │
                 approval  │                        │
                    ┌──────▼───────┐                │
                    │ human_review │ (interrupt)    │
                    └──────┬───────┘                │
                    ┌──────▼───────┐◄───────────────┘
                    │   finalize   │
                    └──────────────┘
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from sentinel.agents.nodes import Agents
from sentinel.agents.state import CaseState
from sentinel.container import Container


def build_workflow(c: Container, checkpointer: Any | None = None) -> Any:
    a = Agents(c)
    g = StateGraph(CaseState)
    g.add_node("planner", a.planner)
    g.add_node("researcher", a.researcher)
    g.add_node("graph_analyst", a.graph_analyst)
    g.add_node("screener", a.screener)
    g.add_node("risk_assessor", a.risk_assessor)
    g.add_node("report_writer", a.report_writer)
    g.add_node("critic", a.critic)
    g.add_node("research_refine", a.research_refine)
    g.add_node("compliance", a.compliance)
    g.add_node("human_review", a.human_review)
    g.add_node("finalize", a.finalize)

    g.add_edge(START, "planner")
    for branch in ("researcher", "graph_analyst", "screener"):
        g.add_edge("planner", branch)
    g.add_edge(["researcher", "graph_analyst", "screener"], "risk_assessor")  # barrier join
    g.add_edge("risk_assessor", "report_writer")
    g.add_edge("report_writer", "critic")
    g.add_conditional_edges(
        "critic", a.route_after_critic, {"research_refine": "research_refine", "compliance": "compliance"}
    )
    g.add_edge("research_refine", "report_writer")
    g.add_conditional_edges(
        "compliance", a.route_after_compliance, {"human_review": "human_review", "finalize": "finalize"}
    )
    g.add_edge("human_review", "finalize")
    g.add_edge("finalize", END)
    # Production: swap for langgraph-checkpoint-postgres (PostgresSaver) for durable, multi-pod resumes.
    return g.compile(checkpointer=checkpointer or InMemorySaver())
