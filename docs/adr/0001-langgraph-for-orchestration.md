# ADR-0001: LangGraph as the system-of-record orchestrator

**Status:** Accepted

## Context
We need multi-agent orchestration with deterministic control flow, parallel branches, durable pause/resume for
human approval, and replayable state for audit.

## Decision
Use LangGraph `StateGraph` with typed state, reducers for parallel merges, `interrupt()`/`Command(resume=…)` for
HITL, and a pluggable checkpointer (in-memory for dev, Postgres for prod). CrewAI and AutoGen are supported only as
*alternative front-ends* over the same tool registry. They have no approval tool and cannot bypass policy.

## Consequences
+ Explicit, testable graph; the governance gate is a node, not a prompt.
+ Durable HITL: the process can restart between submission and decision.
- Graph changes need migrations for in-flight checkpoints (we version state keys).
