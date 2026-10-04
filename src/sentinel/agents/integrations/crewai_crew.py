"""CrewAI integration: the same Sentinel tools driven by a role-based CrewAI crew.

Useful when a client standardises on CrewAI. Install with ``pip install sentinel-dd[crews]``.
The LangGraph workflow remains the governed system of record; this crew is an alternative
exploratory front-end and never bypasses the HITL policy (it has no approval tool).
"""

from __future__ import annotations

from typing import Any

from sentinel.agents.tools import ToolSpec


def to_crewai_tools(specs: dict[str, ToolSpec]) -> list[Any]:
    from crewai.tools import BaseTool

    tools = []
    for spec in specs.values():

        def _run(self: Any, _spec: ToolSpec = spec, **kwargs: Any) -> str:
            import json

            return json.dumps(_spec.invoke(kwargs), default=str)

        cls = type(
            f"{spec.name.title().replace('_', '')}Tool",
            (BaseTool,),
            {
                "__annotations__": {"name": str, "description": str, "args_schema": type},
                "name": spec.name,
                "description": spec.description,
                "args_schema": spec.input_model,
                "_run": _run,
            },
        )
        tools.append(cls())
    return tools


def build_crew(specs: dict[str, ToolSpec], entity: str, llm: str = "anthropic/claude-sonnet-4-5") -> Any:
    from crewai import Agent, Crew, Process, Task

    tools = to_crewai_tools({k: v for k, v in specs.items() if k != "start_due_diligence"})
    researcher = Agent(
        role="Due-Diligence Researcher",
        goal=f"Collect cited facts about {entity}",
        backstory="Ex-Big4 forensic analyst.",
        tools=tools,
        llm=llm,
        allow_delegation=False,
    )
    analyst = Agent(
        role="Risk Analyst",
        goal="Quantify and explain counter-party risk",
        backstory="Model-risk specialist.",
        tools=tools,
        llm=llm,
        allow_delegation=False,
    )
    writer = Agent(
        role="Compliance Writer",
        goal="Write a concise cited memo",
        backstory="Compliance officer.",
        llm=llm,
        allow_delegation=False,
    )
    t1 = Task(
        description=f"Research ownership, sanctions, media and financials of {entity}.",
        expected_output="Bullet list of facts with citations",
        agent=researcher,
    )
    t2 = Task(
        description=f"Score {entity} and explain the top drivers.",
        expected_output="Risk band + drivers",
        agent=analyst,
        context=[t1],
    )
    t3 = Task(
        description="Write the due-diligence memo; escalate any sanctions match to a human.",
        expected_output="Markdown memo",
        agent=writer,
        context=[t1, t2],
    )
    return Crew(agents=[researcher, analyst, writer], tasks=[t1, t2, t3], process=Process.sequential)
