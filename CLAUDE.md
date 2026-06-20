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
│   ├── models.py            # Event + EventMeta dataclasses + SystemEventType(StrEnum)
│   ├── router.py            # Async callback fanout (Subscriber = Callable[[Event], Awaitable[None]])
│   ├── store.py             # JSONL per-stream persistence (~/.cache/<app>/events-<stream>.jsonl)
│   ├── headlines.py         # EventHeadline + append_headline + load_headlines + expand_event (LCLM-inspired)
│   └── confidence.py        # aggregate_confidence() — TrustLevel-weighted, depth-decayed mean
│
├── agents/
│   ├── base.py              # AgentBase — OODA loop (observe → orient → decide → act); AgentState + load_state/save_state
│   └── projection.py        # ProjectionAgent + ProjectionEventType — live view materialisation from event log
│
├── loops/
│   ├── ralf.py              # RALFExecutor — bounded task loop (retrieve → act → learn → follow-up)
│   ├── react.py             # ReActExecutor — bounded tool-use loop (think → execute, action="done")
│   ├── plan.py              # PlanExecutor — front-loaded decomposition (plan → execute_step × N)
│   ├── reflexion.py         # ReflexionExecutor — RALFExecutor + critique() between act() and learn()
│   └── outcome.py           # OutcomeExecutor — RALFExecutor + rubric-governed isolated evaluation
│
├── utils/
│   └── time.py              # Shared UTC helpers: utc_now(), iso_format(), now_ms(), now_unix(), ms_to_iso()
│
└── adapters/
    ├── base.py              # PollingAdapter — tick-driven external source bridge
    ├── clickup.py           # ClickUpAdapter + ClickUpEventType — polls ClickUp REST API
    ├── slack.py             # SlackAdapter + SlackEventType — polls conversations.history
    ├── git.py               # LocalGitAdapter + GitEventType — polls local git repo via subprocess
    └── community.py         # CommunityFeedAdapter + CommunityEventType — JSONL feed; byte-offset cursor; trust_level param

dashboard/
└── ui/                           # Bun + Vite + React 19 + TypeScript (built 2026-05-11)
    ├── package.json              # bun runtime; vite dev server; dagre + @xyflow/react + recharts
    ├── vite.config.ts            # Tailwind v4 plugin; /api + /ws proxy to :8765
    ├── src/
    │   ├── main.tsx
    │   ├── App.tsx               # state-based routing shell; /chains/:id route wired
    │   ├── types/events.ts       # TypeScript mirror of Python Event dataclass (incl. trust_level, delegation_depth)
    │   ├── stores/               # Zustand: graph.ts, filters.ts, livetail.ts
    │   ├── hooks/                # useApi.ts, useEventStream.ts (WS + backoff)
    │   ├── components/
    │   │   ├── graph/            # EventChainGraph.tsx — @xyflow/react DAG, dagre layout, node colour coding
    │   │   ├── layout/           # Sidebar.tsx, EventDetailPanel.tsx (payload + context tab)
    │   │   ├── table/            # EventTable.tsx
    │   │   ├── timeline/         # EventTimeline.tsx — Recharts scatter (time × stream)
    │   │   └── livetail/         # LiveTail.tsx
    │   └── pages/                # StreamsPage (+ timeline), EventsPage, ChainPage, AgentsPage, AdaptersPage
    └── dist/                     # gitignored; bun run build → served by FastAPI

agentic_loopkit/dashboard/           # Optional FastAPI management API (pip install agentic-loopkit[dashboard])
├── __init__.py              # exports create_app(bus) → FastAPI
├── app.py                   # app factory + static file mount
├── constants.py             # shared route constants (DEFAULT_HOURS, DEFAULT_LIMIT, MAX_LIMIT)
├── dependencies.py          # get_bus() FastAPI dependency
├── ws.py                    # WS /ws/tail live-tail endpoint
└── routes/
    ├── events.py            # GET /api/events, GET /api/events/{event_id}
    ├── chains.py            # GET /api/chains/{correlation_id}
    ├── streams.py           # GET /api/streams
    ├── agents.py            # GET /api/agents
    └── adapters.py          # GET /api/adapters

