"""
agentic_loopkit/agents/performance.py — PerformanceMeasure protocol.

Post-hoc analytical tool for evaluating agent output quality over time.
Not wired into the runtime loop — compute from stored events on demand.

Usage:
    from agentic_loopkit import load_events, SimpleConfidencePerformance

    events = load_events("*", hours=72)
    score  = SimpleConfidencePerformance().score("my-agent", events)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..utils.time import utc_now, iso_format

if TYPE_CHECKING:
    from ..events.models import Event


@dataclass
class PerformanceScore:
    """Agent performance snapshot computed from a window of events."""

    agent_name:       str
    window_hours:     int
    mean_confidence:  float | None   # mean _meta.confidence; None if no events carry confidence
    event_count:      int            # events emitted by this agent in the window
    follow_up_rate:   float | None   # fraction of runs producing a follow_up; None = not computed
    governance_flags: int            # governance.* events referencing this agent in window
    trend:            str            # "improving" | "stable" | "degrading" | "insufficient_data"
    computed_at:      str            # ISO 8601 UTC


class PerformanceMeasure(ABC):
    """
    Session-spanning evaluator for a named agent.

    Implementations read from a provided event list and return a PerformanceScore.
    Deterministic — no LLM calls.

    Integration points:
      - GovernanceLearningAgent.analyse() — pass per-agent scores as analysis context
      - Dashboard /api/agents route — augment agent list with current score
      - Compass session close — record mean_confidence + governance_flags in reality snapshot
    """

    @abstractmethod
    def score(
        self,
        agent_name: str,
        events: list["Event"],
        *,
        window_hours: int = 72,
    ) -> PerformanceScore:
        """
        Compute a performance score for agent_name from a list of events.

        events       — full event list in scope (load via load_events("*", hours=N))
        window_hours — informational; included in PerformanceScore for context
        """


class SimpleConfidencePerformance(PerformanceMeasure):
    """
    Baseline PerformanceMeasure: mean _meta.confidence + governance flag count.

    Trend is computed by splitting the agent's confidence-bearing events in two
    halves (chronological order) and comparing means:
        diff > +0.05  → "improving"
        diff < -0.05  → "degrading"
        |diff| ≤ 0.05 → "stable"
        < 4 confidence events → "insufficient_data"

    follow_up_rate is always None — computing it requires causation-chain analysis
    beyond the scope of this baseline implementation.
    """

    def score(
        self,
        agent_name: str,
        events: list["Event"],
        *,
        window_hours: int = 72,
    ) -> PerformanceScore:
        agent_events = [e for e in events if e.source == agent_name]

        confidences: list[float] = [
            e.meta()["confidence"]
            for e in agent_events
            if e.meta() and "confidence" in e.meta()
        ]
        mean_conf = sum(confidences) / len(confidences) if confidences else None

        gov_flags = sum(
            1 for e in events
            if e.stream == "governance"
            and e.payload.get("flagged_source") == agent_name
        )

        return PerformanceScore(
            agent_name       = agent_name,
            window_hours     = window_hours,
            mean_confidence  = round(mean_conf, 4) if mean_conf is not None else None,
            event_count      = len(agent_events),
            follow_up_rate   = None,
            governance_flags = gov_flags,
            trend            = self._trend(confidences),
            computed_at      = iso_format(utc_now()),
        )

    @staticmethod
    def _trend(confidences: list[float]) -> str:
        if len(confidences) < 4:
            return "insufficient_data"
        mid         = len(confidences) // 2
        first_mean  = sum(confidences[:mid]) / mid
        second_mean = sum(confidences[mid:]) / (len(confidences) - mid)
        diff = second_mean - first_mean
        if diff > 0.05:
            return "improving"
        if diff < -0.05:
            return "degrading"
        return "stable"
