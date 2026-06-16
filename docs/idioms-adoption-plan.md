# Idioms Adoption Plan

_Decided: 2026-05-02_

## Summary

Five harness idioms were evaluated against the loopkit. Four are adopted; one deferred.
The OODA+ReAct composition pattern is documented as the canonical wiring example.

---

## Decision Table

| Idiom | Verdict | File | Priority | Status |
|---|---|---|---|---|
| ReAct (Thought→Action→Observation) | **Build** | `loops/react.py` | 1 | ✅ Built 2026-05-04 |
| Plan-and-Execute | **Build** | `loops/plan.py` | 2 | ✅ Built 2026-05-04 |
| Reflexion | **Build** | `loops/reflexion.py` | 3 | ✅ Built 2026-05-05 |
| Outcome (rubric-governed evaluation) | **Build** | `loops/outcome.py` | 4 | ✅ Built 2026-05-07 |
| Tree-of-Thoughts | **Defer** | — | Out of scope v1 | Deferred |
| OODA+ReAct composition | **Document** | CLAUDE.md + architecture.md | — | ✅ Done 2026-05-02 |
| ProjectionAgent (live view materialisation) | **Build** | `agents/projection.py` | v3-1 | ✅ Built 2026-05-12 |
| Replay-as-archaeology | **Document** | `docs/idioms-adoption-plan.md` | v3-2 | ✅ Done 2026-05-12 |
| ConflictResolutionExecutor | **Build** | `agentic_govkit/loops/conflict.py` | v3-3 | ✅ Built 2026-05-15 |
| Bus backpressure + liveness + graceful shutdown | **Build** | `bus.py`, `adapters/base.py` | v4-3 | ✅ Built 2026-06-08 |
| ProblemGeneratorAgent + `agenda.*` stream | **Build** | `agents/problem_generator.py` | v4-5 | ✅ Built 2026-06-09 |
| UtilityExecutor (generate-and-rank) | **Build** | `loops/utility.py` | v4-6 | ✅ Built 2026-06-09 |
| `world_model` bucket in `AgentState` + `PerformanceMeasure` | **Build** | `agents/base.py`, `agents/performance.py` | v4-7 | ✅ Built 2026-06-08 |
| GovernanceLearningAgent | **Build** | `agentic_govkit/agents/learning.py` | v4-8 | ✅ Built 2026-06-09 |
| `AgentTestHarness` + `AsyncLLMCallable` + `TestTask`/`TestSuiteResult` | **Build** | `agentic_loopkit/testing.py` | v4-4 | ✅ Built 2026-06-11 |
| `FailurePatternAgent` + `FailureSignature` | **Build** | `agents/failure_pattern.py` | v5-1 | ✅ Built 2026-06-16 |
| `HarnessEventType` + `harness.*` stream | **Build** | `events/models.py` extension | v5-2 | ✅ Built 2026-06-16 |
| `SkillOptExecutor` + `SkillEdit` + `RejectedEdit` | **Build** | `loops/skillopt.py` | v5-3 | ✅ Built 2026-06-11 |
| `SelfHarnessExecutor` | **Build** | `loops/self_harness.py` | v5-4 | ✅ Built 2026-06-16 |

---

## 1. ReActExecutor

**Location:** `agentic_loopkit/loops/react.py`

The highest-value addition. ReAct is a step-level tool-use pattern that composes naturally
inside OODA's `act()` phase. The loopkit currently has no tool-calling abstraction — this fills
that gap.

### Dataclasses

```python
@dataclass
class ReActStep:
    step_number:  int
    thought:      str
    action:       str   # tool name; "done" = terminal signal
    action_input: Any
    observation:  str   # tool output; empty string on "done" step

@dataclass
class ReActResult:
    status:  str             # "complete" | "error" | "max_steps_reached"
    answer:  Any             # final output on "complete"; None otherwise
    trace:   list[ReActStep]

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"
```

### Abstract interface

