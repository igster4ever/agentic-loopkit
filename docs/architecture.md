# agentic-loopkit — Logical Architecture

## Overview

agentic-loopkit implements the "Cheap Kafka" pattern: a local-first event bus backed by
append-only JSONL log files, with a reactive agent model (OODA) and a family of bounded
executor patterns layered on top.

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
│        ┌─────────▼───┐   ┌─────▼──────────┐  ┌─────────────────┐          │
│        │  AgentBase  │   │ RALFExecutor   │  │ ReActExecutor   │          │
│        │  (OODA)     │   │ Reflexion-     │  │ PlanExecutor    │          │
│        └─────────────┘   │ Executor       │  └─────────────────┘          │
│                          │ OutcomeExec.   │                               │
│                          └────────────────┘                               │
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
│                │ State persistence: save_state(AgentState) / load_state()  │
│                │ decomposed by CoALA type (episodic, semantic, procedural). │
│                │ Wire MemoryStore via _memory_store for semantic facts.     │
├────────────────┼────────────────────────────────────────────────────────────┤
│ RALFExecutor   │ Bounded task loop. Triggered by a single event. Iterates  │
│                │ retrieve → act → learn → follow_up. Hard cap on loops.    │
│                │ LLM in act() only. Crash-safe via learn().                │
├────────────────┼────────────────────────────────────────────────────────────┤
│ ReActExecutor  │ Bounded tool-use loop. Think → Execute until action="done"│
│                │ or max_steps. LLM in think() only. execute() is           │
│                │ deterministic. on_step() hook for telemetry. Composes     │
│                │ inside OODA act() or PlanExecutor execute_step().         │
├────────────────┼────────────────────────────────────────────────────────────┤
│ PlanExecutor   │ Front-loaded decomposition. One LLM call in plan() to     │
│                │ produce ordered PlanSteps; execute_step() runs each in    │
│                │ sequence. Wire ReActExecutor inside execute_step() for    │
│                │ tool-using steps. Status: complete / partial / failed.    │
├────────────────┼────────────────────────────────────────────────────────────┤
│ Reflexion-     │ RALF variant. Adds critique() in the same LLM context     │
│ Executor       │ between act() and learn(). Lowers confidence to force     │
│                │ another iteration. Extends via _post_act_hook(); does not │
│                │ duplicate run(). Use for self-critique quality loops.     │
├────────────────┼────────────────────────────────────────────────────────────┤
│ Outcome-       │ RALF variant. Adds evaluate(artifact, rubric) in an       │
│ Executor       │ isolated context (no agent history). Satisfied → complete  │
│                │ (confidence=1.0); gaps fed back to next act() iteration.  │
│                │ Mirrors Anthropic Managed Agents grader contract.         │
├────────────────┼────────────────────────────────────────────────────────────┤
│ Projection-    │ AgentBase subclass. Subscribes to trigger streams; on each │
│ Agent          │ event loads the full event log for projection_streams and  │
│                │ calls materialise() (LLM phase in orient()). Emits a      │
│                │ projection.updated event with content + aggregate_         │
│                │ confidence(). should_materialise() hook for trigger        │
│                │ filtering. The wiki page is a projection; the event log   │
│                │ is the source of truth.                                   │
├────────────────┼────────────────────────────────────────────────────────────┤
│ ProblemGen-    │ AgentBase subclass (AIMA "Problem Generator" module).      │
│ eratorAgent    │ Proactive exploration: subscribes to trigger streams,      │
│                │ loads context_streams, calls explore() (LLM phase in      │
│                │ orient()) to produce AgendaItems. Emits agenda.item_added  │
│                │ for each item above min_priority. should_explore() hook    │
│                │ for throttling. Context_streams defaults to subscriptions. │
├────────────────┼────────────────────────────────────────────────────────────┤
│ Event          │ Immutable record. stream auto-derived from event_type     │
│                │ prefix. causation_id + correlation_id for traceability.   │
│                │ trust_level (TrustLevel enum) + delegation_depth (int)    │
│                │ for governance observability. caused() auto-increments    │
│                │ delegation_depth and inherits trust_level.                │
│                │ Optional payload["_meta"] (EventMeta) for framework       │
│                │ metadata — phase, loop_type, confidence, context.         │
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

