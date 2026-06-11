"""
agentic_loopkit/testing.py — AgentTestHarness: runtime evaluation primitive.

Runs any AgentBase subclass against a structured task list, collects
pass/fail outcomes and event traces per task, and applies a non-regressive
acceptance gate — so harness-level changes can be validated empirically.

Based on the Self-Harness paper (arXiv:2606.09498) §3.4 regression rule.

Zero extra deps — stdlib asyncio + loopkit's own classes only.
"""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Optional
from typing import Protocol, runtime_checkable

from .events.models import Event, WILDCARD_STREAM
from .events.store import load_all_events
from .utils.time import now_ms

if TYPE_CHECKING:
    from .agents.base import AgentBase

log = logging.getLogger("agentic_loopkit.testing")


@runtime_checkable
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


@dataclass
class TestTask:
    task_id: str
    description: str
    input_event: Event
    split: Literal["held_in", "held_out"]
    expected_outcome: Optional[str] = None  # event_type to match; None = any emission


@dataclass
class TestResult:
    task_id: str
    split: Literal["held_in", "held_out"]
    passed: bool
    emitted_events: list[Event]
    duration_ms: int
    repeat: int       # 0-based repeat index
    error: Optional[str] = None


@dataclass
class TestSuiteResult:
    results: list[TestResult] = field(default_factory=list)

    @property
    def held_in_pass(self) -> int:
        return sum(1 for r in self.results if r.split == "held_in" and r.passed)

    @property
    def held_in_total(self) -> int:
        return sum(1 for r in self.results if r.split == "held_in")

    @property
    def held_out_pass(self) -> int:
        return sum(1 for r in self.results if r.split == "held_out" and r.passed)

    @property
    def held_out_total(self) -> int:
        return sum(1 for r in self.results if r.split == "held_out")

    @property
    def held_in_rate(self) -> float:
        return self.held_in_pass / self.held_in_total if self.held_in_total else 0.0

    @property
    def held_out_rate(self) -> float:
        return self.held_out_pass / self.held_out_total if self.held_out_total else 0.0


class AgentTestHarness:
    """
    Runs an agent against a structured task list.
    Each (task, repeat) pair gets a fresh EventBus and fresh agent instance —
    full isolation; no state bleeds between runs.

    Pass/fail for each task is determined by majority vote across repeats.
    """

    def __init__(
        self,
        store_dir: Path,
        *,
        repeats: int = 2,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._store_dir = store_dir
        self._repeats = repeats
        self._timeout_seconds = timeout_seconds

    async def run_suite(
        self,
        agent_factory: Callable[[], "AgentBase"],
        tasks: list[TestTask],
    ) -> TestSuiteResult:
        """
        Run agent_factory() against each task on an isolated EventBus.
        agent_factory is called fresh for each (task, repeat) pair.
        Pass/fail is majority vote across repeats; all per-repeat results are stored.
        """
        all_results: list[TestResult] = []

        for task in tasks:
            repeat_results: list[TestResult] = []

            for repeat in range(self._repeats):
                result = await self._run_once(agent_factory, task, repeat)
                repeat_results.append(result)

            # Majority vote: task passes if raw pass count >= ceil(repeats / 2)
            raw_pass_count = sum(1 for r in repeat_results if r.passed)
            majority = raw_pass_count >= math.ceil(self._repeats / 2)

            # Apply majority decision uniformly across all repeats for this task
            for r in repeat_results:
                r.passed = majority

            all_results.extend(repeat_results)

        return TestSuiteResult(results=all_results)

    async def evaluate_result(
        self, task: TestTask, emitted: list[Event]
    ) -> bool:
        """
        Default: pass if any emitted event matches task.expected_outcome as event_type.
        If expected_outcome is None, pass if any events were emitted at all.
        Override for richer verification (payload checks, event sequences, etc.).
        """
        if task.expected_outcome is None:
            return len(emitted) > 0
        return any(e.event_type == task.expected_outcome for e in emitted)

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

    async def _run_once(
        self,
        agent_factory: Callable[[], "AgentBase"],
        task: TestTask,
        repeat: int,
    ) -> TestResult:
        """Run a single (task, repeat) pair in full isolation.

        Calls agent._handle() directly rather than routing through bus.publish +
        subscription — this avoids feedback loops when the agent emits events on
        the same stream it receives from.  The bus is still used to persist output
        events so they can be collected and inspected after the run.
        """
        from .bus import EventBus

        self._store_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = Path(tempfile.mkdtemp(dir=self._store_dir))

        try:
            bus = EventBus(store_dir=tmp_path)
            agent = agent_factory()
            agent._bus = bus
            # No subscription — _handle is called directly; output events are
            # published to the bus for persistence but not re-routed to the agent.

            start_ms = now_ms()
            error: Optional[str] = None

            try:
                async with asyncio.timeout(self._timeout_seconds):
                    await agent._handle(task.input_event)
            except asyncio.TimeoutError:
                error = f"timeout after {self._timeout_seconds}s"
            except Exception as exc:
                error = str(exc)

            duration_ms = now_ms() - start_ms

            # Collect events the agent emitted — exclude system events
            all_events = load_all_events(WILDCARD_STREAM, tmp_path)
            emitted = [e for e in all_events if e.stream != "system"]

            passed = False if error else await self.evaluate_result(task, emitted)

            return TestResult(
                task_id=task.task_id,
                split=task.split,
                passed=passed,
                emitted_events=emitted,
                duration_ms=duration_ms,
                repeat=repeat,
                error=error,
            )
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)
