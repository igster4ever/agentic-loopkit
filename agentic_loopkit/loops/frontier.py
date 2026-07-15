"""
agentic_loopkit/loops/frontier.py — BranchScore + FrontierSelector (P62a).

Generalises MetaSkill-Evolve's frontier-selection formula
(arXiv:2607.05297, eta1*U + eta2*P_hat + eta3*N) into a reusable, domain-agnostic
ranking primitive: utility, productivity (is this branch still generating gains),
and novelty/visit-cooling — not utility alone.

Sibling to UtilityExecutor (single-pass generate-and-rank against fixed criteria).
FrontierSelector instead ranks an existing pool of candidates that carry their
own utility *history*, favouring branches that are still improving and haven't
been picked to death over branches that are merely highest-scoring right now.

Scope guard (matches the source paper's own bounded recursion): this module is
a scoring primitive only. No automatic branch-forking, no persistent evolution
DAG — callers supply their own candidate pool and history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class BranchScore:
    """Weighted utility + productivity + novelty score for one candidate branch."""

    utility:      float
    productivity: float
    novelty:      float
    weights:      tuple[float, float, float] = (1.0, 1.0, 1.0)   # (eta1, eta2, eta3)

    def score(self) -> float:
        eta1, eta2, eta3 = self.weights
        return eta1 * self.utility + eta2 * self.productivity + eta3 * self.novelty


@dataclass
class FrontierCandidate:
    """
    A candidate branch tracked across selection rounds.

    ``utility_history`` — utility score at each past round this candidate was
    evaluated, oldest first. Used by the default productivity estimator.
    ``times_selected`` — number of prior rounds this candidate was chosen as
    the winner. Used by the default novelty (visit-cooling) estimator.
    """

    id:                  str
    artifact:            Any
    utility:             float
    utility_history:     list[float] = field(default_factory=list)
    times_selected:      int = 0
    revoked:             bool = False
    revocation_reason:   Optional[str] = None


class FrontierSelector:
    """
    Ranks a pool of FrontierCandidate objects by BranchScore.

    Default estimators:
      productivity — mean utility delta over utility_history (MetaSkill-Evolve
                     Eq. 3 shape: empirical mean of Delta-U over the last H rounds).
                     0.0 if fewer than 2 history points (insufficient signal).
      novelty       — 1 / (1 + times_selected) — the paper's exact visit-cooling term.

    Both are overridable via ``productivity_fn`` / ``novelty_fn`` for callers
    with a domain-specific signal (e.g. compass's goal-outcome-linked
    productivity from P61a).
    """

    def __init__(
        self,
        weights: tuple[float, float, float] = (1.0, 1.0, 1.0),
        productivity_fn: Optional[Callable[[FrontierCandidate], float]] = None,
        novelty_fn: Optional[Callable[[FrontierCandidate], float]] = None,
    ) -> None:
        self.weights = weights
        self._productivity_fn = productivity_fn or self._default_productivity
        self._novelty_fn = novelty_fn or self._default_novelty

    @staticmethod
    def _default_productivity(candidate: FrontierCandidate) -> float:
        history = candidate.utility_history
        if len(history) < 2:
            return 0.0
        deltas = [history[i] - history[i - 1] for i in range(1, len(history))]
        return sum(deltas) / len(deltas)

    @staticmethod
    def _default_novelty(candidate: FrontierCandidate) -> float:
        return 1.0 / (1.0 + candidate.times_selected)

    @staticmethod
    def revoke(candidate: FrontierCandidate, reason: str) -> None:
        """
        De-authorize a candidate branch — e.g. a later regression traced back to
        it. Revocation is permanent and absolute: unlike novelty/productivity,
        which merely shift a candidate's rank via future utility_history, a
        revoked candidate is excluded from rank()/select() entirely regardless
        of any utility it accrues afterward. Callers still holding a reference
        to the candidate see the recorded reason via ``revocation_reason``.
        """
        candidate.revoked = True
        candidate.revocation_reason = reason

    def score(self, candidate: FrontierCandidate) -> BranchScore:
        return BranchScore(
            utility=candidate.utility,
            productivity=self._productivity_fn(candidate),
            novelty=self._novelty_fn(candidate),
            weights=self.weights,
        )

    def rank(
        self, candidates: list[FrontierCandidate]
    ) -> list[tuple[FrontierCandidate, BranchScore]]:
        """Return (candidate, BranchScore) pairs, highest score() first.

        Revoked candidates are excluded entirely — they never surface, even
        at the bottom of the ranking.
        """
        scored = [
            (c, self.score(c)) for c in candidates if not c.revoked
        ]
        scored.sort(key=lambda pair: pair[1].score(), reverse=True)
        return scored

    def select(
        self, candidates: list[FrontierCandidate]
    ) -> Optional[tuple[FrontierCandidate, BranchScore]]:
        """Return the top-ranked (candidate, BranchScore) pair, or None if empty."""
        ranked = self.rank(candidates)
        return ranked[0] if ranked else None
