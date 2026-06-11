import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event
from agentic_loopkit.loops.skillopt import (
    SkillOptExecutor,
    SkillEdit,
    RejectedEdit,
    SkillOptResult,
    _apply_edits,
    _is_protected,
)


def make_event(**kwargs) -> Event:
    return Event(event_type="test.trigger", source="test", payload={}, **kwargs)


def make_edit(op="append", target="", content="new content", source_type="failure") -> SkillEdit:
    return SkillEdit(op=op, target=target, content=content, source_type=source_type)


def make_context(train=None, selection=None) -> dict:
    return {"train": train or [], "selection": selection or []}


# ── Concrete executor for testing ─────────────────────────────────────────────


class SimpleSkillOpt(SkillOptExecutor):
    """Minimal concrete implementation for unit tests."""

    max_iterations = 3
    edit_budget = 2

    def __init__(self, name, bus, initial_skill, proposals=None, score_fn=None):
        super().__init__(name, bus, initial_skill)
        # proposals: list of SkillEdit to return from reflect() (cycled if needed)
        self._proposals = proposals or []
        self._reflect_calls: list[dict] = []
        self._score_fn = score_fn or (lambda skill, traj: 0.5)

    async def retrieve(self, event) -> dict:
        return make_context()

    async def score(self, skill: str, trajectories: list) -> float:
        return self._score_fn(skill, trajectories)

    async def reflect(self, skill, failures, successes, rejected_buffer, meta_skill) -> list[SkillEdit]:
        self._reflect_calls.append({
            "skill": skill,
            "failures": failures,
            "successes": successes,
            "rejected_buffer": list(rejected_buffer),
            "meta_skill": meta_skill,
        })
        return list(self._proposals)


# ── _apply_edits helper ────────────────────────────────────────────────────────


def test_apply_edits_append():
    result = _apply_edits("base", [SkillEdit("append", "", "extra", "success")])
    assert result.endswith("extra")
    assert "base" in result


def test_apply_edits_insert_after():
    skill = "line one\nline two"
    edit = SkillEdit("insert_after", "line one", "inserted", "success")
    result = _apply_edits(skill, [edit])
    assert "inserted" in result
    assert result.index("line one") < result.index("inserted") < result.index("line two")


def test_apply_edits_replace():
    result = _apply_edits("old text here", [SkillEdit("replace", "old text", "new text", "failure")])
    assert "new text here" == result


def test_apply_edits_delete():
    result = _apply_edits("keep this remove this", [SkillEdit("delete", "remove this", "", "failure")])
    assert "remove this" not in result
    assert "keep this" in result


def test_apply_edits_missing_target_is_noop():
    result = _apply_edits("unchanged", [SkillEdit("replace", "nonexistent", "x", "failure")])
    assert result == "unchanged"


def test_apply_edits_multiple_in_order():
    result = _apply_edits("a", [
        SkillEdit("append", "", "b", "success"),
        SkillEdit("append", "", "c", "success"),
    ])
    assert result.index("a") < result.index("b") < result.index("c")


# ── _is_protected ──────────────────────────────────────────────────────────────


def test_is_protected_returns_true_inside_block():
    skill = "before\n<!-- SLOW_UPDATE_START -->\nprotected line\n<!-- SLOW_UPDATE_END -->\nafter"
    edit = SkillEdit("replace", "protected line", "hacked", "failure")
    assert _is_protected(edit, skill) is True


def test_is_protected_returns_false_outside_block():
    skill = "before\n<!-- SLOW_UPDATE_START -->\nprotected\n<!-- SLOW_UPDATE_END -->\nafter"
    edit = SkillEdit("replace", "after", "x", "failure")
    assert _is_protected(edit, skill) is False


def test_is_protected_returns_false_append():
    skill = "<!-- SLOW_UPDATE_START -->protected<!-- SLOW_UPDATE_END -->"
    edit = SkillEdit("append", "", "extra", "success")
    assert _is_protected(edit, skill) is False


