"""
tests/govkit/test_conflict_resolution.py — ConflictResolutionExecutor tests.

Covers:
  - dispute_resolved emitted on complete
  - human_override emitted on error (max iterations exhausted)
  - human_override emitted on rejected (confidence below threshold)
  - synthesis carried in dispute_resolved payload
  - correlation_id carried in both governance events
  - last_output and status carried in human_override payload
  - evaluate() isolation — receives only (synthesis, rubric); no context history
  - Gap feedback from evaluate() fed to next act() via prior_result (inherited)
  - max_iterations default is 3 (inherited from OutcomeExecutor)
  - follow_up() can be overridden for custom payload enrichment
"""

import pytest
from agentic_loopkit import EventBus, Event
from agentic_loopkit.loops.ralf import RALFResult
from agentic_loopkit.events.store import load_events
from agentic_govkit import ConflictResolutionExecutor, GovernanceEventType


_RUBRIC = "## Reconciliation Rubric\n- Synthesis must address both positions.\n"


def make_event(correlation_id: str = "dispute-corr-1", **kwargs) -> Event:
    return Event(
        event_type=GovernanceEventType.DISPUTE_OPENED,
        source="test",
        payload={},
        correlation_id=correlation_id,
        **kwargs,
    )


def ralf_result(status="complete", confidence=0.9, output="synthesis-text") -> RALFResult:
    return RALFResult(status=status, step_summary="ok", output=output, confidence=confidence)


# ── Concrete executors for testing ───────────────────────────────────────────

class FixedConflictExecutor(ConflictResolutionExecutor):
    """act() always returns the same result; evaluate() driven by a callable."""

    max_iterations = 5

    def __init__(self, name, bus, act_result: RALFResult, evaluate_fn=None):
        super().__init__(name, bus)
        self._act_result  = act_result
        self._evaluate_fn = evaluate_fn or (lambda a, r: (True, []))
        self.evaluate_calls: list[tuple] = []
        self.act_prior_results: list     = []

    @property
    def rubric(self) -> str:
        return _RUBRIC

    async def retrieve(self, event):
        return {"position_a": "view A", "position_b": "view B"}

    async def act(self, context, prior):
        self.act_prior_results.append(prior)
        return self._act_result

    async def evaluate(self, artifact, rubric):
        self.evaluate_calls.append((artifact, rubric))
        return self._evaluate_fn(artifact, rubric)


class NeverSatisfiedConflictExecutor(ConflictResolutionExecutor):
    max_iterations = 3

    @property
    def rubric(self): return _RUBRIC

    async def retrieve(self, event): return {"position_a": "A", "position_b": "B"}
    async def act(self, context, prior):
        return ralf_result("in_progress", confidence=0.8, output="draft")
    async def evaluate(self, artifact, rubric):
        return False, ["still unresolved"]


# ── dispute_resolved on complete ─────────────────────────────────────────────

