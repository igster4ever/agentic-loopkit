"""Tests for agentic_loopkit.integrations.compass_skillopt."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentic_loopkit.integrations.compass_skillopt import _build_trajectories, _run_compass_cli

COMPASS_SCRIPT = Path.home() / ".claude" / "skills" / "compass" / "scripts" / "compass.py"


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


# ── _run_compass_cli — exercised against the real compass CLI, not mocked ──

pytestmark = pytest.mark.skipif(
    not COMPASS_SCRIPT.exists(), reason="compass skill not installed at expected path",
)


def _write_minimal_namespace(loop_dir: Path, namespace: str) -> Path:
    """Write the minimal compass namespace files skill-opt commands need."""
    ns_dir = loop_dir / namespace
    (ns_dir / "history").mkdir(parents=True)
    (ns_dir / "state.json").write_text(json.dumps({
        "namespace": namespace,
        "quality_history": [
            {"session_id": "S1", "score": 0.9},
            {"session_id": "S2", "score": 0.3},
        ],
    }))
    (ns_dir / "config.json").write_text(json.dumps({}))
    return ns_dir


@pytest.fixture
def compass_namespace(tmp_path):
    ns_dir = _write_minimal_namespace(tmp_path, "test-ns")
    return tmp_path, ns_dir


def test_run_compass_cli_returns_parsed_json(compass_namespace):
    loop_dir, ns_dir = compass_namespace
    result = _run_compass_cli(
        COMPASS_SCRIPT, "get-skillopt-status", "test-ns", loop_dir=loop_dir,
    )
    assert result["ok"] is True
    assert result["quality_history"] == [
        {"session_id": "S1", "score": 0.9},
        {"session_id": "S2", "score": 0.3},
    ]


def test_run_compass_cli_raises_on_missing_namespace(tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        _run_compass_cli(
            COMPASS_SCRIPT, "get-skillopt-status", "does-not-exist", loop_dir=tmp_path,
        )


def test_run_compass_cli_passes_payload(compass_namespace):
    loop_dir, ns_dir = compass_namespace
    payload = json.dumps({
        "op": "append", "target": "x", "content": "y", "rationale": "test",
    })
    result = _run_compass_cli(
        COMPASS_SCRIPT, "reject-skill-opt-edit", "test-ns", payload=payload, loop_dir=loop_dir,
    )
    assert result["ok"] is True
    rejects_file = ns_dir / "skill_edit_rejections.jsonl"
    assert rejects_file.exists()
