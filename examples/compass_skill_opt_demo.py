"""
examples/compass_skill_opt_demo.py — CompassSkillOptExecutor end-to-end demo.

Wires compass session history as the SkillOptExecutor training corpus and emits
auditable telemetry via HarnessEventType events.  Demonstrates the full
self-improvement loop without requiring a live LLM (uses a deterministic stub
by default; pass an AsyncLLMCallable via the constructor for production use).

Usage — real compass data:
    python examples/compass_skill_opt_demo.py --namespace agentic-loopkit
    python examples/compass_skill_opt_demo.py --namespace agentic-loopkit --verbose
    python examples/compass_skill_opt_demo.py --namespace agentic-loopkit \\
        --skill ~/.claude/skills/compass/SKILL.md

Usage — synthetic fixtures (same corpus the test suite uses; no real data needed):
    python examples/compass_skill_opt_demo.py --demo
    python examples/compass_skill_opt_demo.py --demo --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event, HarnessEventType
from agentic_loopkit.events.store import load_all_events
from agentic_loopkit.loops.skillopt import RejectedEdit, SkillEdit, SkillOptExecutor, SkillOptResult
from agentic_loopkit.testing import AsyncLLMCallable


# ── Compass history parsing ────────────────────────────────────────────────────

_DEFAULT_LOOP_DIR = Path("~/.claude/loop").expanduser()


def _parse_history_file(path: Path) -> dict:
    """
    Parse a compass history .md file into a trajectory dict.

    Returns:
        session_id, planned, completed, incomplete, learnings, outcome
    """
    text = path.read_text()

    def _extract_list(section: str) -> list[str]:
        m = re.search(rf"## {section}\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
        if not m:
            return []
        items = []
        for line in m.group(1).strip().splitlines():
            line = line.strip()
            if line.startswith("- ") and line != "- (none)":
                items.append(line[2:].strip())
        return items

    planned = _extract_list("Planned")
    completed = _extract_list("Completed")
    incomplete = _extract_list("Incomplete")

    raw_learnings = _extract_list("Learnings extracted")
    # Strip tag prefix [tag1, tag2] if present
    learnings = [re.sub(r"^\[[^\]]+\]\s*", "", l) for l in raw_learnings]

    # Success: ≥80% of planned goals completed AND at least one learning extracted
    if planned:
        completion_rate = len(completed) / len(planned)
        outcome = "success" if completion_rate >= 0.8 and learnings else "failure"
    else:
        outcome = "failure"

    return {
        "session_id": path.stem,
        "planned": planned,
        "completed": completed,
        "incomplete": incomplete,
        "learnings": learnings,
        "outcome": outcome,
    }


def load_trajectories(store_dir: Path) -> list[dict]:
    """
    Load and parse all history files from a compass namespace store.

    Returns trajectories in chronological order (filename sort = date sort).
    """
    history_dir = store_dir / "history"
    if not history_dir.exists():
        return []
    trajectories = []
    for f in sorted(history_dir.glob("*.md")):
        try:
            trajectories.append(_parse_history_file(f))
        except Exception:
            pass
    return trajectories


def _train_selection_split(trajectories: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Chronological 80/20 split.  Oldest sessions = train; newest = selection.
    Guarantees at least one item in each split when len >= 2.
    """
    n = len(trajectories)
    if n < 2:
        return trajectories, trajectories
    split = max(1, int(n * 0.8))
    return trajectories[:split], trajectories[split:]


# ── Deterministic score ────────────────────────────────────────────────────────

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


# ── CompassSkillOptExecutor ────────────────────────────────────────────────────