agentic_govkit/                  # Governance layer (pip install agentic-loopkit[governance])
├── __init__.py              # exports AuditAgent, KillSwitchAgent, GovernanceEventType, ConflictResolutionExecutor, CouncilExecutor, CouncilOpinion, CommunityTrustLearner
├── agents/
│   ├── audit.py             # AuditAgent — OODA wildcard observer; emits governance.* events
│   ├── killswitch.py        # KillSwitchAgent — policy enforcement; emits halt/quarantine/human_override
│   ├── learning.py          # GovernanceLearningAgent + PolicyRecommendation — rolling window → analyse() → policy_recommendation
│   └── community_trust.py   # CommunityTrustLearner — concrete GovernanceLearningAgent; UNTRUSTED→LOW trust graduation
├── loops/
│   ├── conflict.py          # ConflictResolutionExecutor — OutcomeExecutor subclass; dispute mediation
│   └── council.py           # CouncilExecutor + CouncilOpinion — fan-out to N specialists → weighted consensus → governance.council_decision
└── events/
    └── models.py            # GovernanceEventType StrEnum (governance.* stream) incl. COUNCIL_DECISION

docs/
├── architecture.md          # Logical architecture, component roles, data flow (ASCII diagrams)
├── idioms-adoption-plan.md  # ReActExecutor / PlanExecutor / ReflexionExecutor design decisions
├── dashboard-architecture.md # FastAPI management API + Bun/React dashboard spec
├── dashboard-stack.md       # Logical layer diagram: browser / FastAPI / loopkit runtime / JSONL
├── event-catalog.md         # All event types by module; module communication contract; trust levels
├── mem0-appraisal.md        # Mem0 architecture appraisal; build-bespoke recommendation for v4 memory
├── memorykit-design.md      # agentic_memorykit v0.1 design brief (interface, storage, reconciliation)
└── community-feed-trust-pathway.md  # Trust graduation guide: CommunityFeedAdapter + AuditAgent + CommunityTrustLearner wiring

tests/
├── events/                  # test_models (incl. EventMeta, TrustLevel, delegation_depth), test_router, test_store
├── agents/                  # test_base (OODA pipeline)
├── loops/                   # test_ralf, test_react, test_plan, test_reflexion, test_outcome
├── adapters/                # test_base (PollingAdapter), test_clickup, test_slack, test_git, test_community
├── dashboard/               # test_routes_streams, test_routes_events, test_routes_chains, test_routes_agents_adapters
└── govkit/                  # test_module_boundaries, test_audit_agent, test_killswitch, test_conflict_resolution, test_community_trust
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

Traceability and governance fields on every Event:
- `causation_id` — event_id of the direct cause (chain tracing)
- `correlation_id` — business workflow ID shared by all events in a flow
- `trust_level: TrustLevel` — declared trust level of the source; defaults to `TrustLevel.MEDIUM`
- `delegation_depth: int` — hop count from the root event; defaults to `0`

Use `event.caused("child.type", "source", payload)` to create a child that inherits `correlation_id`,
inherits `trust_level`, and auto-increments `delegation_depth` by 1.

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

Fields: `phase`, `loop_type` (`"ooda"|"ralf"|"react"|"plan"|"reflexion"|"outcome"`), `iteration`, `confidence`, `context`, `tags`.
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

**State persistence (v4):** CoALA-decomposed state hooks on every `AgentBase`.
- `await agent.save_state(AgentState(...))` — persists semantic facts to `_memory_store` if set
- `await agent.load_state()` → `AgentState` — loads semantic facts from `_memory_store` if set
- `AgentState(episodic=[...], semantic={...}, procedural={...})` — never an opaque blob
- Wire a store: `agent._memory_store = MemoryStore(store_dir)` (agentic-memorykit; no hard dep)
- `procedural` bucket is reserved for v4+ behavioural adjustments; always `{}` in base impl

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

### ReflexionExecutor (RALF + self-critique)
Extends `RALFExecutor` — adds an explicit `critique()` phase between `act()` and `learn()` in each iteration. The critique can revise confidence to force another iteration or drive toward completion.
- `retrieve(event)` → context (inherited; deterministic, no LLM)
- `act(context, prior_result)` → `RALFResult` (primary drafting phase; LLM appropriate)
- `critique(event, result)` → `(RALFResult, str)` (evaluation phase; LLM appropriate; **new**)
- `learn(event, result)` → persist post-critique result (inherited)
- `follow_up(event, result)` → return downstream Event or None (inherited)

