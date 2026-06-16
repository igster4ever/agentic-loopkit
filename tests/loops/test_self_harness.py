"""
tests/loops/test_self_harness.py — SelfHarnessExecutor test suite.

Covers:
  - retrieve() loads system.failure_pattern_detected events, splits train/selection
  - act() creates fresh SkillOptExecutor via factory, returns SkillOptResult as artifact
  - evaluate() calls regression_gate() with (baseline, candidate) — isolation contract
  - evaluate signature: only (artifact, rubric) — no prior context
  - satisfied first-pass → status="complete", follow_up emits harness.edit_accepted
  - needs-revision → loops, follow_up emits harness.edit_rejected on exhaustion
  - causation chain preserved through follow_up events
  - max_iterations cap (inherited from OutcomeExecutor)
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event, HarnessEventType, SystemEventType
from agentic_loopkit.events.store import append_event
from agentic_loopkit.loops.self_harness import SelfHarnessExecutor
from agentic_loopkit.loops.skillopt import SkillOptExecutor, SkillEdit, SkillOptResult
from agentic_loopkit.loops.ralf import RALFResult
from agentic_loopkit.testing import AgentTestHarness, TestSuiteResult, TestTask, TestResult


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_trigger(**kwargs) -> Event:
    return Event(event_type="harness.trigger", source="test", payload={}, **kwargs)


def make_failure_pattern_event(tmp_path: Path) -> Event:
    e = Event(
        event_type=SystemEventType.FAILURE_PATTERN_DETECTED,
        source="failure-detector",
        payload={
            "terminal_cause":  "halt_enforced",
            "causal_status":   "halted",
            "agent_mechanism": "killswitch",
            "pattern_summary": "KillSwitch halted correlation chain",
        },
    )
    append_event(e, store_dir=tmp_path)
    return e


def make_suite_result(held_in_pass=2, held_in_total=2, held_out_pass=1, held_out_total=1) -> TestSuiteResult:
    results = []
    for i in range(held_in_total):
        results.append(TestResult(
            task_id=f"hi-{i}", split="held_in",
            passed=(i < held_in_pass), emitted_events=[], duration_ms=0, repeat=0,
        ))
    for i in range(held_out_total):
        results.append(TestResult(
            task_id=f"ho-{i}", split="held_out",
            passed=(i < held_out_pass), emitted_events=[], duration_ms=0, repeat=0,
        ))
    return TestSuiteResult(results=results)


def make_skill_opt(bus, context_holder: list) -> SkillOptExecutor:
    """Concrete SkillOptExecutor that uses context captured by SelfHarnessExecutor."""

    class ConcreteOpt(SkillOptExecutor):
        max_iterations = 2
        edit_budget = 1

        async def retrieve(self, event) -> dict:
            return context_holder[0] if context_holder else {"train": [], "selection": []}

        async def score(self, skill, trajectories) -> float:
            return 0.8  # always improves

        async def reflect(self, skill, failures, successes, rejected_buffer, meta_skill):
            return [SkillEdit("append", "", "# optimised", "success")]

    return ConcreteOpt("opt", bus, "initial skill")


def make_self_harness(
    bus,
    tmp_path: Path,
    baseline: TestSuiteResult,
    candidate: TestSuiteResult,
) -> SelfHarnessExecutor:
    """Build a SelfHarnessExecutor with all dependencies stubbed."""
    context_holder: list[dict] = []

    def skill_opt_factory(ctx: dict) -> SkillOptExecutor:
        context_holder.clear()
        context_holder.append(ctx)
        return make_skill_opt(bus, context_holder)

    harness = AgentTestHarness(store_dir=tmp_path)
    harness.run_suite = AsyncMock(return_value=candidate)

    from agentic_loopkit.agents.base import AgentBase

    class DummyAgent(AgentBase):
        def __init__(self, skill):
            super().__init__("dummy", bus)

        async def orient(self, event, context):
            return {}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            pass

    def agent_factory(skill: str):
        return lambda: DummyAgent(skill)

    task = TestTask(task_id="t1", description="dummy", input_event=make_trigger(), split="held_in")

    return SelfHarnessExecutor(
        "self-harness", bus,
        skill_opt_factory=skill_opt_factory,
        harness=harness,
        agent_factory=agent_factory,
        tasks=[task],
        baseline_result=baseline,
    )


# ── retrieve ───────────────────────────────────────────────────────────────────

async def test_retrieve_loads_failure_pattern_events(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    baseline = make_suite_result()
    candidate = make_suite_result(held_in_pass=3, held_in_total=3)

    make_failure_pattern_event(tmp_path)
    make_failure_pattern_event(tmp_path)

    sh = make_self_harness(bus, tmp_path, baseline, candidate)
    context = await sh.retrieve(make_trigger())

    assert len(context["train"]) + len(context["selection"]) == 2
    assert all(t["outcome"] == "failure" for t in context["train"])


async def test_retrieve_excludes_non_pattern_events(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    noise = Event(event_type="system.bus_started", source="bus", payload={})
    append_event(noise, store_dir=tmp_path)

    sh = make_self_harness(bus, tmp_path, make_suite_result(), make_suite_result())
    ctx = await sh.retrieve(make_trigger())
    assert ctx["train"] == []
    assert ctx["selection"] == []


async def test_retrieve_train_selection_split(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    for _ in range(5):
        make_failure_pattern_event(tmp_path)

    sh = make_self_harness(bus, tmp_path, make_suite_result(), make_suite_result())
    ctx = await sh.retrieve(make_trigger())
    n_train = len(ctx["train"])
    n_sel   = len(ctx["selection"])
    assert n_train + n_sel == 5
    assert n_train >= n_sel  # 80/20 default


# ── act ────────────────────────────────────────────────────────────────────────

async def test_act_returns_skill_opt_result_as_artifact(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    sh = make_self_harness(bus, tmp_path, make_suite_result(), make_suite_result())
    await sh.retrieve(make_trigger())
    ralf = await sh.act({"train": [], "selection": []}, None)

    assert ralf.status == "complete"
    assert isinstance(ralf.output, SkillOptResult)


async def test_act_artifact_carries_best_skill(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    sh = make_self_harness(bus, tmp_path, make_suite_result(), make_suite_result())
    await sh.retrieve(make_trigger())
    ralf = await sh.act({"train": [], "selection": []}, None)
    assert ralf.output.skill != ""


# ── evaluate — isolation contract ─────────────────────────────────────────────

async def test_evaluate_signature_is_artifact_and_rubric_only(tmp_path):
    """evaluate() must accept exactly (artifact, rubric) — no other args."""
    import inspect
    sig = inspect.signature(SelfHarnessExecutor.evaluate)
    params = list(sig.parameters.keys())
    assert params == ["self", "artifact", "rubric"], (
        f"evaluate() signature must be (self, artifact, rubric), got {params}"
    )


async def test_evaluate_satisfied_when_regression_gate_passes(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    baseline  = make_suite_result(held_in_pass=2, held_in_total=3, held_out_pass=1, held_out_total=2)
    candidate = make_suite_result(held_in_pass=3, held_in_total=3, held_out_pass=2, held_out_total=2)

    sh = make_self_harness(bus, tmp_path, baseline, candidate)
    artifact = SkillOptResult(
        skill="candidate skill text",
        accepted_edits=[], rejected_edits=[],
        selection_score=0.9, epoch=2, status="improved",
    )
    satisfied, gaps = await sh.evaluate(artifact, sh.rubric)
    assert satisfied is True
    assert gaps == []


async def test_evaluate_not_satisfied_when_regression_gate_fails(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    baseline  = make_suite_result(held_in_pass=3, held_in_total=3, held_out_pass=2, held_out_total=2)
    candidate = make_suite_result(held_in_pass=2, held_in_total=3, held_out_pass=1, held_out_total=2)

    sh = make_self_harness(bus, tmp_path, baseline, candidate)
    artifact = SkillOptResult(
        skill="worse skill",
        accepted_edits=[], rejected_edits=[],
        selection_score=0.4, epoch=2, status="unchanged",
    )
    satisfied, gaps = await sh.evaluate(artifact, sh.rubric)
    assert satisfied is False
    assert len(gaps) == 1
    assert "regression_gate" in gaps[0]


# ── follow_up ──────────────────────────────────────────────────────────────────

async def test_follow_up_emits_edit_accepted_on_complete(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    sh = make_self_harness(bus, tmp_path, make_suite_result(), make_suite_result())
    trigger = make_trigger()

    artifact = SkillOptResult(
        skill="best skill text",
        accepted_edits=[], rejected_edits=[],
        selection_score=0.95, epoch=3, status="improved",
    )
    result = RALFResult(status="complete", step_summary="done", output=artifact, confidence=1.0)
    follow = await sh.follow_up(trigger, result)

    assert follow is not None
    assert follow.event_type == HarnessEventType.EDIT_ACCEPTED
    assert follow.payload["best_skill"] == "best skill text"
    assert follow.payload["selection_score"] == 0.95


async def test_follow_up_emits_edit_rejected_on_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    sh = make_self_harness(bus, tmp_path, make_suite_result(), make_suite_result())
    trigger = make_trigger()

    result = RALFResult(status="error", step_summary="max iterations", output=None, confidence=0.5)
    follow = await sh.follow_up(trigger, result)

    assert follow is not None
    assert follow.event_type == HarnessEventType.EDIT_REJECTED
    assert "max iterations" in follow.payload["reason"]


async def test_follow_up_preserves_causation_chain(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    sh = make_self_harness(bus, tmp_path, make_suite_result(), make_suite_result())
    trigger = make_trigger()

    artifact = SkillOptResult(skill="s", accepted_edits=[], rejected_edits=[],
                              selection_score=0.9, epoch=1, status="improved")
    result = RALFResult(status="complete", step_summary="ok", output=artifact)
    follow = await sh.follow_up(trigger, result)

    assert follow.causation_id == trigger.event_id
    assert follow.correlation_id == trigger.correlation_id


async def test_follow_up_meta_present(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    sh = make_self_harness(bus, tmp_path, make_suite_result(), make_suite_result())
    trigger = make_trigger()

    artifact = SkillOptResult(skill="s", accepted_edits=[], rejected_edits=[],
                              selection_score=0.9, epoch=1, status="improved")
    result = RALFResult(status="complete", step_summary="ok", output=artifact)
    follow = await sh.follow_up(trigger, result)

    assert follow.meta() is not None
    assert follow.meta()["loop_type"] == "self_harness"


# ── full run integration ───────────────────────────────────────────────────────

async def test_full_run_satisfied_first_pass(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()

    baseline  = make_suite_result(held_in_pass=2, held_in_total=3)
    candidate = make_suite_result(held_in_pass=3, held_in_total=3)

    sh = make_self_harness(bus, tmp_path, baseline, candidate)

    emitted: list[Event] = []
    from agentic_loopkit.agents.base import AgentBase

    class Collector(AgentBase):
        async def orient(self, event, context):
            return {}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            emitted.append(event)

    collector = Collector("col", bus)
    collector.subscribe("harness")
    bus.register(collector)

    trigger = make_trigger()
    result = await sh.run(trigger)

    assert result.status == "complete"

    accepted = [e for e in emitted if e.event_type == HarnessEventType.EDIT_ACCEPTED]
    assert len(accepted) == 1
    assert accepted[0].payload["best_skill"] != ""
    await bus.stop()


async def test_full_run_max_iterations_emits_rejected(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()

    # candidate never improves over baseline
    baseline  = make_suite_result(held_in_pass=3, held_in_total=3)
    candidate = make_suite_result(held_in_pass=2, held_in_total=3)

    sh = make_self_harness(bus, tmp_path, baseline, candidate)
    sh.max_iterations = 2

    emitted: list[Event] = []
    from agentic_loopkit.agents.base import AgentBase

    class Collector(AgentBase):
        async def orient(self, event, context):
            return {}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            emitted.append(event)

    collector = Collector("col", bus)
    collector.subscribe("harness")
    bus.register(collector)

    result = await sh.run(make_trigger())

    assert result.status == "error"
    rejected = [e for e in emitted if e.event_type == HarnessEventType.EDIT_REJECTED]
    assert len(rejected) == 1
    await bus.stop()
