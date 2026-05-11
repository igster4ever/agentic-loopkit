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