Confidence enforcement applies to the **post-critique** result, not the raw `act()` output.
`result.status`: same as `RALFExecutor` — `"complete"` | `"in_progress"` | `"rejected"` | `"error"`.

### OutcomeExecutor (rubric-governed iteration)
Extends `RALFExecutor` — adds a rubric and an isolated `evaluate()` phase via `_post_act_hook()`.
Each iteration: `act()` produces an artifact, `evaluate()` checks it against the rubric in a
**fresh context** (no agent reasoning history). Gaps feed the next `act()` call; satisfied exits.

- `rubric` → abstract property: markdown criteria (explicit + gradeable)
- `retrieve(event)` → context (inherited; deterministic, no LLM)
- `act(context, prior_result)` → `RALFResult` (primary drafting phase; LLM appropriate)
- `evaluate(artifact, rubric)` → `(satisfied: bool, gaps: list[str])` — **isolated context only**
- `learn(event, result)` → persist post-evaluate result (inherited)
- `follow_up(event, result)` → return downstream Event or None (inherited)

**Isolation contract**: `evaluate()` must call the LLM with *only* `(artifact, rubric)` — no prior
chain. This prevents anchoring and mirrors the Anthropic Managed Agents grader.

Key distinction from `ReflexionExecutor`: Reflexion's `critique()` runs in the same context as
`act()` (self-critique); `OutcomeExecutor.evaluate()` runs in an isolated context (external grader).

Default `max_iterations = 3` (matches Anthropic Managed Agents default).
Exit states: `satisfied → "complete"` (confidence=1.0) | max_iterations → `"error"` (inherited).

### PollingAdapter
External system bridge. Tick-driven (APScheduler, asyncio loop, etc.).
- `poll(cursor)` → `(list[Event], new_cursor)`
- Cursor persisted as JSON at `store_dir/cursor-{name}.json`
- Errors emit `system.adapter_error` events rather than raising

### ClickUpAdapter
Polls `/list/{id}/task` or `/team/{id}/task` for updates since cursor.
Cursor = Unix ms timestamp. Emits `clickup.task_updated` / `clickup.task_created`.
Requires `aiohttp` (optional dep — lazy-imported at call time).

### SlackAdapter
Polls `conversations.history` per channel. Cursor = `{channel_id: ts}` dict.
Emits `slack.message_received`. Handles pagination and 429 rate limits.
Requires `aiohttp` (lazy-imported at call time).

### LocalGitAdapter
Polls a local git repository via subprocess `git log`. Zero extra deps — pure stdlib.
Cursor = last seen commit SHA; first run fetches commits since `initial_since_hours` (default 24h).
Emits `git.commit_added`. Use for any locally-cloned repo you pull regularly.

### ProjectionAgent (live view materialisation)
Reactive `AgentBase` subclass. Subscribes to trigger streams; on each event loads the full
event log for `projection_streams` and calls `materialise(events)` (the LLM phase, in `orient()`).
- `materialise(events)` → `str` (abstract; primary LLM call)
- `should_materialise(event)` → `bool` (hook; default True; override to filter triggers)
- `projection_streams` property — defaults to subscription streams; set explicitly to decouple
  load scope from trigger scope
- Emits `projection.updated` (stream: `projection`) with content, confidence, event_count, streams
- `aggregate_confidence()` wired in by default — page-level confidence from `_meta.confidence`
  across source events, weighted by TrustLevel and decayed by delegation_depth

### aggregate_confidence()
Utility in `agentic_loopkit/events/confidence.py`. Weighted mean of `_meta.confidence` across
a list of Events. Weight = TrustLevel ordinal (HIGH=3, MEDIUM=2, LOW=1, UNTRUSTED=0) ×
depth decay (1 / (1 + delegation_depth)). Returns `None` when no events carry confidence data
or all sources are UNTRUSTED.

## Public API

