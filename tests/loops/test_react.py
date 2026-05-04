import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.loops.react import ReActExecutor, ReActResult, ReActStep
from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import load_events


def make_event(**kwargs) -> Event:
    return Event(event_type="test.trigger", source="test", payload={}, **kwargs)


# ── Concrete executors for testing ───────────────────────────────────────────

class ScriptedExecutor(ReActExecutor):
    """
    Runs a scripted sequence: each call to think() pops from the front of
    `steps`.  Each step is (action, action_input, observation).
    Use action="done" as the terminal step.
    """

    def __init__(self, name, bus, steps: list[tuple], *, max_steps: int = 10):
        super().__init__(name, bus)
        self.max_steps = max_steps
        self._steps = list(steps)
        self.on_step_calls: list[ReActStep] = []

    async def think(self, event, trace):
        action, action_input, _obs = self._steps[len(trace)]
        thought = f"step {len(trace) + 1}"
        return thought, action, action_input

    async def execute(self, action, action_input):
        idx = len([s for s in self._steps[:] if s[0] != "done"])
        # find the observation for the current non-done action in sequence
        for i, (a, _inp, obs) in enumerate(self._steps):
            if a == action and obs is not None:
                self._steps[i] = (a, _inp, None)  # consume
                return obs
        return ""

    async def on_step(self, step):
        self.on_step_calls.append(step)


class EchoExecutor(ReActExecutor):
    """Simple executor: does one tool call, then done."""
    max_steps = 5
    tool_called = False

    async def think(self, event, trace):
        if not trace:
            return "checking", "lookup", {"key": "x"}
        return "got it", "done", {"result": trace[0].observation}

    async def execute(self, action, action_input):
        self.tool_called = True
        return f"found:{action_input['key']}"

    async def on_step(self, step):
        pass


class ImmediateDoneExecutor(ReActExecutor):
    """Returns done on the very first think() call."""
    max_steps = 5

    async def think(self, event, trace):
        return "already know", "done", {"answer": 42}

    async def execute(self, action, action_input):
        raise AssertionError("execute() should not be called when action='done'")


class NeverDoneExecutor(ReActExecutor):
    """Always returns a tool action — never terminates by "done"."""
    max_steps = 3

    async def think(self, event, trace):
        return "still thinking", "search", {}

    async def execute(self, action, action_input):
        return "more results"


class ThinkErrorExecutor(ReActExecutor):
    """Raises in think()."""
    max_steps = 5

    async def think(self, event, trace):
        raise RuntimeError("LLM unavailable")

    async def execute(self, action, action_input):
        return ""


class ExecuteErrorExecutor(ReActExecutor):
    """Raises in execute()."""
    max_steps = 5

    async def think(self, event, trace):
        return "thinking", "tool", {}

    async def execute(self, action, action_input):
        raise ConnectionError("API unreachable")


# ── Basic happy path ──────────────────────────────────────────────────────────