LLM placement rule: **orient() only** in AgentBase. observe, decide, act are deterministic.
In executor composition: LLM also lives in `think()` (ReActExecutor), `plan()` (PlanExecutor),
`critique()` (ReflexionExecutor), `evaluate()` (OutcomeExecutor), and `reflect()` (SkillOptExecutor)
— never in `execute()`, `execute_step()`, `retrieve()`, or `score()`.
Note: `SelfHarnessExecutor.evaluate()` is deterministic (calls `regression_gate()`, no LLM).

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
    event_id:         uuid4           ─── unique identifier
    event_type:       str             ─── "<stream>.<action>"  e.g. "gps.cycle_complete"
    stream:           str             ─── auto-derived: event_type.split(".")[0]
    source:           str             ─── emitting component name
    timestamp:        datetime (UTC)
    payload:          dict
    causation_id:     str | None      ─── event_id of direct cause
    correlation_id:   str | None      ─── business workflow ID (e.g. ClickUp task ID)
    trust_level:      TrustLevel      ─── declared source trust; default MEDIUM
    delegation_depth: int             ─── hop count from root; auto-incremented by caused()
  }

  TrustLevel: HIGH | MEDIUM (default) | LOW | UNTRUSTED
  delegation_depth: root=0; event.caused() → depth+1, trust_level inherited
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

### Executors (see `docs/idioms-adoption-plan.md`)

RALF variants extend `RALFExecutor` via `_post_act_hook()` — never by copying `run()`.
`UtilityExecutor` is standalone (ABC) — generate-and-rank single-pass, not a RALF variant.

| Executor | File | Pattern | Status |
|---|---|---|---|
| `ReActExecutor` | `loops/react.py` | Thought→Action→Observation bounded loop | ✅ Built (2026-05-04) |
| `PlanExecutor` | `loops/plan.py` | LLM decomposition + per-step execution | ✅ Built (2026-05-04) |
| `ReflexionExecutor` | `loops/reflexion.py` | RALF + same-context self-critique | ✅ Built (2026-05-05) |
| `OutcomeExecutor` | `loops/outcome.py` | RALF + rubric-governed isolated evaluation | ✅ Built (2026-05-07) |
| `UtilityExecutor` | `loops/utility.py` | Standalone generate-and-rank; LLM in `generate_candidates()` + `utility_score()` | ✅ Built (2026-06-09) |
| `AgentTestHarness` | `testing.py` | Run agent against `TestTask` list; `regression_gate()` — non-regressive acceptance rule; `AsyncLLMCallable` protocol for stub injection | ✅ Built (2026-06-11) |
| `SkillOptExecutor` | `loops/skillopt.py` | RALF-based bounded skill optimiser (arXiv:2605.23904); LLM in `reflect()` only; `score()` deterministic; validation gate + rejected-edit buffer + `slow_update()` hook; `edit_budget` cap | ✅ Built (2026-06-11) |
| `FailurePatternAgent` | `agents/failure_pattern.py` | `ProjectionAgent` subclass; clusters error events by `FailureSignature(terminal_cause, causal_status, agent_mechanism)`; emits `system.failure_pattern_detected` | ✅ Built (2026-06-16) |
| `SelfHarnessExecutor` | `loops/self_harness.py` | `OutcomeExecutor` subclass; wires `FailurePatternAgent` → `SkillOptExecutor` → `AgentTestHarness.regression_gate()` → emit `harness.edit_accepted/rejected`; evaluate() is deterministic — no LLM | ✅ Built (2026-06-16) |
| `CouncilExecutor` | `agentic_govkit/loops/council.py` | `OutcomeExecutor` subclass (govkit); submits question to N specialist agents in parallel; isolated `evaluate()` for consensus quality gate; emits `governance.council_decision` on consensus or `governance.human_override` on failure | ✅ Built (2026-06-18) |

### EventMeta convention (see `CLAUDE.md`)

`EventMeta` is implemented in `events/models.py` (2026-05-04). Loopkit components
write structured framework metadata (phase, loop_type, confidence, context text) into
`payload["_meta"]`. Consumer payload keys are never modified.
Read back via `event.meta()` — returns the dict or `None` if absent.

### EventHeadline / LCLM storage compression (see `events/headlines.py`)

