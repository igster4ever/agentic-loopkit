# agentic-loopkit — Claude Reference

## What this is

A standalone Python package providing a local-first, event-driven agent runtime.
Zero runtime dependencies. Pure stdlib asyncio + dataclasses.

Primary consumer: GPS·ADR Radar suite (`igster4ever/squad-gps-radar`).
Designed to be reusable across any project — including MPSM and future agentic work.

## Repo layout

```
agentic_loopkit/
├── __init__.py              # Public surface — everything exported here
├── bus.py                   # EventBus: owns router, store, agent/adapter registry
│
├── events/
│   ├── models.py            # Event dataclass + SystemEventType(StrEnum)
│   ├── router.py            # Async callback fanout (Subscriber = Callable[[Event], Awaitable[None]])
│   └── store.py             # JSONL per-stream persistence (~/.cache/<app>/events-<stream>.jsonl)
│
├── agents/
│   └── base.py              # AgentBase — OODA loop (observe → orient → decide → act)
│
├── loops/
│   ├── ralf.py              # RALFExecutor — bounded task loop (retrieve → act → learn → follow-up)
│   ├── react.py             # ReActExecutor — bounded tool-use loop (think → execute, action="done")
│   ├── plan.py              # PlanExecutor — front-loaded decomposition (plan → execute_step × N)
│   └── # planned: reflexion.py — RALFExecutor + critique() phase (see docs/idioms-adoption-plan.md)
│
└── adapters/
    ├── base.py              # PollingAdapter — tick-driven external source bridge
    └── clickup.py           # ClickUpAdapter + ClickUpEventType — first concrete adapter

docs/
├── architecture.md          # Logical architecture, component roles, data flow (ASCII diagrams)
├── idioms-adoption-plan.md  # ReActExecutor / PlanExecutor / ReflexionExecutor design decisions
└── dashboard-architecture.md # FastAPI management API + Bun/React dashboard spec

tests/
├── events/                  # test_models (incl. EventMeta), test_router, test_store
├── agents/                  # test_base (OODA pipeline)
├── loops/                   # test_ralf, test_react, test_plan
└── adapters/                # test_base (PollingAdapter), test_clickup
```

## Core concepts

### Event
The unit of communication. `event_type` is an open `str` field — consumers define their own
`StrEnum` subclasses and pass them in. `stream` is auto-derived from the event_type prefix.

```python
from enum import StrEnum
class MyEventType(StrEnum):
    THING_HAPPENED = "things.happened"

event = Event(event_type=MyEventType.THING_HAPPENED, source="my-service", payload={...})
# event.stream == "things"
```

Traceability fields on every Event:
- `causation_id` — event_id of the direct cause (chain tracing)
- `correlation_id` — business workflow ID shared by all events in a flow

Use `event.caused("child.type", "source", payload)` to create a child that inherits correlation_id.

**EventMeta convention** (implemented 2026-05-04):
Loopkit components emit structured framework metadata via a reserved `payload["_meta"]` key.
Consumer domain payload keys are never touched. Use when emitting events from agents/executors:

```python
from agentic_loopkit import EventMeta

payload = {
    **domain_data,
    "_meta": EventMeta(
        phase="act", loop_type="ooda", confidence=0.82,
        context="Agent reasoning text for dashboard Context tab",
    ).to_dict()
}
```

Fields: `phase`, `loop_type` (`"ooda"|"ralf"|"react"|"plan"|"reflexion"`), `iteration`, `confidence`, `context`, `tags`.
All fields optional. `to_dict()` omits None fields and empty tag lists.
Read back via `event.meta()` — returns the `_meta` dict or `None` if absent.
The dashboard renders `payload["_meta"]["context"]` in the Context tab.

### EventBus
Single entry point. Owns the router, store directory, and registered agents/adapters.
Persist-before-fanout: JSONL write happens before router dispatch — no silent event loss on crash.

```python
bus = EventBus(store_dir=Path("~/.cache/my-app").expanduser())
bus.register(MyAgent("agent", bus))
bus.add_adapter(MyAdapter(bus))
await bus.start()
await bus.publish(Event(...))
await bus.stop()
```

