"""Model Context Protocol (MCP) server exposing Sentinel tools to MCP clients (Claude Desktop, Claude Code, IDEs).

Security model: the server runs *as a principal*. Set ``SENTINEL_MCP_TOKEN`` to a JWT issued to a service account
(``sentinel users token <username>``); every tool call is authorised against that principal's permissions and
audited. There is deliberately no tool that approves or rejects a case.

Run:  ``sentinel-mcp`` (stdio)  or  ``sentinel-mcp --transport streamable-http``
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from mcp.server import MCPServer as _Server  # MCP Python SDK >= 2.0
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from sentinel.agents.service import CaseNotFound, CaseService, GuardrailViolation
from sentinel.agents.tools import ToolDenied, build_tools
from sentinel.container import Container
from sentinel.ingestion.reference import freshness
from sentinel.security.auth import Principal

INSTRUCTIONS = (
    "Sentinel provides governed third-party due-diligence intelligence from authoritative sources (SEC EDGAR, GLEIF, "
    "OFAC/UN/UK/EU sanctions lists, DOJ). Results are evidence-cited; screening hits are potential matches; risk "
    "scores are analytical assessments; final decisions are made by human reviewers."
)


def create_server(container: Container, service: CaseService, principal: Principal) -> Any:
    tools = build_tools(service)
    server = _Server("sentinel-dd", instructions=INSTRUCTIONS)

    def _call(tool_name: str, /, **kwargs: Any) -> Any:
        container.audit.append(
            principal.subject,
            "mcp.tool_call",
            "tool",
            tool_name,
            {k: str(v)[:200] for k, v in kwargs.items()},
            roles=principal.role_names,
            tenant=principal.tenant,
        )
        try:
            return tools[tool_name].invoke(principal, {k: v for k, v in kwargs.items() if v is not None})
        except (ToolDenied, CaseNotFound, GuardrailViolation, ValidationError) as exc:
            # deliberate, client-visible errors (surfaced to the MCP client as isError results)
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    @server.tool(description=tools["search_case_evidence"].description)
    def search_case_evidence(case_id: str, query: str, k: int = 5) -> dict[str, Any]:
        return _call("search_case_evidence", case_id=case_id, query=query, k=k)

    @server.tool(description=tools["screen_name"].description)
    def screen_name(name: str, entity_type: str | None = None) -> list[dict[str, Any]]:
        return _call("screen_name", name=name, entity_type=entity_type)

    @server.tool(description=tools["get_case"].description)
    def get_case(case_id: str) -> dict[str, Any]:
        return _call("get_case", case_id=case_id)

    @server.tool(description=tools["start_investigation"].description)
    def start_investigation(
        query: str, cik: int | None = None, lei: str | None = None, question: str = ""
    ) -> dict[str, Any]:
        return _call("start_investigation", query=query, cik=cik, lei=lei, question=question)

    @server.tool(description=tools["list_review_queue"].description)
    def list_review_queue() -> list[dict[str, Any]]:
        return _call("list_review_queue")

    @server.resource("sentinel://sources/freshness")
    def source_freshness() -> str:
        """Freshness and last-run status of every data source."""
        return json.dumps(freshness(container.db), default=str)

    @server.resource("sentinel://policy/current")
    def policy() -> str:
        """Current policy and scorecard versions and thresholds."""
        s = container.settings
        return json.dumps(
            {
                "policy_version": s.policy_version,
                "scorecard_version": s.scorecard_version,
                "auto_clear_enabled": s.auto_clear_enabled,
                "screening_threshold": s.screening_review_threshold,
                "grounding_threshold": s.grounding_threshold,
            }
        )

    return server


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - process entrypoint
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    args = ap.parse_args(argv)
    c = Container.build()
    token = os.environ.get("SENTINEL_MCP_TOKEN")
    if not token:
        raise SystemExit("SENTINEL_MCP_TOKEN is required (JWT for a service account; see docs/mcp.md)")
    principal, _, _ = c.tokens.verify(token)
    create_server(c, CaseService(c), principal).run(transport=args.transport)


if __name__ == "__main__":  # pragma: no cover
    main()
