import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.loops.plan import PlanExecutor, PlanResult, PlanStep
from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import load_events


def make_event(**kwargs) -> Event:
    return Event(event_type="test.trigger", source="test", payload={}, **kwargs)


def make_steps(*descriptions: str) -> list[PlanStep]:
    return [PlanStep(index=i, description=d) for i, d in enumerate(descriptions)]


# ── Concrete executors for testing ───────────────────────────────────────────

class FixedPlanExecutor(PlanExecutor):
    """Plans a fixed list of steps; each step result is driven by a callable."""

    def __init__(self, name, bus, steps: list[PlanStep], step_results: list[tuple]):
        super().__init__(name, bus)
        self._steps = steps
        self._step_results = step_results   # list of (output, success) per step
        self.executed: list[PlanStep] = []
        self.prior_outputs_seen: list[list] = []

    async def plan(self, event):
        return list(self._steps)

    async def execute_step(self, event, step, prior_outputs):
        self.executed.append(step)
        self.prior_outputs_seen.append(list(prior_outputs))
        return self._step_results[step.index]


class EmptyPlanExecutor(PlanExecutor):
    """Plans zero steps."""
    async def plan(self, event): return []
    async def execute_step(self, event, step, prior_outputs): return None, True


class PlanRaisesExecutor(PlanExecutor):
    """plan() always raises."""
    async def plan(self, event): raise RuntimeError("LLM offline")
    async def execute_step(self, event, step, prior_outputs): return None, True


class StepRaisesExecutor(PlanExecutor):
    """execute_step() raises on the specified step index."""
    def __init__(self, name, bus, raise_on: int):
        super().__init__(name, bus)
        self._raise_on = raise_on

    async def plan(self, event):
        return make_steps("step-0", "step-1", "step-2")

    async def execute_step(self, event, step, prior_outputs):
        if step.index == self._raise_on:
            raise ValueError(f"tool error on step {step.index}")
        return f"output-{step.index}", True


# ── All steps complete ────────────────────────────────────────────────────────

