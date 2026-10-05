# ADR-0001: LangGraph as the system-of-record orchestrator

**Status:** Accepted

## Context
We need multi-agent orchestration with deterministic control flow, parallel branches, durable pause/resume for
human approval, and replayable state for audit.

## Decision
Use LangGraph `StateGraph` with typed state, reducers for parallel merges, `interrupt()`/`Command(resume=…)` for
HITL, and a durable checkpointer (SQLite file for single-node, PostgreSQL for multi-replica). Optional CrewAI/AutoGen
adapters (`agents/integrations`, untested in CI) wrap the same tool registry; they have no approval tool and cannot
bypass policy.

## Consequences
+ Explicit, testable graph; the governance gate is a node, not a prompt.
+ Durable HITL: the process can restart between submission and decision.
- Graph changes can break in-flight checkpoints; retries start a new attempt (`thread_id = case_id:attempt`).

*v2 update:* parallel collectors with a barrier join, a bounded critic loop and per-node `case_events` were added.