```python
class ReActExecutor(ABC):
    max_steps: int = 10

    def __init__(self, name: str, bus: EventBus) -> None: ...

    @abstractmethod
    async def think(
        self, event: Event, trace: list[ReActStep]
    ) -> tuple[str, str, Any]:
        """
        THINK — reason about next step (primary LLM phase).
        Returns (thought, action_name, action_input).
        Return action_name="done" to terminate; action_input becomes the answer.
        """

    @abstractmethod
    async def execute(self, action: str, action_input: Any) -> str:
        """
        EXECUTE — run the tool, return observation string.
        Deterministic — no LLM calls here.
        """

    async def on_step(self, step: ReActStep) -> None:
        """Hook after each step — override for logging / dashboard telemetry. Default no-op."""

    async def follow_up(self, event: Event, result: ReActResult) -> Optional[Event]:
        """Emit downstream event on completion. Use event.caused(). Default no-op."""
        return None

    async def run(self, event: Event) -> ReActResult:
        """Bounded ReAct loop. Caps at max_steps; publishes follow_up event on exit."""
```

### Design decisions

- **No `retrieve()`** — ReAct builds context through the trace itself. If upfront context
  assembly is needed, use RALF instead (or compose: RALF.retrieve → ReAct.run).
- **`action="done"` as terminal signal** — clean protocol, no separate boolean flag.
- **`on_step()` hook** — exists for the dashboard live-tail; each step is a publishable event.
- **`follow_up()` mirrors RALF** — all executors emit a downstream event on completion.
- **LLM in `think()` only** — `execute()` is deterministic tool dispatch.

---

## 2. PlanExecutor

**Location:** `agentic_loopkit/loops/plan.py`

Builds on ReAct. Front-loads task decomposition (one LLM call to produce an ordered step
list), then executes each step. Each `execute_step()` can internally run a `ReActExecutor`.

### Dataclasses

```python
@dataclass
class PlanStep:
    index:       int
    description: str
    status:      str = "pending"   # "pending" | "complete" | "failed"

@dataclass
class PlanResult:
    status:  str              # "complete" | "partial" | "failed"
    steps:   list[PlanStep]
    outputs: list[Any]
```

### Abstract interface

```python
class PlanExecutor(ABC):

    @abstractmethod
    async def plan(self, event: Event) -> list[PlanStep]:
        """Decompose task into ordered steps. Primary LLM call."""

    @abstractmethod
    async def execute_step(
        self, event: Event, step: PlanStep, prior_outputs: list[Any]
    ) -> tuple[Any, bool]:
        """Execute one step. Returns (output, success). Wire a ReActExecutor here."""

    async def follow_up(self, event: Event, result: PlanResult) -> Optional[Event]:
        return None

    async def run(self, event: Event) -> PlanResult: ...
```

### Design decisions

- `execute_step()` is where a `ReActExecutor` is wired — the composition is explicit in
  the subclass, not baked into the base. Keeps it flexible.
- No max_iterations on the plan itself — the step list is fixed at plan time. Each
  `ReActExecutor` inside `execute_step()` carries its own `max_steps`.

---

## 3. ReflexionExecutor

**Location:** `agentic_loopkit/loops/reflexion.py`

RALF variant. Adds an explicit self-critique phase between `act()` and `learn()`.
The critique can lower confidence to force another iteration, driving quality improvement.

### Abstract interface

```python
class ReflexionExecutor(RALFExecutor):
    """
    RALFExecutor + explicit critique phase.

    Loop: retrieve → [act → critique → learn] × max_iterations → follow_up
    """

    @abstractmethod
    async def critique(
        self, event: Event, result: RALFResult
    ) -> tuple[RALFResult, str]:
        """
        CRITIQUE — evaluate the act() output.
        Returns (revised_result, critique_note).
        Lower result.confidence to force another iteration.
        Raise confidence to proceed toward completion.
        """
```

### Design decisions

- **Extends `RALFExecutor`** rather than standalone — the loop machinery (bounded iteration,
  confidence rejection, follow_up) is identical; only the step interior changes. Avoids
  duplicating ~60 lines of loop infrastructure.
- **`_post_act_hook()` is overridden**, not `run()`. `critique()` is wired in via the hook —
  `run()` is never copied. This is the canonical extension pattern for all RALF variants.

---

## 4. OutcomeExecutor

**Location:** `agentic_loopkit/loops/outcome.py`

RALF variant. Adds a rubric and an isolated `evaluate()` phase via `_post_act_hook()`.
Mirrors the Anthropic Managed Agents grader contract: the evaluator sees only the artifact
and rubric — never the agent's reasoning history — preventing anchoring bias.