```python
from agentic_loopkit import (
    # Bus
    EventBus,
    # Events
    Event, EventMeta, SystemEventType, TrustLevel, WILDCARD_STREAM,
    EventRouter, Subscriber,
    append_event, load_events,
    # Headlines (LCLM-inspired corpus skimming)
    EventHeadline, append_headline, load_headlines, expand_event,
    # Agents
    AgentBase, AgentState,
    ProjectionAgent, ProjectionEventType,
    # Confidence
    aggregate_confidence,
    # Executors — RALF
    RALFExecutor, RALFResult,
    CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH,
    # Executors — ReAct
    ReActExecutor, ReActResult, ReActStep,
    # Executors — Plan
    PlanExecutor, PlanResult, PlanStep,
    # Executors — Reflexion
    ReflexionExecutor,
    # Executors — Outcome
    OutcomeExecutor,
    # Adapters
    PollingAdapter,
    ClickUpAdapter, ClickUpEventType,
    SlackAdapter, SlackEventType,
    LocalGitAdapter, GitEventType,
)
```

## Documentation hygiene

After any session that adds executors, changes the public API surface, or modifies
implementation details (adapters, bus, dashboard routes, governance): review `/docs` before committing.

Check:
- `docs/architecture.md` — component roles table, LLM placement rule, executors table, Event model fields
- `docs/idioms-adoption-plan.md` — decision table, executor section, implementation order, Public API additions
- `docs/dashboard-architecture.md` — code sketches, enum values (`loop_type`, `executor_type`), TypeScript Event type
- `docs/dashboard-stack.md` — layer diagram; update if routes, bindings, or WS lifecycle change
- `docs/event-catalog.md` — update when adding event types to either loopkit or govkit modules

A five-minute review at commit time prevents a session of stale-docs archaeology later.

---

## Key design rules

- **LLM is not the orchestrator** — it's called inside `orient()` (OODA), `act()` (RALF), `think()` (ReAct), `plan()` (PlanExecutor), `act()`/`critique()` (ReflexionExecutor), and `act()`/`evaluate()` (OutcomeExecutor) only
- **Loops must be bounded** — `max_iterations` hard cap, error result if exhausted
- **Persist before fanout** — EventBus writes JSONL before routing
- **Adapters are not agents** — no reasoning, no LLM calls; deduplicate + emit only
- **Open EventType** — loopkit never imports consumer event types; consumers own their domain enums
- **Zero runtime deps** — stdlib only; `aiohttp` is consumer-supplied for ClickUpAdapter
- **Observability before enforcement** — governance is a participant layer on the bus, not a wrapper around it; all audit decisions are themselves events

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
from .react import ReActExecutor   # or RALFExecutor / PlanExecutor / ReflexionExecutor / OutcomeExecutor as base

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

**Extending RALFExecutor without duplicating `run()`:** override `_post_act_hook()` to inject a
phase between `act()` and confidence enforcement. The hook receives `(event, result, iteration)`
and must return a `RALFResult`. This is how `ReflexionExecutor` wires `critique()` — never copy
`run()`.