class CompassSkillOptExecutor(SkillOptExecutor):
    """
    SkillOptExecutor wired to compass session history as its training corpus.

    retrieve()   — loads sessions from <store_dir>/history/, splits train/selection.
    score()      — deterministic: keyword overlap between skill text and session learnings.
    reflect()    — stub: proposes edits based on recurring incomplete goal patterns.
                   Override or pass llm= for a real LLM-backed optimiser.
    learn()      — emits HarnessEventType.CANDIDATE_EVAL per epoch (auditable telemetry).
    follow_up()  — emits a final CANDIDATE_EVAL event summarising the best skill.

    The stub reflect() is sufficient for demos and tests.  To use a real LLM:
        executor = CompassSkillOptExecutor("name", bus, skill, store_dir, llm=my_llm)
    where my_llm satisfies the AsyncLLMCallable protocol.
    """

    max_iterations: int = 3
    edit_budget: int = 2

    def __init__(
        self,
        name: str,
        bus: EventBus,
        initial_skill: str,
        store_dir: Path,
        llm: Optional[AsyncLLMCallable] = None,
    ) -> None:
        super().__init__(name, bus, initial_skill)
        self._store_dir = store_dir
        self._llm = llm

    # ── RETRIEVE ──────────────────────────────────────────────────────────────

    async def retrieve(self, event: Event) -> dict:
        trajectories = load_trajectories(self._store_dir)
        train, selection = _train_selection_split(trajectories)
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
            f"## Success patterns\n" + "\n".join(success_patterns or ["(none)"]) + "\n\n"
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


# ── Synthetic demo fixtures ────────────────────────────────────────────────────

DEMO_SKILL = """\
# Compass skill — demo stub

## Purpose
Guide productive agentic sessions using OODA/RALF hybrid loop.

## When to apply
- When starting a new work session in a tracked namespace
- When closing a session to capture learnings and update reality

## Procedure
1. Read namespace state with compass.py read
2. Present orient brief: intent, reality, top learnings, gaps
3. Propose session goals grounded in the gaps
4. Open session and populate todo list
5. Close session: record learnings, update reality, emit telemetry
"""

DEMO_TRAJECTORIES = [
    {
        "session_id": "demo-S01",
        "planned": ["Implement CouncilExecutor", "Write tests", "Update docs"],
        "completed": ["Implement CouncilExecutor", "Write tests", "Update docs"],
        "incomplete": [],
        "learnings": [
            "CouncilExecutor is structurally distinct from ConflictResolutionExecutor",
            "always write tests before refactoring",
        ],
        "outcome": "success",
    },
    {
        "session_id": "demo-S02",
        "planned": ["Run docs hygiene", "Define v6 intent", "Implement LCLM EventHeadline"],
        "completed": ["Define v6 intent", "Implement LCLM EventHeadline"],
        "incomplete": ["Run docs hygiene"],
        "learnings": [],
        "outcome": "failure",
    },
    {
        "session_id": "demo-S03",
        "planned": ["Run docs hygiene", "Seed P34 taxonomy", "Update event catalog"],
        "completed": ["Run docs hygiene", "Seed P34 taxonomy", "Update event catalog"],
        "incomplete": [],
        "learnings": [
            "docs hygiene should run after every executor is added",
            "module boundary tests must match full import prefix",
        ],
        "outcome": "success",
    },
    {
        "session_id": "demo-S04",
        "planned": ["Run docs hygiene", "Fix boundary test"],
        "completed": ["Fix boundary test"],
        "incomplete": ["Run docs hygiene"],
        "learnings": [],
        "outcome": "failure",
    },
    {
        "session_id": "demo-S05",
        "planned": ["Run docs hygiene", "Write PollingAdapter doc"],
        "completed": ["Write PollingAdapter doc"],
        "incomplete": ["Run docs hygiene"],
        "learnings": [],
        "outcome": "failure",
    },
]


# ── Demo runner ────────────────────────────────────────────────────────────────


