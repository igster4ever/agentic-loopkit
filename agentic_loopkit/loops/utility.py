"""
agentic_loopkit/loops/utility.py — UtilityExecutor.

Generate N candidates; rank by utility score; select the winner.

This is the Utility-Based Agent pattern from the AIMA taxonomy:
  - Maintains goals *and* a utility function
  - Ranks outcomes by preferences or probabilities
  - Handles conflicting goals and trade-offs
  - Aims for maximum satisfaction, not just threshold success

Contrast with OutcomeExecutor:
  OutcomeExecutor   — single artifact, iteratively improved to rubric pass/fail
  UtilityExecutor   — N candidates generated once, ranked by weighted criteria

This executor does not extend RALFExecutor.  The generate-and-rank pattern
is a single pass, not an iterative convergence loop.

Usage::

    class AdrSummarySelector(UtilityExecutor):
        max_candidates = 4
        min_utility    = 0.6

        @property
        def criteria(self):
            return {
                "technical_accuracy": 0.50,
                "decision_clarity":   0.30,
                "brevity":            0.20,
            }

        async def generate_candidates(self, context):
            return await asyncio.gather(*[
                llm.call("Write an ADR summary.", context) for _ in range(self.max_candidates)
            ])

        async def utility_score(self, artifact, criteria, context):
            # Fresh LLM context — no generate_candidates history
            scores = await llm.score(artifact, criteria)
            weighted = sum(criteria[k] * scores[k] for k in criteria)
            return UtilityCandidate(
                artifact=artifact, utility_score=weighted, criteria_scores=scores
            )

        async def follow_up(self, event, result):
            if result.is_complete:
                return event.caused("adr.summary_selected", self.name,
                                    {"summary": result.winner, "score": result.winner_score})
            return None
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..events.models import Event

log = logging.getLogger("agentic_loopkit.utility")


@dataclass
class UtilityCandidate:
    """A single scored candidate from UtilityExecutor."""
    artifact:        Any
    utility_score:   float                            # 0.0–1.0; higher = more preferred
    criteria_scores: dict[str, float] = field(default_factory=dict)
    rationale:       str = ""


@dataclass
class UtilityResult:
    """Result returned by UtilityExecutor.run()."""
    status:         str          # "complete" | "no_candidates" | "below_threshold" | "error"
    winner:         Any          # artifact of highest-scoring candidate; None if not complete
    winner_score:   float        # utility score of winner; 0.0 if not complete
    all_candidates: list[UtilityCandidate] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"


class UtilityExecutor(ABC):
    """
    Generate N candidates; rank by utility; select the winner.

    Pattern:
        retrieve(event)                → context          (deterministic; no LLM)
        generate_candidates(context)   → list[Any]        (primary LLM phase)
        utility_score(artifact,        → UtilityCandidate (LLM or deterministic)
                      criteria,
                      context)
        follow_up(event, result)       → Event or None

    Isolation contract for utility_score():
        Must be called with only (artifact, criteria, context).  No conversation
        history from generate_candidates(), no prior candidates.  LLM implementations
        should use a fresh context per call — this mirrors OutcomeExecutor.evaluate().

    Exit statuses:
        "complete"        — winner selected, score >= min_utility
        "no_candidates"   — generate_candidates() returned empty list
        "below_threshold" — all candidates scored below min_utility
        "error"           — unexpected exception during run()
    """

    max_candidates: int = 3
    min_utility: float = 0.5

    def __init__(self, name: str, bus: Any) -> None:
        self.name = name
        self._bus = bus

    # ── Criteria (abstract) ───────────────────────────────────────────────────

    @property
    @abstractmethod
    def criteria(self) -> dict[str, float]:
        """
        Scoring dimensions and their weights.  Weights must sum to 1.0.

        Example::

            {
                "correctness":  0.5,
                "conciseness":  0.3,
                "tone":         0.2,
            }
        """
        ...

    # ── Retrieve ─────────────────────────────────────────────────────────────

    async def retrieve(self, event: "Event") -> dict:
        """
        Assemble context before generation.  Default: return event payload.
        Override to load from the event store, call APIs, etc.  No LLM here.
        """
        return dict(event.payload)

    # ── Generate ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def generate_candidates(self, context: dict) -> list[Any]:
        """
        PRIMARY LLM PHASE — generate up to max_candidates distinct artifacts.

        Prompt for N *different* alternatives, not N variations of the same
        answer.  Returning fewer than max_candidates is valid.
        Empty list → status="no_candidates".
        """
        ...

    # ── Score ─────────────────────────────────────────────────────────────────

    @abstractmethod
    async def utility_score(
        self, artifact: Any, criteria: dict[str, float], context: dict
    ) -> UtilityCandidate:
        """
        Score a single candidate against the criteria.

        Called with ONLY (artifact, criteria, context) — no prior candidates,
        no generate_candidates() history.  For LLM-based scoring each call
        must use a fresh context window.

        Return a UtilityCandidate where criteria_scores keys match the
        criteria dict and utility_score is the weighted sum.
        """
        ...

    # ── Follow-up ─────────────────────────────────────────────────────────────

    async def follow_up(
        self, event: "Event", result: UtilityResult
    ) -> "Optional[Event]":
        """
        Emit a downstream event on complete.  Default: no-op.
        Only called when result.is_complete.  Use event.caused() to
        preserve correlation and causation chains.
        """
        return None

    # ── Runner ────────────────────────────────────────────────────────────────

    async def run(self, event: "Event") -> UtilityResult:
        """
        Execute generate-and-rank:

        1. retrieve(event)  → context
        2. generate_candidates(context) → artifacts
        3. utility_score(artifact, criteria, context) for each → UtilityCandidate
        4. Sort descending by utility_score; select winner
        5. winner.utility_score < min_utility → status="below_threshold"
        6. publish follow_up event if complete
        """
        try:
            context = await self.retrieve(event)

            raw_candidates = await self.generate_candidates(context)
            if not raw_candidates:
                return UtilityResult(status="no_candidates", winner=None, winner_score=0.0)

            scored: list[UtilityCandidate] = []
            for artifact in raw_candidates:
                candidate = await self.utility_score(artifact, self.criteria, context)
                scored.append(candidate)

            scored.sort(key=lambda c: c.utility_score, reverse=True)
            winner = scored[0]

            if winner.utility_score < self.min_utility:
                result = UtilityResult(
                    status="below_threshold",
                    winner=None,
                    winner_score=winner.utility_score,
                    all_candidates=scored,
                )
            else:
                result = UtilityResult(
                    status="complete",
                    winner=winner.artifact,
                    winner_score=winner.utility_score,
                    all_candidates=scored,
                )

            if result.is_complete:
                downstream = await self.follow_up(event, result)
                if downstream is not None:
                    await self._bus.publish(downstream)

            return result

        except Exception as exc:
            log.error("[%s] UtilityExecutor error: %s", self.name, exc, exc_info=True)
            return UtilityResult(status="error", winner=None, winner_score=0.0)