```python
class MyRalfVariant(RALFExecutor):
    async def _post_act_hook(self, event, result, iteration):
        revised, note = await self.my_eval_phase(event, result)
        return revised   # confidence enforcement + learn() run on this result
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

Note: governance events land on `events-governance.jsonl` alongside all other streams.

## Tests

```bash
.venv/bin/python -m pytest   # asyncio_mode = auto, testpaths = tests/
# Note: system Python is blocked by PEP 668 on macOS — always use .venv/bin/python
```

605 tests, all passing (as of 2026-06-20). Coverage: EventBus, EventRouter, EventStore,
AgentBase (all OODA short-circuit paths, AgentState defaults + world_model field, save_state/load_state with and without memory store, semantic/world_model tag separation, roundtrip), RALFExecutor (confidence rejection, learn, follow-up,
_post_act_hook extension), ReActExecutor (happy path, max_steps, error handling, on_step hook,
follow-up), PlanExecutor (all-complete, partial, failed, plan() raises, step exception recovery,
prior_outputs), ReflexionExecutor (critique hook, forced iterations, post-critique confidence
rejection, learn receives revised result, max_steps, follow-up),
OutcomeExecutor (evaluate called with artifact+rubric only, satisfied first-pass, needs-revision
loop, gaps fed to next act() via prior_result, learn sequence, max_iterations, follow_up,
isolation contract — evaluate signature verified, default max_iterations=3),
EventMeta (to_dict field omission, event.meta() helper),
TrustLevel + delegation_depth (Event defaults, round-trip serialisation, caused() propagation),
PollingAdapter (cursor, error event, adapter_alive/stalled liveness events, tick registration),
ClickUpAdapter (payload mapping, dedup, cursor),
SlackAdapter (event mapping, per-channel cursor, pagination, rate-limit handling),
LocalGitAdapter (git log parsing, SHA cursor, first-run since window, real-repo integration),
EventBus backpressure (bus_pressure signal, threshold config, counter reset, system-event exclusion),
EventBus shutdown (is_stopping, drain_timeout, active tick drain, force-stop after timeout),
dashboard routes (streams, events filter/pagination, events/{id} + related chain, chains DAG +
summary, agents, adapters), dashboard chain builder (edge derivation, summary.status logic),
dashboard WS /ws/tail (connect/disconnect, stream filter, router lifecycle, queue-full drop),
AuditAgent (OODA pipeline, depth exceeded, trust escalation, combined flags, governance event
payload, EventMeta in governance events, self-exclusion of governance stream),
KillSwitchAgent (halt/quarantine/human_override enforcement, TrustLevel.HIGH on all outputs,
policy miss ignored, self-exclusion prevents feedback loop, causation chain preserved),
ConflictResolutionExecutor (dispute_resolved on complete, human_override on exhaustion/error,
evaluate isolation contract, correlation_id threading, causation chain),
module boundaries (govkit→loopkit one-way, loopkit→govkit zero, governance stream namespace),
PerformanceMeasure (mean_confidence, event_count, governance_flags, trend improving/stable/degrading/insufficient_data, follow_up_rate=None baseline, agent filtering, empty list handling),
ProblemGeneratorAgent (observe gate, min_priority filter, decide None on empty, act emits agenda.item_added, _meta phase/loop_type, causation/correlation, should_explore override, context_streams defaults),
UtilityExecutor (winner selection, sorted descending, below_threshold, no_candidates, error, follow_up on complete only, isolation contract, criteria_scores, retrieve default, min_utility boundary),
GovernanceLearningAgent (window accumulation, window trigger, self-exclusion, policy_recommendation/applied exclusion, bus_started trigger, orient calls analyse, confidence filtering, act payload/TrustLevel.HIGH/_meta, window clear, evidence_event_ids, module boundary import check),
AgentTestHarness (TestSuiteResult properties, regression_gate accept/reject paths, run_suite pass/fail/silent/held_out, isolation + no state bleed, majority vote, emitted_events collection, evaluate_result override, AsyncLLMCallable protocol + stub injection),
SkillOptExecutor (_apply_edits all ops, _is_protected block detection, accepted/rejected edit paths, buffer negative feedback, edit_budget clip, protected proposals stripped, max_iterations cap, learn() hook, slow_update/meta noop defaults + override, retrieve/score/best_skill),
FailurePatternAgent (observe gate, stream filter system+governance, FailureSignature clustering by terminal_cause/causal_status/agent_mechanism, materialise() → system.failure_pattern_detected, should_materialise override),
SelfHarnessExecutor (retrieve loads failure_pattern_detected events, act() instantiates SkillOptExecutor via factory, evaluate() calls regression_gate() deterministically, follow_up emits harness.edit_accepted/rejected, max_iterations=3),
CouncilExecutor (council_decision on complete, human_override on exhaustion/confidence rejection, evaluate isolation contract, opinions+question via context, gather_opinions wired via retrieve, causation chain, CouncilOpinion defaults + custom weight, default max_iterations=3, module boundary import check),
EventHeadline (to_dict/from_dict round-trip, from_event: summary field + fallback + confidence suffix + truncation to 120, append_headline chunk_id sequential per stream, load_headlines newest-first + limit, expand_event correct event + None for unknown, compact_stream compatibility, stream isolation, EventBus publish writes headline + load_headlines + expand_event delegates),
CommunityFeedAdapter (constructor defaults, poll missing file, poll empty, reads entries, advances cursor, no new entries, TrustLevel.UNTRUSTED default, CommunityEventType, correlation_id from id/correlation_id/absent fields, custom name, malformed JSON skip, truncation reset, public API export),
CommunityTrustLearner (analyse recommends after min_observations, no rec below threshold, halt veto, multiple sources independent, confidence scaling, confidence cap 0.9, tags include source, OODA pipeline window fill, OODA below threshold, full chain integration CommunityFeedAdapter→AuditAgent→learner, module boundary export).

## Dashboard

Optional FastAPI management API + Bun/Vite/React event inspector.
Install with: `pip install agentic-loopkit[dashboard]`

**Backend API skeleton — built (2026-05-05):**
- `create_app(bus)` — factory; bind to any running EventBus
- `GET /api/streams` — stream names, event counts, last timestamp
- `GET /api/events` — filtered list (stream, event_type, correlation_id, source, since, limit)
- `GET /api/events/{event_id}` — single event + related chain events
- `GET /api/chains/{correlation_id}` — full DAG: events + causation edges + summary
- `GET /api/agents` — registered agents + subscription streams
- `GET /api/adapters` — registered adapters + cursor state
- `WS /ws/tail` — live event stream with stream/event_type filtering

**Frontend — complete (2026-05-11):** `dashboard/ui/` — React 19 + Vite + Bun + TypeScript.
All REST endpoints wired. WS live-tail working. Build clean.

Built components:
- `EventChainGraph` — dagre-laid-out DAG via `@xyflow/react`; nodes colour-coded by source type; selected node highlights; `EventMeta` phase + loop_type in node subtitle
- `EventDetailPanel` — right panel (320px); Payload tab (JSON, `_meta` excluded) + Context tab (`_meta.context`); `_meta` strip with phase/loop/confidence pills; close button
- `EventTimeline` — Recharts scatter (time × stream); fetches last 300 events; shown on StreamsPage
- `ChainPage` — `/chains/:id` route; breadcrumb + status badge; graph + detail panel flex layout; clears store on unmount

Full spec: `docs/dashboard-architecture.md`. Stack diagram: `docs/dashboard-stack.md`.

## Governance layer (agentic_govkit)

Separate top-level package in the same repo. Depends on `agentic_loopkit` (one-way).
Install alongside loopkit: `pip install agentic-loopkit[governance]` (no extra runtime deps).

### Architecture

```
agentic_govkit        →  agentic_loopkit  (public API only)
agentic_loopkit       →  agentic_govkit   (NEVER — enforced by test)
inter-module comms    →  published Events only (never direct calls)
```

### AuditAgent

Wildcard observer. Subscribes to all streams at init; never audits `governance.*` (prevents loops).
Emits structured governance events when thresholds are breached — the auditor is itself observable.

```python
from agentic_govkit import AuditAgent, GovernanceEventType
from agentic_loopkit import EventBus, WILDCARD_STREAM