async def test_complete_when_done_action_returned(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = ImmediateDoneExecutor("e", bus)
    result = await executor.run(make_event())
    assert result.status == "complete"
    assert result.is_complete


async def test_answer_is_action_input_on_done(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = ImmediateDoneExecutor("e", bus)
    result = await executor.run(make_event())
    assert result.answer == {"answer": 42}


async def test_trace_has_done_step_on_immediate_done(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = ImmediateDoneExecutor("e", bus)
    result = await executor.run(make_event())
    assert len(result.trace) == 1
    assert result.trace[0].action == "done"
    assert result.trace[0].observation == ""


async def test_two_step_trace(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = EchoExecutor("e", bus)
    result = await executor.run(make_event())
    assert result.status == "complete"
    assert len(result.trace) == 2
    assert result.trace[0].action == "lookup"
    assert result.trace[0].observation == "found:x"
    assert result.trace[1].action == "done"


async def test_execute_not_called_on_done(tmp_path):
    """execute() must never be called when action="done"."""
    bus = EventBus(store_dir=tmp_path)
    executor = ImmediateDoneExecutor("e", bus)
    # ImmediateDoneExecutor raises AssertionError in execute() — test passes if no error
    result = await executor.run(make_event())
    assert result.is_complete


# ── Max steps ─────────────────────────────────────────────────────────────────

async def test_max_steps_reached_status(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = NeverDoneExecutor("e", bus)
    result = await executor.run(make_event())
    assert result.status == "max_steps_reached"
    assert result.answer is None


async def test_max_steps_trace_length(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = NeverDoneExecutor("e", bus)
    result = await executor.run(make_event())
    assert len(result.trace) == 3   # max_steps = 3


# ── Error handling ────────────────────────────────────────────────────────────

async def test_exception_in_think_produces_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    result = await ThinkErrorExecutor("e", bus).run(make_event())
    assert result.status == "error"
    assert result.answer is None


async def test_exception_in_execute_produces_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    result = await ExecuteErrorExecutor("e", bus).run(make_event())
    assert result.status == "error"


async def test_trace_preserved_on_error(tmp_path):
    """Trace contains steps completed before the error."""
    bus = EventBus(store_dir=tmp_path)

    class ErrorOnSecondStep(ReActExecutor):
        max_steps = 5
        async def think(self, event, trace):
            if not trace:
                return "first", "tool", {}
            raise RuntimeError("oops")
        async def execute(self, action, action_input):
            return "obs"

    result = await ErrorOnSecondStep("e", bus).run(make_event())
    assert result.status == "error"
    assert len(result.trace) == 1   # first step completed before error


# ── on_step hook ──────────────────────────────────────────────────────────────

async def test_on_step_called_for_intermediate_and_done(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = EchoExecutor("e", bus)

    calls: list[ReActStep] = []

    async def track(step):
        calls.append(step)

    executor.on_step = track  # type: ignore[method-assign]
    await executor.run(make_event())

    assert len(calls) == 2
    assert calls[0].action == "lookup"
    assert calls[1].action == "done"


async def test_on_step_receives_correct_step_numbers(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = EchoExecutor("e", bus)
    calls: list[ReActStep] = []
    executor.on_step = lambda s: calls.append(s)  # type: ignore[method-assign]

    # on_step is not async here — need async wrapper
    async def async_track(step):
        calls.append(step)

    executor.on_step = async_track  # type: ignore[method-assign]
    await executor.run(make_event())
    assert [s.step_number for s in calls] == [1, 2]


# ── follow_up event ───────────────────────────────────────────────────────────

async def test_follow_up_event_published(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class FollowUpExecutor(ReActExecutor):
        max_steps = 5
        async def think(self, event, trace):
            return "done thinking", "done", "the-answer"
        async def execute(self, action, action_input):
            return ""
        async def follow_up(self, event, result):
            if result.is_complete:
                return event.caused("react.complete", self.name, {"answer": result.answer})
            return None

    await FollowUpExecutor("e", bus).run(make_event(correlation_id="corr-42"))

    stored = load_events("react", store_dir=tmp_path)
    assert len(stored) == 1
    assert stored[0].event_type == "react.complete"
    assert stored[0].correlation_id == "corr-42"
    assert stored[0].payload["answer"] == "the-answer"


async def test_no_follow_up_event_when_none_returned(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = ImmediateDoneExecutor("e", bus)  # default follow_up returns None
    await executor.run(make_event())
    # Only the original trigger event should exist in the test stream
    stored = load_events("test", store_dir=tmp_path)
    assert all(e.event_type == "test.trigger" for e in stored)


# ── ReActResult helpers ───────────────────────────────────────────────────────

def test_react_result_is_complete():
    assert ReActResult(status="complete", answer="x").is_complete
    assert not ReActResult(status="error", answer=None).is_complete
    assert not ReActResult(status="max_steps_reached", answer=None).is_complete


def test_react_step_fields():
    step = ReActStep(
        step_number=1, thought="thinking", action="search",
        action_input={"q": "events"}, observation="found 3",
    )
    assert step.step_number == 1
    assert step.thought == "thinking"
    assert step.action == "search"
    assert step.observation == "found 3"