async def _run_demo(store_dir: Path, skill_text: str, verbose: bool = False) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bus = EventBus(store_dir=Path(tmp))
        await bus.start()

        executor = CompassSkillOptExecutor(
            name="compass-skill-opt",
            bus=bus,
            initial_skill=skill_text,
            store_dir=store_dir,
        )
        trigger = Event(
            event_type="harness.skill_opt_requested",
            source="demo",
            payload={"namespace": str(store_dir)},
        )

        trajectories = load_trajectories(store_dir)
        train, selection = _train_selection_split(trajectories)

        print(f"\n{'='*62}")
        print("CompassSkillOptExecutor — audit trail")
        print(f"{'='*62}")
        print(f"Store:       {store_dir}")
        print(f"Skill:       {len(skill_text)} chars")
        print(f"Trajectories: {len(trajectories)} total  "
              f"({len(train)} train / {len(selection)} selection)")
        successes = sum(1 for t in trajectories if t["outcome"] == "success")
        print(f"Outcomes:    {successes} success / {len(trajectories) - successes} failure")
        print(f"{'='*62}\n")

        result = await executor.run(trigger)
        await bus.stop()

        events = load_all_events("harness", Path(tmp))
        eval_events = [e for e in events if e.event_type == HarnessEventType.CANDIDATE_EVAL]

        print(f"Telemetry — {len(eval_events)} CANDIDATE_EVAL event(s):\n")
        for ev in eval_events:
            p = ev.payload
            if p.get("final"):
                print(
                    f"  ✓ FINAL   best_score={p['best_score']:.4f}  "
                    f"epochs={p['total_epochs']}  "
                    f"skill_length={p['best_skill_length']} chars"
                )
            else:
                print(
                    f"  · epoch {p['epoch']:02d}  "
                    f"status={p['status']:<10}  "
                    f"score={p['selection_score']:.4f}  "
                    f"accepted={len(p['accepted_edits'])}  "
                    f"rejected={len(p['rejected_edits'])}"
                )
                if verbose and p["accepted_edits"]:
                    for edit in p["accepted_edits"]:
                        print(f"             └ {edit['op']}: {edit['content'][:60]!r}")

        print(f"\nBest skill ({len(executor.best_skill)} chars):")
        print("─" * 62)
        print(executor.best_skill[:600])
        print(f"\nRun status: {result.status}")


def _write_demo_store(dest: Path) -> None:
    """Write DEMO_TRAJECTORIES as compass history files to dest."""
    (dest / "history").mkdir(parents=True, exist_ok=True)
    for traj in DEMO_TRAJECTORIES:
        sid = traj["session_id"]
        planned = "\n".join(f"- {g}" for g in traj["planned"]) or "- (none)"
        completed = "\n".join(f"- {g}" for g in traj["completed"]) or "- (none)"
        incomplete = "\n".join(f"- {g}" for g in traj["incomplete"]) or "- (none)"
        learnings = "\n".join(f"- {l}" for l in traj["learnings"]) or "- (none)"
        md = (
            f"# Session {sid} — demo\n\n"
            f"## Planned\n{planned}\n\n"
            f"## Completed\n{completed}\n\n"
            f"## Incomplete\n{incomplete}\n\n"
            f"## Learnings extracted\n{learnings}\n"
        )
        (dest / "history" / f"{sid}.md").write_text(md)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CompassSkillOptExecutor demo")
    parser.add_argument(
        "--namespace", default="agentic-loopkit",
        help="Compass namespace to load history from (default: agentic-loopkit)",
    )
    parser.add_argument(
        "--loop-dir", default=str(_DEFAULT_LOOP_DIR),
        help="Root compass loop directory (default: ~/.claude/loop)",
    )
    parser.add_argument(
        "--skill", default=None,
        help="Path to skill document to optimise (default: built-in demo stub)",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Run with synthetic fixtures instead of real compass data",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show accepted edit content in the audit trail",
    )
    args = parser.parse_args()

    tmp_store: Optional[Path] = None
    try:
        if args.demo:
            tmp_store = Path(tempfile.mkdtemp())
            _write_demo_store(tmp_store)
            store_dir = tmp_store
            skill_text = DEMO_SKILL
        else:
            store_dir = Path(args.loop_dir) / args.namespace
            if not store_dir.exists():
                print(f"Error: namespace store not found: {store_dir}", file=sys.stderr)
                sys.exit(1)
            skill_text = Path(args.skill).read_text() if args.skill else DEMO_SKILL

        asyncio.run(_run_demo(store_dir, skill_text, verbose=args.verbose))

    finally:
        if tmp_store:
            shutil.rmtree(tmp_store, ignore_errors=True)
