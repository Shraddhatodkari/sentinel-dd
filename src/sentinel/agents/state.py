"""Typed shared state for the investigation graph.

State holds only JSON-serialisable values (ids, summaries); evidence, signals and decisions live in the
database. This keeps checkpoints small and makes the graph resumable after a human-review pause or restart.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


class CaseState(TypedDict, total=False):
    case_id: str
    tenant: str
    query: str
    hints: dict[str, Any]
    question: str
    requested_by: str
    resolution: dict[str, Any]
    plan: list[str]
    research: list[dict[str, Any]]
    graph: dict[str, Any]
    peer: dict[str, Any]
    assessment: dict[str, Any]
    report: str
    report_grounding: float
    guardrail_violations: list[str]
    follow_ups: list[str]
    iterations: int
    policy: dict[str, Any]
    review: dict[str, Any] | None
    status: str
    # reducers: parallel branches merge without clobbering each other
    coverage: Annotated[dict[str, str], merge]
    details: Annotated[dict[str, Any], merge]
    errors: Annotated[list[str], operator.add]
