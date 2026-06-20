"""
agentic_govkit/agents/community_trust.py — Community feed trust graduation agent.

Analyses governance event history for community sources (those that emit events at
TrustLevel.UNTRUSTED) and recommends trust promotion once a source has demonstrated
sustained compliant behaviour.

Trust graduation is strictly one level at a time:
    UNTRUSTED → LOW → MEDIUM → HIGH

Skipping levels is not supported — aggregate_confidence() weighting decays steeply
across delegation_depth and a jump would produce misleading confidence scores.

Wiring::

    from agentic_govkit import AuditAgent
    from agentic_govkit.agents.community_trust import CommunityTrustLearner

    audit   = AuditAgent("audit", bus)
    learner = CommunityTrustLearner("community-trust", bus)
    learner.subscribe("governance")
    bus.register(audit)
    bus.register(learner)

    # AuditAgent emits governance.trust_escalation for every UNTRUSTED event.
    # CommunityTrustLearner accumulates those signals in its rolling window;
    # once it has seen min_observations trust_escalation events from a source
    # with no halt/quarantine, it emits governance.policy_recommendation
    # recommending promotion to TrustLevel.LOW.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from agentic_loopkit import Event
from agentic_govkit.agents.learning import GovernanceLearningAgent, PolicyRecommendation
from agentic_govkit.events.models import GovernanceEventType


class CommunityTrustLearner(GovernanceLearningAgent):
    """
    Concrete GovernanceLearningAgent for community feed trust graduation.

    Reads governance event history for the community stream and recommends
    UNTRUSTED → LOW promotion for sources that have accumulated
    ``min_observations`` trust_escalation events without triggering a
    halt or quarantine.

    Class attributes:
        analysis_window    — inherited; events to accumulate before triggering
        min_observations   — minimum trust_escalation count before a source
                             is considered for promotion (default: 5)
        halt_veto_window   — if a source triggered halt/quarantine within this
                             many events, it is excluded from promotion (default: 50)
    """

    min_observations:  int = 5
    halt_veto_window:  int = 50

    async def analyse(self, events: list[Event]) -> list[PolicyRecommendation]:
        """
        PRIMARY LLM PHASE (here: deterministic rule-based for infrastructure trust).

        Examines the governance event history for community sources.
        For each source that has:
          - ≥ min_observations trust_escalation events in the window
          - No halt or quarantine events within halt_veto_window events

        Recommends promotion from UNTRUSTED to LOW.

        Returns one PolicyRecommendation per eligible source.
        """
        # Tally trust_escalation events by source
        escalations_by_source: Counter[str] = Counter()
        vetoed_sources: set[str] = set()

        # Walk events oldest-first; track the last halt_veto_window events per source
        recent_by_source: defaultdict[str, list[str]] = defaultdict(list)

        for event in events:
            # AuditAgent stores the flagged source as "flagged_source";
            # allow "source" as a fallback for tests and custom emitters.
            source = (
                event.payload.get("flagged_source") or event.payload.get("source") or ""
            ) if event.payload else ""
            if not source:
                continue

            event_type = str(event.event_type)

            if event_type == str(GovernanceEventType.TRUST_ESCALATION):
                escalations_by_source[source] += 1
                recent_by_source[source].append(event_type)

            elif event_type in (
                str(GovernanceEventType.HALT),
                str(GovernanceEventType.QUARANTINE),
            ):
                vetoed_sources.add(source)
                recent_by_source[source].append(event_type)

        recommendations: list[PolicyRecommendation] = []

        for source, count in escalations_by_source.items():
            if source in vetoed_sources:
                continue
            if count < self.min_observations:
                continue

            # Check recent window for halt/quarantine veto
            recent = recent_by_source[source][-self.halt_veto_window:]
            if any(
                t in (str(GovernanceEventType.HALT), str(GovernanceEventType.QUARANTINE))
                for t in recent
            ):
                continue

            confidence = min(0.9, 0.65 + (count - self.min_observations) * 0.02)

            recommendations.append(PolicyRecommendation(
                policy_key        = "source_trust_graduation",
                current_value     = "UNTRUSTED",
                recommended_value = "LOW",
                rationale         = (
                    f"Source '{source}' has submitted {count} community events "
                    f"without triggering halt or quarantine — eligible for UNTRUSTED → LOW promotion."
                ),
                confidence        = round(confidence, 2),
                evidence_event_ids= [],
                tags              = ["trust-graduation", "community", source],
            ))

        return recommendations
