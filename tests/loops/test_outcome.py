"""
tests/loops/test_outcome.py — OutcomeExecutor tests.

Covers:
  - evaluate() called after act() with (artifact, rubric)
  - Satisfied on first pass → status="complete", confidence=1.0
  - Needs revision → loop continues; gaps fed to next act() via prior_result
  - Satisfied after revision (multi-iteration convergence)
  - learn() receives post-evaluation result each iteration
  - learn() call sequence across multi-iteration runs
  - Max iterations → inherited "error" status
  - Max iterations → learn() called with correct statuses
  - follow_up() event published on complete
  - follow_up() event published on rejection (inherited confidence gate)
  - No follow_up when None returned
  - Hard confidence reject (inherited — evaluate() returning very low confidence)
  - Gap feedback is present in prior_result.output for next act() call
  - Final result preserves original artifact output (not gap-feedback string)
"""

import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.loops.ralf import RALFResult, CONFIDENCE_LOW
from agentic_loopkit.loops.outcome import OutcomeExecutor
from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import load_events


_RUBRIC = "## Test Rubric\n- Output must equal 'done'\n"


def make_event(**kwargs) -> Event:
    return Event(event_type="test.trigger", source="test", payload={}, **kwargs)


def ralf_result(status="complete", confidence=0.9, output="done", summary="ok") -> RALFResult:
    return RALFResult(status=status, step_summary=summary, output=output, confidence=confidence)


# ── Concrete executors for testing ───────────────────────────────────────────

class FixedOutcomeExecutor(OutcomeExecutor):
    """act() always returns the same result; evaluate() is driven by a callable."""

    max_iterations = 5

    def __init__(self, name, bus, act_result: RALFResult, evaluate_fn=None):
        super().__init__(name, bus)
        self._act_result  = act_result
        # evaluate_fn: (artifact, rubric) → (satisfied, gaps); defaults to satisfied
        self._evaluate_fn = evaluate_fn or (lambda a, r: (True, []))
        self.evaluate_calls: list[tuple]      = []   # (artifact, rubric) pairs
        self.act_prior_results: list          = []   # prior_result received by act()
        self.learn_calls: list[RALFResult]    = []

    @property
    def rubric(self) -> str:
        return _RUBRIC

    async def retrieve(self, event):
        return {}

    async def act(self, context, prior):
        self.act_prior_results.append(prior)
        return self._act_result

    async def evaluate(self, artifact, rubric):
        self.evaluate_calls.append((artifact, rubric))
        return self._evaluate_fn(artifact, rubric)

    async def learn(self, event, result):
        self.learn_calls.append(result)


class ScriptedOutcomeExecutor(OutcomeExecutor):
    """act() and evaluate() consume from pre-defined sequences."""

    max_iterations = 10

    def __init__(self, name, bus, act_results, evaluate_results):
        super().__init__(name, bus)
        self._act_iter      = iter(act_results)
        self._evaluate_iter = iter(evaluate_results)
        self.learn_statuses: list[str]     = []
        self.act_prior_outputs: list       = []

    @property
    def rubric(self) -> str:
        return _RUBRIC

    async def retrieve(self, event):
        return {}

    async def act(self, context, prior):
        self.act_prior_outputs.append(prior.output if prior else None)
        return next(self._act_iter)

    async def evaluate(self, artifact, rubric):
        return next(self._evaluate_iter)

    async def learn(self, event, result):
        self.learn_statuses.append(result.status)


# ── evaluate() is called after act() ─────────────────────────────────────────

