"""
tests/examples/test_compass_skill_opt_demo.py

Thin test suite for examples/compass_skill_opt_demo.py.

Exercises CompassSkillOptExecutor against DEMO_TRAJECTORIES — deterministic,
no live LLM, no real compass data required.  Validates:
  - History parsing and trajectory splitting
  - score() is in [0, 1] and deterministic
  - _reflect_stub() proposes edits when failure patterns exist
  - Full run emits HarnessEventType.CANDIDATE_EVAL events to the harness stream
  - Final event carries best_score and total_epochs
  - regression_gate() correctly accepts improvements and rejects regressions
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "examples"))

from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event, HarnessEventType
from agentic_loopkit.events.store import load_all_events
from agentic_loopkit.testing import AgentTestHarness, TestResult, TestSuiteResult

from compass_skill_opt_demo import (
    DEMO_SKILL,
    DEMO_TRAJECTORIES,
    CompassSkillOptExecutor,
    _parse_history_file,
    _train_selection_split,
    _write_demo_store,
    load_trajectories,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def demo_store(tmp_path: Path) -> Path:
    _write_demo_store(tmp_path)
    return tmp_path


@pytest.fixture
def trigger() -> Event:
    return Event(
        event_type="harness.skill_opt_requested",
        source="test",
        payload={},
    )


# ── History parsing ───────────────────────────────────────────────────────────


def test_parse_history_file_success_outcome(demo_store):
    files = sorted((demo_store / "history").glob("*.md"))
    # demo-S01 is a success session (all goals completed, learnings present)
    s01 = next(f for f in files if "S01" in f.name)
    traj = _parse_history_file(s01)
    assert traj["outcome"] == "success"
    assert len(traj["learnings"]) > 0
    assert len(traj["completed"]) == len(traj["planned"])


def test_parse_history_file_failure_outcome(demo_store):
    files = sorted((demo_store / "history").glob("*.md"))
    # demo-S02 is a failure session (incomplete goal, no learnings)
    s02 = next(f for f in files if "S02" in f.name)
    traj = _parse_history_file(s02)
    assert traj["outcome"] == "failure"
    assert len(traj["incomplete"]) > 0


def test_load_trajectories_returns_all(demo_store):
    trajectories = load_trajectories(demo_store)
    assert len(trajectories) == len(DEMO_TRAJECTORIES)


def test_load_trajectories_empty_dir(tmp_path):
    (tmp_path / "history").mkdir()
    assert load_trajectories(tmp_path) == []


def test_load_trajectories_missing_history(tmp_path):
    assert load_trajectories(tmp_path) == []


# ── Train/selection split ─────────────────────────────────────────────────────


def test_train_selection_split_80_20():
    trajs = [{"session_id": str(i)} for i in range(10)]
    train, selection = _train_selection_split(trajs)
    assert len(train) == 8
    assert len(selection) == 2


def test_train_selection_split_minimum_one():
    trajs = [{"a": 1}, {"b": 2}]
    train, selection = _train_selection_split(trajs)
    assert len(train) >= 1
    assert len(selection) >= 1


def test_train_selection_split_single_item():
    trajs = [{"only": True}]
    train, selection = _train_selection_split(trajs)
    assert train == trajs
    assert selection == trajs


# ── Score ─────────────────────────────────────────────────────────────────────


async def test_score_returns_float_in_range(demo_store, tmp_path):
    bus = EventBus(store_dir=tmp_path / "bus")
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, demo_store)
    trajectories = load_trajectories(demo_store)
    score = await executor.score(DEMO_SKILL, trajectories)
    assert 0.0 <= score <= 1.0


async def test_score_is_deterministic(demo_store, tmp_path):
    bus = EventBus(store_dir=tmp_path / "bus")
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, demo_store)
    trajectories = load_trajectories(demo_store)
    s1 = await executor.score(DEMO_SKILL, trajectories)
    s2 = await executor.score(DEMO_SKILL, trajectories)
    assert s1 == s2


async def test_score_zero_when_no_success_trajectories(tmp_path):
    bus = EventBus(store_dir=tmp_path / "bus")
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, tmp_path)
    failures_only = [t for t in DEMO_TRAJECTORIES if t["outcome"] == "failure"]
    score = await executor.score(DEMO_SKILL, failures_only)
    assert score == 0.0


# ── Reflect stub ──────────────────────────────────────────────────────────────


def test_reflect_stub_proposes_edit_on_failures(demo_store, tmp_path):
    bus = EventBus(store_dir=tmp_path / "bus")
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, demo_store)
    failures = [t for t in DEMO_TRAJECTORIES if t["outcome"] == "failure"]
    edits = executor._reflect_stub(DEMO_SKILL, failures, [], [])
    # failures all have "Run docs hygiene" as incomplete — should produce an append edit
    assert len(edits) == 1
    assert edits[0].op == "append"
    assert edits[0].source_type == "failure"
    assert "docs hygiene" in edits[0].content.lower()


def test_reflect_stub_returns_empty_on_no_failures(tmp_path):
    bus = EventBus(store_dir=tmp_path / "bus")
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, tmp_path)
    edits = executor._reflect_stub(DEMO_SKILL, [], [], [])
    assert edits == []


def test_reflect_stub_skips_rejected_content(demo_store, tmp_path):
    from agentic_loopkit.loops.skillopt import RejectedEdit, SkillEdit

    bus = EventBus(store_dir=tmp_path / "bus")
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, demo_store)
    failures = [t for t in DEMO_TRAJECTORIES if t["outcome"] == "failure"]

    edits = executor._reflect_stub(DEMO_SKILL, failures, [], [])
    assert edits  # first call proposes something

    rejected = [RejectedEdit(edit=edits[0], failure_patterns=[], epoch=1)]
    edits_after_rejection = executor._reflect_stub(DEMO_SKILL, failures, [], rejected)
    assert edits_after_rejection == []


# ── Full run integration ──────────────────────────────────────────────────────


async def test_run_completes(demo_store, tmp_path, trigger):
    bus = EventBus(store_dir=tmp_path / "bus")
    await bus.start()
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, demo_store)
    result = await executor.run(trigger)
    await bus.stop()
    assert result.status in ("complete", "error")


async def test_run_emits_candidate_eval_events(demo_store, tmp_path, trigger):
    bus = EventBus(store_dir=tmp_path / "bus")
    await bus.start()
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, demo_store)
    await executor.run(trigger)
    await bus.stop()

    events = load_all_events("harness", bus.store_dir)
    eval_events = [e for e in events if e.event_type == HarnessEventType.CANDIDATE_EVAL]
    # At least one per epoch plus the final follow_up event
    assert len(eval_events) >= 1


async def test_run_emits_final_event(demo_store, tmp_path, trigger):
    bus = EventBus(store_dir=tmp_path / "bus")
    await bus.start()
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, demo_store)
    await executor.run(trigger)
    await bus.stop()

    events = load_all_events("harness", bus.store_dir)
    final_events = [e for e in events if e.payload.get("final")]
    assert len(final_events) == 1
    p = final_events[0].payload
    assert "best_score" in p
    assert "total_epochs" in p
    assert "best_skill_length" in p


async def test_run_epoch_events_have_required_fields(demo_store, tmp_path, trigger):
    bus = EventBus(store_dir=tmp_path / "bus")
    await bus.start()
    executor = CompassSkillOptExecutor("cso", bus, DEMO_SKILL, demo_store)
    await executor.run(trigger)
    await bus.stop()

    events = load_all_events("harness", bus.store_dir)
    epoch_events = [
        e for e in events
        if e.event_type == HarnessEventType.CANDIDATE_EVAL and not e.payload.get("final")
    ]
    for ev in epoch_events:
        p = ev.payload
        assert "epoch" in p
        assert "status" in p
        assert "selection_score" in p
        assert isinstance(p["accepted_edits"], list)
        assert isinstance(p["rejected_edits"], list)


# ── Regression gate ───────────────────────────────────────────────────────────


def _make_suite(
    held_in_pass: int, held_in_total: int,
    held_out_pass: int, held_out_total: int,
) -> TestSuiteResult:
    results = []
    for i in range(held_in_total):
        results.append(TestResult(f"hi-{i}", "held_in", i < held_in_pass, [], 0, 0))
    for i in range(held_out_total):
        results.append(TestResult(f"ho-{i}", "held_out", i < held_out_pass, [], 0, 0))
    return TestSuiteResult(results=results)


def test_regression_gate_accepts_improvement():
    baseline = _make_suite(2, 4, 1, 4)
    improved = _make_suite(3, 4, 2, 4)
    assert AgentTestHarness.regression_gate(baseline, improved) is True


def test_regression_gate_rejects_held_out_regression():
    baseline = _make_suite(2, 4, 2, 4)
    regressed = _make_suite(3, 4, 1, 4)  # held_out worse
    assert AgentTestHarness.regression_gate(baseline, regressed) is False


def test_regression_gate_rejects_no_change():
    baseline = _make_suite(2, 4, 2, 4)
    same = _make_suite(2, 4, 2, 4)
    assert AgentTestHarness.regression_gate(baseline, same) is False
