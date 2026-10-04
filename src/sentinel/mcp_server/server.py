"""Model Context Protocol (MCP) server exposing Sentinel capabilities to any MCP client
(Claude Desktop, Claude Code, IDE agents, other enterprise agents).

Run:  ``sentinel-mcp`` (stdio)  or  ``sentinel-mcp --transport streamable-http``

Claude Desktop config::

    {"mcpServers": {"sentinel": {"command": "sentinel-mcp"}}}
"""

from __future__ import annotations

import argparse
from typing import Any

try:  # MCP Python SDK >= 2.0
    from mcp.server import MCPServer as _Server
except ImportError:  # pragma: no cover - SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef,attr-defined]

from sentinel.agents.service import CaseService
from sentinel.agents.tools import build_tools
from sentinel.container import Container

INSTRUCTIONS = (
    "Sentinel provides governed due-diligence intelligence: grounded document search with citations, "
    "sanctions/PEP screening, ownership knowledge-graph analysis, ML risk scoring and a full multi-agent "
    "investigation workflow with human approval for high-risk outcomes."
)


def create_server(container: Container | None = None, service: CaseService | None = None) -> Any:
    c = container or Container.build()
    svc = service or CaseService(c)
    tools = build_tools(c, svc)
    server = _Server("sentinel-dd", instructions=INSTRUCTIONS)

    def _call(name: str, **kwargs: Any) -> Any:
        c.audit.append("mcp-client", "mcp.tool_call", name, {k: str(v)[:200] for k, v in kwargs.items()})
        return tools[name].invoke(kwargs)

    @server.tool(description=tools["search_documents"].description)
    def search_documents(query: str, entity: str | None = None, k: int = 5) -> dict[str, Any]:
        return _call("search_documents", query=query, entity=entity, k=k)

    @server.tool(description=tools["screen_entity"].description)
    def screen_entity(entity: str) -> list[dict[str, Any]]:
        return _call("screen_entity", entity=entity)

    @server.tool(description=tools["entity_graph"].description)
    def entity_graph(entity: str, hops: int = 2) -> dict[str, Any]:
        return _call("entity_graph", entity=entity, hops=hops)

    @server.tool(description=tools["score_risk"].description)
    def score_risk(entity: str) -> dict[str, Any]:
        return _call("score_risk", entity=entity)

    @server.tool(description=tools["start_due_diligence"].description)
    def start_due_diligence(entity: str, question: str = "") -> dict[str, Any]:
        return _call("start_due_diligence", entity=entity, question=question)

    @server.tool(description=tools["get_case"].description)
    def get_case(case_id: str) -> dict[str, Any]:
        return _call("get_case", case_id=case_id)

    @server.resource("sentinel://policy/vendor-risk")
    def vendor_policy() -> str:
        """Current third-party risk policy thresholds."""
        s = c.settings
        return (
            f"HITL risk threshold: {s.hitl_risk_threshold}; minimum grounding: {s.grounding_threshold}; "
            "sanctions matches and PEP exposure always require human adjudication."
        )

    return server


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - process entrypoint
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    args = ap.parse_args(argv)
    create_server().run(transport=args.transport)


if __name__ == "__main__":  # pragma: no cover
    main()