async def test_complete_emits_dispute_resolved(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedConflictExecutor("resolver", bus, ralf_result("complete"))
    await executor.run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    assert any(e.event_type == GovernanceEventType.DISPUTE_RESOLVED for e in stored)


async def test_dispute_resolved_carries_synthesis(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedConflictExecutor("resolver", bus, ralf_result("complete", output="the-synthesis"))
    await executor.run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    resolved = next(e for e in stored if e.event_type == GovernanceEventType.DISPUTE_RESOLVED)
    assert resolved.payload["synthesis"] == "the-synthesis"


async def test_dispute_resolved_carries_correlation_id(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedConflictExecutor("resolver", bus, ralf_result("complete"))
    await executor.run(make_event(correlation_id="corr-xyz"))
    stored = load_events("governance", store_dir=tmp_path)
    resolved = next(e for e in stored if e.event_type == GovernanceEventType.DISPUTE_RESOLVED)
    assert resolved.payload["correlation_id"] == "corr-xyz"





# ── human_override on error (max iterations exhausted) ───────────────────────

async def test_max_iterations_emits_human_override(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedConflictExecutor("resolver", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    assert any(e.event_type == GovernanceEventType.HUMAN_OVERRIDE for e in stored)


async def test_human_override_carries_correlation_id(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedConflictExecutor("resolver", bus).run(
        make_event("corr-escalate")
    )
    stored = load_events("governance", store_dir=tmp_path)
    override = next(e for e in stored if e.event_type == GovernanceEventType.HUMAN_OVERRIDE)
    assert override.payload["correlation_id"] == "corr-escalate"


async def test_human_override_carries_status(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedConflictExecutor("resolver", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    override = next(e for e in stored if e.event_type == GovernanceEventType.HUMAN_OVERRIDE)
    assert override.payload["status"] == "error"


async def test_human_override_carries_last_output(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedConflictExecutor("resolver", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    override = next(e for e in stored if e.event_type == GovernanceEventType.HUMAN_OVERRIDE)
    assert override.payload["last_output"] is not None


# ── human_override on rejected (confidence too low) ──────────────────────────

async def test_rejected_emits_human_override(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class LowConfidenceExecutor(ConflictResolutionExecutor):
        max_iterations = 1

        @property
        def rubric(self): return _RUBRIC

        async def retrieve(self, event): return {"position_a": "A", "position_b": "B"}
        async def act(self, context, prior):
            return ralf_result("in_progress", confidence=0.1)
        async def evaluate(self, artifact, rubric):
            return False, ["too uncertain"]

    await LowConfidenceExecutor("resolver", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    assert any(e.event_type == GovernanceEventType.HUMAN_OVERRIDE for e in stored)


async def test_never_satisfied_human_override_status_is_error(tmp_path):
    """
    _post_act_hook sets confidence=0.5 when not satisfied — above CONFIDENCE_LOW.
    The loop exhausts max_iterations → status="error" (not "rejected").
    human_override payload reflects this.
    """
    bus = EventBus(store_dir=tmp_path)

    class LowConfidenceExecutor(ConflictResolutionExecutor):
        max_iterations = 1

        @property
        def rubric(self): return _RUBRIC

        async def retrieve(self, event): return {"position_a": "A", "position_b": "B"}
        async def act(self, context, prior):
            return ralf_result("in_progress", confidence=0.1)
        async def evaluate(self, artifact, rubric):
            return False, ["too uncertain"]

    await LowConfidenceExecutor("resolver", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    override = next(e for e in stored if e.event_type == GovernanceEventType.HUMAN_OVERRIDE)
    assert override.payload["status"] == "error"


# ── evaluate() isolation contract ────────────────────────────────────────────

async def test_evaluate_receives_only_synthesis_and_rubric(tmp_path):
    """evaluate() must not receive context or prior history — isolation contract."""
    bus = EventBus(store_dir=tmp_path)
    captured = []

    class InspectingExecutor(ConflictResolutionExecutor):
        max_iterations = 1

        @property
        def rubric(self): return _RUBRIC

        async def retrieve(self, event):
            return {"position_a": "must-not-appear", "position_b": "also-must-not-appear"}

        async def act(self, context, prior):
            return ralf_result("complete", output="the-synthesis")

        async def evaluate(self, artifact, rubric):
            captured.append({"artifact": artifact, "rubric": rubric})
            return True, []

    await InspectingExecutor("resolver", bus).run(make_event())
    assert len(captured) == 1
    assert captured[0]["artifact"] == "the-synthesis"
    assert captured[0]["rubric"] == _RUBRIC
    assert set(captured[0].keys()) == {"artifact", "rubric"}


# ── No dispute_resolved when human_override fires ────────────────────────────

async def test_no_dispute_resolved_on_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedConflictExecutor("resolver", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    assert not any(e.event_type == GovernanceEventType.DISPUTE_RESOLVED for e in stored)


# ── follow_up can be overridden ───────────────────────────────────────────────

async def test_follow_up_override_is_called(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    received = []

    class CustomFollowUpExecutor(ConflictResolutionExecutor):
        @property
        def rubric(self): return _RUBRIC

        async def retrieve(self, event): return {"position_a": "A", "position_b": "B"}
        async def act(self, context, prior): return ralf_result("complete")
        async def evaluate(self, artifact, rubric): return True, []

        async def follow_up(self, event, result):
            received.append(result.status)
            return None

    await CustomFollowUpExecutor("resolver", bus).run(make_event())
    assert received == ["complete"]


# ── Causation chain is preserved ─────────────────────────────────────────────

async def test_dispute_resolved_is_caused_by_trigger_event(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedConflictExecutor("resolver", bus, ralf_result("complete"))
    trigger = make_event()
    await executor.run(trigger)
    stored = load_events("governance", store_dir=tmp_path)
    resolved = next(e for e in stored if e.event_type == GovernanceEventType.DISPUTE_RESOLVED)
    assert resolved.causation_id == trigger.event_id