def test_is_protected_returns_false_no_block():
    edit = SkillEdit("replace", "foo", "bar", "failure")
    assert _is_protected(edit, "no protected block here") is False


# ── Accepted edit path ─────────────────────────────────────────────────────────


async def test_accepted_edit_updates_best_skill(tmp_path):
    initial = "initial skill"
    proposal = SkillEdit("append", "", "improvement", "success")
    executor = SimpleSkillOpt(
        "so", None, initial,
        proposals=[proposal],
        score_fn=lambda skill, traj: 0.9 if "improvement" in skill else 0.0,
    )
    event = make_event()
    context = make_context(train=[{"outcome": "success"}])
    result = await executor.act(context, None)

    assert isinstance(result.output, SkillOptResult)
    assert result.output.status == "improved"
    assert "improvement" in executor.best_skill
    assert len(result.output.accepted_edits) == 1
    assert len(result.output.rejected_edits) == 0
    assert len(executor._step_buffer) == 0


async def test_accepted_edit_confidence_is_high(tmp_path):
    executor = SimpleSkillOpt(
        "so", None, "base",
        proposals=[make_edit()],
        score_fn=lambda s, t: 0.99,
    )
    result = await executor.act(make_context(), None)
    assert result.confidence == 0.9


# ── Rejected edit path ────────────────────────────────────────────────────────


async def test_rejected_edit_does_not_update_best_skill(tmp_path):
    executor = SimpleSkillOpt(
        "so", None, "initial",
        proposals=[make_edit(content="bad idea")],
        score_fn=lambda s, t: 0.0,
    )
    result = await executor.act(make_context(), None)

    assert result.output.status == "unchanged"
    assert executor.best_skill == "initial"
    assert len(result.output.accepted_edits) == 0
    assert len(result.output.rejected_edits) == 1


async def test_rejected_edit_appended_to_buffer():
    executor = SimpleSkillOpt(
        "so", None, "base",
        proposals=[make_edit()],
        score_fn=lambda s, t: 0.0,
    )
    await executor.act(make_context(), None)
    assert len(executor._step_buffer) == 1
    assert isinstance(executor._step_buffer[0], RejectedEdit)
    assert executor._step_buffer[0].epoch == 1


async def test_rejected_confidence_is_medium():
    executor = SimpleSkillOpt("so", None, "base", proposals=[make_edit()],
                               score_fn=lambda s, t: 0.0)
    result = await executor.act(make_context(), None)
    assert result.confidence == 0.6


# ── Buffer negative feedback reaches reflect() ───────────────────────────────


async def test_buffer_passed_to_subsequent_reflect():
    executor = SimpleSkillOpt(
        "so", None, "base",
        proposals=[make_edit()],
        score_fn=lambda s, t: 0.0,  # always reject
    )
    await executor.act(make_context(), None)  # epoch 1 — rejects, fills buffer
    await executor.act(make_context(), None)  # epoch 2 — should see buffer

    assert len(executor._reflect_calls) == 2
    second_call = executor._reflect_calls[1]
    assert len(second_call["rejected_buffer"]) == 1
    assert second_call["rejected_buffer"][0].epoch == 1


# ── Edit budget cap ───────────────────────────────────────────────────────────


async def test_edit_budget_clips_proposals():
    many_edits = [make_edit(content=f"edit{i}") for i in range(10)]
    executor = SimpleSkillOpt(
        "so", None, "base",
        proposals=many_edits,
        score_fn=lambda s, t: 0.9,
    )
    executor.edit_budget = 2
    result = await executor.act(make_context(), None)
    # Only 2 edits should have been applied (score would differ if more were applied)
    assert len(result.output.accepted_edits) <= 2


# ── Protected block strips proposals ─────────────────────────────────────────


