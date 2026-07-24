"""
tests/govkit/test_community_trust_concurrency.py

Session hypothesis (2026-07-04, agentic-loopkit compass namespace):

  "With two or more concurrent community sources, a high-volume misbehaving
  source can dominate the shared analysis_window (20 events, cleared on
  every fill — see GovernanceLearningAgent._window in learning.py) and get
  cleared before a quieter, genuinely-promotable source accumulates its
  min_observations — starving legitimate trust graduation."

This runs the full live OODA pipeline (CommunityFeedAdapter-style events via
AuditAgent → CommunityTrustLearner, real EventBus + disk-persisted store) with
two concurrent sources: a noisy one that alone fills the window many times
over, and a quiet one that only ever accumulates exactly min_observations
escalations, interleaved so its threshold is crossed mid-window rather than
at a window boundary.

Result informs whether the hypothesis holds — see the two tests below.
"""

from pathlib import Path

import pytest

from agentic_govkit import AuditAgent, GovernanceEventType
from agentic_govkit.agents.community_trust import CommunityTrustLearner
from agentic_loopkit import Event, EventBus, TrustLevel


def untrusted_event(source: str) -> Event:
    """A community.entry_received-shaped event at TrustLevel.UNTRUSTED — what
    CommunityFeedAdapter emits, and what AuditAgent flags as trust_escalation."""
    return Event(
        event_type="community.entry_received",
        source=source,
        payload={"source": source, "detail": "entry"},
        trust_level=TrustLevel.UNTRUSTED,
    )


async def _collect_recommendations(bus: EventBus, published: list[Event]) -> list[Event]:
    recs = []

    async def _collect(event: Event) -> None:
        if event.event_type == GovernanceEventType.POLICY_RECOMMENDATION:
            recs.append(event)

    bus.router.subscribe("governance", _collect)
    for event in published:
        await bus.publish(event)
    return recs


@pytest.mark.asyncio
async def test_quiet_source_is_not_starved_by_noisy_concurrent_source(tmp_path: Path):
    """Interleave a noisy source's 30 escalations with a quiet source's 5 —
    the quiet source's 5th escalation lands well inside a window the noisy
    source will fill, never at a window boundary of its own."""
    bus = EventBus(store_dir=tmp_path / "events")
    audit = AuditAgent("audit", bus)
    audit.subscribe("*")
    bus.register(audit)

    learner = CommunityTrustLearner("community-trust", bus)
    learner.subscribe("governance")
    bus.register(learner)

    # Interleave: 4 noisy events, 1 quiet, repeat — so by the time the window
    # fills (20 governance events => ~20 community events, since AuditAgent
    # emits 1:1), the quiet source has already crossed min_observations=5
    # somewhere in the middle of that window, never at its edge.
    published: list[Event] = []
    quiet_count = 0
    for _ in range(30):
        published.append(untrusted_event("noisy-source"))
        if quiet_count < 5:
            published.append(untrusted_event("quiet-source"))
            quiet_count += 1

    recs = await _collect_recommendations(bus, published)

    sources_promoted = {rec.payload["tags"][2] for rec in recs}
    assert "quiet-source" in sources_promoted, (
        "quiet-source accumulated 5 trust_escalation events (min_observations) "
        "but never received a policy_recommendation — starvation hypothesis CONFIRMED"
    )
    assert "noisy-source" in sources_promoted


@pytest.mark.asyncio
async def test_quiet_source_promoted_even_if_window_fills_entirely_from_noisy_source(tmp_path: Path):
    """Stricter variant: all of the quiet source's events land BEFORE any
    window-fill trigger, then a purely noisy burst fills the window. If the
    shared window were the only evidence analyse() saw, the quiet source's
    earlier events would have been evicted/cleared and never counted."""
    bus = EventBus(store_dir=tmp_path / "events")
    audit = AuditAgent("audit", bus)
    audit.subscribe("*")
    bus.register(audit)

    learner = CommunityTrustLearner("community-trust", bus)
    learner.subscribe("governance")
    bus.register(learner)

    published: list[Event] = []
    # Quiet source crosses min_observations first, in isolation.
    for _ in range(5):
        published.append(untrusted_event("quiet-source"))
    # Then a noisy burst alone fills (and clears) the shared window.
    for _ in range(25):
        published.append(untrusted_event("noisy-source"))

    recs = await _collect_recommendations(bus, published)

    sources_promoted = {rec.payload["tags"][2] for rec in recs}
    assert "quiet-source" in sources_promoted, (
        "quiet-source's escalations were recorded before the window filled with "
        "noisy-source traffic, but the recommendation never fired — the shared "
        "window evicted history that orient()'s disk reload should have restored"
    )
