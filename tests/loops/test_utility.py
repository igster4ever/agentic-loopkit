"""Tests for UtilityExecutor — v4-6 generate-and-rank executor."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from agentic_loopkit import Event, EventBus, UtilityExecutor, UtilityResult, UtilityCandidate


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_event(**kwargs) -> Event:
    defaults = dict(event_type="system.test", source="test", payload={"task": "test"})
    return Event(**{**defaults, **kwargs})


def make_bus(tmp_path: Path) -> EventBus:
    return EventBus(store_dir=tmp_path)


class SimpleUtilityExecutor(UtilityExecutor):
    """
    Concrete test executor.

    Generates string candidates and scores them by length (shorter = better).
    """

    max_candidates = 3
    min_utility = 0.5

    def __init__(self, name, bus, candidates=None, scores=None):
        super().__init__(name, bus)
        self._candidates = candidates or ["short", "a medium answer", "a much longer answer than the others"]
        self._scores = scores  # list of floats, one per candidate; None = use auto

    @property
    def criteria(self):
        return {"brevity": 0.6, "clarity": 0.4}

    async def generate_candidates(self, context):
        return list(self._candidates)

    async def utility_score(self, artifact, criteria, context):
        if self._scores is not None:
            idx = self._candidates.index(artifact)
            score = self._scores[idx]
        else:
            # shorter = higher utility; normalise against max length
            max_len = max(len(c) for c in self._candidates) or 1
            score = 1.0 - (len(artifact) / max_len)
        return UtilityCandidate(
            artifact=artifact,
            utility_score=score,
            criteria_scores={"brevity": score, "clarity": score},
            rationale=f"length={len(artifact)}",
        )


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_happy_path_selects_highest_scoring_candidate(tmp_path):
    bus = make_bus(tmp_path)
    executor = SimpleUtilityExecutor("u", bus)

    result = await executor.run(make_event())

    assert result.status == "complete"
    assert result.is_complete
    assert result.winner is not None
    # "short" should win — shortest string
    assert result.winner == "short"


@pytest.mark.asyncio
async def test_all_candidates_returned_sorted_descending(tmp_path):
    bus = make_bus(tmp_path)
    executor = SimpleUtilityExecutor("u", bus)

    result = await executor.run(make_event())

    scores = [c.utility_score for c in result.all_candidates]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_below_threshold_when_all_scores_too_low(tmp_path):
    bus = make_bus(tmp_path)
    # All candidates score 0.1 — below min_utility=0.5
    executor = SimpleUtilityExecutor("u", bus, scores=[0.1, 0.1, 0.1])

    result = await executor.run(make_event())

    assert result.status == "below_threshold"
    assert not result.is_complete
    assert result.winner is None
    assert result.winner_score == 0.1  # highest score, still below threshold
    assert len(result.all_candidates) == 3


@pytest.mark.asyncio
async def test_no_candidates_when_generate_returns_empty(tmp_path):
    bus = make_bus(tmp_path)

    class EmptyGenerator(SimpleUtilityExecutor):
        async def generate_candidates(self, context):
            return []

    executor = EmptyGenerator("u", bus)
    result = await executor.run(make_event())

    assert result.status == "no_candidates"
    assert result.winner is None
    assert result.winner_score == 0.0


@pytest.mark.asyncio
async def test_error_status_on_unexpected_exception(tmp_path):
    bus = make_bus(tmp_path)

    class BrokenScorer(SimpleUtilityExecutor):
        async def utility_score(self, artifact, criteria, context):
            raise RuntimeError("LLM timeout")

    executor = BrokenScorer("u", bus)
    result = await executor.run(make_event())

    assert result.status == "error"
    assert result.winner is None


@pytest.mark.asyncio
async def test_follow_up_called_on_complete_not_below_threshold(tmp_path):
    bus = make_bus(tmp_path)
    bus.publish = AsyncMock()

    class WithFollowUp(SimpleUtilityExecutor):
        async def follow_up(self, event, result):
            if result.is_complete:
                return event.caused("utility.done", self.name, {"winner": result.winner})
            return None

    executor = WithFollowUp("u", bus)

    # Complete run — follow_up should publish
    await executor.run(make_event())
    assert bus.publish.call_count == 1
    published = bus.publish.call_args[0][0]
    assert published.event_type == "utility.done"

    bus.publish.reset_mock()

    # Below threshold — follow_up should NOT publish
    executor2 = WithFollowUp("u2", bus)
    executor2._scores = [0.1, 0.1, 0.1]
    await executor2.run(make_event())
    assert bus.publish.call_count == 0


@pytest.mark.asyncio
async def test_utility_score_isolation_receives_only_artifact_criteria_context(tmp_path):
    """Isolation contract: utility_score must only be called with (artifact, criteria, context)."""
    bus = make_bus(tmp_path)
    calls = []

    class TrackingScorer(SimpleUtilityExecutor):
        async def utility_score(self, artifact, criteria, context):
            calls.append((artifact, set(criteria.keys()), set(context.keys())))
            return UtilityCandidate(artifact=artifact, utility_score=0.8)

    executor = TrackingScorer("u", bus)
    await executor.run(make_event(payload={"task": "rank this"}))

    assert len(calls) == 3
    for artifact, crit_keys, ctx_keys in calls:
        # criteria keys match the property
        assert crit_keys == {"brevity", "clarity"}
        # context comes from retrieve() (event payload) — no candidate history
        assert "task" in ctx_keys


@pytest.mark.asyncio
async def test_criteria_scores_keys_match_criteria_property(tmp_path):
    bus = make_bus(tmp_path)
    executor = SimpleUtilityExecutor("u", bus)

    result = await executor.run(make_event())

    for candidate in result.all_candidates:
        assert set(candidate.criteria_scores.keys()) == set(executor.criteria.keys())


@pytest.mark.asyncio
async def test_retrieve_default_returns_event_payload(tmp_path):
    bus = make_bus(tmp_path)
    executor = SimpleUtilityExecutor("u", bus)

    event = make_event(payload={"key": "value", "num": 42})
    context = await executor.retrieve(event)

    assert context == {"key": "value", "num": 42}


@pytest.mark.asyncio
async def test_min_utility_boundary_exactly_at_threshold(tmp_path):
    bus = make_bus(tmp_path)
    # Score exactly at min_utility=0.5 — should be "complete" (>=, not >)
    executor = SimpleUtilityExecutor("u", bus, scores=[0.5, 0.3, 0.2])

    result = await executor.run(make_event())
    assert result.status == "complete"
    assert result.winner_score == 0.5
