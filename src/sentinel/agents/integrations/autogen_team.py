"""Microsoft AutoGen integration: Sentinel tools registered as AutoGen functions in a two-agent
(assistant + executor) conversation. Install with ``pip install sentinel-dd[crews]``."""

from __future__ import annotations

import json
from typing import Any

from sentinel.agents.tools import ToolSpec
from sentinel.security.auth import Principal


def build_autogen_pair(specs: dict[str, ToolSpec], principal: Principal, llm_config: dict[str, Any]) -> tuple[Any, Any]:
    import autogen

    assistant = autogen.AssistantAgent(
        "sentinel_analyst",
        system_message="You are a due-diligence analyst. Use tools; cite sources; escalate sanctions matches.",
        llm_config={**llm_config, "tools": [s.openai_spec() for s in specs.values()]},
    )
    executor = autogen.UserProxyAgent(
        "executor", human_input_mode="NEVER", code_execution_config=False, max_consecutive_auto_reply=6
    )
    for spec in specs.values():

        def _fn(_spec: ToolSpec = spec, **kwargs: Any) -> str:
            return json.dumps(_spec.invoke(principal, kwargs), default=str)

        executor.register_function(function_map={spec.name: _fn})
    return assistant, executor
