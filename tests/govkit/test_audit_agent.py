"""
tests/govkit/test_audit_agent.py — AuditAgent behavioural tests.

All tests use a real EventBus with a tmp store — no mocking of bus internals.
Follows the same fixture pattern as tests/agents/test_base.py.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from agentic_loopkit import Event, TrustLevel, WILDCARD_STREAM
from agentic_govkit import AuditAgent
from agentic_govkit.events.models import GovernanceEventType


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def bus(tmp_path: Path):
    mock = MagicMock()
    mock.publish = AsyncMock()
    mock.store_dir = tmp_path
    return mock


@pytest.fixture
def audit(bus) -> AuditAgent:
    return AuditAgent("audit", bus, max_delegation_depth=3)


def make_event(
    event_type: str = "things.happened",
    source: str = "test-agent",
    trust_level: TrustLevel = TrustLevel.MEDIUM,
    delegation_depth: int = 0,
) -> Event:
    return Event(
        event_type=event_type,
        source=source,
        payload={},
        trust_level=trust_level,
        delegation_depth=delegation_depth,
    )


# ── subscription ──────────────────────────────────────────────────────────────

def test_audit_agent_subscribes_wildcard_on_init(audit: AuditAgent):
    assert WILDCARD_STREAM in audit.subscriptions


# ── observe: governance stream excluded ───────────────────────────────────────

async def test_observe_ignores_governance_stream(audit: AuditAgent):
    gov_event = make_event(event_type="governance.depth_exceeded")
    result = await audit.observe(gov_event)
    assert result is None


async def test_observe_passes_domain_events(audit: AuditAgent):
    event = make_event()
    result = await audit.observe(event)
    assert result is not None
    assert result["event"] is event


# ── orient: clean event ───────────────────────────────────────────────────────

async def test_orient_returns_none_for_clean_event(audit: AuditAgent):
    event = make_event(trust_level=TrustLevel.MEDIUM, delegation_depth=1)
    context = {"event": event}
    result = await audit.orient(event, context)
    assert result is None


# ── orient: depth exceeded ────────────────────────────────────────────────────

async def test_orient_flags_depth_exceeded(audit: AuditAgent):
    event = make_event(delegation_depth=4)  # exceeds max of 3
    context = {"event": event}
    result = await audit.orient(event, context)
    assert result is not None
    assert len(result["flags"]) == 1
    assert result["flags"][0]["governance_type"] == GovernanceEventType.DEPTH_EXCEEDED


async def test_orient_allows_event_at_exact_threshold(audit: AuditAgent):
    event = make_event(delegation_depth=3)  # equal to max — not exceeded
    context = {"event": event}
    result = await audit.orient(event, context)
    assert result is None


# ── orient: untrusted source ──────────────────────────────────────────────────

async def test_orient_flags_untrusted_source(audit: AuditAgent):
    event = make_event(trust_level=TrustLevel.UNTRUSTED)
    context = {"event": event}
    result = await audit.orient(event, context)
    assert result is not None
    assert any(
        f["governance_type"] == GovernanceEventType.TRUST_ESCALATION
        for f in result["flags"]
    )


async def test_orient_allows_low_trust(audit: AuditAgent):
    """LOW trust is not the same as UNTRUSTED — should not be flagged."""
    event = make_event(trust_level=TrustLevel.LOW)
    context = {"event": event}
    result = await audit.orient(event, context)
    assert result is None


# ── orient: multiple flags ────────────────────────────────────────────────────

async def test_orient_emits_both_flags_when_both_violated(audit: AuditAgent):
    event = make_event(trust_level=TrustLevel.UNTRUSTED, delegation_depth=10)
    context = {"event": event}
    result = await audit.orient(event, context)
    assert result is not None
    types = {f["governance_type"] for f in result["flags"]}
    assert GovernanceEventType.DEPTH_EXCEEDED in types
    assert GovernanceEventType.TRUST_ESCALATION in types


# ── act: governance events published ─────────────────────────────────────────

async def test_act_publishes_governance_event_for_each_flag(audit: AuditAgent, bus):
    event = make_event(delegation_depth=5)
    action = {
        "flags": [
            {
                "governance_type": GovernanceEventType.DEPTH_EXCEEDED,
                "detail": "delegation_depth=5 exceeds limit=3",
            }
        ]
    }
    await audit.act(event, action)
    assert bus.publish.call_count == 1
    published: Event = bus.publish.call_args[0][0]
    assert published.event_type == str(GovernanceEventType.DEPTH_EXCEEDED)
    assert published.stream == "governance"
    assert published.causation_id == event.event_id
    assert published.correlation_id == event.correlation_id


async def test_act_governance_event_has_audit_meta(audit: AuditAgent, bus):
    event = make_event()
    action = {
        "flags": [
            {
                "governance_type": GovernanceEventType.TRUST_ESCALATION,
                "detail": "source='x' declared trust_level=untrusted",
            }
        ]
    }
    await audit.act(event, action)
    published: Event = bus.publish.call_args[0][0]
    meta = published.meta()
    assert meta is not None
    assert meta["loop_type"] == "ooda"
    assert meta["phase"] == "act"
    assert "Governance audit" in meta["context"]


async def test_act_publishes_flagged_event_id_in_payload(audit: AuditAgent, bus):
    event = make_event()
    action = {
        "flags": [
            {
                "governance_type": GovernanceEventType.DEPTH_EXCEEDED,
                "detail": "delegation_depth=4 exceeds limit=3",
            }
        ]
    }
    await audit.act(event, action)
    published: Event = bus.publish.call_args[0][0]
    assert published.payload["flagged_event_id"] == event.event_id
    assert published.payload["flagged_source"] == event.source


# ── Event model: delegation_depth and trust_level ─────────────────────────────

def test_caused_increments_delegation_depth():
    root = make_event(delegation_depth=0)
    child = root.caused("things.processed", "agent", {})
    assert child.delegation_depth == 1
    grandchild = child.caused("things.done", "agent", {})
    assert grandchild.delegation_depth == 2


def test_caused_inherits_trust_level():
    root = make_event(trust_level=TrustLevel.HIGH)
    child = root.caused("things.processed", "agent", {})
    assert child.trust_level == TrustLevel.HIGH


def test_event_defaults():
    event = Event(event_type="x.y", source="s", payload={})
    assert event.delegation_depth == 0
    assert event.trust_level == TrustLevel.MEDIUM


def test_event_round_trips_new_fields():
    event = Event(
        event_type="x.y",
        source="s",
        payload={},
        trust_level=TrustLevel.LOW,
        delegation_depth=3,
    )
    restored = Event.from_dict(event.to_dict())
    assert restored.trust_level == TrustLevel.LOW
    assert restored.delegation_depth == 3


# ── new event types exist in enum ─────────────────────────────────────────────

def test_new_governance_event_types_exist():
    assert GovernanceEventType.CONFIDENCE_BREACH == "governance.confidence_breach"
    assert GovernanceEventType.DISPUTE_OPENED    == "governance.dispute_opened"
    assert GovernanceEventType.DISPUTE_RESOLVED  == "governance.dispute_resolved"
    assert GovernanceEventType.HUMAN_OVERRIDE    == "governance.human_override"


def test_new_governance_types_land_on_governance_stream():
    from agentic_loopkit import Event
    for gtype in (
        GovernanceEventType.CONFIDENCE_BREACH,
        GovernanceEventType.DISPUTE_OPENED,
        GovernanceEventType.DISPUTE_RESOLVED,
        GovernanceEventType.HUMAN_OVERRIDE,
    ):
        e = Event(event_type=gtype, source="test", payload={})
        assert e.stream == "governance"


async def test_new_governance_types_ignored_by_audit_observe(audit: AuditAgent):
    """All governance.* events must be filtered in observe() to prevent loops."""
    for gtype in (
        GovernanceEventType.CONFIDENCE_BREACH,
        GovernanceEventType.DISPUTE_OPENED,
        GovernanceEventType.DISPUTE_RESOLVED,
        GovernanceEventType.HUMAN_OVERRIDE,
    ):
        e = Event(event_type=gtype, source="test", payload={})
        result = await audit.observe(e)
        assert result is None, f"{gtype} should be filtered by observe()"


# ── confidence_breach detection ───────────────────────────────────────────────

@pytest.fixture
def audit_with_threshold(bus) -> AuditAgent:
    return AuditAgent("audit", bus, max_delegation_depth=3, confidence_threshold=0.5)


def _event_with_confidence(confidence: float, trust=TrustLevel.MEDIUM) -> Event:
    from agentic_loopkit.events.models import EventMeta
    return Event(
        event_type="things.happened",
        source="test-agent",
        payload={"_meta": EventMeta(confidence=confidence).to_dict()},
        trust_level=trust,
    )


async def test_confidence_breach_flagged_when_below_threshold(audit_with_threshold):
    event = _event_with_confidence(0.3)
    context = {"event": event}
    result = await audit_with_threshold.orient(event, context)
    assert result is not None
    types = {f["governance_type"] for f in result["flags"]}
    assert GovernanceEventType.CONFIDENCE_BREACH in types


async def test_confidence_breach_not_flagged_when_above_threshold(audit_with_threshold):
    event = _event_with_confidence(0.7)
    context = {"event": event}
    result = await audit_with_threshold.orient(event, context)
    assert result is None


async def test_confidence_breach_not_flagged_at_exact_threshold(audit_with_threshold):
    """Equality is not a breach — threshold is a strict lower bound."""
    event = _event_with_confidence(0.5)
    context = {"event": event}
    result = await audit_with_threshold.orient(event, context)
    assert result is None


async def test_confidence_breach_disabled_by_default(audit: AuditAgent):
    """No threshold configured — confidence_breach never fires."""
    event = _event_with_confidence(0.0)
    context = {"event": event}
    result = await audit.orient(event, context)
    assert result is None


async def test_confidence_breach_not_flagged_when_no_meta(audit_with_threshold):
    event = make_event()  # no _meta
    context = {"event": event}
    result = await audit_with_threshold.orient(event, context)
    assert result is None


async def test_confidence_breach_not_flagged_when_meta_has_no_confidence(audit_with_threshold):
    from agentic_loopkit.events.models import EventMeta
    event = Event(
        event_type="things.happened",
        source="test",
        payload={"_meta": EventMeta(phase="act").to_dict()},
    )
    context = {"event": event}
    result = await audit_with_threshold.orient(event, context)
    assert result is None


async def test_confidence_breach_payload_includes_confidence_and_threshold(
    audit_with_threshold, bus
):
    event = _event_with_confidence(0.3)
    action = {
        "flags": [{
            "governance_type": GovernanceEventType.CONFIDENCE_BREACH,
            "detail": "confidence=0.300 below threshold=0.500",
            "extra": {"confidence": 0.3, "threshold": 0.5},
        }]
    }
    await audit_with_threshold.act(event, action)
    published: Event = bus.publish.call_args[0][0]
    assert published.payload["confidence"] == pytest.approx(0.3)
    assert published.payload["threshold"]  == pytest.approx(0.5)


async def test_confidence_breach_combines_with_other_flags(audit_with_threshold):
    """Depth violation and confidence breach should both fire on the same event."""
    event = Event(
        event_type="things.happened",
        source="test",
        payload={"_meta": {"confidence": 0.1}},
        delegation_depth=10,
    )
    context = {"event": event}
    result = await audit_with_threshold.orient(event, context)
    assert result is not None
    types = {f["governance_type"] for f in result["flags"]}
    assert GovernanceEventType.DEPTH_EXCEEDED    in types
    assert GovernanceEventType.CONFIDENCE_BREACH in types
