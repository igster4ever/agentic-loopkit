"""
tests/govkit/test_council.py — CouncilExecutor tests.

Covers:
  - council_decision emitted on complete
  - council_decision payload carries decision + correlation_id
  - human_override emitted on max iterations exhausted
  - human_override emitted on rejected (confidence below threshold)
  - human_override payload carries correlation_id, status, last_output
  - evaluate() isolation contract — receives only (artifact, rubric)
  - gather_opinions() called from retrieve()
  - opinions fed to act() via context["opinions"]
  - question fed to act() via context["question"]
  - follow_up() can be overridden
  - causation chain preserved (causation_id on emitted event)
  - default max_iterations is 3
  - no council_decision when human_override fires
"""

import pytest
from agentic_loopkit import EventBus, Event
from agentic_loopkit.loops.ralf import RALFResult
from agentic_loopkit.events.store import load_events
from agentic_govkit import CouncilExecutor, CouncilOpinion, GovernanceEventType


_RUBRIC = "## Council Rubric\n- Decision must reference all opinions.\n"

_OPINIONS = [
    CouncilOpinion(source="agent-a", opinion="go with option A", weight=1.0, confidence=0.9),
    CouncilOpinion(source="agent-b", opinion="prefer option B", weight=1.5, confidence=0.8),
]


def make_event(correlation_id: str = "council-corr-1", question: str = "what to do?") -> Event:
    from agentic_loopkit.events.models import SystemEventType
    return Event(
        event_type=SystemEventType.BUS_STARTED,
        source="test",
        payload={"question": question},
        correlation_id=correlation_id,
    )


def ralf_result(status="complete", confidence=0.9, output="consensus-text") -> RALFResult:
    return RALFResult(status=status, step_summary="ok", output=output, confidence=confidence)


# ── Concrete executors ────────────────────────────────────────────────────────

class FixedCouncilExecutor(CouncilExecutor):
    """act() always returns a fixed result; evaluate() driven by a callable."""

    max_iterations = 5

    def __init__(self, name, bus, act_result: RALFResult, evaluate_fn=None):
        super().__init__(name, bus)
        self._act_result  = act_result
        self._evaluate_fn = evaluate_fn or (lambda a, r: (True, []))
        self.evaluate_calls: list[tuple] = []
        self.act_contexts: list[dict]    = []
        self.gather_called: int          = 0

    @property
    def rubric(self) -> str:
        return _RUBRIC

    async def gather_opinions(self, event):
        self.gather_called += 1
        return _OPINIONS

    async def act(self, context, prior):
        self.act_contexts.append(context)
        return self._act_result

    async def evaluate(self, artifact, rubric):
        self.evaluate_calls.append({"artifact": artifact, "rubric": rubric})
        return self._evaluate_fn(artifact, rubric)


class NeverSatisfiedCouncilExecutor(CouncilExecutor):
    max_iterations = 3

    @property
    def rubric(self): return _RUBRIC

    async def gather_opinions(self, event): return _OPINIONS
    async def act(self, context, prior):
        return ralf_result("in_progress", confidence=0.8, output="draft")
    async def evaluate(self, artifact, rubric):
        return False, ["still no consensus"]


# ── council_decision on complete ─────────────────────────────────────────────

