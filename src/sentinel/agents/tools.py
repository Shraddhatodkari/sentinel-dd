"""Single source of truth for agent tools.

Each :class:`ToolSpec` (name, description, pydantic input schema, callable) is exposed — without
duplication — through: the MCP server, Claude native tool-use, CrewAI tools and AutoGen functions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from sentinel.agents.service import CaseService
    from sentinel.container import Container


class SearchInput(BaseModel):
    query: str = Field(description="Natural-language question about a counter-party")
    entity: str | None = Field(None, description="Restrict search to this entity's documents")
    k: int = Field(5, ge=1, le=20)


class EntityInput(BaseModel):
    entity: str = Field(description="Legal name of the company or person")


class GraphInput(BaseModel):
    entity: str
    hops: int = Field(2, ge=1, le=4)


class CaseInput(BaseModel):
    entity: str
    question: str = ""


class CaseIdInput(BaseModel):
    case_id: str


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Any], Any]

    def json_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def invoke(self, args: dict[str, Any]) -> Any:
        return self.fn(self.input_model.model_validate(args))

    def anthropic_spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.json_schema()}

    def openai_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.json_schema()},
        }


def build_tools(c: Container, service: CaseService | None = None) -> dict[str, ToolSpec]:
    def search(a: SearchInput) -> dict[str, Any]:
        ev = c.qa.answer(a.query, k=a.k, flt={"entity": a.entity} if a.entity else None)
        return {"answer": ev.answer, "citations": ev.citations, "grounding_score": round(ev.grounding_score, 3)}

    def screen(a: EntityInput) -> list[dict[str, Any]]:
        return [asdict(h) for h in c.screener.screen(a.entity)]

    def graph(a: GraphInput) -> dict[str, Any]:
        return {
            "facts": c.graph.facts_for(a.entity, a.hops),
            "ultimate_owners": c.graph.ultimate_owners(a.entity),
            "offshore_layers": c.graph.offshore_layers(a.entity),
        }

    def score(a: EntityInput) -> dict[str, Any]:
        profile = c.entities.lookup(a.entity)
        feats = c.risk.build_features(profile, c.risk.graph_features(c.graph, a.entity), 0)
        r = c.risk.score(a.entity, feats)
        return {"probability": r.probability, "band": r.band, "drivers": r.drivers, "anomaly": r.anomaly}

    tools = [
        ToolSpec("search_documents", "Grounded, cited search over the due-diligence corpus.", SearchInput, search),
        ToolSpec("screen_entity", "Fuzzy sanctions/PEP watch-list screening for a name.", EntityInput, screen),
        ToolSpec(
            "entity_graph", "Ownership/officer knowledge-graph facts, UBOs and offshore layering.", GraphInput, graph
        ),
        ToolSpec(
            "score_risk",
            "ML counter-party risk score with explanation (structured + graph features).",
            EntityInput,
            score,
        ),
    ]
    if service is not None:

        def start_case(a: CaseInput) -> dict[str, Any]:
            st = service.start(a.entity, a.question, requested_by="mcp-client")
            return {
                "case_id": st["case_id"],
                "status": st["status"],
                "risk": st.get("risk", {}).get("band"),
                "approval_id": st.get("approval_id"),
            }

        def get_case(a: CaseIdInput) -> dict[str, Any]:
            st = service.get(a.case_id)
            return {"case_id": a.case_id, "status": st.get("status"), "report": st.get("report", "")}

        tools += [
            ToolSpec(
                "start_due_diligence",
                "Run the full multi-agent due-diligence workflow on an entity. "
                "High-risk outcomes pause for human approval.",
                CaseInput,
                start_case,
            ),
            ToolSpec("get_case", "Fetch status and memo of a due-diligence case.", CaseIdInput, get_case),
        ]
    return {t.name: t for t in tools}
