# AgentTestHarness — Design Brief

_Date: 2026-06-10_
_Status: Draft — v4-4_
_Informs: loopkit v4 developer testkit; prerequisite for SelfHarnessExecutor (v5)_

---

## North Star

A lightweight, zero-dependency test harness that lets a caller run any `AgentBase` subclass
against a structured task list, collect pass/fail outcomes and event traces per task, and apply
a non-regressive acceptance gate — so harness-level changes can be validated empirically rather
than by inspection alone.

This is not a test framework replacement. It is a runtime evaluation primitive: the same
mechanism used in production (run agent → collect events → evaluate outcome) expressed as a
reusable, isolated component.

---

## Motivation

The Self-Harness paper (arXiv:2606.09498) shows that harness-level changes produce measurable,
generalisable improvements — but only when validated through regression testing. Without a
structured way to run an agent against verifiable tasks and compare held-in vs held-out outcomes,
any harness change is anecdotal.

The `AgentTestHarness` is also the prerequisite for `FailurePatternAgent` and `SelfHarnessExecutor`
(v5 work): you cannot mine failure patterns or validate harness proposals without a way to execute
runs and collect structured results.

---

## Core Data Model

### TestTask

```python
@dataclass
class TestTask:
    task_id: str
    description: str
    input_event: Event                       # the trigger event the agent receives
    split: Literal["held_in", "held_out"]    # held_in = evidence; held_out = regression gate
    expected_outcome: str | None = None      # optional expected payload key/value for pass check
```

`input_event` is a normal loopkit `Event` — the harness publishes it to a fresh `EventBus`
and collects what the agent emits in response. No special test-only event type needed.

`expected_outcome` is intentionally minimal. For simple cases, pass/fail is determined by
whether the agent emitted a specific event type. For richer verification, subclass
`AgentTestHarness` and override `evaluate_result()`.

### TestResult

```python
@dataclass
class TestResult:
    task_id: str
    split: Literal["held_in", "held_out"]
    passed: bool
    emitted_events: list[Event]    # all events published by the agent during this run
    duration_ms: int
    repeat: int                    # repeat index (0-based) for noise-reduction runs
    error: str | None              # exception message if the run raised
```

### TestSuiteResult

```python
@dataclass
class TestSuiteResult:
    results: list[TestResult]

    @property
    def held_in_pass(self) -> int: ...    # sum of passed=True where split="held_in"
    @property
    def held_in_total(self) -> int: ...
    @property
    def held_out_pass(self) -> int: ...
    @property
    def held_out_total(self) -> int: ...
    @property
    def held_in_rate(self) -> float: ...  # held_in_pass / held_in_total; 0.0 if total=0
    @property
    def held_out_rate(self) -> float: ...
```

---

## Interface

```python
class AgentTestHarness:
    """
    Runs an agent against a structured task list.
    Each task gets a fresh EventBus and agent instance — full isolation between runs.
    """

    def __init__(
        self,
        store_dir: Path,
        *,
        repeats: int = 2,        # repeat each task N times; pass = majority vote
        timeout_seconds: float = 30.0,
    ) -> None: ...

    async def run_suite(
        self,
        agent_factory: Callable[[], AgentBase],
        tasks: list[TestTask],
    ) -> TestSuiteResult:
        """
        Run agent_factory() + each task on an isolated EventBus.
        agent_factory is called fresh for each (task, repeat) pair — no state bleeds.
        Returns aggregated TestSuiteResult.
        """

    async def evaluate_result(
        self, task: TestTask, emitted: list[Event]
    ) -> bool:
        """
        Default: pass if any emitted event matches task.expected_outcome as event_type.
        Override for richer verification (payload checks, multi-event sequences, etc.).
        """

    @staticmethod
    def regression_gate(
        baseline: TestSuiteResult, candidate: TestSuiteResult
    ) -> bool:
        """
        Non-regressive acceptance rule from Self-Harness (arXiv:2606.09498 §3.4):
            ∆in  = candidate.held_in_pass  − baseline.held_in_pass
            ∆ho  = candidate.held_out_pass − baseline.held_out_pass
            accept iff ∆in ≥ 0 AND ∆ho ≥ 0 AND max(∆in, ∆ho) > 0

        Rejects proposals that trade one split against the other, even if total improves.
        """
        delta_in = candidate.held_in_pass - baseline.held_in_pass
        delta_ho = candidate.held_out_pass - baseline.held_out_pass
        return delta_in >= 0 and delta_ho >= 0 and max(delta_in, delta_ho) > 0
```

---

## AsyncLLMCallable Protocol

The test harness itself has no LLM dependency. But agents under test call LLMs inside
`orient()`, `act()` etc. — and tests should be able to inject controllable stubs.

```python
class AsyncLLMCallable(Protocol):
    """
    Protocol for any callable that takes a prompt and returns a string.
    Satisfied by: real LLM clients, lambda stubs, recorded-replay fixtures.

    Usage in agent construction:
        agent = MyAgent("agent", bus, llm=some_callable)
    """
    async def __call__(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "",
    ) -> str: ...
```

