import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.loops.ralf import RALFResult, CONFIDENCE_LOW
from agentic_loopkit.loops.reflexion import ReflexionExecutor
from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import load_events


def make_event(**kwargs) -> Event:
    return Event(event_type="test.trigger", source="test", payload={}, **kwargs)


def ralf_result(status="complete", confidence=0.9, output="done", summary="ok") -> RALFResult:
    return RALFResult(status=status, step_summary=summary, output=output, confidence=confidence)


# ── Concrete executors for testing ───────────────────────────────────────────

class FixedReflexionExecutor(ReflexionExecutor):
    """act() always returns the same result; critique() is driven by a callable."""

    max_iterations = 5

    def __init__(self, name, bus, act_result: RALFResult, critique_fn=None):
        super().__init__(name, bus)
        self._act_result = act_result
        # critique_fn: RALFResult → (RALFResult, str); defaults to passthrough
        self._critique_fn = critique_fn or (lambda r: (r, "ok"))
        self.critique_calls: list[RALFResult] = []
        self.learn_calls:    list[RALFResult] = []

    async def retrieve(self, event):
        return {}

    async def act(self, context, prior):
        return self._act_result

    async def critique(self, event, result):
        self.critique_calls.append(result)
        return self._critique_fn(result)

    async def learn(self, event, result):
        self.learn_calls.append(result)


class ScriptedReflexionExecutor(ReflexionExecutor):
    """act() and critique() consume from pre-defined sequences."""

    max_iterations = 10

    def __init__(self, name, bus, act_results, critique_results):
        super().__init__(name, bus)
        self._act_iter  = iter(act_results)
        self._crit_iter = iter(critique_results)
        self.learn_statuses: list[str] = []

    async def retrieve(self, event):
        return {}

    async def act(self, context, prior):
        return next(self._act_iter)

    async def critique(self, event, result):
        return next(self._crit_iter)

    async def learn(self, event, result):
        self.learn_statuses.append(result.status)


# ── critique() is called after act() ─────────────────────────────────────────

