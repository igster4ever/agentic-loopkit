"""Tests for agentic_loopkit.integrations.compass_skillopt."""
from __future__ import annotations

from agentic_loopkit.integrations.compass_skillopt import _build_trajectories


def test_high_quality_evidence_is_success():
    evidence = [{"quality": "high", "learnings": ["x"], "incomplete": []}]
    quality_history = [{"session_id": "S1", "score": 0.9}]
    train, selection = _build_trajectories(evidence, quality_history, holdout_ids=[])
    assert train[0]["outcome"] == "success"
    assert train[0]["session_id"] == "S1"


def test_poor_and_neutral_evidence_is_failure():
    evidence = [
        {"quality": "poor", "learnings": [], "incomplete": ["a"]},
        {"quality": "neutral", "learnings": [], "incomplete": ["b"]},
    ]
    quality_history = [{"session_id": "S1", "score": 0.3}, {"session_id": "S2", "score": 0.5}]
    train, selection = _build_trajectories(evidence, quality_history, holdout_ids=[])
    outcomes = {t["session_id"]: t["outcome"] for t in train}
    assert outcomes == {"S2": "failure", "S1": "failure"}


def test_pairs_by_rank_from_most_recent():
    # evidence is most-recent-first; quality_history is oldest-first.
    # evidence[0] (most recent) must pair with quality_history[-1] (most recent).
    evidence = [
        {"quality": "high", "learnings": [], "incomplete": [], "date": "recent"},
        {"quality": "poor", "learnings": [], "incomplete": [], "date": "older"},
    ]
    quality_history = [{"session_id": "OLD", "score": 0.3}, {"session_id": "NEW", "score": 0.9}]
    train, selection = _build_trajectories(evidence, quality_history, holdout_ids=[])
    recent = next(t for t in train if t["date"] == "recent")
    older = next(t for t in train if t["date"] == "older")
    assert recent["session_id"] == "NEW"
    assert older["session_id"] == "OLD"


def test_holdout_membership_selects_by_session_id():
    evidence = [
        {"quality": "high", "learnings": [], "incomplete": []},
        {"quality": "poor", "learnings": [], "incomplete": []},
    ]
    quality_history = [{"session_id": "S1", "score": 0.3}, {"session_id": "S2", "score": 0.9}]
    train, selection = _build_trajectories(evidence, quality_history, holdout_ids=["S2"])
    assert [t["session_id"] for t in selection] == ["S2"]
    assert [t["session_id"] for t in train] == ["S1"]


def test_shorter_list_truncates_pairing():
    evidence = [
        {"quality": "high", "learnings": [], "incomplete": []},
        {"quality": "high", "learnings": [], "incomplete": []},
        {"quality": "high", "learnings": [], "incomplete": []},
    ]
    quality_history = [{"session_id": "S1", "score": 0.9}]
    train, selection = _build_trajectories(evidence, quality_history, holdout_ids=[])
    assert len(train) + len(selection) == 1