### AgentBase (OODA)
Reactive. Subscribe to streams; pipeline runs on each event:
- `observe(event)` → context dict or None (filter; no LLM)
- `orient(event, context)` → orientation or None (primary LLM phase)
- `decide(event, orientation)` → action or None (apply confidence thresholds here)
- `act(event, action)` → side effects, publish downstream events (no LLM)

### RALFExecutor (bounded task loop)
Retrieve → Act → Learn → Follow-up. Hard cap at `max_iterations`.
- `retrieve(event)` → context (deterministic; no LLM)
- `act(context, prior_result)` → RALFResult (primary LLM phase; returns confidence score)
- `learn(event, result)` → persist state after every step (crash-safe)
- `follow_up(event, result)` → return downstream Event or None

Confidence bands: HIGH ≥ 0.85, MEDIUM ≥ 0.65, LOW ≥ 0.40, **< 0.40 hard reject**.

### ReActExecutor (bounded tool-use loop)
Think → Execute. Hard cap at `max_steps`. Composes inside OODA's `act()` phase.
- `think(event, trace)` → `(thought, action, action_input)` (primary LLM phase)
- `execute(action, action_input)` → observation string (deterministic tool dispatch; no LLM)
- `on_step(step)` → hook after each step (default no-op; use for dashboard telemetry)
- `follow_up(event, result)` → return downstream Event or None

Terminal signal: `action="done"` — `action_input` becomes `result.answer`.
`result.status`: `"complete"` | `"max_steps_reached"` | `"error"`.

### PlanExecutor (front-loaded task decomposition)
Plan → Execute × N. Step list is fixed at plan time; no iteration cap on the plan itself.
- `plan(event)` → `list[PlanStep]` (primary LLM call; decomposes task up front)
- `execute_step(event, step, prior_outputs)` → `(output, success)` (wire ReActExecutor here)
- `follow_up(event, result)` → return downstream Event or None

`result.status`: `"complete"` (all steps succeeded) | `"partial"` (some failed) | `"failed"` (all failed or `plan()` raised).
Each `ReActExecutor` wired inside `execute_step()` carries its own `max_steps` cap.

### PollingAdapter
External system bridge. Tick-driven (APScheduler, asyncio loop, etc.).
- `poll(cursor)` → `(list[Event], new_cursor)`
- Cursor persisted as JSON at `store_dir/cursor-{name}.json`
- Errors emit `system.adapter_error` events rather than raising

### ClickUpAdapter
First concrete adapter. Polls `/list/{id}/task` or `/team/{id}/task` for updates since cursor.
Cursor = Unix ms timestamp. Emits `clickup.task_updated` / `clickup.task_created`.
Requires `aiohttp` (optional dep — lazy-imported at call time).

## Public API

```python
from agentic_loopkit import (
    # Bus
    EventBus,
    # Events
    Event, EventMeta, SystemEventType, WILDCARD_STREAM,
    EventRouter, Subscriber,
    append_event, load_events,
    # Agents
    AgentBase,
    # Executors — RALF
    RALFExecutor, RALFResult,
    CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH,
    # Executors — ReAct
    ReActExecutor, ReActResult, ReActStep,
    # Executors — Plan
    PlanExecutor, PlanResult, PlanStep,
    # Adapters
    PollingAdapter, ClickUpAdapter, ClickUpEventType,
)
```

## Key design rules

- **LLM is not the orchestrator** — it's called inside `orient()` (OODA), `act()` (RALF), `think()` (ReAct), and `plan()` (PlanExecutor) only
- **Loops must be bounded** — `max_iterations` hard cap, error result if exhausted
- **Persist before fanout** — EventBus writes JSONL before routing
- **Adapters are not agents** — no reasoning, no LLM calls; deduplicate + emit only
- **Open EventType** — loopkit never imports consumer event types; consumers own their domain enums
- **Zero runtime deps** — stdlib only; `aiohttp` is consumer-supplied for ClickUpAdapter