Agents that accept `AsyncLLMCallable` are trivially testable: pass a `lambda` stub in tests,
swap in the real client in production. No monkeypatching, no `unittest.mock`.

---

## Isolation Contract

Each `(task, repeat)` pair runs in full isolation:

```
agent_factory()          → fresh AgentBase instance, no shared state
EventBus(tmp_store_dir)  → fresh JSONL store in a temp directory (cleaned up after run)
bus.publish(task.input_event)
collect all emitted events until agent quiesces (no pending publishes within timeout)
evaluate_result(task, emitted)
bus.stop()
```

The `store_dir` passed to `AgentTestHarness.__init__` is the parent for per-run temp
directories. It is the caller's store (for storing suite results); per-run stores are
ephemeral subdirectories cleaned up after each task.

---

## Usage Example

```python
from pathlib import Path
from agentic_loopkit import Event, EventBus
from agentic_loopkit.testing import AgentTestHarness, TestTask, AsyncLLMCallable

# Define a minimal LLM stub
async def stub_llm(prompt: str, *, system: str = "", model: str = "") -> str:
    return '{"decision": "approve", "confidence": 0.9}'

# Define tasks
tasks = [
    TestTask(
        task_id="approve-low-risk",
        description="Low-risk event should be approved",
        input_event=Event(event_type="orders.submitted", source="test", payload={"risk": 0.1}),
        split="held_in",
        expected_outcome="orders.approved",
    ),
    TestTask(
        task_id="reject-high-risk",
        description="High-risk event should be rejected",
        input_event=Event(event_type="orders.submitted", source="test", payload={"risk": 0.95}),
        split="held_out",
        expected_outcome="orders.rejected",
    ),
]

harness = AgentTestHarness(Path("~/.cache/my-app/tests").expanduser(), repeats=2)

# Baseline run
baseline = await harness.run_suite(
    lambda: MyOrderAgent("agent", None, llm=stub_llm),   # bus injected by harness
    tasks,
)

# After a harness change, validate it
candidate = await harness.run_suite(
    lambda: MyOrderAgent("agent", None, llm=stub_llm),
    tasks,
)

if AgentTestHarness.regression_gate(baseline, candidate):
    print("✓ Change accepted — non-regressive improvement")
else:
    print("✗ Change rejected — regression or no improvement")
```

---

## Module Layout

```
agentic_loopkit/
└── testing.py        # AgentTestHarness, TestTask, TestResult, TestSuiteResult,
                      # AsyncLLMCallable — zero extra deps; stdlib + asyncio only

tests/
└── test_harness.py   # happy path, regression_gate, isolation (no state bleed),
                      # timeout handling, evaluate_result override
```

`testing.py` is part of the core package (not a dashboard or governance extra) — it is needed
by any team building on loopkit, not just dashboard users.

Public API additions:

```python
from agentic_loopkit import (
    AgentTestHarness,
    TestTask,
    TestResult,
    TestSuiteResult,
    AsyncLLMCallable,
)
```

---

## Design Decisions

1. **`agent_factory` not `agent`** — the factory is called fresh per `(task, repeat)` pair.
   Passing an existing agent instance would allow state to bleed between test runs, which
   defeats the isolation contract. The factory pattern is one line for callers: `lambda: MyAgent(...)`.

2. **`regression_gate` is static and deterministic** — no LLM, no probability. The paper's
   acceptance rule is arithmetic on pass counts. Making it a static method means it can be
   called anywhere without an instance, and its behaviour is trivially testable.

3. **`expected_outcome` as `event_type` string** — the simplest useful default. Complex
   verification (payload checks, ordering, multi-event patterns) belongs in
   `evaluate_result()` overrides, not in the dataclass. Keep the base case simple.

4. **`repeats=2` default** — matches the Self-Harness paper's experimental protocol. Reduces
   the chance of promoting a change due to a single lucky run without making the suite slow.
   Majority vote: pass count across repeats ≥ `ceil(repeats / 2)`.

5. **`testing.py` not a separate extra** — unlike dashboard or governance, testability is
   not optional for teams adopting loopkit. It should be importable without any `pip install`
   extras flag. Zero new deps — it uses only `asyncio`, `tempfile`, and loopkit's own classes.

---

## Relationship to SelfHarnessExecutor (v5)

`AgentTestHarness` is the evaluation primitive that `SelfHarnessExecutor` uses in its
`evaluate()` phase. Without it, `SelfHarnessExecutor` cannot validate candidate harness
proposals. Build order is strictly:

```
AgentTestHarness (v4-4)  →  FailurePatternAgent (v5-1)  →  SelfHarnessExecutor (v5-4)
```

`FailurePatternAgent` produces the failure evidence that motivates `SelfHarnessExecutor`'s
proposals. `AgentTestHarness` validates whether a proposal actually fixes the failures without
causing regressions.