async def test_all_steps_complete(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedPlanExecutor(
        "e", bus,
        make_steps("a", "b", "c"),
        [("out-a", True), ("out-b", True), ("out-c", True)],
    )
    result = await executor.run(make_event())
    assert result.status == "complete"
    assert result.outputs == ["out-a", "out-b", "out-c"]
    assert all(s.status == "complete" for s in result.steps)


async def test_all_steps_marked_complete(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedPlanExecutor(
        "e", bus, make_steps("x", "y"), [("1", True), ("2", True)]
    )
    result = await executor.run(make_event())
    assert [s.status for s in result.steps] == ["complete", "complete"]


# ── All steps fail ────────────────────────────────────────────────────────────

async def test_all_steps_fail_returns_failed(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedPlanExecutor(
        "e", bus,
        make_steps("a", "b"),
        [(None, False), (None, False)],
    )
    result = await executor.run(make_event())
    assert result.status == "failed"
    assert result.outputs == []


async def test_all_steps_marked_failed(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedPlanExecutor(
        "e", bus, make_steps("a", "b"), [(None, False), (None, False)]
    )
    result = await executor.run(make_event())
    assert [s.status for s in result.steps] == ["failed", "failed"]


# ── Mixed success / failure ───────────────────────────────────────────────────

async def test_partial_returns_partial(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedPlanExecutor(
        "e", bus,
        make_steps("a", "b", "c"),
        [("out-a", True), (None, False), ("out-c", True)],
    )
    result = await executor.run(make_event())
    assert result.status == "partial"
    assert result.outputs == ["out-a", "out-c"]   # failed step excluded


async def test_partial_step_statuses(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedPlanExecutor(
        "e", bus,
        make_steps("a", "b", "c"),
        [("x", True), (None, False), ("z", True)],
    )
    result = await executor.run(make_event())
    assert [s.status for s in result.steps] == ["complete", "failed", "complete"]


# ── Empty plan ────────────────────────────────────────────────────────────────

async def test_empty_plan_is_complete(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    result = await EmptyPlanExecutor("e", bus).run(make_event())
    assert result.status == "complete"
    assert result.steps == []
    assert result.outputs == []


# ── plan() raises ─────────────────────────────────────────────────────────────

async def test_plan_raises_returns_failed(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    result = await PlanRaisesExecutor("e", bus).run(make_event())
    assert result.status == "failed"
    assert result.steps == []
    assert result.outputs == []


# ── execute_step() raises ─────────────────────────────────────────────────────

async def test_execute_step_raises_marks_step_failed(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    result = await StepRaisesExecutor("e", bus, raise_on=1).run(make_event())
    assert result.steps[1].status == "failed"


async def test_execute_step_raises_other_steps_continue(tmp_path):
    """Raising in step 1 should not prevent steps 0 and 2 from completing."""
    bus = EventBus(store_dir=tmp_path)
    result = await StepRaisesExecutor("e", bus, raise_on=1).run(make_event())
    assert result.steps[0].status == "complete"
    assert result.steps[2].status == "complete"
    assert result.status == "partial"


async def test_execute_step_raises_output_excluded(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    result = await StepRaisesExecutor("e", bus, raise_on=1).run(make_event())
    # Steps 0 and 2 succeed; step 1 raises — only 2 outputs
    assert result.outputs == ["output-0", "output-2"]


# ── prior_outputs accumulation ────────────────────────────────────────────────

async def test_prior_outputs_accumulate_across_steps(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedPlanExecutor(
        "e", bus,
        make_steps("a", "b", "c"),
        [("x", True), ("y", True), ("z", True)],
    )
    await executor.run(make_event())
    assert executor.prior_outputs_seen[0] == []
    assert executor.prior_outputs_seen[1] == ["x"]
    assert executor.prior_outputs_seen[2] == ["x", "y"]


async def test_prior_outputs_is_copy(tmp_path):
    """execute_step() receives a copy — mutating it should not affect the loop."""
    bus = EventBus(store_dir=tmp_path)
    mutations = []

    class MutatingExecutor(PlanExecutor):
        async def plan(self, event):
            return make_steps("a", "b")
        async def execute_step(self, event, step, prior_outputs):
            prior_outputs.append("injected")   # mutate the copy
            mutations.append(len(prior_outputs))
            return f"out-{step.index}", True

    await MutatingExecutor("e", bus).run(make_event())
    # Step 0 got [] → mutated to ["injected"] (len 1)
    # Step 1 got ["out-0"] (copy, not mutated by step 0's execute_step)
    assert mutations[0] == 1     # was []        → mutated to ["injected"]
    assert mutations[1] == 2     # was ["out-0"] → mutated to ["out-0", "injected"]


# ── follow_up event ───────────────────────────────────────────────────────────

async def test_follow_up_event_published_on_complete(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class FollowUpExecutor(PlanExecutor):
        async def plan(self, event):
            return make_steps("only")
        async def execute_step(self, event, step, prior_outputs):
            return "done", True
        async def follow_up(self, event, result):
            return event.caused("plan.done", self.name, {"status": result.status})

    await FollowUpExecutor("e", bus).run(make_event(correlation_id="corr-99"))
    stored = load_events("plan", store_dir=tmp_path)
    assert len(stored) == 1
    assert stored[0].event_type == "plan.done"
    assert stored[0].correlation_id == "corr-99"


async def test_follow_up_called_even_on_plan_failure(tmp_path):
    """follow_up() is called even when plan() raises."""
    bus = EventBus(store_dir=tmp_path)
    follow_up_results = []

    class TrackingPlanRaisesExecutor(PlanExecutor):
        async def plan(self, event): raise RuntimeError("boom")
        async def execute_step(self, event, step, prior_outputs): return None, True
        async def follow_up(self, event, result):
            follow_up_results.append(result.status)
            return None

    await TrackingPlanRaisesExecutor("e", bus).run(make_event())
    assert follow_up_results == ["failed"]


async def test_no_follow_up_event_when_none_returned(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = EmptyPlanExecutor("e", bus)   # default follow_up returns None
    await executor.run(make_event())
    stored = load_events("test", store_dir=tmp_path)
    assert all(e.event_type == "test.trigger" for e in stored)


# ── PlanStep and PlanResult fields ────────────────────────────────────────────

def test_plan_step_default_status():
    step = PlanStep(index=0, description="do something")
    assert step.status == "pending"


def test_plan_result_fields():
    steps = make_steps("a", "b")
    result = PlanResult(status="complete", steps=steps, outputs=["x", "y"])
    assert result.status == "complete"
    assert len(result.steps) == 2
    assert result.outputs == ["x", "y"]