### Abstract interface

```python
class OutcomeExecutor(RALFExecutor):
    """
    RALFExecutor + rubric-governed isolated evaluation.

    Loop: retrieve → [act → evaluate] × max_iterations → follow_up
    evaluate() receives (artifact, rubric) only — no agent reasoning history.
    """
    max_iterations: int = 3   # matches Anthropic Managed Agents default

    @property
    @abstractmethod
    def rubric(self) -> str:
        """Markdown criteria. Explicit and gradeable — not vague."""

    @abstractmethod
    async def evaluate(
        self, artifact: Any, rubric: str
    ) -> tuple[bool, list[str]]:
        """
        EVALUATE — isolated context only.
        Returns (satisfied, gaps).
        Satisfied → status="complete", confidence=1.0.
        Gaps → fed to next act() via prior_result.output.
        """
```

### Design decisions

- **Isolated evaluation context** — `evaluate()` must call the LLM with only `(artifact, rubric)`,
  no prior chain. This is the key distinction from `ReflexionExecutor.critique()`, which runs in
  the same context as `act()` and may rationalise the agent's own choices.
- **Extends `RALFExecutor` via `_post_act_hook()`** — same extension pattern as ReflexionExecutor.
  Neither executor duplicates `run()`.
- **`satisfied=True` sets `confidence=1.0`** — overrides whatever confidence `act()` returned.
  The rubric verdict supersedes self-assessed confidence.
- **Gap feedback in `prior_result.output`** — when not satisfied, the gaps are prepended to the
  previous artifact in `prior_result.output` so the next `act()` iteration has clear revision context.
- **`max_iterations = 3` default** — matches the Anthropic Managed Agents default (max 20).

### Comparison with ReflexionExecutor

| | `ReflexionExecutor` | `OutcomeExecutor` |
|---|---|---|
| Evaluation context | Same as `act()` (full history) | Isolated (`artifact + rubric` only) |
| Evaluation type | Self-critique | External rubric grader |
| Anchoring risk | Present | Eliminated by design |
| Use case | Iterative quality improvement | Outcome-gate against explicit criteria |

---

## 5. ProjectionAgent

**Location:** `agentic_loopkit/agents/projection.py`

Reactive agent that materialises a live document from the event log.  The event log is the
source of truth; the materialised document is a *projection* — regenerated whenever a trigger
event arrives on a subscribed stream.

This distinction matters for governance: pages can evolve automatically, historical versions
are reconstructable by replaying the log at any point in time, and conflicting interpretations
can coexist as separate `ProjectionAgent` subclasses reading the same source events.

### Abstract interface

```python
class ProjectionAgent(AgentBase):

    def __init__(
        self, name: str, bus: EventBus,
        projection_streams: list[str] | None = None,
    ) -> None: ...

    @property
    def projection_streams(self) -> list[str]:
        """Streams to load when materialising. Defaults to subscription streams."""

    def should_materialise(self, event: Event) -> bool:
        """Gate: return False to skip this trigger event. Default: True."""

    @abstractmethod
    async def materialise(self, events: list[Event]) -> str:
        """
        PRIMARY LLM PHASE — called from orient().
        Receives all events for projection_streams; returns the document as a string.
        """
```

### OODA wiring

```
observe()   — calls should_materialise(); None if False                (deterministic)
orient()    — loads events via load_all_events(); calls materialise()  (LLM here)
             — calls aggregate_confidence() to compute page-level score
decide()    — pass-through                                             (deterministic)
act()       — publishes projection.updated with content + confidence   (deterministic)
```

### Emitted event

```python
Event(
    event_type = "projection.updated",  # stream: "projection"
    source     = agent.name,
    payload = {
        "projection_id": agent.name,
        "content":       "<materialised document>",
        "confidence":    0.82,           # aggregate_confidence() result; None if no _meta
        "event_count":   42,
        "streams":       ["gps", "adr"],
        "_meta": { "phase": "orient", "loop_type": "ooda", "confidence": 0.82, ... }
    }
)
```

### Usage example