async def test_protected_proposals_stripped():
    skill = "preamble\n<!-- SLOW_UPDATE_START -->\nguided block\n<!-- SLOW_UPDATE_END -->\nrest"
    protected_edit = SkillEdit("replace", "guided block", "overwritten", "failure")
    safe_edit = SkillEdit("append", "", "safe addition", "success")
    executor = SimpleSkillOpt(
        "so", None, skill,
        proposals=[protected_edit, safe_edit],
        score_fn=lambda s, t: 0.9,
    )
    result = await executor.act(make_context(), None)
    # Protected edit must be stripped; safe edit accepted
    accepted_targets = [e.target for e in result.output.accepted_edits]
    assert protected_edit.target not in accepted_targets
    assert "guided block" in executor.best_skill  # protected content preserved


# ── max_iterations cap ────────────────────────────────────────────────────────


async def test_max_iterations_status_complete():
    executor = SimpleSkillOpt("so", None, "base", proposals=[], score_fn=lambda s, t: 0.9)
    executor.max_iterations = 2

    r1 = await executor.act(make_context(), None)
    assert r1.status == "in_progress"

    r2 = await executor.act(make_context(), None)
    assert r2.status == "complete"


# ── learn() persists best_skill to state.procedural ──────────────────────────


async def test_learn_callable_and_best_skill_maintained_in_memory(tmp_path):
    """learn() is a no-op hook; best_skill / best_score are maintained as instance attrs."""
    executor = SimpleSkillOpt(
        "so", None, "initial skill",
        proposals=[make_edit(content="improvement")],
        score_fn=lambda s, t: 0.9,
    )
    event = make_event()
    result = await executor.act(make_context(), None)
    await executor.learn(event, result)  # must not raise

    # State is maintained in memory
    assert executor.best_skill != "initial skill"
    assert "improvement" in executor.best_skill
    assert executor._best_score == 0.9


# ── slow_update no-op by default ─────────────────────────────────────────────


async def test_slow_update_noop_by_default():
    executor = SimpleSkillOpt("so", None, "base skill", proposals=[], score_fn=lambda s, t: 0.0)
    result_skill = await executor.slow_update("prev", "curr skill", [])
    assert result_skill == "curr skill"


async def test_update_meta_skill_noop_by_default():
    executor = SimpleSkillOpt("so", None, "base skill")
    result = await executor.update_meta_skill("prev", "curr")
    assert result == ""  # _meta_skill initial value


# ── slow_update override is called after each act() epoch ────────────────────


async def test_slow_update_called_each_epoch():
    calls = []

    class SlowUpdateOpt(SimpleSkillOpt):
        async def slow_update(self, prev, curr, evidence):
            calls.append((prev, curr))
            return curr

    executor = SlowUpdateOpt("so", None, "base", proposals=[], score_fn=lambda s, t: 0.0)
    await executor.act(make_context(), None)
    await executor.act(make_context(), None)
    assert len(calls) == 2


# ── retrieve() train/selection split ─────────────────────────────────────────


async def test_retrieve_returns_splits():
    executor = SimpleSkillOpt("so", None, "base")
    context = await executor.retrieve(make_event())
    assert "train" in context
    assert "selection" in context


# ── score() is deterministic ─────────────────────────────────────────────────


async def test_score_deterministic():
    trajectories = [{"outcome": "success"}, {"outcome": "failure"}]
    executor = SimpleSkillOpt("so", None, "base", score_fn=lambda s, t: len(t) * 0.1)
    s1 = await executor.score("skill", trajectories)
    s2 = await executor.score("skill", trajectories)
    assert s1 == s2


# ── SkillOptResult fields ─────────────────────────────────────────────────────


async def test_skilloptresult_fields():
    executor = SimpleSkillOpt(
        "so", None, "skill text",
        proposals=[make_edit()],
        score_fn=lambda s, t: 0.75,
    )
    result = await executor.act(make_context(), None)
    opt: SkillOptResult = result.output
    assert opt.epoch == 1
    assert isinstance(opt.selection_score, float)
    assert opt.skill == executor._current_skill
    assert opt.status in ("improved", "unchanged")


# ── best_skill property ───────────────────────────────────────────────────────


def test_best_skill_initial_equals_initial_skill():
    executor = SimpleSkillOpt("so", None, "my initial skill")
    assert executor.best_skill == "my initial skill"
