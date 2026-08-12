"""
examples/compass_skill_opt_demo.py — CompassSkillOptExecutor demo.

Runs the full self-improvement loop against synthetic fixtures — no live
compass data, no live LLM (uses a deterministic stub by default). For a real
run against real compass history, use CompassSkillOptExecutor directly, from
agentic_loopkit.integrations.compass_skillopt:

    from agentic_loopkit.integrations.compass_skillopt import CompassSkillOptExecutor
    executor = CompassSkillOptExecutor("name", bus, skill_text, "your-namespace")

Usage:
    python examples/compass_skill_opt_demo.py
    python examples/compass_skill_opt_demo.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event, HarnessEventType
from agentic_loopkit.events.store import load_all_events
from agentic_loopkit.integrations.compass_skillopt import CompassSkillOptExecutor

# ── Synthetic demo fixtures ───────────────────────────────────────────

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


class _DemoCompassSkillOptExecutor(CompassSkillOptExecutor):
    """CompassSkillOptExecutor with a static retrieve() — no compass CLI call."""

    def __init__(self, name: str, bus: EventBus, initial_skill: str) -> None:
        super().__init__(name, bus, initial_skill, namespace="demo")

    async def retrieve(self, event: Event) -> dict:
        n = len(DEMO_TRAJECTORIES)
        split = max(1, int(n * 0.8))
        return {"train": DEMO_TRAJECTORIES[:split], "selection": DEMO_TRAJECTORIES[split:]}


# ── Demo runner ──────────────────────────────────────────────────────────

async def _run_demo(verbose: bool = False) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bus = EventBus(store_dir=Path(tmp))
        await bus.start()

        executor = _DemoCompassSkillOptExecutor("compass-skill-opt", bus, DEMO_SKILL)
        trigger = Event(
            event_type="harness.skill_opt_requested", source="demo", payload={},
        )

        successes = sum(1 for t in DEMO_TRAJECTORIES if t["outcome"] == "success")
        print(f"\n{'='*62}")
        print("CompassSkillOptExecutor — demo audit trail")
        print(f"{'='*62}")
        print(f"Skill:        {len(DEMO_SKILL)} chars")
        print(f"Trajectories: {len(DEMO_TRAJECTORIES)} total  "
              f"({successes} success / {len(DEMO_TRAJECTORIES) - successes} failure)")
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CompassSkillOptExecutor demo")
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show accepted edit content in the audit trail",
    )
    args = parser.parse_args()
    asyncio.run(_run_demo(verbose=args.verbose))
