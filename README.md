# agentic-loopkit

> Local-first, event-driven agent runtime — OODA reactive agents, RALF bounded task loops, and a family of task/tool/skill-optimising executors over a "cheap Kafka" JSONL event bus.

Zero runtime dependencies. Pure Python 3.11+ stdlib + asyncio. Optional extras add a dashboard, a governance layer, and a semantic memory store — none of them touch the core.

---

## What it is

A lightweight runtime for building event-driven agentic systems without a message broker, cloud infrastructure, or heavy framework. Events are appended to JSONL log files (one per stream). Agents and executors subscribe to streams and react. The LLM is a reasoning engine — it is never the orchestrator.

**Core pattern:**

```
External systems  →  PollingAdapters  →  EventBus  →  AgentBase (OODA)
                                                    →  RALFExecutor family (bounded task loops)
                                                    →  JSONL store (crash-safe replay)
```

---

## Concepts

### Event
The unit of communication. Any `StrEnum` value works as `event_type` — consumers define their own domain enums; the loopkit only defines system-level events.

```python
from enum import StrEnum
from agentic_loopkit import Event, EventMeta, TrustLevel

class MyEventType(StrEnum):
    TASK_UPDATED = "tasks.updated"

event = Event(
    event_type      = MyEventType.TASK_UPDATED,
    source          = "clickup-adapter",
    payload         = {
        "id": "abc123", "status": "in progress",
        "_meta": EventMeta(phase="act", loop_type="ralf", confidence=0.82).to_dict(),
    },
    correlation_id  = "abc123",              # threads all events in a workflow
    trust_level     = TrustLevel.MEDIUM,     # default — self-declared by the source
)
```

Traceability and governance are built in:
- `causation_id` / `correlation_id` — direct-cause and business-workflow threading; `event.caused(type, source, payload)` propagates both automatically, plus `trust_level` and an auto-incremented `delegation_depth`
- `trust_level` (`TrustLevel` StrEnum) + `delegation_depth` (hop count from root) — the governance layer (see below) flags runaway delegation and untrusted sources
- `payload["_meta"]` (`EventMeta`) — reserved key for framework metadata (`phase`, `loop_type`, `confidence`, `context`, `tags`) without touching your domain payload; read back via `event.meta()`

### EventBus
Coordinator. Persist-before-fanout: JSONL written before router dispatch so no event is silently lost on crash. Also owns backpressure signals, adapter liveness tracking, and graceful drain on shutdown.

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