audit = AuditAgent("audit", bus, max_delegation_depth=5, confidence_threshold=0.4)
# subscribes to WILDCARD_STREAM automatically
bus.register(audit)
```

Flags raised as events on the `governance` stream:
- `governance.depth_exceeded` — `delegation_depth > max_delegation_depth`
- `governance.trust_escalation` — `trust_level == TrustLevel.UNTRUSTED`
- `governance.audit_flagged` — generic policy flag (reserved for future rules)
- `governance.confidence_breach` — `_meta.confidence < confidence_threshold` (opt-in; disabled if threshold=None)
- `governance.dispute_opened` — emitted by ConflictResolutionExecutor when mediation begins
- `governance.dispute_resolved` — emitted by ConflictResolutionExecutor on consensus (status=complete)
- `governance.human_override` — emitted by ConflictResolutionExecutor (exhaustion/error) or KillSwitchAgent or CouncilExecutor
- `governance.halt` — emitted by KillSwitchAgent; correlation chain halted by policy
- `governance.quarantine` — emitted by KillSwitchAgent; source quarantined by policy
- `governance.council_decision` — emitted by CouncilExecutor; N-specialist weighted consensus reached

### KillSwitchAgent

Policy enforcement agent. Subscribes to `governance.*`; maps event types to `EnforcementAction` callables.
Built-in actions: `halt_correlation`, `quarantine_source`, `emit_human_override` (all set `TrustLevel.HIGH`).
Self-excludes events it emits (`event.source == self.name`) to prevent feedback loops.

```python
from agentic_govkit import KillSwitchAgent, GovernanceEventType
from agentic_govkit.agents.killswitch import halt_correlation, quarantine_source

