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

## 5. Tree-of-Thoughts — deferred

High complexity, niche use case. Requires branching state management and a scoring/pruning
function that doesn't map to the loopkit's linear bounded-loop model. Revisit post-v1.

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

Add to `agentic_loopkit/__init__.py` as each executor is implemented:

```python
from .loops.react import ReActExecutor, ReActResult, ReActStep
from .loops.plan import PlanExecutor, PlanResult, PlanStep
from .loops.reflexion import ReflexionExecutor
from .loops.outcome import OutcomeExecutor
```

---

## Implementation Order

1. ✅ `loops/react.py` — ReActExecutor + tests (2026-05-04)
2. ✅ `loops/plan.py` — PlanExecutor + tests (2026-05-04)
3. ✅ `loops/reflexion.py` — ReflexionExecutor + tests; `_post_act_hook()` extension point added to RALFExecutor (2026-05-05)
4. ✅ `loops/outcome.py` — OutcomeExecutor + tests; rubric-governed isolated evaluation (2026-05-07)
5. ✅ Update `CLAUDE.md` and `docs/architecture.md` with composition pattern (2026-05-02, updated 2026-05-07)
6. ✅ Update `__init__.py` exports after each (ongoing)

---

## Source

Based on: `/Users/ismith/Downloads/agentic-loopkit-ideas/agent-harness-idioms.md`
