"""Single source of truth for agent-callable tools, with per-tool authorisation.

Each :class:`ToolSpec` declares the permission it requires. Every invocation is executed *as a principal*:
the MCP server, the Claude tool-use agent and the CrewAI/AutoGen adapters all go through ``invoke`` — tools
cannot be used to bypass RBAC, case scoping or maker-checker.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from sentinel.security.auth import Permission, Principal

if TYPE_CHECKING:
    from sentinel.agents.service import CaseService


class ToolDenied(Exception):
    pass


class CaseSearchInput(BaseModel):
    case_id: str = Field(description="Case to search (results are limited to that case's evidence)")
    query: str = Field(min_length=3, max_length=500)
    k: int = Field(5, ge=1, le=10)


class ScreenInput(BaseModel):
    name: str = Field(min_length=2, max_length=300, description="Person or organisation name")
    entity_type: str | None = Field(None, description="individual | entity")


class CaseIdInput(BaseModel):
    case_id: str


class StartInput(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    cik: int | None = None
    lei: str | None = Field(None, pattern=r"^[A-Z0-9]{20}$")
    question: str = Field("", max_length=1000)


class EmptyInput(BaseModel):
    pass


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    permission: Permission
    fn: Callable[[Principal, Any], Any]
    mutating: bool = False

    def json_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def invoke(self, principal: Principal, args: dict[str, Any]) -> Any:
        allowed = principal.can(self.permission) or (
            self.permission == Permission.CASE_READ_OWN and principal.can(Permission.CASE_READ_ALL)
        )
        if not allowed:
            raise ToolDenied(f"{principal.subject} lacks {self.permission.value} for tool {self.name}")
        return self.fn(principal, self.input_model.model_validate(args))

    def anthropic_spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.json_schema()}

    def openai_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.json_schema()},
        }


def build_tools(service: CaseService) -> dict[str, ToolSpec]:
    c = service.c

    def search(p: Principal, a: CaseSearchInput) -> dict[str, Any]:
        service.get(p, a.case_id)  # enforces case visibility (raises CaseNotFound)
        from sentinel.rag.qa import GroundedQA

        ev = GroundedQA(c.case_index.retriever(a.case_id), c.llm, c.guardrails, c.grounding).answer(a.query, k=a.k)
        return {"answer": ev.answer, "citations": ev.citations, "grounding_score": round(ev.grounding_score, 3)}

    def screen(p: Principal, a: ScreenInput) -> list[dict[str, Any]]:
        hits = c.screening.screen(a.name, "adhoc", a.entity_type, c.settings.screening_review_threshold)
        c.audit.append(
            p.subject, "screening.adhoc", "name", a.name[:120], {"hits": len(hits)}, roles=p.role_names, tenant=p.tenant
        )
        return [{k: v for k, v in h.to_dict().items() if k != "remarks"} for h in hits[:20]]

    def get_case(p: Principal, a: CaseIdInput) -> dict[str, Any]:
        d = service.get(p, a.case_id)
        return {
            k: d.get(k)
            for k in (
                "case_id",
                "status",
                "resolved_name",
                "risk_band",
                "risk_score",
                "policy_outcome",
                "review",
                "report",
            )
        }

    def start(p: Principal, a: StartInput) -> dict[str, Any]:
        d = service.create(p, a.query, cik=a.cik, lei=a.lei, question=a.question)
        return {"case_id": d["case_id"], "status": d["status"]}

    def queue(p: Principal, _a: EmptyInput) -> list[dict[str, Any]]:
        return [
            {k: r[k] for k in ("case_id", "entity", "tier", "risk_band", "requested_at", "can_decide")}
            for r in service.review_queue(p)
        ]

    tools = [
        ToolSpec(
            "search_case_evidence",
            "Grounded, cited search over one case's evidence (filings, registry records, list records, media).",
            CaseSearchInput,
            Permission.SEARCH,
            search,
        ),
        ToolSpec(
            "screen_name",
            "Screen a name against current sanctions (OFAC, UN, UK, EU) and PEP lists. Returns "
            "potential matches for human adjudication.",
            ScreenInput,
            Permission.SEARCH,
            screen,
        ),
        ToolSpec(
            "get_case",
            "Fetch a case's status, risk assessment, policy outcome, review and memo.",
            CaseIdInput,
            Permission.CASE_READ_OWN,
            get_case,
        ),
        ToolSpec(
            "start_investigation",
            "Open a due-diligence investigation on an entity (runs asynchronously; "
            "outcomes always require human review per policy).",
            StartInput,
            Permission.CASE_CREATE,
            start,
            mutating=True,
        ),
        ToolSpec(
            "list_review_queue",
            "List cases awaiting human review (decisions are made in the dashboard, not by agents).",
            EmptyInput,
            Permission.REVIEW_DECIDE,
            queue,
        ),
    ]
    return {t.name: t for t in tools}
