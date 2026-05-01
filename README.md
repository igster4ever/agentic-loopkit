# agentic-loopkit

> Local-first, event-driven agent runtime — OODA reactive agents and RALF bounded task loops over a "cheap Kafka" JSONL event bus.

Zero runtime dependencies. Pure Python 3.11+ stdlib + asyncio.

---

## What it is

A lightweight runtime for building event-driven agentic systems without a message broker, cloud infrastructure, or heavy framework. Events are appended to JSONL log files (one per stream). Agents and executors subscribe to streams and react. The LLM is a reasoning engine — it is never the orchestrator.

**Core pattern:**

```
External systems  →  PollingAdapters  →  EventBus  →  AgentBase (OODA)
                                                    →  RALFExecutor (task loop)
                                                    →  JSONL store (crash-safe replay)
```

---

## Concepts

### Event
The unit of communication. Any `StrEnum` value works as `event_type` — consumers define their own domain enums; the loopkit only defines system-level events.

```python
from enum import StrEnum
from agentic_loopkit import Event

class MyEventType(StrEnum):
    TASK_UPDATED = "tasks.updated"

event = Event(
    event_type     = MyEventType.TASK_UPDATED,
    source         = "clickup-adapter",
    payload        = {"id": "abc123", "status": "in progress"},
    correlation_id = "abc123",   # threads all events in a workflow
)
```

Traceability is built in: `causation_id` (what caused this event) and `correlation_id` (business workflow ID) are first-class fields. Use `event.caused(type, source, payload)` to propagate them automatically.

### EventBus
Coordinator. Persist-before-fanout: JSONL written before router dispatch so no event is silently lost on crash.

```python
from agentic_loopkit import EventBus, Event
from pathlib import Path

bus = EventBus(store_dir=Path("~/.cache/my-app"))
await bus.start()
await bus.publish(event)
await bus.stop()
```

### AgentBase — OODA loop
Reactive agents subscribe to event streams. Each event runs through:

```
observe()  →  orient()  →  decide()  →  act()
  filter      LLM here    thresholds   side effects
```

Any phase can return `None` to short-circuit. LLM calls belong in `orient()`. Confidence thresholds belong in `decide()`.

### RALFExecutor — bounded task loop
Retrieve → Act → Learn → Follow-up. For multi-step tasks triggered by events.

```
retrieve()  →  act()  →  learn()  →  follow_up()
 context       LLM     persist      downstream event
```

Hard cap at `max_iterations`. Hard reject if `confidence < 0.40`. Every step calls `learn()` for crash-safe state — a restart can resume.

### PollingAdapter
Bridge for external systems that don't push webhooks. Tick-driven; call `tick()` on a schedule. Cursor managed automatically (JSON file per adapter).

---

## Quick start

```python
from agentic_loopkit import (
    EventBus, Event, AgentBase, RALFExecutor, RALFResult,
    CONFIDENCE_MEDIUM,
)
from pathlib import Path

# 1. Define domain event types
from enum import StrEnum
class AppEventType(StrEnum):
    TICKET_UPDATED  = "ticket.updated"
    SUMMARY_CREATED = "ticket.summary_created"

# 2. Build an OODA agent
class SummaryAgent(AgentBase):
    async def observe(self, event):
        if event.event_type != AppEventType.TICKET_UPDATED: return None
        return {"ticket": event.payload}

    async def orient(self, event, context):
        # call your LLM here
        summary   = await my_llm(context["ticket"]["description"])
        confidence = 0.87
        return {"summary": summary, "confidence": confidence}

    async def decide(self, event, orientation):
        if orientation["confidence"] < CONFIDENCE_MEDIUM: return None
        return orientation

    async def act(self, event, action):
        await self._bus.publish(
            event.caused(AppEventType.SUMMARY_CREATED, self.name, action)
        )

# 3. Wire up and run
async def main():
    bus   = EventBus(store_dir=Path("~/.cache/my-app"))
    agent = SummaryAgent("summary-agent", bus)
    agent.subscribe("ticket")

    bus.register(agent)
    await bus.start()

    await bus.publish(Event(
        event_type = AppEventType.TICKET_UPDATED,
        source     = "clickup",
        payload    = {"id": "T-42", "description": "Add retry logic to payment service"},
    ))

    await bus.stop()
```

---

## ClickUp adapter

```python
from agentic_loopkit import ClickUpAdapter
import os

adapter = ClickUpAdapter(
    bus       = bus,
    api_token = os.environ["CLICKUP_API_TOKEN"],
    list_ids  = ["abc123", "def456"],  # preferred over team_id
)
bus.add_adapter(adapter)

# Tick from APScheduler or asyncio:
await adapter.tick()   # fetches tasks updated since last cursor, emits events
```

Requires `aiohttp` (`pip install aiohttp`).

---

## Install

No PyPI release yet. Add as a path dependency:

```bash
# pip
pip install -e /path/to/agentic-loopkit

# or in pyproject.toml
[tool.uv.sources]
agentic-loopkit = { path = "../agentic-loopkit", editable = true }

# or sys.path (dev / no packaging)
sys.path.insert(0, "/path/to/agentic-loopkit")
```

---

## Design principles

| Principle | Detail |
|-----------|--------|
| **LLM is not the orchestrator** | LLM called in `orient()` and `act()` only; routing is deterministic |
| **Cheap Kafka** | JSONL append log per stream; no broker; replay from disk on restart |
| **Bounded loops** | `max_iterations` hard cap; confidence < 0.40 → hard reject |
| **Persist before fanout** | JSONL written before router dispatch; no silent event loss |
| **Open EventType** | Consumers own their domain enums; loopkit never imports them |
| **Zero runtime deps** | Pure stdlib asyncio; `aiohttp` is consumer-supplied |

---

## Confidence bands

| Band | Range | Behaviour |
|------|-------|-----------|
| High | ≥ 0.85 | Proceed |
| Medium | 0.65 – 0.84 | Proceed, note uncertainty |
| Low | 0.40 – 0.64 | Recommend clarification |
| Very low | < 0.40 | **Hard reject** — mandatory |

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```

---

## See also

- `docs/architecture.md` — logical architecture and data flow (ASCII diagrams)
- `CLAUDE.md` — codebase reference for Claude Code sessions
