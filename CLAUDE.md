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
│   └── ralf.py              # RALFExecutor — bounded task loop (retrieve → act → learn → follow-up)
│
└── adapters/
    ├── base.py              # PollingAdapter — tick-driven external source bridge
    └── clickup.py           # ClickUpAdapter + ClickUpEventType — first concrete adapter

docs/
└── architecture.md          # Logical architecture, component roles, data flow (ASCII diagrams)
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
    EventBus, Event, SystemEventType, WILDCARD_STREAM,
    EventRouter, Subscriber,
    append_event, load_events,
    AgentBase, RALFExecutor, RALFResult,
    CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH,
    PollingAdapter, ClickUpAdapter, ClickUpEventType,
)
```

## Key design rules

- **LLM is not the orchestrator** — it's called inside `orient()` and `act()` only
- **Loops must be bounded** — `max_iterations` hard cap, error result if exhausted
- **Persist before fanout** — EventBus writes JSONL before routing
- **Adapters are not agents** — no reasoning, no LLM calls; deduplicate + emit only
- **Open EventType** — loopkit never imports consumer event types; consumers own their domain enums
- **Zero runtime deps** — stdlib only; `aiohttp` is consumer-supplied for ClickUpAdapter

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