ks = KillSwitchAgent("killswitch", bus, policy={
    GovernanceEventType.DEPTH_EXCEEDED:   halt_correlation,
    GovernanceEventType.TRUST_ESCALATION: quarantine_source,
})
ks.subscribe("governance")
bus.register(ks)
```

### ConflictResolutionExecutor

`OutcomeExecutor` subclass in `agentic_govkit/loops/conflict.py`. Mediates between competing agent
positions. Emits `governance.dispute_resolved` on consensus (`status="complete"`) or
`governance.human_override` on exhaustion/error. Inherits `max_iterations=3` default.
The `confidence=0.5` path in `OutcomeExecutor._post_act_hook` means `status="rejected"` is
structurally unreachable — exhaustion exits as `status="error"`, triggering `human_override`.

### CouncilExecutor

`OutcomeExecutor` subclass in `agentic_govkit/loops/council.py`. Fan-out governance executor:
submits a question to N specialist agents via `gather_opinions()`, synthesises weighted consensus
in `act()`, gate-checks with isolated `evaluate()`. Emits `governance.council_decision` on
consensus or `governance.human_override` on exhaustion/error.

Distinct from `ConflictResolutionExecutor` (two-party mediation) and `UtilityExecutor`
(single-agent generate-and-rank). `CouncilOpinion(source, opinion, weight=1.0, confidence=1.0)`
carries each specialist's contribution.

```python
from agentic_govkit import CouncilExecutor, CouncilOpinion

class TechCouncil(CouncilExecutor):
    @property
    def rubric(self):
        return "## Rubric\n- Decision is actionable\n- References at least two opinions\n"

    async def gather_opinions(self, event):
        return [
            CouncilOpinion("security", await ask_security(event), weight=1.5),
            CouncilOpinion("perf",     await ask_perf(event)),
        ]

    async def act(self, context, prior):
        ...  # LLM synthesises context["opinions"]

    async def evaluate(self, artifact, rubric):
        ...  # isolated LLM check — no prior history
```

### TrustLevel

`TrustLevel` is a `StrEnum` on `agentic_loopkit.events.models`. Sources self-declare.

| Level | Meaning | AuditAgent |
|---|---|---|
| `HIGH` | Fully trusted internal component | No flag |
| `MEDIUM` | Default — standard agent | No flag |
| `LOW` | External / partially trusted | No flag (informational) |
| `UNTRUSTED` | Known-hostile or unverified | → `governance.trust_escalation` |

### delegation_depth

`Event.delegation_depth` increments by 1 on every `event.caused(...)` call.
Root events start at 0. Use to detect runaway delegation chains.
`AuditAgent` flags events where `delegation_depth > max_delegation_depth` (default: 5).

## Module boundary conventions (Modulith-inspired)

Adopted from Spring Modulith's modular monolith principles. Apply whenever a new module
is added to this repo.

**Rules:**
1. Each top-level package (`agentic_loopkit`, `agentic_govkit`) is a module with a declared public surface in its `__init__.py`
2. Cross-module imports are allowed only from the target module's `__init__.py` — never from sub-modules (`from agentic_loopkit.events.models import X` is forbidden; use `from agentic_loopkit import X`)
3. Inter-module communication is via published `Event` objects on the shared bus — never direct method calls across module boundaries
4. Dependencies are one-way: govkit → loopkit; loopkit has zero govkit imports
5. Module tests are isolated — govkit fixtures initialise only govkit components + a minimal EventBus

**Enforcement:** `tests/govkit/test_module_boundaries.py` catches import leaks at CI time via `grep`.
Update this test when adding new modules.

**Event catalog:** `docs/event-catalog.md` — update when adding event types to any module.
