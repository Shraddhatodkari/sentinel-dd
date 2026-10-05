"""Run the native Claude tool-use agent against Sentinel's governed tools (requires SENTINEL_ANTHROPIC_API_KEY).

The agent acts *as* an existing Sentinel user: every tool call is authorised against that user's RBAC permissions,
inputs pass the prompt-injection guardrails, and each call is written to the audit log. The agent can open and read
investigations over real sources; it has no tool that records a review decision.

    python scripts/claude_agent.py --as alice "Investigate The Boeing Company and summarise the policy outcome"
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from sentinel.agents.claude_agent import ClaudeToolAgent
from sentinel.agents.service import CaseService
from sentinel.agents.tools import build_tools
from sentinel.container import Container
from sentinel.security.users import UserService


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--as", dest="as_user", required=True)
    args = ap.parse_args()
    key = os.environ.get("SENTINEL_ANTHROPIC_API_KEY")
    if not key:
        print("error: SENTINEL_ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2
    import anthropic

    c = Container.build()
    c.settings.runner_mode = "inline"
    principal = UserService(c.db).get_principal(args.as_user)
    if principal is None:
        print("error: unknown user", file=sys.stderr)
        return 2
    agent = ClaudeToolAgent(
        anthropic.Anthropic(api_key=key), c.settings.anthropic_model, build_tools(CaseService(c)), c.guardrails, c.audit
    )
    try:
        run = agent.run(args.prompt, principal)
    finally:
        c.close()
    print(run.answer)
    print(json.dumps({"tool_calls": run.tool_calls, "stopped": run.stopped_reason}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