## OODA + ReAct composition pattern

OODA and ReAct are **not alternatives** — they are layers. The canonical wiring:

```
OODA (outer — strategic loop):
  observe()  → filter/gather signals from the event stream
  orient()   → LLM reasons about what needs to happen
  decide()   → choose which executor to invoke
  act()      → await ReActExecutor.run(event)
                 └─ ReAct (inner — tool execution loop):
                      think()   → LLM picks next tool
                      execute() → call tool, get observation
                      (repeat until action="done")
```

OODA governs strategy and adaptation across events.
ReAct governs step-by-step tool use within a single decision.
RALF governs multi-step task execution with crash-safe state and confidence enforcement.

See `docs/idioms-adoption-plan.md` for full executor specs and build order.

## Adding a new executor

```python
# agentic_loopkit/loops/my_executor.py
from abc import abstractmethod
from .react import ReActExecutor   # or RALFExecutor / PlanExecutor as base

class MyExecutor(ReActExecutor):
    max_steps = 5

    async def think(self, event, trace):
        thought = await call_llm(event.payload, trace)
        return thought, action, action_input   # action="done" to terminate

    async def execute(self, action, action_input):
        return await dispatch_tool(action, action_input)

    async def follow_up(self, event, result):
        if result.is_complete:
            return event.caused("my.complete", self.name, {"answer": result.answer})
        return None
```

Then add to `agentic_loopkit/__init__.py` exports.
Tests go in `tests/loops/test_my_executor.py` — follow `test_react.py` or `test_plan.py` as template.

## Adding a new adapter

```python
# agentic_loopkit/adapters/my_source.py
from enum import StrEnum
from .base import PollingAdapter
from ..events.models import Event

class MyEventType(StrEnum):
    THING_CREATED = "my_source.thing_created"

class MySourceAdapter(PollingAdapter):
    name = "my_source"

    async def poll(self, cursor):
        items, new_cursor = await _fetch_since(cursor)
        events = [Event(event_type=MyEventType.THING_CREATED, source=self.name,
                        payload=item, correlation_id=item["id"])
                  for item in items]
        return events, new_cursor
```

Then add to `agentic_loopkit/adapters/__init__.py` and `agentic_loopkit/__init__.py`.

## Adding a new agent

```python
class MyAgent(AgentBase):
    async def observe(self, event):
        if event.event_type != "things.happened": return None
        return {"data": event.payload}

    async def orient(self, event, context):
        result = await call_llm(context)           # LLM here
        return {"decision": result, "confidence": 0.82}

    async def decide(self, event, orientation):
        if orientation["confidence"] < CONFIDENCE_MEDIUM: return None
        return orientation

    async def act(self, event, action):
        await self._bus.publish(
            event.caused("things.processed", self.name, action)
        )
```

Register: `bus.register(MyAgent("my-agent", bus))`; subscribe: `agent.subscribe("things")`

## Store layout

```
~/.cache/<app>/
├── events-gps.jsonl          # one file per stream
├── events-adr.jsonl
├── events-system.jsonl
├── cursor-clickup.json       # adapter cursors
└── cursor-<adapter-name>.json
```

Stream wildcard `"*"` loads all stream files when calling `load_events()`.

## Tests

```bash
python -m pytest          # asyncio_mode = auto, testpaths = tests/
```

127 tests, all passing (as of 2026-05-04). Coverage: EventBus, EventRouter, EventStore,
AgentBase (all OODA short-circuit paths), RALFExecutor (confidence rejection, learn, follow-up),
ReActExecutor (happy path, max_steps, error handling, on_step hook, follow-up),
PlanExecutor (all-complete, partial, failed, plan() raises, step exception recovery, prior_outputs),
EventMeta (to_dict field omission, event.meta() helper),
PollingAdapter (cursor, error event), ClickUpAdapter (payload mapping, dedup, cursor).

## Dashboard (planned — not yet built)

Optional FastAPI management API + Bun/Vite/React event inspector.
Install with: `pip install agentic-loopkit[dashboard]`
Full spec: `docs/dashboard-architecture.md`
