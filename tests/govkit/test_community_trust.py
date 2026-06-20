"""
tests/govkit/test_community_trust.py — CommunityTrustLearner unit + integration tests.

Unit tests cover the analyse() logic directly.
Integration test proves the end-to-end chain:
  CommunityFeedAdapter → AuditAgent → CommunityTrustLearner
                                          → governance.policy_recommendation (UNTRUSTED → LOW)
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_loopkit import Event, TrustLevel
from agentic_loopkit.bus import EventBus
from agentic_govkit import AuditAgent, GovernanceEventType
from agentic_govkit.agents.community_trust import CommunityTrustLearner


# ── helpers ───────────────────────────────────────────────────────────────────

def mock_bus(tmp_path: Path):
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.store_dir = tmp_path
    return bus


def trust_escalation_event(source: str = "community") -> Event:
    return Event(
        event_type=GovernanceEventType.TRUST_ESCALATION,
        source="audit",
        payload={
            "flagged_source": source,
            "flagged_event_type": "community.entry_received",
            "detail": f"source='{source}' declared trust_level=untrusted",
        },
        trust_level=TrustLevel.HIGH,
    )


def halt_event(source: str = "community") -> Event:
    return Event(
        event_type=GovernanceEventType.HALT,
        source="killswitch",
        payload={"flagged_source": source},
        trust_level=TrustLevel.HIGH,
    )


# ── CommunityTrustLearner.analyse() — unit tests ──────────────────────────────

@pytest.mark.asyncio
async def test_analyse_recommends_promotion_after_min_observations(tmp_path):
    learner = CommunityTrustLearner("trust-learner", mock_bus(tmp_path))
    learner.min_observations = 3

    events = [trust_escalation_event("gps-feed") for _ in range(3)]
    recs = await learner.analyse(events)

    assert len(recs) == 1
    rec = recs[0]
    assert rec.policy_key == "source_trust_graduation"
    assert rec.current_value == "UNTRUSTED"
    assert rec.recommended_value == "LOW"
    assert "gps-feed" in rec.rationale
    assert rec.confidence >= 0.65


@pytest.mark.asyncio
async def test_analyse_no_recommendation_below_min_observations(tmp_path):
    learner = CommunityTrustLearner("trust-learner", mock_bus(tmp_path))
    learner.min_observations = 5

    events = [trust_escalation_event("gps-feed") for _ in range(4)]
    recs = await learner.analyse(events)

    assert recs == []


@pytest.mark.asyncio
async def test_analyse_vetoes_source_with_halt(tmp_path):
    learner = CommunityTrustLearner("trust-learner", mock_bus(tmp_path))
    learner.min_observations = 3

    events = [trust_escalation_event("bad-feed") for _ in range(5)]
    events.append(halt_event("bad-feed"))

    recs = await learner.analyse(events)
    assert recs == []


@pytest.mark.asyncio
async def test_analyse_multiple_sources_independent(tmp_path):
    learner = CommunityTrustLearner("trust-learner", mock_bus(tmp_path))
    learner.min_observations = 3

    events = (
        [trust_escalation_event("feed-a") for _ in range(5)] +
        [trust_escalation_event("feed-b") for _ in range(2)] +  # below threshold
        [trust_escalation_event("feed-c") for _ in range(4)] +
        [halt_event("feed-c")]  # vetoed
    )

    recs = await learner.analyse(events)
    sources = {r.tags[2] for r in recs}

    assert "feed-a" in sources
    assert "feed-b" not in sources   # below min_observations
    assert "feed-c" not in sources   # halted


@pytest.mark.asyncio
async def test_analyse_confidence_scales_with_observation_count(tmp_path):
    learner = CommunityTrustLearner("trust-learner", mock_bus(tmp_path))
    learner.min_observations = 3

    events_5  = [trust_escalation_event("feed-a") for _ in range(5)]
    events_15 = [trust_escalation_event("feed-b") for _ in range(15)]

    recs_5  = await learner.analyse(events_5)
    recs_15 = await learner.analyse(events_15)

    assert recs_5[0].confidence < recs_15[0].confidence


@pytest.mark.asyncio
async def test_analyse_confidence_capped_at_0_9(tmp_path):
    learner = CommunityTrustLearner("trust-learner", mock_bus(tmp_path))
    learner.min_observations = 3

    events = [trust_escalation_event("feed-a") for _ in range(200)]
    recs = await learner.analyse(events)

    assert recs[0].confidence <= 0.9


@pytest.mark.asyncio
async def test_analyse_recommendation_tags_include_source(tmp_path):
    learner = CommunityTrustLearner("trust-learner", mock_bus(tmp_path))
    learner.min_observations = 3

    events = [trust_escalation_event("my-feed") for _ in range(3)]
    recs = await learner.analyse(events)

    assert "my-feed" in recs[0].tags
    assert "trust-graduation" in recs[0].tags
    assert "community" in recs[0].tags


# ── Integration — OODA pipeline wiring ───────────────────────────────────────

@pytest.mark.asyncio
async def test_ooda_pipeline_emits_policy_recommendation_on_window_fill(tmp_path):
    """
    CommunityTrustLearner accumulates governance.trust_escalation events
    via its OODA observe() → orient() → decide() → act() pipeline.
    On window fill it calls analyse() and emits governance.policy_recommendation.
    """
    bus = mock_bus(tmp_path)
    learner = CommunityTrustLearner("trust-learner", bus)
    learner.analysis_window = 3
    learner.min_observations = 3
    learner.subscribe("governance")

    for _ in range(3):
        event = trust_escalation_event("gps-feed")
        await learner._handle(event)

    published = bus.publish.call_args_list
    rec_events = [
        call.args[0] for call in published
        if call.args[0].event_type == GovernanceEventType.POLICY_RECOMMENDATION
    ]

    assert len(rec_events) >= 1
    payload = rec_events[0].payload
    assert payload["policy_key"] == "source_trust_graduation"
    assert payload["recommended_value"] == "LOW"
    assert payload["current_value"] == "UNTRUSTED"


@pytest.mark.asyncio
async def test_ooda_pipeline_no_recommendation_if_below_min_observations(tmp_path):
    bus = mock_bus(tmp_path)
    learner = CommunityTrustLearner("trust-learner", bus)
    learner.analysis_window = 3
    learner.min_observations = 5  # needs 5, only 3 will be fed
    learner.subscribe("governance")

    for _ in range(3):
        event = trust_escalation_event("gps-feed")
        await learner._handle(event)

    published = bus.publish.call_args_list
    rec_events = [
        call.args[0] for call in published
        if call.args[0].event_type == GovernanceEventType.POLICY_RECOMMENDATION
    ]
    assert len(rec_events) == 0


# ── Full chain integration (real EventBus) ───────────────────────────────────

@pytest.mark.asyncio
async def test_full_chain_untrusted_to_low_promotion(tmp_path):
    """
    End-to-end: CommunityFeedAdapter → AuditAgent → CommunityTrustLearner
    Proves the UNTRUSTED → LOW governance.policy_recommendation is emitted.
    """
    from agentic_loopkit.adapters.community import CommunityFeedAdapter

    bus = EventBus(store_dir=tmp_path)

    # Audit agent catches UNTRUSTED events → emits trust_escalation
    audit = AuditAgent("audit", bus)
    audit.subscribe("*")
    bus.register(audit)

    # Trust learner accumulates trust_escalation → recommends promotion
    learner = CommunityTrustLearner("trust-learner", bus)
    learner.analysis_window = 5
    learner.min_observations = 5
    learner.subscribe("governance")
    bus.register(learner)

    # Track emitted policy_recommendation events
    emitted: list[Event] = []

    async def capture(event: Event) -> None:
        if event.event_type == GovernanceEventType.POLICY_RECOMMENDATION:
            emitted.append(event)

    bus.router.subscribe("governance", capture)

    # Write a JSONL community feed
    feed_path = tmp_path / "community.jsonl"
    entries = [{"id": f"entry-{i}", "data": f"item {i}"} for i in range(5)]
    feed_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

    # Tick the adapter — emits 5 UNTRUSTED community.entry_received events
    adapter = CommunityFeedAdapter(bus=bus, feed_path=feed_path, name="gps-feed")
    await adapter.tick()

    # AuditAgent processes each event synchronously via the bus router
    # governance.trust_escalation events are now in the store.
    # Manually drive the trust learner's OODA for each trust_escalation event.
    from agentic_loopkit.events.store import load_events
    gov_events = load_events("governance", store_dir=tmp_path, hours=1)
    trust_escalations = [
        e for e in gov_events
        if str(e.event_type) == str(GovernanceEventType.TRUST_ESCALATION)
    ]

    for event in trust_escalations[:5]:
        await learner._handle(event)

    assert len(emitted) >= 1
    rec = emitted[0]
    assert rec.payload["policy_key"] == "source_trust_graduation"
    assert rec.payload["recommended_value"] == "LOW"
    assert rec.trust_level == TrustLevel.HIGH


# ── Module boundary ────────────────────────────────────────────────────────────

def test_community_trust_learner_exported_from_govkit():
    from agentic_govkit import CommunityTrustLearner as CTL
    assert CTL is not None
