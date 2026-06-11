import pytest
from pathlib import Path
from agentic_loopkit import Event, AgentBase
from agentic_loopkit.testing import (
    AgentTestHarness,
    TestTask,
    TestResult,
    TestSuiteResult,
    AsyncLLMCallable,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def make_task(
    task_id="t1",
    split="held_in",
    event_type="orders.submitted",
    expected_outcome="orders.approved",
) -> TestTask:
    return TestTask(
        task_id=task_id,
        description="test task",
        input_event=Event(event_type=event_type, source="test", payload={}),
        split=split,
        expected_outcome=expected_outcome,
    )


class EmittingAgent(AgentBase):
    """Agent that unconditionally emits a fixed event_type on act()."""

    def __init__(self, name, bus, emit_type: str):
        super().__init__(name, bus)
        self.emit_type = emit_type

    async def orient(self, event, context):
        return {"ok": True}

    async def decide(self, event, orientation):
        return orientation

    async def act(self, event, action):
        await self._bus.publish(
            event.caused(self.emit_type, self.name, {})
        )


class SilentAgent(AgentBase):
    """Agent that never emits anything."""

    async def orient(self, event, context):
        return {"ok": True}

    async def decide(self, event, orientation):
        return orientation

    async def act(self, event, action):
        pass


# ── TestSuiteResult properties ─────────────────────────────────────────────────


def test_suite_result_counts():
    results = [
        TestResult("t1", "held_in", True, [], 10, 0),
        TestResult("t1", "held_in", True, [], 12, 1),
        TestResult("t2", "held_out", False, [], 11, 0),
        TestResult("t2", "held_out", False, [], 9, 1),
    ]
    suite = TestSuiteResult(results=results)
    assert suite.held_in_pass == 2
    assert suite.held_in_total == 2
    assert suite.held_out_pass == 0
    assert suite.held_out_total == 2
    assert suite.held_in_rate == 1.0
    assert suite.held_out_rate == 0.0


def test_suite_result_empty():
    suite = TestSuiteResult()
    assert suite.held_in_pass == 0
    assert suite.held_in_rate == 0.0
    assert suite.held_out_rate == 0.0


# ── regression_gate ────────────────────────────────────────────────────────────


def make_suite(held_in_pass, held_out_pass) -> TestSuiteResult:
    results = []
    for i in range(held_in_pass):
        results.append(TestResult(f"hi{i}", "held_in", True, [], 10, 0))
    for i in range(2 - held_in_pass):
        results.append(TestResult(f"hi_fail{i}", "held_in", False, [], 10, 0))
    for i in range(held_out_pass):
        results.append(TestResult(f"ho{i}", "held_out", True, [], 10, 0))
    for i in range(2 - held_out_pass):
        results.append(TestResult(f"ho_fail{i}", "held_out", False, [], 10, 0))
    return TestSuiteResult(results=results)


def test_regression_gate_accepts_improvement():
    baseline = make_suite(held_in_pass=1, held_out_pass=1)
    candidate = make_suite(held_in_pass=2, held_out_pass=1)
    assert AgentTestHarness.regression_gate(baseline, candidate) is True


def test_regression_gate_accepts_both_improve():
    baseline = make_suite(held_in_pass=1, held_out_pass=1)
    candidate = make_suite(held_in_pass=2, held_out_pass=2)
    assert AgentTestHarness.regression_gate(baseline, candidate) is True


def test_regression_gate_rejects_regression():
    baseline = make_suite(held_in_pass=2, held_out_pass=2)
    candidate = make_suite(held_in_pass=1, held_out_pass=2)
    assert AgentTestHarness.regression_gate(baseline, candidate) is False


def test_regression_gate_rejects_trade_off():
    # held_in improves but held_out regresses — reject
    baseline = make_suite(held_in_pass=1, held_out_pass=2)
    candidate = make_suite(held_in_pass=2, held_out_pass=1)
    assert AgentTestHarness.regression_gate(baseline, candidate) is False


def test_regression_gate_rejects_no_improvement():
    baseline = make_suite(held_in_pass=2, held_out_pass=2)
    candidate = make_suite(held_in_pass=2, held_out_pass=2)
    assert AgentTestHarness.regression_gate(baseline, candidate) is False


# ── run_suite: happy path ──────────────────────────────────────────────────────


async def test_run_suite_passes_on_expected_event(tmp_path):
    harness = AgentTestHarness(tmp_path, repeats=2)
    task = make_task(split="held_in", expected_outcome="orders.approved")

    suite = await harness.run_suite(
        lambda: EmittingAgent("agent", None, "orders.approved"),
        [task],
    )

    assert suite.held_in_pass == 2  # 2 repeats, both majority-voted pass
    assert suite.held_in_rate == 1.0


async def test_run_suite_fails_on_wrong_event(tmp_path):
    harness = AgentTestHarness(tmp_path, repeats=2)
    task = make_task(split="held_in", expected_outcome="orders.approved")

    suite = await harness.run_suite(
        lambda: EmittingAgent("agent", None, "orders.rejected"),
        [task],
    )

    assert suite.held_in_pass == 0


async def test_run_suite_fails_on_silent_agent(tmp_path):
    harness = AgentTestHarness(tmp_path, repeats=2)
    task = make_task(split="held_out", expected_outcome="orders.approved")

    suite = await harness.run_suite(
        lambda: SilentAgent("agent", None),
        [task],
    )

    assert suite.held_out_pass == 0


async def test_run_suite_held_out_split(tmp_path):
    harness = AgentTestHarness(tmp_path, repeats=1)
    task = make_task(split="held_out", expected_outcome="orders.approved")

    suite = await harness.run_suite(
        lambda: EmittingAgent("agent", None, "orders.approved"),
        [task],
    )

    assert suite.held_out_pass == 1
    assert suite.held_in_pass == 0


# ── run_suite: isolation ───────────────────────────────────────────────────────


async def test_no_state_bleed_between_runs(tmp_path):
    """Each (task, repeat) must get a fresh agent — no shared instance state."""
    call_count = {"n": 0}

    class CountingAgent(AgentBase):
        async def orient(self, event, context):
            call_count["n"] += 1
            return {"ok": True}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            await self._bus.publish(event.caused("orders.approved", self.name, {}))

    task = make_task(split="held_in")
    harness = AgentTestHarness(tmp_path, repeats=3)

    await harness.run_suite(lambda: CountingAgent("agent", None), [task])

    # orient() called once per (task, repeat) — must be exactly 3
    assert call_count["n"] == 3


async def test_tmp_dirs_cleaned_up(tmp_path):
    harness = AgentTestHarness(tmp_path, repeats=2)
    task = make_task()

    await harness.run_suite(
        lambda: EmittingAgent("agent", None, "orders.approved"),
        [task],
    )

    # Only the harness store_dir itself should remain — no tmp subdirs
    remaining = list(tmp_path.iterdir())
    assert all(p.is_file() for p in remaining) or remaining == []


# ── run_suite: multiple tasks ──────────────────────────────────────────────────


async def test_multiple_tasks_independent(tmp_path):
    harness = AgentTestHarness(tmp_path, repeats=1)
    tasks = [
        make_task("t1", "held_in", expected_outcome="orders.approved"),
        make_task("t2", "held_out", expected_outcome="orders.approved"),
    ]

    suite = await harness.run_suite(
        lambda: EmittingAgent("agent", None, "orders.approved"),
        tasks,
    )

    assert len(suite.results) == 2
    assert suite.held_in_pass == 1
    assert suite.held_out_pass == 1


# ── run_suite: majority vote ───────────────────────────────────────────────────


async def test_majority_vote_applied_uniformly(tmp_path):
    """All repeats for a task carry the majority-voted passed value."""
    harness = AgentTestHarness(tmp_path, repeats=2)
    task = make_task(split="held_in", expected_outcome="orders.approved")

    suite = await harness.run_suite(
        lambda: EmittingAgent("agent", None, "orders.approved"),
        [task],
    )

    # Both repeats should have the same passed value
    assert suite.results[0].passed == suite.results[1].passed


# ── run_suite: emitted_events ─────────────────────────────────────────────────


async def test_emitted_events_contains_agent_output(tmp_path):
    harness = AgentTestHarness(tmp_path, repeats=1)
    task = make_task(split="held_in", expected_outcome="orders.approved")

    suite = await harness.run_suite(
        lambda: EmittingAgent("agent", None, "orders.approved"),
        [task],
    )

    result = suite.results[0]
    event_types = [e.event_type for e in result.emitted_events]
    # Input event is never published to the store (harness calls _handle directly)
    assert task.input_event.event_type not in event_types
    assert "orders.approved" in event_types


async def test_emitted_events_excludes_system(tmp_path):
    harness = AgentTestHarness(tmp_path, repeats=1)
    task = make_task(split="held_in", expected_outcome="orders.approved")

    suite = await harness.run_suite(
        lambda: EmittingAgent("agent", None, "orders.approved"),
        [task],
    )

    result = suite.results[0]
    assert all(e.stream != "system" for e in result.emitted_events)


# ── run_suite: evaluate_result override ───────────────────────────────────────


async def test_evaluate_result_override(tmp_path):
    """Subclass can override evaluate_result for payload-level checks."""

    class StrictHarness(AgentTestHarness):
        async def evaluate_result(self, task, emitted):
            return any(
                e.event_type == task.expected_outcome and e.payload.get("approved")
                for e in emitted
            )

    class RichAgent(AgentBase):
        async def orient(self, event, context):
            return {}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            await self._bus.publish(
                event.caused("orders.approved", self.name, {"approved": True})
            )

    harness = StrictHarness(tmp_path, repeats=1)
    task = make_task(split="held_in", expected_outcome="orders.approved")

    suite = await harness.run_suite(lambda: RichAgent("agent", None), [task])

    assert suite.held_in_pass == 1


# ── AsyncLLMCallable Protocol ─────────────────────────────────────────────────


async def test_async_llm_callable_protocol():
    """A plain async lambda satisfies the AsyncLLMCallable protocol."""

    async def stub(prompt: str, *, system: str = "", model: str = "") -> str:
        return "response"

    assert isinstance(stub, AsyncLLMCallable)


async def test_async_llm_callable_stub_works(tmp_path):
    """Agent wired with an AsyncLLMCallable stub is testable without a real LLM."""

    class LLMAgent(AgentBase):
        def __init__(self, name, bus, llm: AsyncLLMCallable):
            super().__init__(name, bus)
            self.llm = llm

        async def orient(self, event, context):
            response = await self.llm(str(event.payload))
            return {"response": response}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            await self._bus.publish(
                event.caused("orders.approved", self.name, action)
            )

    async def stub_llm(prompt: str, *, system: str = "", model: str = "") -> str:
        return "approve"

    harness = AgentTestHarness(tmp_path, repeats=1)
    task = make_task(split="held_in", expected_outcome="orders.approved")

    suite = await harness.run_suite(
        lambda: LLMAgent("agent", None, llm=stub_llm),
        [task],
    )

    assert suite.held_in_pass == 1
