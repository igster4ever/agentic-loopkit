# agentic-loopkit — Logical Architecture

## Overview

agentic-loopkit implements the "Cheap Kafka" pattern: a local-first event bus backed by
append-only JSONL log files, with two agent execution models layered on top.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          agentic-loopkit runtime                            │
│                                                                             │
│   External systems                                                          │
│   ┌────────────┐  ┌────────────┐  ┌────────────┐                           │
│   │  ClickUp   │  │   Slack    │  │    Git     │  ...                      │
│   └─────┬──────┘  └─────┬──────┘  └─────┬──────┘                           │
│         │               │               │                                   │
│         └───────────────┴───────────────┘                                   │
│                         │ tick()                                             │
│               ┌─────────▼──────────┐                                        │
│               │  PollingAdapter(s)  │  deduplicate, cursor, emit            │
│               └─────────┬──────────┘                                        │
│                         │ publish(Event)                                     │
│               ┌─────────▼──────────┐                                        │
│               │      EventBus      │◄── app publishes direct events too     │
│               │                    │                                        │
│               │  1. append to      │                                        │
│               │     JSONL store    │                                        │
│               │  2. router fanout  │                                        │
│               └──┬─────────────┬───┘                                        │
│                  │             │                                             │
│        ┌─────────▼───┐   ┌─────▼──────────┐                                │
│        │  AgentBase  │   │ RALFExecutor   │                                │
│        │  (OODA)     │   │ (task loop)    │                                │
│        └─────────────┘   └────────────────┘                                │
│                                                                             │
│   JSONL store  ~/.cache/<app>/events-<stream>.jsonl  (one file per stream) │
│                ~/.cache/<app>/cursor-<adapter>.json  (adapter cursors)     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component roles

```
┌────────────────┬────────────────────────────────────────────────────────────┐
│ Component      │ Role                                                        │
├────────────────┼────────────────────────────────────────────────────────────┤
│ PollingAdapter │ Bridge to external systems. No reasoning. Deduplicates     │
│                │ via cursor. Emits typed Events. Tick-driven.               │
├────────────────┼────────────────────────────────────────────────────────────┤
│ EventBus       │ Coordinator. Owns router + store + registry. Persist-      │
│                │ before-fanout ensures no silent event loss on crash.       │
├────────────────┼────────────────────────────────────────────────────────────┤
│ EventRouter    │ Async callback fanout. Each stream has a subscriber list.  │
│                │ WILDCARD_STREAM "*" receives all events.                   │
├────────────────┼────────────────────────────────────────────────────────────┤
│ Event store    │ JSONL files. One per stream. Append-only. Replayable on    │
│                │ restart. compact_stream() for pruning.                     │
├────────────────┼────────────────────────────────────────────────────────────┤
│ AgentBase      │ OODA reactive agent. Subscribes to streams. Runs the 4-   │
│                │ phase pipeline on each event. LLM in orient() only.       │
├────────────────┼────────────────────────────────────────────────────────────┤
│ RALFExecutor   │ Bounded task loop. Triggered by a single event. Iterates  │
│                │ retrieve → act → learn → follow_up. Hard cap on loops.    │
│                │ LLM in act() only. Crash-safe via learn().                │
├────────────────┼────────────────────────────────────────────────────────────┤
│ Event          │ Immutable record. stream auto-derived from event_type     │
│                │ prefix. causation_id + correlation_id for traceability.   │
└────────────────┴────────────────────────────────────────────────────────────┘
```

---

## OODA loop (AgentBase)

Reactive pattern. Runs on every subscribed event.

```
                     ┌─── event arrives on subscribed stream ───┐
                     │                                           │
             ┌───────▼────────┐                                  │
             │   observe()    │  filter; gather initial context  │
             │  no LLM call   │  return None → skip event       │
             └───────┬────────┘                                  │
                     │ context dict                              │
             ┌───────▼────────┐                                  │
             │   orient()     │  interpret; call LLM here        │
             │ ← LLM here →  │  return None → skip              │
             └───────┬────────┘                                  │
                     │ orientation                               │
             ┌───────▼────────┐                                  │
             │   decide()     │  apply confidence thresholds     │
             │  no LLM call   │  return None → do nothing        │
             └───────┬────────┘                                  │
                     │ action                                    │
             ┌───────▼────────┐                                  │
             │    act()       │  execute; publish downstream     │
             │  no LLM call   │  events via bus                 │
             └────────────────┘                                  │
                                                                 │
             ← any phase returning None short-circuits here ─────┘
```