LCLM-inspired (arXiv:2606.09659 §7) headline layer for the event log. Each event gets a
compact one-line summary written to a parallel `headlines-<stream>.jsonl` file at publish time.
Agents can skim the full corpus in a single cheap pass, then expand only the events they need.

```python
from agentic_loopkit import load_headlines, expand_event

# Skim the last 500 headlines — low token cost
headlines = load_headlines(bus.store_dir, stream="harness", limit=500)
# → list[EventHeadline(event_id, stream, event_type, timestamp, headline, chunk_id)]

# Expand one event by chunk_id — O(1) lookup
event = expand_event(bus.store_dir, stream="harness", chunk_id=headlines[0].chunk_id)
```

`EventBus.publish()` auto-writes the headline — no extra call needed. Stored at
`<store_dir>/headlines-<stream>.jsonl`. Compatible with `compact_stream()`.

**Built 2026-06-18.** Exported from `agentic_loopkit`: `EventHeadline`, `append_headline`,
`load_headlines`, `expand_event`.

### Additional adapters

| Adapter | File | Source | Cursor | Status |
|---|---|---|---|---|
| `ClickUpAdapter` | `adapters/clickup.py` | ClickUp REST API | Unix ms timestamp | ✅ Built (2026-05-01) |
| `SlackAdapter`   | `adapters/slack.py`   | Slack `conversations.history` | `{channel_id: ts}` dict | ✅ Built (2026-05-05) |
| `LocalGitAdapter` | `adapters/git.py`   | Local git repo (`git log`) | commit SHA | ✅ Built (2026-05-05) |
| `CommunityFeedAdapter` | `adapters/community.py` | JSONL community feed | byte offset | ✅ Built (2026-06-20) |

### Dashboard (see `docs/dashboard-architecture.md`)

`agentic_loopkit/dashboard/` — optional FastAPI management API.
Install via `pip install agentic-loopkit[dashboard]`.

**Backend built (2026-05-05):** `create_app(bus)` factory,
GET `/api/streams`, `/api/events`, `/api/events/{id}`, `/api/chains/{correlation_id}`,
`/api/agents`, `/api/adapters`, WS `/ws/tail` live-tail endpoint.

**Frontend built (2026-05-11):** `dashboard/ui/` — React 19 + Vite + Bun + TypeScript.
EventChainGraph (dagre DAG), EventDetailPanel (payload + context tab),
EventTimeline (Recharts scatter), ChainPage (`/chains/:id`). Spec in `docs/dashboard-architecture.md`.

### Phase B governance — enforcement layer (✅ Built 2026-05-15)

Phase A governance (`AuditAgent`) is **observability**: watch all streams, flag threshold
violations, emit structured events.  Phase B is **enforcement**: respond to governance events
with policy actions.

The two phases are intentionally separate components with a clean interface between them:

```
Phase A — AuditAgent                    Phase B — KillSwitchAgent
─────────────────────────               ─────────────────────────────────────
Subscribes to: all streams (*)          Subscribes to: governance.* only
Emits:  governance.*                    Emits:  governance.halt, governance.quarantine,
                                                governance.human_override
Role:   observe and flag                Role:   enforce policy on flagged events
```

**Rule**: Phase A must be running before Phase B.  You cannot enforce what you cannot observe.

#### KillSwitchAgent (✅ Built 2026-05-15)

**Location:** `agentic_govkit/agents/killswitch.py`

```python
class KillSwitchAgent(AgentBase):
    """
    Policy enforcement agent.  Subscribes to governance.* and executes
    configurable enforcement actions when governance thresholds are breached.

    All enforcement decisions are themselves events — the enforcer is
    observable and auditable, consistent with Phase A.

    trust_level: HIGH — enforcement events carry the highest trust weight
    so they propagate correctly through aggregate_confidence().
    """

    def __init__(
        self,
        name: str,
        bus: EventBus,
        policy: dict[GovernanceEventType, EnforcementAction],
    ) -> None: ...
```

**Built-in enforcement actions:**

| Action | Effect | Event emitted |
|---|---|---|
| `halt_correlation` | Prevents further processing in a correlation chain | `governance.halt` |
| `quarantine_source` | Flags a source as quarantined; downstream agents check | `governance.quarantine` |
| `emit_human_override` | Escalates to human review queue | `governance.human_override` |