async def test_complete_emits_council_decision(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedCouncilExecutor("council", bus, ralf_result("complete"))
    await executor.run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    assert any(e.event_type == GovernanceEventType.COUNCIL_DECISION for e in stored)


async def test_council_decision_carries_decision(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedCouncilExecutor("council", bus, ralf_result("complete", output="the-decision"))
    await executor.run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    decision_event = next(e for e in stored if e.event_type == GovernanceEventType.COUNCIL_DECISION)
    assert decision_event.payload["decision"] == "the-decision"


async def test_council_decision_carries_correlation_id(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedCouncilExecutor("council", bus, ralf_result("complete"))
    await executor.run(make_event(correlation_id="corr-xyz"))
    stored = load_events("governance", store_dir=tmp_path)
    decision_event = next(e for e in stored if e.event_type == GovernanceEventType.COUNCIL_DECISION)
    assert decision_event.payload["correlation_id"] == "corr-xyz"


# ── human_override on max iterations exhausted ───────────────────────────────

async def test_max_iterations_emits_human_override(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedCouncilExecutor("council", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    assert any(e.event_type == GovernanceEventType.HUMAN_OVERRIDE for e in stored)


async def test_human_override_carries_correlation_id(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedCouncilExecutor("council", bus).run(make_event("corr-escalate"))
    stored = load_events("governance", store_dir=tmp_path)
    override = next(e for e in stored if e.event_type == GovernanceEventType.HUMAN_OVERRIDE)
    assert override.payload["correlation_id"] == "corr-escalate"


async def test_human_override_carries_status(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedCouncilExecutor("council", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    override = next(e for e in stored if e.event_type == GovernanceEventType.HUMAN_OVERRIDE)
    assert override.payload["status"] == "error"


async def test_human_override_carries_last_output(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedCouncilExecutor("council", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    override = next(e for e in stored if e.event_type == GovernanceEventType.HUMAN_OVERRIDE)
    assert override.payload["last_output"] is not None


# ── human_override on rejected (confidence too low) ──────────────────────────

async def test_rejected_emits_human_override(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class LowConfidenceExecutor(CouncilExecutor):
        max_iterations = 1

        @property
        def rubric(self): return _RUBRIC

        async def gather_opinions(self, event): return _OPINIONS
        async def act(self, context, prior):
            return ralf_result("in_progress", confidence=0.1)
        async def evaluate(self, artifact, rubric):
            return False, ["too uncertain"]

    await LowConfidenceExecutor("council", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    assert any(e.event_type == GovernanceEventType.HUMAN_OVERRIDE for e in stored)


# ── evaluate() isolation contract ────────────────────────────────────────────

async def test_evaluate_receives_only_artifact_and_rubric(tmp_path):
    """evaluate() must not receive opinions or context — isolation contract."""
    bus = EventBus(store_dir=tmp_path)
    captured = []

    class InspectingExecutor(CouncilExecutor):
        max_iterations = 1

        @property
        def rubric(self): return _RUBRIC

        async def gather_opinions(self, event):
            return [CouncilOpinion("must-not-appear", "must-not-appear")]

        async def act(self, context, prior):
            return ralf_result("complete", output="the-consensus")

        async def evaluate(self, artifact, rubric):
            captured.append({"artifact": artifact, "rubric": rubric})
            return True, []

    await InspectingExecutor("inspector", bus).run(make_event())
    assert len(captured) == 1
    assert captured[0]["artifact"] == "the-consensus"
    assert captured[0]["rubric"] == _RUBRIC
    assert set(captured[0].keys()) == {"artifact", "rubric"}


# ── gather_opinions() wired via retrieve() ───────────────────────────────────

async def test_gather_opinions_called_from_retrieve(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedCouncilExecutor("council", bus, ralf_result("complete"))
    await executor.run(make_event())
    assert executor.gather_called >= 1


async def test_opinions_fed_to_act_via_context(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedCouncilExecutor("council", bus, ralf_result("complete"))
    await executor.run(make_event())
    assert len(executor.act_contexts) >= 1
    assert executor.act_contexts[0]["opinions"] == _OPINIONS


async def test_question_fed_to_act_via_context(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedCouncilExecutor("council", bus, ralf_result("complete"))
    await executor.run(make_event(question="which approach?"))
    assert executor.act_contexts[0]["question"] == "which approach?"


async def test_question_defaults_to_empty_string_when_absent(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedCouncilExecutor("council", bus, ralf_result("complete"))
    event = Event(
        event_type="system.bus_started",
        source="test",
        payload={},  # no "question" key
    )
    await executor.run(event)
    assert executor.act_contexts[0]["question"] == ""


# ── No council_decision when human_override fires ────────────────────────────

async def test_no_council_decision_on_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await NeverSatisfiedCouncilExecutor("council", bus).run(make_event())
    stored = load_events("governance", store_dir=tmp_path)
    assert not any(e.event_type == GovernanceEventType.COUNCIL_DECISION for e in stored)


# ── follow_up() can be overridden ────────────────────────────────────────────

async def test_follow_up_override_is_called(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    received = []

    class CustomFollowUpExecutor(CouncilExecutor):
        @property
        def rubric(self): return _RUBRIC

        async def gather_opinions(self, event): return _OPINIONS
        async def act(self, context, prior): return ralf_result("complete")
        async def evaluate(self, artifact, rubric): return True, []

        async def follow_up(self, event, result):
            received.append(result.status)
            return None

    await CustomFollowUpExecutor("council", bus).run(make_event())
    assert received == ["complete"]


# ── Causation chain preserved ─────────────────────────────────────────────────

async def test_council_decision_is_caused_by_trigger_event(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    executor = FixedCouncilExecutor("council", bus, ralf_result("complete"))
    trigger = make_event()
    await executor.run(trigger)
    stored = load_events("governance", store_dir=tmp_path)
    decision_event = next(e for e in stored if e.event_type == GovernanceEventType.COUNCIL_DECISION)
    assert decision_event.causation_id == trigger.event_id


# ── Default max_iterations ────────────────────────────────────────────────────

async def test_default_max_iterations_is_3(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class Bare(CouncilExecutor):
        @property
        def rubric(self): return _RUBRIC

        async def gather_opinions(self, event): return []
        async def act(self, context, prior): return ralf_result("complete")
        async def evaluate(self, artifact, rubric): return True, []

    executor = Bare("council", bus)
    assert executor.max_iterations == 3


# ── CouncilOpinion dataclass ──────────────────────────────────────────────────

def test_council_opinion_defaults():
    opinion = CouncilOpinion(source="agent-x", opinion="do it")
    assert opinion.weight == 1.0
    assert opinion.confidence == 1.0


def test_council_opinion_custom_weight():
    opinion = CouncilOpinion(source="agent-x", opinion="do it", weight=2.0, confidence=0.75)
    assert opinion.weight == 2.0
    assert opinion.confidence == 0.75


# ── Module boundary: imports only from loopkit public API ────────────────────

def test_council_imports_from_loopkit_public_api_only():
    """council.py must import from agentic_loopkit top-level, never from sub-modules."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).parent.parent.parent / "agentic_govkit" / "loops" / "council.py"
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("agentic_loopkit."), (
                    f"Direct sub-module import: {node.module} — use agentic_loopkit public API only"
                )