async def test_critique_called_after_act(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    act_r = ralf_result("complete", confidence=0.9)
    executor = FixedReflexionExecutor("e", bus, act_r)
    await executor.run(make_event())
    assert len(executor.critique_calls) == 1
    assert executor.critique_calls[0] is act_r


async def test_critique_receives_act_result(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    act_r = ralf_result("complete", output="my-output")
    executor = FixedReflexionExecutor("e", bus, act_r)
    await executor.run(make_event())
    assert executor.critique_calls[0].output == "my-output"


# ── Passthrough critique (no revision) ───────────────────────────────────────

async def test_passthrough_critique_completes(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedReflexionExecutor("e", bus, ralf_result("complete"))
    result = await executor.run(make_event())
    assert result.status == "complete"


async def test_passthrough_critique_single_iteration(tmp_path):
    """Critique that passes the result through should terminate in one iteration."""
    bus = EventBus(store_dir=tmp_path)
    executor = FixedReflexionExecutor("e", bus, ralf_result("complete"))
    await executor.run(make_event())
    assert len(executor.critique_calls) == 1


# ── Critique forces another iteration ────────────────────────────────────────

async def test_critique_can_force_additional_iteration(tmp_path):
    """Critique lowers confidence on first call; passes on second."""
    bus = EventBus(store_dir=tmp_path)

    call_count = 0

    def critique_fn(result):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Downgrade to in_progress — not terminal, loop continues
            return ralf_result("in_progress", confidence=0.6), "needs more work"
        return ralf_result("complete", confidence=0.9), "good enough"

    executor = FixedReflexionExecutor("e", bus, ralf_result("complete"), critique_fn)
    result = await executor.run(make_event())
    assert result.status == "complete"
    assert call_count == 2


async def test_critique_iterations_count(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    calls = []

    def critique_fn(result):
        calls.append(1)
        if len(calls) < 3:
            return ralf_result("in_progress", confidence=0.7), "not yet"
        return ralf_result("complete", confidence=0.9), "done"

    executor = FixedReflexionExecutor("e", bus, ralf_result("complete"), critique_fn)
    result = await executor.run(make_event())
    assert len(calls) == 3
    assert result.status == "complete"


# ── learn() receives post-critique result ────────────────────────────────────

async def test_learn_receives_critique_revised_result(tmp_path):
    """learn() must see the critique-revised result, not the raw act() result."""
    bus = EventBus(store_dir=tmp_path)
    act_r = ralf_result("complete", confidence=0.9, output="act-output")
    revised = ralf_result("complete", confidence=0.95, output="revised-output")
    executor = FixedReflexionExecutor(
        "e", bus, act_r, critique_fn=lambda r: (revised, "revised")
    )
    await executor.run(make_event())
    assert executor.learn_calls[0].output == "revised-output"


async def test_learn_called_once_per_iteration_with_critique(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = ScriptedReflexionExecutor(
        "e", bus,
        act_results=[
            ralf_result("in_progress", confidence=0.8),
            ralf_result("in_progress", confidence=0.8),
            ralf_result("complete",    confidence=0.9),
        ],
        critique_results=[
            (ralf_result("in_progress", confidence=0.8), "keep going"),
            (ralf_result("in_progress", confidence=0.8), "keep going"),
            (ralf_result("complete",    confidence=0.9), "good"),
        ],
    )
    await executor.run(make_event())
    assert executor.learn_statuses == ["in_progress", "in_progress", "complete"]


# ── Post-critique confidence rejection ───────────────────────────────────────

async def test_critique_low_confidence_triggers_hard_reject(tmp_path):
    """Critique returning confidence < CONFIDENCE_LOW → hard reject."""
    bus = EventBus(store_dir=tmp_path)
    executor = FixedReflexionExecutor(
        "e", bus,
        ralf_result("complete", confidence=0.9),
        critique_fn=lambda r: (ralf_result("in_progress", confidence=0.2), "terrible"),
    )
    result = await executor.run(make_event())
    assert result.status == "rejected"
    assert "0.2" in result.uncertainty


async def test_critique_at_confidence_boundary_not_rejected(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedReflexionExecutor(
        "e", bus,
        ralf_result("complete"),
        critique_fn=lambda r: (ralf_result("complete", confidence=CONFIDENCE_LOW), "ok"),
    )
    result = await executor.run(make_event())
    assert result.status == "complete"


async def test_critique_just_below_boundary_is_rejected(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedReflexionExecutor(
        "e", bus,
        ralf_result("complete"),
        critique_fn=lambda r: (ralf_result("in_progress", confidence=0.39), "bad"),
    )
    result = await executor.run(make_event())
    assert result.status == "rejected"


async def test_critique_rejection_learn_called_with_rejected(tmp_path):
    """learn() receives the rejected result after confidence-based rejection."""
    bus = EventBus(store_dir=tmp_path)
    executor = FixedReflexionExecutor(
        "e", bus,
        ralf_result("complete"),
        critique_fn=lambda r: (ralf_result("in_progress", confidence=0.1), "reject me"),
    )
    await executor.run(make_event())
    assert executor.learn_calls[0].status == "rejected"


# ── Max iterations ────────────────────────────────────────────────────────────

async def test_max_iterations_produces_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class NeverDoneExecutor(ReflexionExecutor):
        max_iterations = 3
        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return ralf_result("in_progress", confidence=0.8)
        async def critique(self, event, result):
            return ralf_result("in_progress", confidence=0.8), "not done yet"

    result = await NeverDoneExecutor("e", bus).run(make_event())
    assert result.status == "error"
    assert "3" in result.step_summary


async def test_max_iterations_learn_called_on_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    learn_statuses = []

    class NeverDoneExecutor(ReflexionExecutor):
        max_iterations = 2
        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return ralf_result("in_progress", confidence=0.8)
        async def critique(self, event, result):
            return ralf_result("in_progress", confidence=0.8), "keep going"
        async def learn(self, event, result):
            learn_statuses.append(result.status)

    await NeverDoneExecutor("e", bus).run(make_event())
    # 2 in_progress steps + 1 error = 3 learn calls
    assert learn_statuses == ["in_progress", "in_progress", "error"]


# ── follow_up event ───────────────────────────────────────────────────────────

async def test_follow_up_event_published_on_complete(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class FollowUpExecutor(ReflexionExecutor):
        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return ralf_result("complete", output="the-answer")
        async def critique(self, event, result):
            return result, "good"
        async def follow_up(self, event, result):
            return event.caused("reflexion.done", self.name, {"output": result.output})

    await FollowUpExecutor("e", bus).run(make_event(correlation_id="corr-42"))
    stored = load_events("reflexion", store_dir=tmp_path)
    assert len(stored) == 1
    assert stored[0].event_type == "reflexion.done"
    assert stored[0].correlation_id == "corr-42"


async def test_follow_up_event_published_on_rejection(tmp_path):
    """follow_up() fires even when the result is rejected."""
    bus = EventBus(store_dir=tmp_path)

    class FollowUpOnRejectExecutor(ReflexionExecutor):
        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return ralf_result("complete")
        async def critique(self, event, result):
            return ralf_result("in_progress", confidence=0.1), "reject"
        async def follow_up(self, event, result):
            return event.caused("reflexion.rejected", self.name, {"status": result.status})

    await FollowUpOnRejectExecutor("e", bus).run(make_event(correlation_id="corr-99"))
    stored = load_events("reflexion", store_dir=tmp_path)
    assert stored[0].event_type == "reflexion.rejected"
    assert stored[0].correlation_id == "corr-99"


async def test_no_follow_up_when_none_returned(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedReflexionExecutor("e", bus, ralf_result("complete"))
    await executor.run(make_event())
    stored = load_events("test", store_dir=tmp_path)
    assert all(e.event_type == "test.trigger" for e in stored)


# ── Critique output wins over act output ─────────────────────────────────────

async def test_final_result_is_critique_output_not_act_output(tmp_path):
    """The returned result must be the post-critique version."""
    bus = EventBus(store_dir=tmp_path)
    act_r     = ralf_result("complete", output="raw",      confidence=0.7)
    revised_r = ralf_result("complete", output="polished", confidence=0.95)
    executor = FixedReflexionExecutor(
        "e", bus, act_r, critique_fn=lambda r: (revised_r, "upgraded")
    )
    result = await executor.run(make_event())
    assert result.output == "polished"
    assert result.confidence == 0.95


# ── Inherited RALF behaviour still works ─────────────────────────────────────

async def test_rejected_by_act_status_terminates(tmp_path):
    """act() returning status='rejected' still terminates (no critique needed to reject)."""
    bus = EventBus(store_dir=tmp_path)
    # critique passthrough — rejection originates from act()
    executor = FixedReflexionExecutor("e", bus, ralf_result("rejected", confidence=0.6))
    result = await executor.run(make_event())
    assert result.status == "rejected"
    assert len(executor.critique_calls) == 1


async def test_error_status_from_act_terminates(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedReflexionExecutor("e", bus, ralf_result("error", confidence=0.9))
    result = await executor.run(make_event())
    assert result.status == "error"