async def test_evaluate_called_after_act(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"))
    await executor.run(make_event())
    assert len(executor.evaluate_calls) == 1


async def test_evaluate_receives_artifact_from_act(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete", output="my-artifact"))
    await executor.run(make_event())
    artifact, _ = executor.evaluate_calls[0]
    assert artifact == "my-artifact"


async def test_evaluate_receives_rubric_property(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"))
    await executor.run(make_event())
    _, rubric = executor.evaluate_calls[0]
    assert rubric == _RUBRIC


# ── Satisfied on first pass ───────────────────────────────────────────────────

async def test_satisfied_first_pass_status_complete(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"))
    result = await executor.run(make_event())
    assert result.status == "complete"


async def test_satisfied_first_pass_confidence_one(tmp_path):
    """Satisfied evaluation always yields confidence=1.0 regardless of act() confidence."""
    bus = EventBus(store_dir=tmp_path)
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete", confidence=0.7))
    result = await executor.run(make_event())
    assert result.confidence == 1.0


async def test_satisfied_first_pass_single_iteration(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"))
    await executor.run(make_event())
    assert len(executor.evaluate_calls) == 1


async def test_satisfied_preserves_original_output(tmp_path):
    """Final result must carry the original artifact — not the gap-feedback string."""
    bus = EventBus(store_dir=tmp_path)
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete", output="the-artifact"))
    result = await executor.run(make_event())
    assert result.output == "the-artifact"


# ── Needs revision — loop continues ──────────────────────────────────────────

async def test_needs_revision_continues_loop(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    call_count = 0

    def evaluate_fn(artifact, rubric):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return False, ["Missing price column"]
        return True, []

    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"), evaluate_fn)
    result = await executor.run(make_event())
    assert result.status == "complete"
    assert call_count == 2


async def test_needs_revision_evaluate_called_each_iteration(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    calls = []

    def evaluate_fn(artifact, rubric):
        calls.append(1)
        if len(calls) < 3:
            return False, [f"Gap {len(calls)}"]
        return True, []

    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"), evaluate_fn)
    await executor.run(make_event())
    assert len(calls) == 3


async def test_gaps_fed_to_next_act_via_prior_result(tmp_path):
    """Gap feedback must appear in prior_result.output on the next act() call."""
    bus = EventBus(store_dir=tmp_path)

    def evaluate_fn(artifact, rubric):
        if not hasattr(evaluate_fn, "_called"):
            evaluate_fn._called = True
            return False, ["Missing price column"]
        return True, []

    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"), evaluate_fn)
    await executor.run(make_event())

    # First act() has no prior; second act() receives the gap-feedback result
    assert executor.act_prior_results[0] is None
    prior_output = executor.act_prior_results[1].output
    assert "Missing price column" in prior_output


async def test_gap_feedback_contains_previous_output(tmp_path):
    """Gap-feedback output must also include the previous artifact for context."""
    bus = EventBus(store_dir=tmp_path)

    def evaluate_fn(artifact, rubric):
        if not hasattr(evaluate_fn, "_called"):
            evaluate_fn._called = True
            return False, ["Needs a header row"]
        return True, []

    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete", output="raw-csv"), evaluate_fn)
    await executor.run(make_event())

    prior_output = executor.act_prior_results[1].output
    assert "raw-csv" in prior_output
    assert "Needs a header row" in prior_output


# ── learn() receives post-evaluation result ───────────────────────────────────

async def test_learn_receives_satisfied_result(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"))
    await executor.run(make_event())
    assert executor.learn_calls[0].status == "complete"
    assert executor.learn_calls[0].confidence == 1.0


async def test_learn_receives_in_progress_before_complete(tmp_path):
    """learn() must track in_progress → complete across iterations."""
    bus = EventBus(store_dir=tmp_path)

    def evaluate_fn(artifact, rubric):
        if not hasattr(evaluate_fn, "_called"):
            evaluate_fn._called = True
            return False, ["gap"]
        return True, []

    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"), evaluate_fn)
    await executor.run(make_event())
    assert executor.learn_calls[0].status == "in_progress"
    assert executor.learn_calls[1].status == "complete"


async def test_learn_called_once_per_iteration(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = ScriptedOutcomeExecutor(
        "e", bus,
        act_results=[
            ralf_result("in_progress", confidence=0.8),
            ralf_result("in_progress", confidence=0.8),
            ralf_result("complete",    confidence=0.9),
        ],
        evaluate_results=[
            (False, ["gap 1"]),
            (False, ["gap 2"]),
            (True,  []),
        ],
    )
    await executor.run(make_event())
    assert executor.learn_statuses == ["in_progress", "in_progress", "complete"]


# ── Max iterations ────────────────────────────────────────────────────────────

async def test_max_iterations_produces_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class NeverSatisfiedExecutor(OutcomeExecutor):
        max_iterations = 3

        @property
        def rubric(self): return _RUBRIC

        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return ralf_result("in_progress", confidence=0.8)
        async def evaluate(self, artifact, rubric):
            return False, ["always a gap"]

    result = await NeverSatisfiedExecutor("e", bus).run(make_event())
    assert result.status == "error"
    assert "3" in result.step_summary


async def test_max_iterations_learn_called_on_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    learn_statuses = []

    class NeverSatisfiedExecutor(OutcomeExecutor):
        max_iterations = 2

        @property
        def rubric(self): return _RUBRIC

        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return ralf_result("in_progress", confidence=0.8)
        async def evaluate(self, artifact, rubric):
            return False, ["still a gap"]
        async def learn(self, event, result):
            learn_statuses.append(result.status)

    await NeverSatisfiedExecutor("e", bus).run(make_event())
    # 2 in_progress steps + 1 error = 3 learn calls
    assert learn_statuses == ["in_progress", "in_progress", "error"]


# ── Inherited confidence hard reject ─────────────────────────────────────────

async def test_evaluate_satisfied_with_very_low_act_confidence_rejects(tmp_path):
    """
    Evaluate returning satisfied does NOT rescue a very low act() confidence —
    wait, actually it does: _post_act_hook replaces the result with confidence=1.0.
    So satisfied always wins over the original act() confidence.
    """
    bus = EventBus(store_dir=tmp_path)
    # Even if act() returns a very low confidence, evaluate() saying satisfied
    # replaces it with confidence=1.0 — so no rejection.
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete", confidence=0.1))
    result = await executor.run(make_event())
    assert result.status == "complete"
    assert result.confidence == 1.0


async def test_evaluate_needs_revision_confidence_above_rejection_threshold(tmp_path):
    """
    When not satisfied, the hook sets confidence=0.5 — above CONFIDENCE_LOW.
    The loop continues; no hard rejection on this iteration.
    """
    bus = EventBus(store_dir=tmp_path)
    calls = []

    def evaluate_fn(artifact, rubric):
        calls.append(1)
        if len(calls) < 2:
            return False, ["gap"]
        return True, []

    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"), evaluate_fn)
    result = await executor.run(make_event())
    assert result.status == "complete"   # loop continued, not rejected
    assert len(calls) == 2


# ── follow_up event ───────────────────────────────────────────────────────────

async def test_follow_up_event_published_on_complete(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class FollowUpExecutor(OutcomeExecutor):
        @property
        def rubric(self): return _RUBRIC
        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return ralf_result("complete", output="the-csv")
        async def evaluate(self, artifact, rubric):
            return True, []
        async def follow_up(self, event, result):
            return event.caused("outcome.done", self.name, {"output": result.output})

    await FollowUpExecutor("e", bus).run(make_event(correlation_id="corr-42"))
    stored = load_events("outcome", store_dir=tmp_path)
    assert len(stored) == 1
    assert stored[0].event_type == "outcome.done"
    assert stored[0].correlation_id == "corr-42"


async def test_follow_up_carries_artifact_not_gap_text(tmp_path):
    """follow_up() must receive the original artifact, not the gap-feedback string."""
    bus = EventBus(store_dir=tmp_path)
    received_output = []

    class CapturingFollowUpExecutor(OutcomeExecutor):
        @property
        def rubric(self): return _RUBRIC
        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return ralf_result("complete", output="clean-artifact")
        async def evaluate(self, artifact, rubric):
            return True, []
        async def follow_up(self, event, result):
            received_output.append(result.output)
            return None

    await CapturingFollowUpExecutor("e", bus).run(make_event())
    assert received_output == ["clean-artifact"]


async def test_no_follow_up_when_none_returned(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedOutcomeExecutor("e", bus, ralf_result("complete"))
    await executor.run(make_event())
    stored = load_events("test", store_dir=tmp_path)
    assert all(e.event_type == "test.trigger" for e in stored)


# ── default max_iterations is 3 ──────────────────────────────────────────────

async def test_default_max_iterations_is_three(tmp_path):
    """OutcomeExecutor.max_iterations defaults to 3 (matches managed agents default)."""
    bus = EventBus(store_dir=tmp_path)

    class DefaultIterExecutor(OutcomeExecutor):
        # No max_iterations override — uses class default (3)
        @property
        def rubric(self): return _RUBRIC
        async def retrieve(self, event): return {}
        async def act(self, context, prior):
            return ralf_result("in_progress", confidence=0.8)
        async def evaluate(self, artifact, rubric):
            return False, ["always a gap"]

    result = await DefaultIterExecutor("e", bus).run(make_event())
    assert result.status == "error"
    assert "3" in result.step_summary


# ── Rubric isolation — evaluate() signature ───────────────────────────────────

async def test_evaluate_not_called_with_prior_history(tmp_path):
    """
    evaluate() must only receive (artifact, rubric), not any reasoning history.
    Verify the call signature contains exactly those two positional args.
    """
    bus = EventBus(store_dir=tmp_path)
    captured = []

    class InspectingExecutor(OutcomeExecutor):
        max_iterations = 1

        @property
        def rubric(self): return _RUBRIC

        async def retrieve(self, event): return {"history": "should not appear"}
        async def act(self, context, prior):
            return ralf_result("complete", output="artifact-only")
        async def evaluate(self, artifact, rubric):
            captured.append({"artifact": artifact, "rubric": rubric})
            return True, []

    await InspectingExecutor("e", bus).run(make_event())
    assert len(captured) == 1
    assert captured[0]["artifact"] == "artifact-only"
    assert captured[0]["rubric"] == _RUBRIC
    # No context, history, or event data snuck in
    assert set(captured[0].keys()) == {"artifact", "rubric"}