```python
class WikiPageAgent(ProjectionAgent):
    def should_materialise(self, event):
        return event.event_type in ("gps.cycle_complete", "adr.record_new")

    async def materialise(self, events):
        return await my_llm.call(
            system="Synthesise these events into a wiki page.",
            user="\n".join(e.payload.get("summary","") for e in events),
        )

agent = WikiPageAgent("wiki", bus, projection_streams=["gps", "adr"])
agent.subscribe("gps", "adr")
bus.register(agent)
```

### Design decisions

- **LLM in `materialise()` only** — called from `orient()`; all other phases deterministic.
- **`projection_streams` decoupled from subscriptions** — subscribe to trigger streams, load
  from a broader or different set when materialising (e.g. subscribe to `"tickets"`, load
  from `"tickets"` and `"comments"`).
- **`aggregate_confidence()` wired in by default** — page-level confidence is computed from
  `_meta.confidence` across source events, weighted by `TrustLevel` and `delegation_depth`.
  Returns `None` when no source events carry confidence data.
- **Projection is itself an event** — `projection.updated` is persisted to the bus, making
  the projection queryable, replayable, and subscribable by downstream agents.

---

## 6. ConflictResolutionExecutor

**Location:** `agentic_govkit/loops/conflict.py`
**Status:** ✅ Built 2026-05-15. Extends `OutcomeExecutor`. Exported from `agentic_govkit`.

Note on location: placed in `agentic_govkit` (not `agentic_loopkit`) because it emits
`governance.*` events — keeping it in loopkit would require either a boundary violation
or string literals for GovernanceEventType values.

### Problem

Two agents produce incompatible orientations about the same entity — same `correlation_id`,
contradictory conclusions.  Phase A governance (`AuditAgent`) observes and flags this via
`governance.dispute_opened`.  Phase B needs a mechanism to resolve it.

Characteristics of the problem that drive the design:
- Neither position is obviously wrong — both may be valid from different evidence sets
- A simple "last writer wins" produces silent data loss
- Self-critique (`ReflexionExecutor`) anchors to one position; the mediator must see both
  positions neutrally
- Evaluation must be isolated from the act() context — the same isolation contract as
  `OutcomeExecutor`, for the same reason (prevents anchoring bias)

### Design

`ConflictResolutionExecutor` extends `OutcomeExecutor`.  `OutcomeExecutor` is the right base
because isolated evaluation is already its core contract.  The only new concept is that `act()`
receives two competing positions rather than a single prior result.

```python
class ConflictResolutionExecutor(OutcomeExecutor):
    """
    Mediates between two competing agent positions on the same entity.

    Loop: retrieve (load both positions) →
          [act (synthesise) → evaluate (isolated rubric check)] × max_iterations →
          follow_up (emit dispute_resolved or escalate to human_override)

    Isolation contract (inherited from OutcomeExecutor):
        evaluate(synthesis, rubric) receives ONLY the synthesis and rubric —
        no prior reasoning history from either competing agent. This prevents
        the mediator anchoring on whichever position it encountered first.
    """
    max_iterations: int = 3

    @property
    @abstractmethod
    def rubric(self) -> str:
        """
        Markdown criteria for a valid reconciliation. Must be explicit and gradeable.
        Example criterion: "The synthesis must not contradict either source position
        without explaining why the contradiction was resolved."
        """

    @abstractmethod
    async def retrieve(self, event: Event) -> dict:
        """
        Load both competing positions.  Return context dict with at minimum:
            { "position_a": <Event or payload>, "position_b": <Event or payload> }
        Source: load from the event store by correlation_id or from the event payload.
        """

    @abstractmethod
    async def act(self, context: dict, prior_result: RALFResult) -> RALFResult:
        """
        Produce a reconciled synthesis from both positions.
        Primary LLM call. Use prior_result.output (gap list from evaluate) if present.
        """

    # evaluate() — inherited from OutcomeExecutor.
    # Signature: evaluate(synthesis, rubric) -> (satisfied, gaps)
    # Must call LLM with ONLY (synthesis, rubric) — no agent history.

    async def follow_up(self, event: Event, result: RALFResult) -> Optional[Event]:
        """
        On complete: emit governance.dispute_resolved with the reconciled view.
        On max_iterations / error: emit governance.human_override to escalate.
        """
```

### Event flow

