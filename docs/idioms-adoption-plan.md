# Idioms Adoption Plan

_Decided: 2026-05-02_

## Summary

Four harness idioms were evaluated against the loopkit. Three are adopted; one deferred.
The OODA+ReAct composition pattern is documented as the canonical wiring example.

---

## Decision Table

| Idiom | Verdict | File | Priority | Status |
|---|---|---|---|---|
| ReAct (Thought→Action→Observation) | **Build** | `loops/react.py` | 1 | ✅ Built 2026-05-04 |
| Plan-and-Execute | **Build** | `loops/plan.py` | 2 | ✅ Built 2026-05-04 |
| Reflexion | **Build** | `loops/reflexion.py` | 3 | Planned |
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
- `run()` is overridden to inject `critique()` between `act()` and `learn()`.

---

## 4. Tree-of-Thoughts — deferred

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
```

---

## Implementation Order

1. ✅ `loops/react.py` — ReActExecutor + tests (2026-05-04)
2. ✅ `loops/plan.py` — PlanExecutor + tests (2026-05-04)
3. `loops/reflexion.py` — ReflexionExecutor + tests
4. ✅ Update `CLAUDE.md` and `docs/architecture.md` with composition pattern (2026-05-02, updated 2026-05-04)
5. ✅ Update `__init__.py` exports after each (ongoing)

---

## Source

Based on: `/Users/ismith/Downloads/agentic-loopkit-ideas/agent-harness-idioms.md`
