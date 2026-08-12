"""
agentic_loopkit/integrations/compass_skillopt.py

CompassSkillOptExecutor: wires compass session history into SkillOptExecutor
as a real training corpus. Delegates all compass reads to the compass CLI
(subprocess, JSON stdout) — this module never parses compass history
markdown or reads compass's state.json directly.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Optional

from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event, HarnessEventType
from agentic_loopkit.loops.skillopt import RejectedEdit, SkillEdit, SkillOptExecutor, SkillOptResult
from agentic_loopkit.testing import AsyncLLMCallable


def _run_compass_cli(
    compass_script: Path,
    command: str,
    namespace: str,
    payload: str | None = None,
    loop_dir: Path | None = None,
) -> dict:
    """Run one compass CLI command and parse its JSON stdout."""
    args = ["python3", str(compass_script), command, namespace]
    if payload is not None:
        args.append(payload)
    env = os.environ.copy()
    if loop_dir is not None:
        env["COMPASS_LOOP_DIR"] = str(loop_dir)
    result = subprocess.run(args, capture_output=True, text=True, env=env, check=True)
    return json.loads(result.stdout)


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
    for ev, qh in zip(evidence, reversed(quality_history), strict=False):
        outcome = "success" if ev.get("quality") == "high" else "failure"
        traj = {**ev, "outcome": outcome, "session_id": qh["session_id"]}
        if qh["session_id"] in holdout_set:
            selection.append(traj)
        else:
            train.append(traj)
    return train, selection


# ── Deterministic score ────────────────────────────────────────────────

_STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "of", "and", "or", "is", "are",
    "was", "were", "it", "its", "this", "that", "with", "for", "not", "be",
    "as", "by", "from", "have", "has", "had", "do", "does",
}


def _keyword_overlap(skill_text: str, learning: str) -> float:
    """Fraction of meaningful words in a learning that appear in the skill text."""
    words = {
        w.lower()
        for w in re.findall(r"\b[a-zA-Z_]{3,}\b", learning)
        if w.lower() not in _STOP_WORDS
    }
    if not words:
        return 0.0
    hits = sum(
        1 for w in words
        if re.search(rf"\b{re.escape(w)}\b", skill_text, re.IGNORECASE)
    )
    return hits / len(words)


_DEFAULT_COMPASS_SCRIPT = (
    Path.home() / ".claude" / "skills" / "compass" / "scripts" / "compass.py"
)


class CompassSkillOptExecutor(SkillOptExecutor):
    """
    SkillOptExecutor wired to compass session history as its training corpus.

    retrieve()   — calls the compass CLI (skill-opt-run, get-skillopt-status,
                   freeze-holdout) over subprocess and pairs the results into
                   a train/selection split via _build_trajectories(). Never
                   parses compass history files or reads compass's state.json
                   directly.
    score()      — deterministic: keyword overlap between skill text and session learnings.
    reflect()    — stub: proposes edits based on recurring incomplete goal patterns.
                   Override or pass llm= for a real LLM-backed optimiser.
    learn()      — emits HarnessEventType.CANDIDATE_EVAL per epoch (auditable telemetry).
    follow_up()  — emits a final CANDIDATE_EVAL event summarising the best skill.

    The stub reflect() is sufficient for demos and tests.  To use a real LLM:
        executor = CompassSkillOptExecutor("name", bus, skill, namespace, llm=my_llm)
    where my_llm satisfies the AsyncLLMCallable protocol.
    """

    max_iterations: int = 3
    edit_budget: int = 2

    def __init__(
        self,
        name: str,
        bus: EventBus,
        initial_skill: str,
        namespace: str,
        compass_script: Optional[Path] = None,
        loop_dir: Optional[Path] = None,
        llm: Optional[AsyncLLMCallable] = None,
    ) -> None:
        super().__init__(name, bus, initial_skill)
        self._namespace = namespace
        self._compass_script = compass_script or _DEFAULT_COMPASS_SCRIPT
        self._loop_dir = loop_dir
        self._llm = llm

    def _cli(self, command: str, payload: Optional[str] = None) -> dict:
        return _run_compass_cli(
            self._compass_script, command, self._namespace,
            payload=payload, loop_dir=self._loop_dir,
        )

    # ── RETRIEVE ──────────────────────────────────────────────────────────

    async def retrieve(self, event: Event) -> dict:
        status = self._cli("get-skillopt-status")
        if not status.get("holdout_frozen"):
            self._cli("freeze-holdout")
            status = self._cli("get-skillopt-status")

        evidence_result = self._cli("skill-opt-run")
        evidence = evidence_result.get("evidence", [])
        quality_history = status.get("quality_history", [])
        holdout_ids = status.get("holdout_session_ids", [])

        train, selection = _build_trajectories(evidence, quality_history, holdout_ids)
        return {"train": train, "selection": selection}

    # ── SCORE (deterministic — no LLM) ───────────────────────────────────────

    async def score(self, skill: str, trajectories: list) -> float:
        """
        Score skill coverage against selection-split success trajectories.

        Higher score = skill text addresses more of the observed session learnings.
        Comparable across epochs; deterministic for identical inputs.
        """
        successes = [t for t in trajectories if t.get("outcome") == "success"]
        if not successes:
            return 0.0
        total_overlap, total_learnings = 0.0, 0
        for traj in successes:
            for learning in traj.get("learnings", []):
                total_overlap += _keyword_overlap(skill, learning)
                total_learnings += 1
        return total_overlap / total_learnings if total_learnings else 0.0

    # ── REFLECT (primary LLM phase) ───────────────────────────────────────────

    async def reflect(
        self,
        skill: str,
        failures: list,
        successes: list,
        rejected_buffer: list[RejectedEdit],
        meta_skill: str,
    ) -> list[SkillEdit]:
        if self._llm:
            return await self._reflect_llm(skill, failures, successes, rejected_buffer, meta_skill)
        return self._reflect_stub(skill, failures, successes, rejected_buffer)

    def _reflect_stub(
        self,
        skill: str,
        failures: list,
        successes: list,
        rejected_buffer: list[RejectedEdit],
    ) -> list[SkillEdit]:
        """
        Deterministic stub — propose one append per epoch based on the most common
        incomplete goal pattern across failure sessions.

        Avoids re-proposing content already in the rejected-edit buffer.
        """
        incomplete_goals: list[str] = []
        for t in failures:
            incomplete_goals.extend(t.get("incomplete", []))

        if not incomplete_goals:
            return []

        top_pattern, _ = Counter(incomplete_goals).most_common(1)[0]
        top_pattern = re.sub(r"\[.*?\]", "", top_pattern).strip()

        content = (
            f"\n## Recurring carry-forward (auto-detected)\n\n"
            f"Sessions consistently leave this goal type incomplete:\n"
            f"  {top_pattern[:120]}\n\n"
            f"Prioritise it early in DECIDE to reduce carry-forward rate.\n"
        )

        rejected_texts = {r.edit.content for r in rejected_buffer}
        if content in rejected_texts:
            return []

        return [SkillEdit(op="append", target="", content=content, source_type="failure")]

    async def _reflect_llm(
        self,
        skill: str,
        failures: list,
        successes: list,
        rejected_buffer: list[RejectedEdit],
        meta_skill: str,
    ) -> list[SkillEdit]:
        """LLM-backed reflect.  Calls self._llm with a structured prompt."""
        failure_patterns = [
            f"Incomplete goal: {g}"
            for t in failures for g in t.get("incomplete", [])
        ][:10]
        success_patterns = [
            f"Learning: {l}"
            for t in successes for l in t.get("learnings", [])
        ][:5]
        rejected_notes = [
            f"Rejected (epoch {r.epoch}): {r.edit.content[:80]}"
            for r in rejected_buffer
        ]

        prompt = (
            "You are a skill optimiser. Propose at most 2 bounded edits to the skill "
            "document below to address failure patterns and reinforce success patterns.\n\n"
            f"## Current skill\n{skill}\n\n"
            f"## Failure patterns\n" + "\n".join(failure_patterns or ["(none)"]) + "\n\n"
            "## Success patterns\n" + "\n".join(success_patterns or ["(none)"]) + "\n\n"
            + (
                "## Previously rejected (do not repeat)\n"
                + "\n".join(rejected_notes) + "\n\n"
                if rejected_notes else ""
            )
            + "## Output\n"
            "Return a JSON array of SkillEdit objects:\n"
            '[{"op": "append"|"insert_after"|"replace"|"delete", '
            '"target": "...", "content": "...", "source_type": "failure"|"success"}]\n'
        )
        assert self._llm is not None  # only called from reflect() after an is-not-None check
        raw = await self._llm(prompt)
        try:
            data = json.loads(raw)
            return [SkillEdit(**d) for d in data[: self.edit_budget]]
        except Exception:
            return []

    # ── LEARN (emit telemetry per epoch) ──────────────────────────────────────

    async def learn(self, event: Event, result) -> None:
        """Emit one CANDIDATE_EVAL event per epoch — the auditable telemetry record."""
        opt: SkillOptResult = result.output
        if not isinstance(opt, SkillOptResult):
            return
        payload = {
            "epoch": opt.epoch,
            "status": opt.status,
            "selection_score": round(opt.selection_score, 4),
            "accepted_edits": [
                {"op": e.op, "content": e.content[:120], "source_type": e.source_type}
                for e in opt.accepted_edits
            ],
            "rejected_edits": [
                {"op": e.op, "content": e.content[:120], "source_type": e.source_type}
                for e in opt.rejected_edits
            ],
            "skill_preview": opt.skill[:200],
        }
        await self._bus.publish(
            event.caused(HarnessEventType.CANDIDATE_EVAL, self.name, payload)
        )

    # ── FOLLOW-UP (final summary event) ───────────────────────────────────────

    async def follow_up(self, event: Event, result) -> Optional[Event]:
        """Emit a final CANDIDATE_EVAL event with the best skill summary."""
        opt: SkillOptResult = result.output
        if not isinstance(opt, SkillOptResult):
            return None
        return event.caused(
            HarnessEventType.CANDIDATE_EVAL,
            self.name,
            {
                "final": True,
                "best_score": round(self._best_score, 4),
                "best_skill_length": len(self._best_skill),
                "best_skill_preview": self._best_skill[:300],
                "total_epochs": self._epoch,
            },
        )