```
governance.dispute_opened     ← emitted by something that detected the conflict
        │
        ▼
ConflictResolutionExecutor.run(event)
        │
        ├─ retrieve()   — load position_a, position_b
        ├─ act()        — LLM synthesises a reconciliation
        ├─ evaluate()   — isolated rubric check (no history)
        │
        ├─ satisfied=True  ──▶  governance.dispute_resolved  (content = synthesis)
        └─ max_iterations  ──▶  governance.human_override    (content = both positions + gap list)
```

### Key design decisions

- **Extends `OutcomeExecutor`, not `ReflexionExecutor`** — self-critique (Reflexion) anchors to
  one position; external rubric evaluation (Outcome) is the correct neutral model for mediation.
- **`evaluate()` isolation is non-negotiable** — the mediator must not know which position "came
  first." Both positions must be presented symmetrically in `act()` context.
- **Human escalation on max_iterations** — unresolved disputes become `governance.human_override`
  events, not silent failures.  The human sees both positions + the gap list from the last
  `evaluate()` call.
- **`correlation_id` is the dispute key** — all events in a dispute share a `correlation_id`.
  `retrieve()` uses this to load both positions from the store.
- **`ConflictResolutionExecutor` is not `AuditAgent`** — `AuditAgent` observes and flags;
  `ConflictResolutionExecutor` mediates. They are separate concerns. The audit flag triggers
  the executor; the executor does not flag.

### Comparison with existing executors

| | `ReflexionExecutor` | `OutcomeExecutor` | `ConflictResolutionExecutor` |
|---|---|---|---|
| Inputs | Single prior result | Single artifact | Two competing positions |
| Evaluation context | Same as act() | Isolated | Isolated |
| Evaluation type | Self-critique | Rubric grader | Rubric mediator |
| Anchoring risk | Present | Eliminated | Eliminated by design |
| On max_iterations | error | error | human_override event |

---

## 7. Tree-of-Thoughts — deferred

High complexity, niche use case. Requires branching state management and a scoring/pruning
function that doesn't map to the loopkit's linear bounded-loop model. Revisit post-v1.

---

## Replay-as-Archaeology Pattern

> *"Historical versions are reconstructable. Replay becomes governance archaeology."*

The JSONL event store already supports this — no new infrastructure needed.  `load_events()`
with time-range and filter parameters reconstructs what the system knew at any point in time.

### The key functions

```python
from agentic_loopkit import load_events
from agentic_loopkit.events.store import load_all_events

# Recent window (default 72h)
events = load_events("gps")

# Custom window
events = load_events("gps", hours=6)

# Full history — no time filter
events = load_all_events("gps")

# Across all streams (wildcard)
events = load_all_events("*")

# A complete workflow by correlation_id
events = load_events("*", correlation_id="CU-123", hours=24 * 365)

# A specific event by ID (short-circuits on first match — cheap even over wildcard)
events = load_events("*", event_id="<uuid>")
```

### Archaeology queries

| Question | Query |
|---|---|
| What events led up to this governance flag? | `load_events("*", correlation_id=flag.correlation_id)` |
| What did the system know about stream X six hours ago? | `load_events("x", hours=6)` |
| When did agent Y first emit a low-confidence result? | `load_all_events("*")` + filter `_meta.confidence < 0.65` |
| What was the projection state before this dispute? | `load_events("projection", hours=48)` + filter by `projection_id` |
| Replay a full correlation chain | `load_events("*", correlation_id=..., hours=24*365)` |

### Relationship to ProjectionAgent

A `ProjectionAgent` calls `load_all_events()` every time it materialises.  This means:

- **Any past point in time is reconstructable**: run `ProjectionAgent.materialise()` against
  the event slice that existed at time T — the result is the projection as it would have been
  generated then.
- **Conflicting projections can coexist**: two `ProjectionAgent` subclasses reading the same
  events but with different `materialise()` implementations produce different views.  Both are
  valid; the event log arbitrates disputes by providing the authoritative source for both.
- **Human overrides are events too**: a `governance.human_override` event in the store means
  any post-override replay naturally incorporates the human decision.

### Governance archaeology example

```python
# When was delegation depth first exceeded for this workflow?
all_events = load_events("*", correlation_id="CU-123", hours=24 * 365)
depth_flags = [
    e for e in all_events
    if e.event_type == "governance.depth_exceeded"
]
first_flag = min(depth_flags, key=lambda e: e.timestamp)

# What were the three events immediately before the flag?
preceding = [
    e for e in all_events
    if e.timestamp < first_flag.timestamp
][-3:]
```

