"""
agentic_govkit/agents/learning.py — GovernanceLearningAgent.

The Learning Element of the AIMA 4-module Learning Agent decomposition
applied to the govkit governance layer.

Current govkit covers:
  Performance Element (decisions) — KillSwitchAgent
  Critic (evaluate + correct)     — AuditAgent
  Learning Element (improve)      — GovernanceLearningAgent  ← this module
  Problem Generator (explore)     — (loopkit-layer; ProblemGeneratorAgent)

GovernanceLearningAgent analyses governance event patterns and emits
``governance.policy_recommendation`` events.  It never applies recommendations
directly — enforcement remains KillSwitchAgent's role.

Usage::

    from agentic_govkit import GovernanceLearningAgent
    from agentic_govkit.events.models import GovernanceEventType

    class GpsGovernanceLearner(GovernanceLearningAgent):
        analysis_window = 15

        async def analyse(self, events):
            breach_events = [
                e for e in events
                if e.event_type == GovernanceEventType.CONFIDENCE_BREACH
            ]
            if len(breach_events) > 10:
                avg = sum(e.payload.get("confidence", 0) for e in breach_events) / len(breach_events)
                return [PolicyRecommendation(
                    policy_key="confidence_threshold",
                    current_value=0.4,
                    recommended_value=round(avg + 0.05, 2),
                    rationale=f"{len(breach_events)} breaches; avg confidence {avg:.2f}",
                    confidence=0.75,
                    evidence_event_ids=[e.event_id for e in breach_events[:5]],
                    tags=["confidence"],
                )]
            return []

    learner = GpsGovernanceLearner("governance-learner", bus)
    learner.subscribe("governance")
    bus.register(learner)
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from agentic_loopkit import AgentBase, Event, EventMeta, TrustLevel, load_events
from agentic_govkit.events.models import GovernanceEventType


@dataclass
class PolicyRecommendation:
    """A single policy adjustment recommended by GovernanceLearningAgent."""
    policy_key:          str
    current_value:       Any
    recommended_value:   Any
    rationale:           str
    confidence:          float          # 0.0–1.0
    evidence_event_ids:  list[str] = field(default_factory=list)
    tags:                list[str] = field(default_factory=list)


class GovernanceLearningAgent(AgentBase):
    """
    Learning Element for the govkit governance layer.

    Accumulates governance events into a rolling window buffer.  When the
    buffer reaches ``analysis_window`` events (or the bus starts), calls
    ``analyse()`` to identify policy drift and emits
    ``governance.policy_recommendation`` events.

    Self-exclusion rules:
      - Ignores events where ``event.source == self.name`` (own output)
      - Ignores ``governance.policy_recommendation`` and
        ``governance.policy_applied`` events (prevents feedback loops)

    OODA wiring:
      observe()  — accumulate; return context when window full or BUS_STARTED   (deterministic)
      orient()   — load history + window; call analyse()                         (LLM phase)
      decide()   — filter recommendations below min_confidence                  (deterministic)
      act()      — emit governance.policy_recommendation; clear window           (deterministic)

    Class attributes:
        analysis_window: Number of governance events to accumulate before triggering.
        min_confidence:  Recommendations below this threshold are discarded.
        history_hours:   How far back to load governance history for analysis.
    """

    analysis_window: int   = 20
    min_confidence:  float = 0.65
    history_hours:   int   = 72

    def __init__(self, name: str, bus: Any) -> None:
        super().__init__(name, bus)
        self._window: list[Event] = []

    # ── OODA pipeline ─────────────────────────────────────────────────────────

    async def observe(self, event: Event) -> Optional[dict[str, Any]]:
        """
        Accumulate qualifying governance events.

        Returns a context dict (triggering orient) when:
        - The rolling window reaches analysis_window size, OR
        - event is system.bus_started (startup analysis pass)

        Self-excludes own events and policy_recommendation / policy_applied
        to prevent the learner from analysing its own output.
        """
        from agentic_loopkit import SystemEventType

        # Self-exclusion — never analyse our own recommendations
        if event.source == self.name:
            return None

        # Exclude recommendation and applied events — they are outputs, not inputs
        if event.event_type in (
            GovernanceEventType.POLICY_RECOMMENDATION,
            GovernanceEventType.POLICY_APPLIED,
        ):
            return None

        # Startup analysis pass — analyse even with an empty window
        if str(event.event_type) == str(SystemEventType.BUS_STARTED):
            return {"window": list(self._window), "trigger": "bus_started"}

        # Accumulate governance events
        if event.stream == "governance":
            self._window.append(event)

        if len(self._window) >= self.analysis_window:
            return {"window": list(self._window), "trigger": "window_full"}

        return None

    async def orient(
        self, event: Event, context: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Load governance history, combine with window, call analyse()."""
        history = load_events(
            "governance",
            store_dir=self._bus.store_dir,
            hours=self.history_hours,
        )
        # Combine history with window; deduplicate by event_id
        seen: set[str] = set()
        combined: list[Event] = []
        for e in history + context["window"]:
            if e.event_id not in seen:
                seen.add(e.event_id)
                combined.append(e)

        recommendations = await self.analyse(combined)

        return {
            "recommendations": recommendations,
            "event_count":     len(combined),
            "trigger":         context["trigger"],
        }

    async def decide(
        self, event: Event, orientation: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Filter recommendations below min_confidence; short-circuit if none remain."""
        recs = [
            r for r in orientation["recommendations"]
            if r.confidence >= self.min_confidence
        ]
        if not recs:
            return None
        return {**orientation, "recommendations": recs}

    async def act(self, event: Event, action: dict[str, Any]) -> None:
        """Emit one governance.policy_recommendation per recommendation; clear window."""
        recs: list[PolicyRecommendation] = action["recommendations"]
        count = len(recs)
        event_count = action["event_count"]

        for rec in recs:
            await self._bus.publish(
                Event(
                    event_type     = GovernanceEventType.POLICY_RECOMMENDATION,
                    source         = self.name,
                    trust_level    = TrustLevel.HIGH,
                    causation_id   = event.event_id,
                    correlation_id = event.correlation_id,
                    payload        = {
                        "policy_key":          rec.policy_key,
                        "current_value":       rec.current_value,
                        "recommended_value":   rec.recommended_value,
                        "rationale":           rec.rationale,
                        "confidence":          rec.confidence,
                        "evidence_event_ids":  rec.evidence_event_ids,
                        "tags":                rec.tags,
                        "_meta": EventMeta(
                            phase="orient",
                            loop_type="ooda",
                            confidence=rec.confidence,
                            context=(
                                f"Governance learning: {count} recommendation(s) "
                                f"from {event_count} governance event(s)"
                            ),
                        ).to_dict(),
                    },
                )
            )

        self._window.clear()

    # ── Abstract ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def analyse(self, events: list[Event]) -> list[PolicyRecommendation]:
        """
        PRIMARY LLM PHASE — identify policy drift from governance event history.

        Receives the combined rolling window + history_hours of stored
        governance events.  Returns a list of PolicyRecommendations.

        Patterns to look for:
        - Repeated confidence_breach on same stream → lower threshold
        - Repeated depth_exceeded from same source → lower max_delegation_depth
        - trust_escalation cluster → consider quarantine policy
        - Absence of flags for N sessions → thresholds may be too permissive
        - human_override following halt/quarantine → thresholds may be too strict
        """
        ...
