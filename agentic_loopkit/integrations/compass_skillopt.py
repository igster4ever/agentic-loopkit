"""
agentic_loopkit/integrations/compass_skillopt.py

CompassSkillOptExecutor: wires compass session history into SkillOptExecutor
as a real training corpus. Delegates all compass reads to the compass CLI
(subprocess, JSON stdout) — this module never parses compass history
markdown or reads compass's state.json directly.
"""
from __future__ import annotations


def _build_trajectories(
    evidence: list[dict], quality_history: list[dict], holdout_ids: list[str],
) -> tuple[list[dict], list[dict]]:
    """
    Pair skill-opt-run's evidence (most-recent-first) with quality_history
    (oldest-first) by rank from most recent. Split into train/selection by
    holdout membership.
    """
    holdout_set = set(holdout_ids)
    train: list[dict] = []
    selection: list[dict] = []
    for ev, qh in zip(evidence, reversed(quality_history)):
        outcome = "success" if ev.get("quality") == "high" else "failure"
        traj = {**ev, "outcome": outcome, "session_id": qh["session_id"]}
        if qh["session_id"] in holdout_set:
            selection.append(traj)
        else:
            train.append(traj)
    return train, selection