State persists across restarts via CoALA-decomposed `AgentState` (episodic / semantic / procedural / world_model): `await agent.save_state(...)` / `await agent.load_state()`. Wire a store (e.g. [`agentic-memorykit`](#optional-extras)) via `agent._memory_store = MemoryStore(store_dir)` and call `await agent.recall(text)` for BM25-backed iterative retrieval.

### The executor family
One base pattern (bounded, event-triggered task execution), several variants for different quality/verification needs. All extend `RALFExecutor` via `_post_act_hook()` — never by copying `run()`.

| Executor | Pattern | LLM placement |
|---|---|---|
| `RALFExecutor` | Retrieve → Act → Learn → Follow-up; hard-capped, confidence-gated | `act()` |
| `ReActExecutor` | Thought → Action → Observation tool-use loop; composes inside `act()` | `think()` |
| `PlanExecutor` | Front-loaded decomposition into `PlanStep`s, then per-step execution | `plan()` |
| `ReflexionExecutor` | RALF + same-context self-critique between `act()` and `learn()` | `act()`, `critique()` |
| `OutcomeExecutor` | RALF + rubric-governed **isolated** evaluation (no anchoring) | `act()`, `evaluate()` |
| `UtilityExecutor` | Standalone generate-and-rank, single pass (not a RALF variant) | `generate_candidates()`, `utility_score()` |
| `SkillOptExecutor` | Bounded, validation-gated skill-document optimiser (arXiv:2605.23904) | `reflect()` only — `score()` is deterministic |
| `SelfHarnessExecutor` | Wires `FailurePatternAgent` → `SkillOptExecutor` → `AgentTestHarness.regression_gate()` | none — `evaluate()` is deterministic |
| `FrontierSelector` | Ranks a candidate pool by utility + productivity + novelty; `revoke()` permanently de-authorizes a candidate | none — scoring primitive |
| `VerificationContract` | `criteria` / `evidence_type` / `stopping_condition` → `to_rubric()` bridges straight into `OutcomeExecutor.rubric` | n/a — plumbing only |

`ReflexionExecutor`'s self-critique runs in the *same* context as `act()` — cheap, but exposed to same-model rubber-stamping. `OutcomeExecutor`'s `evaluate()` sees only `(artifact, rubric)`, no prior chain — pick whichever anchoring risk fits your use case.

### PollingAdapter family
Bridges for external systems that don't push webhooks. Tick-driven; call `tick()` on a schedule. Cursor managed automatically (JSON file per adapter). No reasoning, no LLM calls — adapters deduplicate and emit only.

| Adapter | Source | Notes |
|---|---|---|
| `ClickUpAdapter` | ClickUp REST API | requires `aiohttp` |
| `SlackAdapter` | Slack `conversations.history` | pagination + 429 backoff; requires `aiohttp` |
| `LocalGitAdapter` | Local git repo (`git log` subprocess) | zero extra deps |
| `CommunityFeedAdapter` | External JSONL feed | events tagged `TrustLevel.UNTRUSTED` by default — see the governance layer |

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

Requires `aiohttp` (`pip install aiohttp`). `SlackAdapter` and `LocalGitAdapter` follow the same `bus.add_adapter()` + `tick()` shape — see `agentic_loopkit/adapters/`.

---

## Optional extras

Core install has zero runtime dependencies. Three optional extras layer on additional capability without touching it:

```bash
pip install -e ".[dashboard]"    # FastAPI management API + Bun/React event inspector
pip install -e ".[governance]"   # agentic_govkit — no extra runtime deps of its own
pip install -e ".[memory]"       # agentic-memorykit — durable semantic fact store
pip install -e ".[dev]"          # pytest, pytest-asyncio, httpx — for running the test suite
```

### Dashboard (`[dashboard]`)

FastAPI management API + a Bun/Vite/React 19 event inspector (DAG graph, live tail, timeline). Bind to any running `EventBus`:

```python
from agentic_loopkit.dashboard import create_app
import uvicorn

uvicorn.run(create_app(bus), host="0.0.0.0", port=8765)
```

REST: `/api/streams`, `/api/events`, `/api/events/{id}`, `/api/chains/{correlation_id}`, `/api/agents`, `/api/adapters`. Live tail: `WS /ws/tail`. See `docs/dashboard-architecture.md`.

### Governance (`[governance]`)

`agentic_govkit` is a separate top-level package, one-way dependent on `agentic_loopkit` (never the reverse — enforced by a boundary test). Adds a participant layer that observes the bus and enforces policy, rather than wrapping it:

- `AuditAgent` — wildcard observer; flags depth-exceeded delegation chains, untrusted-source escalation, and confidence breaches as `governance.*` events
- `KillSwitchAgent` — policy enforcement (`halt_correlation`, `quarantine_source`, `emit_human_override`)
- `GovernanceLearningAgent` / `CommunityTrustLearner` — accumulates policy recommendations from governance history; graduates community-feed trust `UNTRUSTED → LOW → MEDIUM → HIGH`, one level at a time
- `ConflictResolutionExecutor` — two-party dispute mediation (`OutcomeExecutor` subclass)
- `CouncilExecutor` — fan-out to N specialist agents → weighted consensus (`OutcomeExecutor` subclass)

See `docs/community-feed-trust-pathway.md` for the full trust-graduation wiring guide.

### Memory (`[memory]`)

Wires [`agentic-memorykit`](https://github.com/igster4ever/agentic-memorykit) — a standalone, zero-dependency semantic fact store — into `AgentBase`:

```python
from agentic_memorykit import MemoryStore

agent._memory_store = MemoryStore(store_dir)
await agent.save_state(state)              # persists episodic/semantic/procedural/world_model buckets
loaded = await agent.load_state()
results = await agent.recall("what do we know about X?")   # BM25 iterative retrieval
```

---

## Design principles

| Principle | Detail |
|-----------|--------|
| **LLM is not the orchestrator** | LLM called inside a specific phase per executor (`orient()`, `act()`, `think()`, `plan()`, `critique()`, `evaluate()`, `reflect()`) — routing is always deterministic |
| **Cheap Kafka** | JSONL append log per stream; no broker; replay from disk on restart |
| **Bounded loops** | `max_iterations` hard cap on every executor; confidence < 0.40 → hard reject |
| **Persist before fanout** | JSONL written before router dispatch; no silent event loss |
| **Open EventType** | Consumers own their domain enums; loopkit never imports them |
| **Adapters are not agents** | No reasoning, no LLM calls — deduplicate and emit only |
| **Observability before enforcement** | Governance is a bus participant, not a wrapper — every audit decision is itself an event |
| **Zero runtime deps** | Pure stdlib asyncio; `aiohttp`/`fastapi`/`agentic-memorykit` are consumer-supplied via optional extras |

---

## Confidence bands

| Band | Range | Behaviour |
|------|-------|-----------|
| High | ≥ 0.85 | Proceed |
| Medium | 0.65 – 0.84 | Proceed, note uncertainty |
| Low | 0.40 – 0.64 | Recommend clarification |
| Very low | < 0.40 | **Hard reject** — mandatory |

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

Add `[dashboard]`, `[governance]`, and/or `[memory]` as needed — see [Optional extras](#optional-extras).

---

## Tests

```bash
pip install -e ".[dev]"

# macOS: system Python is PEP 668-blocked — always run through the venv
.venv/bin/python -m pytest
```

650+ tests covering the event bus, both agent bases, the full executor family, all adapters, the dashboard (routes + WS), and the governance layer.

---

## See also

- `docs/architecture.md` — logical architecture, component roles, data flow (ASCII diagrams)
- `docs/idioms-adoption-plan.md` — design decisions and full interface reference for every executor
- `docs/event-catalog.md` — all event types by module, trust levels, module communication contract
- `docs/dashboard-architecture.md` / `docs/dashboard-stack.md` — dashboard backend + frontend spec
- `docs/community-feed-trust-pathway.md` — trust graduation wiring guide
- `docs/memorykit-design.md` — `agentic-memorykit` design brief
- `CLAUDE.md` — codebase reference for Claude Code sessions