LLM placement rule: **orient() only**. observe, decide, act are deterministic.

---

## RALF loop (RALFExecutor)

Bounded task execution. Triggered once by an incoming Event; may run up to `max_iterations`.

```
  trigger event
       │
       ▼
  retrieve()  ──────────────────────────────────────────────────────────────
  │                                                                         │
  │  Assemble context deterministically.                                    │
  │  Load related events, wiki articles, prior outputs.                    │
  │  No LLM calls here.                                                    │
  └──────────────────────────────────────────────────────────────────────▶ context
                                                                           │
                           ┌───────────────────────────────────────────────┘
                           │
                     ┌─────▼──────────────────────────────────────┐
                     │           iteration (up to max_iterations)  │
                     │                                             │
                     │   act(context, prior_result)                │
                     │   │  LLM call here                          │
                     │   │  returns RALFResult {                   │
                     │   │    status:     complete | in_progress   │
                     │   │               rejected | error          │
                     │   │    confidence: 0.0 – 1.0               │
                     │   │    output:     domain result            │
                     │   │    next_step:  hint for next iteration  │
                     │   │  }                                      │
                     │   │                                         │
                     │   │  confidence < 0.40 → HARD REJECT        │
                     │   │                                         │
                     │   ▼                                         │
                     │   learn(event, result)  ◄── always called   │
                     │   │  persist state; crash-safe             │
                     │   │                                         │
                     │   ▼                                         │
                     │   is_terminal? ──yes──────────────────────▶ │
                     │       │                                     │
                     │       no                                    │
                     │       │                                     │
                     └───────┘  loop                               │
                                                                   │
                     max_iterations reached → error result         │
                                                                   ▼
                                                          follow_up(event, result)
                                                          │  return downstream Event
                                                          │  or None
                                                          ▼
                                                     bus.publish()  (if event returned)
```

---

## Event model and traceability

```
  Event {
    event_id:       uuid4           ─── unique identifier
    event_type:     str             ─── "<stream>.<action>"  e.g. "gps.cycle_complete"
    stream:         str             ─── auto-derived: event_type.split(".")[0]
    source:         str             ─── emitting component name
    timestamp:      datetime (UTC)
    payload:        dict
    causation_id:   str | None      ─── event_id of direct cause
    correlation_id: str | None      ─── business workflow ID (e.g. ClickUp task ID)
  }
```

Causal chain example:

```
  clickup.task_updated  [correlation_id="CU-123", causation_id=None]
          │
          │  AgentBase detects, calls orient(), emits:
          ▼
  command.analyse_task  [correlation_id="CU-123", causation_id="<above event_id>"]
          │
          │  RALFExecutor picks up, runs loop, emits:
          ▼
  adr.draft_created     [correlation_id="CU-123", causation_id="<above event_id>"]
```

Every event in the chain shares `correlation_id="CU-123"` — the entire workflow is
queryable from the JSONL store with a single filter.

---

## JSONL store layout

```
~/.cache/<your-app>/
│
├── events-system.jsonl      ← bus lifecycle events (BUS_STARTED, BUS_STOPPED, etc.)
├── events-gps.jsonl         ← all events with stream "gps"
├── events-adr.jsonl         ← all events with stream "adr"
├── events-clickup.jsonl     ← ClickUpAdapter events
├── events-<stream>.jsonl    ← one file per stream name
│
├── cursor-clickup.json      ← ClickUpAdapter cursor (Unix ms timestamp)
└── cursor-<adapter>.json    ← one per registered adapter
```

**Replay:** `load_events(stream, hours=24)` replays from disk — gives crash recovery,
audit trails, and local dev/test without live external systems.