### Rules

- Use `load_events()` for windowed queries (recent activity, dashboards, live-tail).
- Use `load_all_events()` for projections and governance archaeology (full history required).
- Use `correlation_id` to scope queries to a workflow — it's the most powerful filter.
- The store is append-only; `compact_stream()` prunes by retention window but never rewrites
  content. Archaeology is always consistent with what was actually observed.

---

## OODA + ReAct Composition Pattern

This is the canonical wiring pattern. Document in CLAUDE.md and architecture.md.

```
OODA (outer — strategic loop):
  observe()  → gather signals from event stream
  orient()   → LLM reasons about what needs to happen
  decide()   → choose which executor to invoke (ReAct / RALF / Plan)
  act()      → await ReActExecutor.run(event)
                 └─ ReAct (inner — execution loop):
                      think()   → LLM picks next tool
                      execute() → call tool, get observation
                      (repeat until action="done")
```

OODA operates at the strategic/episodic level. ReAct operates at the step/tool level.
They are not alternatives — they are layers.

---

## Public API Additions

```python
# v2 — executor family
from .loops.react      import ReActExecutor, ReActResult, ReActStep
from .loops.plan       import PlanExecutor, PlanResult, PlanStep
from .loops.reflexion  import ReflexionExecutor
from .loops.outcome    import OutcomeExecutor

# v3 — deliberation-space primitives
from .agents.projection import ProjectionAgent, ProjectionEventType
from .events.confidence import aggregate_confidence

# v4 — state persistence
from .agents.base import AgentState   # CoALA-decomposed state (episodic, semantic, procedural)

# v4-5 — proactive exploration
from .agents.problem_generator import ProblemGeneratorAgent, AgendaEventType, AgendaItem

# v4-6 — generate-and-rank
from .loops.utility import UtilityExecutor, UtilityResult, UtilityCandidate

# v4-8 — governance learning (agentic_govkit public API)
# from agentic_govkit import GovernanceLearningAgent, PolicyRecommendation

# v4-4 — test harness
from .testing import AgentTestHarness, TestTask, TestResult, TestSuiteResult, AsyncLLMCallable

# v5 — self-improvement primitives
from .loops.skillopt import SkillOptExecutor, SkillEdit, SkillOptResult
```

---

## Implementation Order

### v2
1. ✅ `loops/react.py` — ReActExecutor + tests (2026-05-04)
2. ✅ `loops/plan.py` — PlanExecutor + tests (2026-05-04)
3. ✅ `loops/reflexion.py` — ReflexionExecutor + tests; `_post_act_hook()` extension point added to RALFExecutor (2026-05-05)
4. ✅ `loops/outcome.py` — OutcomeExecutor + tests; rubric-governed isolated evaluation (2026-05-07)
5. ✅ Update `CLAUDE.md` and `docs/architecture.md` with composition pattern (2026-05-02, updated 2026-05-07)
6. ✅ Update `__init__.py` exports after each (ongoing)

### v3 — deliberation-space primitives
7. ✅ `events/confidence.py` — `aggregate_confidence()` utility + tests (2026-05-12)
8. ✅ `agentic_govkit/events/models.py` — GovernanceEventType extended: CONFIDENCE_BREACH, DISPUTE_OPENED, DISPUTE_RESOLVED, HUMAN_OVERRIDE (2026-05-12)
9. ✅ `agentic_govkit/agents/audit.py` — `confidence_threshold` parameter; auto-flags CONFIDENCE_BREACH (2026-05-12)
10. ✅ `agents/projection.py` — ProjectionAgent + ProjectionEventType + tests (2026-05-12)
11. ✅ Replay-as-archaeology pattern documented (this file, 2026-05-12)
12. ✅ `agentic_govkit/loops/conflict.py` — ConflictResolutionExecutor (2026-05-15); extends OutcomeExecutor; emits governance.dispute_resolved / governance.human_override
13. ✅ `agentic_govkit/agents/killswitch.py` — KillSwitchAgent + halt_correlation / quarantine_source / emit_human_override (2026-05-15)
14. ✅ `agentic_govkit/events/models.py` — GovernanceEventType.HALT + QUARANTINE (2026-05-15)

