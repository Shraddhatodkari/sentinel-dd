"""Native Anthropic Claude tool-use agent (ReAct-style loop over the Messages API).

Claude decides which Sentinel tools to call; we execute them (with guardrails + audit) and feed
``tool_result`` blocks back until Claude produces a final answer or the step budget is exhausted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sentinel.agents.tools import ToolSpec
from sentinel.security.audit import AuditLog
from sentinel.security.auth import Principal
from sentinel.security.guardrails import Guardrails

SYSTEM = (
    "You are Sentinel, an AI due-diligence research assistant. Use the tools to gather evidence and cite sources "
    "exactly as returned by tools. Never state facts that no tool returned. You cannot approve or reject cases — "
    "decisions are made by authorised human reviewers. Screening results are potential matches, not determinations."
)


@dataclass
class AgentRun:
    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    stopped_reason: str = "end_turn"


class ClaudeToolAgent:
    def __init__(
        self,
        client: Any,
        model: str,
        tools: dict[str, ToolSpec],
        guardrails: Guardrails,
        audit: AuditLog,
        max_turns: int = 8,
        max_tokens: int = 1024,
    ) -> None:
        self.client = client  # anthropic.Anthropic() or a test double
        self.model = model
        self.tools = tools
        self.guardrails = guardrails
        self.audit = audit
        self.max_turns = max_turns
        self.max_tokens = max_tokens

    def run(self, user_message: str, principal: Principal) -> AgentRun:
        """Run the tool-use loop *as* ``principal``: every tool call is authorised against its RBAC permissions."""
        actor = principal.subject
        check = self.guardrails.check_input(user_message)
        if not check.allowed:
            return AgentRun(f"Request blocked by guardrails: {check.violations}", stopped_reason="guardrail")
        messages: list[dict[str, Any]] = [{"role": "user", "content": check.sanitized or user_message}]
        run = AgentRun("")
        for turn in range(1, self.max_turns + 1):
            run.turns = turn
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM,
                messages=list(messages),
                tools=[t.anthropic_spec() for t in self.tools.values()],
            )
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                run.answer = "".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
                run.stopped_reason = resp.stop_reason or "end_turn"
                return run
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                tool = self.tools.get(block.name)
                try:
                    if tool is None:
                        raise KeyError(f"unknown tool {block.name}")
                    output = tool.invoke(principal, block.input)
                    content, is_error = json.dumps(output, default=str)[:20_000], False
                except Exception as exc:  # report tool errors back to the model (it can recover)
                    content, is_error = f"ERROR: {exc}", True
                content, _ = self.guardrails.sanitize_context(content)  # indirect-injection defence
                run.tool_calls.append({"tool": block.name, "input": block.input, "error": is_error})
                self.audit.append(
                    actor,
                    "agent.tool_call",
                    "tool",
                    block.name,
                    {"input": block.input, "error": is_error},
                    roles=principal.role_names,
                    tenant=principal.tenant,
                )
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
                )
            messages.append({"role": "user", "content": results})
        run.stopped_reason = "max_turns"
        run.answer = "Step budget exhausted before a final answer was produced."
        return run