**Wildcard:** `load_events("*")` loads all stream files.

---

## Confidence model

```
  1.0 ─────────────────────── absolute certainty (rare)
  0.85 ─────────── HIGH ────── proceed
  0.65 ─────────── MEDIUM ─── proceed, note uncertainty in output
  0.40 ─────────── LOW ─────── recommend clarification
  0.00 ─────────── REJECT ──── hard reject, mandatory — do not proceed
```

The boundary at 0.40 is enforced by the RALF loop runner — it cannot be bypassed by
returning `status="complete"` with low confidence. The executor replaces the result with
`status="rejected"` automatically.

---

## OODA + ReAct composition

OODA and ReAct are not alternatives — they are layers. OODA governs strategy and adaptation
across events; ReAct governs step-by-step tool use within a single act() decision.

```
OODA (outer — strategic loop):
  observe()  ── filter / gather signals from event stream
  orient()   ── LLM reasons about what needs to happen         ← LLM call
  decide()   ── choose which executor to invoke
  act()      ── await ReActExecutor.run(event)
                  └─ ReAct (inner — tool execution loop):
                       think()    ── LLM picks next tool        ← LLM call
                       execute()  ── call tool, get observation  (deterministic)
                       on_step()  ── hook: log / telemetry
                       (repeat until action="done" or max_steps)
```

RALF sits at the same level as ReAct — both are execution patterns invoked from OODA's act():
- Use **ReAct** when the task is tool-driven (search, API calls, code execution).
- Use **RALF** when the task requires bounded iterative refinement with crash-safe state.

---

## PollingAdapter flow

```
   schedule tick (APScheduler / asyncio)
         │
         ▼
   tick()
   ├── poll(cursor)                    ← subclass implements this
   │   ├── fetch from external API     ← HTTP calls, pagination, dedup
   │   └── return (events, new_cursor)
   │
   ├── for event in events:
   │       bus.publish(event)          ← persist + fanout
   │
   ├── save cursor to JSON file        ← on success only
   │
   └── on exception:
           bus.publish(system.adapter_error)   ← never silently swallows errors
           return 0
```

Cursor is opaque — use whatever the source API provides: Unix ms timestamp (ClickUp),
ISO string, page token, sequence number, or a set of seen IDs.

---

## Planned extensions

### Additional executors (see `docs/idioms-adoption-plan.md`)

| Executor | File | Pattern | Status |
|---|---|---|---|
| `ReActExecutor` | `loops/react.py` | Thought→Action→Observation bounded loop | Planned |
| `PlanExecutor` | `loops/plan.py` | LLM decomposition + per-step execution | Planned |
| `ReflexionExecutor` | `loops/reflexion.py` | RALF + explicit critique phase | Planned |

### EventMeta convention (see `CLAUDE.md`)

An optional `EventMeta` dataclass will be added to `events/models.py`. Loopkit components
write structured framework metadata (phase, loop_type, confidence, context text) into
`payload["_meta"]`. Consumer payload keys are never modified. Not yet implemented.

### Dashboard (see `docs/dashboard-architecture.md`)

An optional `agentic_loopkit/dashboard/` package will provide a FastAPI management API
(HTTP + WebSocket) and a `dashboard/ui/` Bun/Vite/React frontend — the event chain
inspector shown in the design reference. Install via `pip install agentic-loopkit[dashboard]`.
Not yet built.

---

## Wire-in example (FastAPI lifespan)

```python
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from agentic_loopkit import EventBus

@asynccontextmanager
async def lifespan(app: FastAPI):
    bus = EventBus(store_dir=Path.home() / ".cache" / "my-app")
    bus.register(MyAgent("agent", bus))
    bus.add_adapter(ClickUpAdapter(bus, api_token=..., list_ids=[...]))
    await bus.start()
    app.state.bus = bus
    yield
    await bus.stop()

app = FastAPI(lifespan=lifespan)
```

Handlers can then do `request.app.state.bus.publish(...)` for explicit event emission
alongside the adapter-driven reactive flow.