### v4 — working memory lifecycle + cognitive architecture
15. ✅ `bus.py` — backpressure signals, graceful drain, is_stopping (2026-06-08)
16. ✅ `adapters/base.py` — liveness tracking, stall threshold, adapter_states() (2026-06-08)
17. ✅ `agents/base.py` — `world_model` field in `AgentState`; save_state/load_state tag separation (2026-06-08)
18. ✅ `agents/performance.py` — `PerformanceMeasure` protocol + `SimpleConfidencePerformance` (2026-06-08)
19. ✅ `agents/problem_generator.py` — `ProblemGeneratorAgent` + `AgendaEventType` + `AgendaItem` (2026-06-09)
20. ✅ `loops/utility.py` — `UtilityExecutor` + `UtilityResult` + `UtilityCandidate` (2026-06-09)
21. ✅ `agentic_govkit/agents/learning.py` — `GovernanceLearningAgent` + `PolicyRecommendation` (2026-06-09)
22. ✅ `agentic_govkit/events/models.py` — `POLICY_RECOMMENDATION` + `POLICY_APPLIED` added to `GovernanceEventType` (2026-06-09)
23. ✅ `agentic_loopkit/testing.py` — `AgentTestHarness` + `AsyncLLMCallable` + `TestTask` / `TestResult` / `TestSuiteResult`; `regression_gate()` static method; tests in `tests/test_harness.py` (spec: `docs/agent-testkit-design.md`) (2026-06-11)

### v5 — self-improvement primitives
_Sources:_
_— arXiv:2606.09498 "Self-Harness: Harnesses That Improve Themselves" (2026-06-10)_
_— arXiv:2605.23904 "SkillOpt: Executive Strategy for Self-Evolving Agent Skills" (Microsoft / SJTU, 2026-06-11) — spec: `docs/skillopt-executor-design.md`_

24. ✅ `events/models.py` — `HarnessEventType` StrEnum: `harness.edit_proposed`, `harness.edit_accepted`, `harness.edit_rejected`, `harness.candidate_eval`; also added `SystemEventType.FAILURE_PATTERN_DETECTED`; `loop_type` comment updated (2026-06-16)
25. ✅ `agents/failure_pattern.py` — `FailurePatternAgent(ProjectionAgent)` + `FailureSignature` dataclass; subscribes to `system.*` + `governance.*`; clusters error events by `(terminal_cause, causal_status, agent_mechanism)`; emits `system.failure_pattern_detected`; 16 tests in `tests/agents/test_failure_pattern.py` (2026-06-16)
26. ✅ `loops/skillopt.py` — `SkillOptExecutor(RALFExecutor)` + `SkillEdit` + `RejectedEdit` + `SkillOptResult`; bounded validation-gated skill optimiser (arXiv:2605.23904); LLM in `reflect()` only (optimizer model, not task agent); `score()` deterministic — no LLM; `edit_budget = 4` (L_t analogue); `_step_buffer: list[RejectedEdit]` = rejected-edit negative feedback passed to subsequent `reflect()` calls; `slow_update()` + `update_meta_skill()` optional hooks (default no-op); `_apply_edits()` internal helper (append/insert_after/replace/delete); `max_iterations = 4` epochs; `best_skill` property exports the deployed artifact; spec: `docs/skillopt-executor-design.md`; tests in `tests/loops/test_skillopt.py` (2026-06-11)
27. ✅ `loops/self_harness.py` — `SelfHarnessExecutor(OutcomeExecutor)`: integration executor wiring the v5 primitives end-to-end; `retrieve()` loads `system.failure_pattern_detected` events from EventStore (train/selection trajectory splits); `act(context, prior_result)` instantiates fresh `SkillOptExecutor` via factory, runs to completion, returns `SkillOptResult`; `evaluate(artifact, rubric)` calls `AgentTestHarness.regression_gate()` (deterministic — no LLM); rubric = `"candidate skill must pass regression_gate() against baseline"`; `follow_up()` emits `harness.edit_accepted` (with `best_skill` payload) or `harness.edit_rejected`; `max_iterations = 3`; 14 tests in `tests/loops/test_self_harness.py` (2026-06-16)

---

## Source

Based on: `/Users/ismith/Downloads/agentic-loopkit-ideas/agent-harness-idioms.md`