**Example policy:**

```python
from agentic_govkit import KillSwitchAgent, GovernanceEventType
from agentic_govkit.agents.killswitch import halt_correlation, emit_human_override

kill = KillSwitchAgent("killswitch", bus, policy={
    GovernanceEventType.DEPTH_EXCEEDED:    halt_correlation,
    GovernanceEventType.TRUST_ESCALATION:  emit_human_override,
    GovernanceEventType.CONFIDENCE_BREACH: emit_human_override,
})
kill.subscribe("governance")
bus.register(kill)
```

**Design principles:**
- `KillSwitchAgent` subscribes to `governance.*`, not `*` — it reacts to decisions, not raw events
- Never calls `AuditAgent` directly — receives its output as bus events (module boundary preserved)
- Disabled by default — must be explicitly instantiated; purely opt-in
- All enforcement actions emit events at `TrustLevel.HIGH`, making them traceable via the
  same governance archaeology pattern as all other events

#### ConflictResolutionExecutor (✅ Built 2026-05-15)

Mediates between two competing agent orientations about the same entity.  Triggered by
`governance.dispute_opened`; resolves to `governance.dispute_resolved` or escalates to
`governance.human_override` on max iterations.  Located at `agentic_govkit/loops/conflict.py`.

Full spec in `docs/idioms-adoption-plan.md § ConflictResolutionExecutor`.

#### GovernanceEventType entries for Phase B (✅ Built 2026-05-15)

```python
HALT        = "governance.halt"        # correlation chain halted by KillSwitchAgent
QUARANTINE  = "governance.quarantine"  # source quarantined by KillSwitchAgent
```

---

### Governance layer (see `docs/event-catalog.md`)

`agentic_govkit/` — separate top-level package in same repo. Zero extra runtime deps.
Install via `pip install agentic-loopkit[governance]`.

- **`AuditAgent`** — subscribes to all streams (`WILDCARD_STREAM`); emits `governance.*` events when
  thresholds are breached. Optional `confidence_threshold` parameter auto-flags events where
  `_meta.confidence < threshold`. All decisions are themselves events on the bus — the auditor
  is fully observable.
- **`GovernanceLearningAgent`** — AIMA "Learning Element" for govkit. Accumulates governance events
  into a rolling window; when the buffer reaches `analysis_window` events (or on `bus.started`),
  calls `analyse()` (LLM phase in `orient()`) to identify policy drift. Emits
  `governance.policy_recommendation` per recommendation at `TrustLevel.HIGH`. Self-excludes own
  events and `policy_recommendation`/`policy_applied` to prevent feedback loops.
- **`CommunityTrustLearner`** — concrete `GovernanceLearningAgent` subclass. Analyses community
  feed events from `AuditAgent` to recommend one-level trust graduation (UNTRUSTED → LOW) when
  a source accumulates sufficient clean observations above `min_observations`. Emits
  `governance.policy_recommendation` at `TrustLevel.HIGH`; graduation is one level at a time only.
- **`GovernanceEventType`** — full vocabulary:
  - `governance.depth_exceeded` — `delegation_depth > max_delegation_depth`
  - `governance.trust_escalation` — `trust_level == UNTRUSTED`
  - `governance.audit_flagged` — generic policy flag (reserved for future rules)
  - `governance.confidence_breach` — `_meta.confidence < confidence_threshold`
  - `governance.dispute_opened` — competing agent interpretations of same entity
  - `governance.dispute_resolved` — dispute closed (consensus or human override)
  - `governance.human_override` — HIGH-trust human decision supersedes agent synthesis
  - `governance.halt` — correlation chain halted by `KillSwitchAgent`
  - `governance.quarantine` — source quarantined by `KillSwitchAgent`
  - `governance.policy_recommendation` — policy adjustment recommended by `GovernanceLearningAgent`
  - `governance.policy_applied` — policy recommendation accepted and applied by operator
  - `governance.council_decision` — N-specialist weighted consensus reached by `CouncilExecutor`
- **Module boundary contract**: `agentic_govkit → agentic_loopkit` (public API only); never reversed.
  Enforced by `tests/govkit/test_module_boundaries.py`.

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
