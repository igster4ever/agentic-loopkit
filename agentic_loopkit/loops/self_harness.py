"""
agentic_loopkit/loops/self_harness.py — SelfHarnessExecutor: end-to-end v5 integration.

Wires SkillOptExecutor + AgentTestHarness inside an OutcomeExecutor outer loop.

Outer loop (OutcomeExecutor — max_iterations=3):
  retrieve()  — load system.failure_pattern_detected events; split train/selection
  act()       — (first iteration only) apply Phase-B adaptation, see below;
                create fresh SkillOptExecutor; run to completion; return SkillOptResult
  evaluate()  — AgentTestHarness.regression_gate(baseline, candidate) — deterministic
  follow_up() — emit harness.edit_accepted (with best_skill) or harness.edit_rejected

Isolation contract (inherited from OutcomeExecutor):
  evaluate() receives ONLY (artifact, rubric). regression_gate() is deterministic —
  no LLM, no prior chain — so the isolation requirement is structurally satisfied.

Sources (arXiv:2606.09498 §3.4 non-regressive acceptance rule):
  ∆in ≥ 0 AND ∆ho ≥ 0 AND max(∆in, ∆ho) > 0
  Rejects proposals that trade one split against the other even if total improves.

Phase-B adaptation (P71, MemoHarness arXiv:2607.14159 §2.6):
  retrieve()'s train/selection split already carries each FailureSignature's
  ``agent_mechanism`` (= originating event.source). At the start of the first
  outer iteration, ``_compute_adaptation()`` filters trajectories to this
  executor's own name, clusters by terminal_cause, and — if a cluster exceeds
  ``adaptation_threshold`` and has a deterministic entry in ``_ADAPTATION_MAP``
  — nudges the bounded ``train_fraction`` parameter for the *next* case/run and
  emits ``harness.harness_adapted``. This is distinct from SkillOptExecutor's
  Phase-A search (which edits the skill text itself via a validation-gated
  loop): no LLM, no retraining, no feedback signal — a fixed lookup applied
  once per case, exactly the "no test-time labels, no gradient updates" shape
  the paper specifies.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..events.models import Event, EventMeta, HarnessEventType, SystemEventType
from ..events.store import load_all_events
from ..testing import AgentTestHarness, TestSuiteResult, TestTask
from .outcome import OutcomeExecutor
from .ralf import RALFResult
from .skillopt import SkillOptExecutor, SkillOptResult


# Deterministic terminal_cause → bounded train_fraction delta (P71). Practical
# heuristics, not a learned controller — MemoHarness §2.6 / Appendix B. Only
# causes with a real, defensible lever appear here; anything absent is a no-op.
_ADAPTATION_MAP: dict[str, float] = {
    "max_iterations_exceeded":     -0.05,  # outer loop exhausted — hold out more for scoring
    "confidence_below_threshold":  -0.05,  # low-confidence acceptances — tighten selection split
}

_TRAIN_FRACTION_MIN = 0.5
_TRAIN_FRACTION_MAX = 0.95


class SelfHarnessExecutor(OutcomeExecutor):
    """
    OutcomeExecutor that wires SkillOptExecutor + AgentTestHarness end-to-end.

    Each outer iteration instantiates a fresh SkillOptExecutor via
    ``skill_opt_factory``, runs it to completion, then evaluates the resulting
    candidate skill against the baseline using ``AgentTestHarness.regression_gate()``.

    Args:
        name:               Executor name registered on the bus.
        bus:                The EventBus instance.
        skill_opt_factory:  ``(context: dict) → SkillOptExecutor`` — called once
                            per outer act() iteration; context carries the train/
                            selection trajectory split from retrieve().
        harness:            Pre-configured AgentTestHarness for evaluate().
        agent_factory:      ``(skill: str) → Callable[[], AgentBase]`` — constructs
                            an agent factory from a candidate skill string; called
                            inside evaluate() to run the candidate against tasks.
        tasks:              TestTask list passed to harness.run_suite().
        baseline_result:    Pre-computed TestSuiteResult for the unoptimised skill.
        train_fraction:     Train/selection split ratio for failure patterns (default 0.8).
        adaptation_threshold: Min cluster count (of this executor's own FailureSignature
                            trajectories, by terminal_cause) before Phase-B adaptation
                            fires (default 3).
    """

    max_iterations: int = 3  # OutcomeExecutor default; matches Anthropic Managed Agents

    def __init__(
        self,
        name: str,
        bus: Any,
        *,
        skill_opt_factory: Callable[[dict], SkillOptExecutor],
        harness: AgentTestHarness,
        agent_factory: Callable[[str], Callable[[], Any]],
        tasks: list[TestTask],
        baseline_result: TestSuiteResult,
        train_fraction: float = 0.8,
        adaptation_threshold: int = 3,
    ) -> None:
        super().__init__(name, bus)
        self._skill_opt_factory = skill_opt_factory
        self._harness = harness
        self._agent_factory = agent_factory
        self._tasks = tasks
        self._baseline = baseline_result
        self._train_fraction = train_fraction
        self._adaptation_threshold = adaptation_threshold
        self._trigger_event: Optional[Event] = None

    # ── Rubric ─────────────────────────────────────────────────────────────────

    @property
    def rubric(self) -> str:
        return "candidate skill must pass regression_gate() against baseline"

    # ── retrieve ───────────────────────────────────────────────────────────────

    async def retrieve(self, event: Event) -> dict:
        """
        Load system.failure_pattern_detected events from the store; split into
        train and selection trajectory dicts for SkillOptExecutor.

        Also captures the trigger event so act() can forward it to the inner
        SkillOptExecutor.run() call.
        """
        self._trigger_event = event

        source_events = load_all_events("system", store_dir=self._bus.store_dir)

        trajectories = [
            {
                "outcome":         "failure",
                "terminal_cause":  e.payload.get("terminal_cause"),
                "agent_mechanism": e.payload.get("agent_mechanism"),
                "pattern_summary": e.payload.get("pattern_summary", ""),
                "event_id":        e.event_id,
            }
            for e in source_events
            if e.event_type == SystemEventType.FAILURE_PATTERN_DETECTED
        ]

        n_train = max(1, int(len(trajectories) * self._train_fraction)) if trajectories else 0
        return {
            "train":     trajectories[:n_train],
            "selection": trajectories[n_train:],
        }

    # ── act ────────────────────────────────────────────────────────────────────

    async def act(self, context: dict, prior_result: Optional[RALFResult]) -> RALFResult:
        """
        Instantiate a fresh SkillOptExecutor for this outer iteration; run it to
        completion; wrap the result as a SkillOptResult artifact for evaluate().

        ``context`` carries the train/selection split from retrieve() and is
        forwarded directly to the factory — the factory's concrete retrieve()
        uses it when called from SkillOptExecutor.run().

        ``prior_result`` is ignored: each outer iteration is a fresh optimisation
        run from the current best skill (determined by the factory).

        On the first outer iteration only (``prior_result is None``), applies
        Phase-B adaptation (P71) — see module docstring — before delegating to
        the factory, so this run's own split is unaffected but the *next*
        ``run()`` call inherits the adapted ``train_fraction``.
        """
        if prior_result is None:
            adaptation = self._compute_adaptation(context)
            if adaptation is not None:
                self._train_fraction = adaptation["new_value"]
                await self._emit_harness_adapted(adaptation)

        opt = self._skill_opt_factory(context)
        trigger = self._trigger_event or Event(
            event_type="harness.trigger", source=self.name, payload={}
        )
        opt_ralf = await opt.run(trigger)

        # opt_ralf.output is the last epoch's SkillOptResult; build a fresh one
        # carrying opt.best_skill (which may differ from the last epoch's skill
        # if the final epoch didn't improve).
        last: Optional[SkillOptResult] = opt_ralf.output if isinstance(
            opt_ralf.output, SkillOptResult
        ) else None

        artifact = SkillOptResult(
            skill           = opt.best_skill,
            accepted_edits  = last.accepted_edits if last else [],
            rejected_edits  = last.rejected_edits if last else [],
            selection_score = last.selection_score if last else opt._best_score,
            epoch           = last.epoch if last else opt._epoch,
            status          = last.status if last else "unchanged",
        )

        return RALFResult(
            status       = "complete",
            step_summary = (
                f"SkillOpt: epoch {artifact.epoch}, "
                f"status={artifact.status}, score={artifact.selection_score:.3f}"
            ),
            output     = artifact,
            confidence = 0.9,
        )

    # ── Phase-B adaptation (P71) ─────────────────────────────────────────────────

    def _compute_adaptation(self, context: dict) -> Optional[dict]:
        """
        Deterministic lookup — no LLM. Filters this run's trajectories to
        FailureSignatures whose ``agent_mechanism`` is this executor's own name,
        clusters by ``terminal_cause``, and returns a bounded ``train_fraction``
        adjustment for the highest-count cause that (a) exceeds
        ``adaptation_threshold`` and (b) has an entry in ``_ADAPTATION_MAP``.

        Returns None when no cluster qualifies, or the resulting value would
        be unchanged after clamping to [_TRAIN_FRACTION_MIN, _TRAIN_FRACTION_MAX]
        (already at bound — no-op, no event).
        """
        own = [
            t for t in context.get("train", []) + context.get("selection", [])
            if t.get("agent_mechanism") == self.name
        ]
        if not own:
            return None

        counts: dict[str, int] = {}
        for t in own:
            cause = t.get("terminal_cause")
            if cause:
                counts[cause] = counts.get(cause, 0) + 1

        candidates = [
            (cause, n) for cause, n in counts.items()
            if n >= self._adaptation_threshold and cause in _ADAPTATION_MAP
        ]
        if not candidates:
            return None

        cause, n = max(candidates, key=lambda cn: cn[1])
        old = self._train_fraction
        new = min(_TRAIN_FRACTION_MAX, max(_TRAIN_FRACTION_MIN, old + _ADAPTATION_MAP[cause]))
        if new == old:
            return None

        return {
            "terminal_cause": cause,
            "count":          n,
            "param":          "train_fraction",
            "old_value":      old,
            "new_value":      new,
        }

    async def _emit_harness_adapted(self, adaptation: dict) -> None:
        """Publish harness.harness_adapted — the causal record of a Phase-B change."""
        trigger = self._trigger_event or Event(
            event_type="harness.trigger", source=self.name, payload={}
        )
        await self._bus.publish(
            trigger.caused(
                HarnessEventType.HARNESS_ADAPTED,
                self.name,
                {
                    **adaptation,
                    "_meta": EventMeta(
                        phase="act",
                        loop_type="self_harness",
                        confidence=1.0,
                        context=(
                            f"SelfHarness adapted {adaptation['param']}: "
                            f"{adaptation['old_value']:.2f} -> {adaptation['new_value']:.2f} "
                            f"after {adaptation['count']} '{adaptation['terminal_cause']}' "
                            f"signal(s) (threshold={self._adaptation_threshold})"
                        ),
                    ).to_dict(),
                },
            )
        )

    # ── evaluate ───────────────────────────────────────────────────────────────

    async def evaluate(
        self, artifact: SkillOptResult, rubric: str
    ) -> tuple[bool, list[str]]:
        """
        EVALUATE — deterministic; no LLM; isolated context.

        Runs AgentTestHarness.run_suite() on the candidate skill and calls
        regression_gate(baseline, candidate).  The isolation contract is
        structurally satisfied: regression_gate() uses only pass/fail counts,
        not any reasoning state from act().

        Args:
            artifact: SkillOptResult carrying the candidate skill text.
            rubric:   Ignored — gate is always regression_gate() (kept for
                      OutcomeExecutor signature compatibility).
        """
        candidate_factory = self._agent_factory(artifact.skill)
        candidate_result = await self._harness.run_suite(candidate_factory, self._tasks)

        passed = AgentTestHarness.regression_gate(self._baseline, candidate_result)
        if passed:
            return True, []
        return False, ["candidate skill did not pass regression_gate() against baseline"]

    # ── follow_up ──────────────────────────────────────────────────────────────

    async def follow_up(
        self, event: Event, result: RALFResult
    ) -> Optional[Event]:
        """
        Emit harness.edit_accepted with best_skill payload when complete;
        harness.edit_rejected on exhaustion or error.
        """
        artifact: Optional[SkillOptResult] = (
            result.output if isinstance(result.output, SkillOptResult) else None
        )

        if result.status == "complete" and artifact is not None:
            return event.caused(
                HarnessEventType.EDIT_ACCEPTED,
                self.name,
                {
                    "best_skill":      artifact.skill,
                    "selection_score": artifact.selection_score,
                    "epochs":          artifact.epoch,
                    "_meta": EventMeta(
                        phase="act",
                        loop_type="self_harness",
                        confidence=1.0,
                        context=(
                            f"SelfHarness: skill accepted — "
                            f"epoch {artifact.epoch}, score={artifact.selection_score:.3f}"
                        ),
                    ).to_dict(),
                },
            )

        return event.caused(
            HarnessEventType.EDIT_REJECTED,
            self.name,
            {
                "reason": result.step_summary,
                "_meta": EventMeta(
                    phase="act",
                    loop_type="self_harness",
                    confidence=result.confidence,
                    context=f"SelfHarness: skill rejected — {result.step_summary}",
                ).to_dict(),
            },
        )
