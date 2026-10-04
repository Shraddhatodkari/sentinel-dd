"""Typed shared state for the due-diligence multi-agent graph.

All values are plain JSON-serialisable types so the state can be checkpointed (Postgres/Redis
checkpointer in production) and resumed after a human-in-the-loop pause or a pod restart.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class CaseState(TypedDict, total=False):
    case_id: str
    entity: str
    question: str
    requested_by: str
    plan: list[str]
    prior_cases: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    graph_findings: dict[str, Any]
    screening: list[dict[str, Any]]
    profile: dict[str, Any] | None
    risk: dict[str, Any]
    report: str
    report_grounding: float
    guardrail_violations: list[str]
    follow_ups: list[str]
    iterations: int
    policy: dict[str, Any]
    approval_id: str | None
    review: dict[str, Any] | None
    status: str
    # reducers: parallel branches append without clobbering each other
    trace: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
