"""Live demo of the native Claude tool-use agent (requires SENTINEL_ANTHROPIC_API_KEY)."""

from __future__ import annotations

import os

import anthropic

from sentinel.agents.claude_agent import ClaudeToolAgent
from sentinel.agents.tools import build_tools
from sentinel.container import Container


def main() -> None:
    c = Container.build()
    client = anthropic.Anthropic(api_key=os.environ["SENTINEL_ANTHROPIC_API_KEY"])
    agent = ClaudeToolAgent(client, c.settings.anthropic_model, build_tools(c), c.guardrails, c.audit)
    run = agent.run("Assess Helios Trade FZE for onboarding as a components supplier. Who is the UBO?")
    print(run.answer)
    print("tools used:", [t["tool"] for t in run.tool_calls])


if __name__ == "__main__":
    main()
