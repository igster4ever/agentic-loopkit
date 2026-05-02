import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.loops.ralf import (
    RALFExecutor, RALFResult, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH,
)
from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import load_events


def make_event(**kwargs) -> Event:
    return Event(event_type="test.trigger", source="test", payload={}, **kwargs)


def result(status="complete", confidence=0.9, output="done", summary="ok") -> RALFResult:
    return RALFResult(status=status, step_summary=summary, output=output, confidence=confidence)


class FixedResultExecutor(RALFExecutor):
    """Returns the same result on every act() call."""
    max_iterations = 5

    def __init__(self, name, bus, fixed: RALFResult):
        super().__init__(name, bus)
        self._fixed = fixed
        self.learn_calls: list[RALFResult] = []
        self.follow_up_calls: list[RALFResult] = []

    async def retrieve(self, event):
        return {"event": event}

    async def act(self, context, prior):
        return self._fixed

    async def learn(self, event, r):
        self.learn_calls.append(r)

    async def follow_up(self, event, r):
        self.follow_up_calls.append(r)
        return None


class StepSequenceExecutor(RALFExecutor):
    """Returns a pre-defined sequence of results."""
    max_iterations = 10

    def __init__(self, name, bus, steps: list[RALFResult]):
        super().__init__(name, bus)
        self._iter = iter(steps)
        self.learn_statuses: list[str] = []

    async def retrieve(self, event):
        return {}

    async def act(self, context, prior):
        return next(self._iter)

    async def learn(self, event, r):
        self.learn_statuses.append(r.status)


# ── Terminal status tests ────────────────────────────────────────────────────

async def test_complete_terminates_loop(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedResultExecutor("e", bus, result("complete"))
    final = await executor.run(make_event())
    assert final.status == "complete"
    assert len(executor.learn_calls) == 1


async def test_rejected_status_terminates_loop(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedResultExecutor("e", bus, result("rejected", confidence=0.5))
    final = await executor.run(make_event())
    assert final.status == "rejected"


async def test_error_status_terminates_loop(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedResultExecutor("e", bus, result("error", confidence=0.9))
    final = await executor.run(make_event())
    assert final.status == "error"


# ── Confidence rejection ─────────────────────────────────────────────────────

async def test_very_low_confidence_triggers_hard_reject(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedResultExecutor("e", bus, result("in_progress", confidence=0.2))
    final = await executor.run(make_event())
    assert final.status == "rejected"
    assert "0.2" in final.uncertainty


async def test_confidence_at_boundary_not_rejected(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedResultExecutor("e", bus, result("complete", confidence=CONFIDENCE_LOW))
    final = await executor.run(make_event())
    assert final.status == "complete"


async def test_confidence_just_below_boundary_is_rejected(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedResultExecutor("e", bus, result("in_progress", confidence=0.39))
    final = await executor.run(make_event())
    assert final.status == "rejected"


# ── Max iterations ───────────────────────────────────────────────────────────

async def test_max_iterations_produces_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class NeverDoneExecutor(RALFExecutor):
        max_iterations = 3
        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return result("in_progress", confidence=0.8)

    executor = NeverDoneExecutor("e", bus)
    final = await executor.run(make_event())
    assert final.status == "error"
    assert "3" in final.step_summary


# ── Learn is called after every step ─────────────────────────────────────────

async def test_learn_called_on_every_step(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = StepSequenceExecutor("e", bus, [
        result("in_progress", confidence=0.8),
        result("in_progress", confidence=0.8),
        result("complete",    confidence=0.9),
    ])
    await executor.run(make_event())
    assert executor.learn_statuses == ["in_progress", "in_progress", "complete"]


async def test_learn_called_after_confidence_reject(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = StepSequenceExecutor("e", bus, [
        result("in_progress", confidence=0.2),
    ])
    await executor.run(make_event())
    assert executor.learn_statuses == ["rejected"]


# ── Follow-up event ──────────────────────────────────────────────────────────

async def test_follow_up_event_published(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class FollowUpExecutor(RALFExecutor):
        max_iterations = 1
        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return result("complete", output="the-answer")
        async def follow_up(self, event, r):
            return event.caused("test.follow_up", "exec", {"output": r.output})

    await FollowUpExecutor("e", bus).run(
        make_event(correlation_id="corr-1")
    )
    stored = load_events("test", store_dir=tmp_path)
    follow_up = [e for e in stored if e.event_type == "test.follow_up"]
    assert len(follow_up) == 1
    assert follow_up[0].correlation_id == "corr-1"


async def test_no_follow_up_event_when_none_returned(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedResultExecutor("e", bus, result("complete"))
    await executor.run(make_event())
    # Only the trigger event in "test" stream — no follow-up
    stored = load_events("test", store_dir=tmp_path)
    assert all(e.event_type == "test.trigger" for e in stored)


# ── RALFResult helpers ───────────────────────────────────────────────────────

def test_ralf_result_is_terminal():
    assert result("complete").is_terminal
    assert result("rejected").is_terminal
    assert result("error").is_terminal
    assert not result("in_progress").is_terminal


def test_ralf_result_confidence_bands():
    assert result(confidence=0.9).confidence_band == "high"
    assert result(confidence=0.7).confidence_band == "medium"
    assert result(confidence=0.5).confidence_band == "low"
    assert result(confidence=0.2).confidence_band == "very_low"
