"""
tests/govkit/test_killswitch.py — KillSwitchAgent tests.

Covers:
  - halt_correlation emitted when policy maps DEPTH_EXCEEDED
  - quarantine_source emitted when policy maps TRUST_ESCALATION
  - emit_human_override emitted when policy maps CONFIDENCE_BREACH
  - enforcement events carry TrustLevel.HIGH
  - enforcement events are caused by the trigger governance event (causation chain)
  - events not in the policy are silently ignored
  - own-emitted events are not re-processed (no feedback loop)
  - halt_correlation carries correlation_id from trigger
  - quarantine_source extracts flagged_source from trigger payload
  - emit_human_override carries flagged_event_id from trigger payload
  - multiple policy entries — each fires independently
"""

import pytest
from agentic_loopkit import EventBus, Event, TrustLevel
from agentic_loopkit.events.store import load_events
from agentic_govkit import KillSwitchAgent, GovernanceEventType
from agentic_govkit.agents.killswitch import (
    halt_correlation,
    quarantine_source,
    emit_human_override,
)


def make_governance_event(
    event_type: GovernanceEventType,
    source: str = "audit",
    correlation_id: str = "corr-1",
    **payload_extra,
) -> Event:
    return Event(
        event_type=event_type,
        source=source,
        payload={
            "flagged_event_id":   "ev-flagged",
            "flagged_event_type": "some.event",
            "flagged_source":     "suspect-agent",
            "detail":             "test detail",
            **payload_extra,
        },
        correlation_id=correlation_id,
    )


# ── halt_correlation ──────────────────────────────────────────────────────────

async def test_halt_emitted_on_depth_exceeded(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.DEPTH_EXCEEDED: halt_correlation,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(GovernanceEventType.DEPTH_EXCEEDED))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    assert any(e.event_type == GovernanceEventType.HALT for e in stored)


async def test_halt_carries_correlation_id(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.DEPTH_EXCEEDED: halt_correlation,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(GovernanceEventType.DEPTH_EXCEEDED, correlation_id="corr-halt"))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    halt = next(e for e in stored if e.event_type == GovernanceEventType.HALT)
    assert halt.payload["correlation_id"] == "corr-halt"


async def test_halt_trust_level_is_high(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.DEPTH_EXCEEDED: halt_correlation,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(GovernanceEventType.DEPTH_EXCEEDED))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    halt = next(e for e in stored if e.event_type == GovernanceEventType.HALT)
    assert halt.trust_level == TrustLevel.HIGH


# ── quarantine_source ─────────────────────────────────────────────────────────

async def test_quarantine_emitted_on_trust_escalation(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.TRUST_ESCALATION: quarantine_source,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(GovernanceEventType.TRUST_ESCALATION))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    assert any(e.event_type == GovernanceEventType.QUARANTINE for e in stored)


async def test_quarantine_extracts_flagged_source(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.TRUST_ESCALATION: quarantine_source,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(
        GovernanceEventType.TRUST_ESCALATION, flagged_source="bad-agent"
    ))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    q = next(e for e in stored if e.event_type == GovernanceEventType.QUARANTINE)
    assert q.payload["quarantined_source"] == "bad-agent"


async def test_quarantine_trust_level_is_high(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.TRUST_ESCALATION: quarantine_source,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(GovernanceEventType.TRUST_ESCALATION))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    q = next(e for e in stored if e.event_type == GovernanceEventType.QUARANTINE)
    assert q.trust_level == TrustLevel.HIGH


# ── emit_human_override ───────────────────────────────────────────────────────

async def test_human_override_emitted_on_confidence_breach(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.CONFIDENCE_BREACH: emit_human_override,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(GovernanceEventType.CONFIDENCE_BREACH))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    assert any(e.event_type == GovernanceEventType.HUMAN_OVERRIDE for e in stored)


async def test_human_override_carries_flagged_event_id(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.CONFIDENCE_BREACH: emit_human_override,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(
        GovernanceEventType.CONFIDENCE_BREACH, flagged_event_id="ev-123"
    ))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    override = next(e for e in stored if e.event_type == GovernanceEventType.HUMAN_OVERRIDE)
    assert override.payload["flagged_event_id"] == "ev-123"


async def test_human_override_trust_level_is_high(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.CONFIDENCE_BREACH: emit_human_override,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(GovernanceEventType.CONFIDENCE_BREACH))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    override = next(e for e in stored if e.event_type == GovernanceEventType.HUMAN_OVERRIDE)
    assert override.trust_level == TrustLevel.HIGH


# ── Causation chain ───────────────────────────────────────────────────────────

async def test_enforcement_event_caused_by_trigger(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.DEPTH_EXCEEDED: halt_correlation,
    })
    kill.subscribe("governance")
    bus.register(kill)

    trigger = make_governance_event(GovernanceEventType.DEPTH_EXCEEDED)
    await bus.start()
    await bus.publish(trigger)
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    halt = next(e for e in stored if e.event_type == GovernanceEventType.HALT)
    assert halt.causation_id == trigger.event_id


# ── Policy miss — no action ───────────────────────────────────────────────────

async def test_event_not_in_policy_ignored(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.DEPTH_EXCEEDED: halt_correlation,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    # TRUST_ESCALATION is not in the policy
    await bus.publish(make_governance_event(GovernanceEventType.TRUST_ESCALATION))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    enforcement = [e for e in stored if e.event_type in (
        GovernanceEventType.HALT,
        GovernanceEventType.QUARANTINE,
        GovernanceEventType.HUMAN_OVERRIDE,
    )]
    assert enforcement == []


# ── No feedback loop ──────────────────────────────────────────────────────────

async def test_own_emitted_events_not_reprocessed(tmp_path):
    """KillSwitchAgent must not react to events it emitted itself."""
    bus = EventBus(store_dir=tmp_path)
    # Map HALT back to halt_correlation — would loop if not self-excluded
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.DEPTH_EXCEEDED: halt_correlation,
        GovernanceEventType.HALT:           halt_correlation,  # potential loop
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(GovernanceEventType.DEPTH_EXCEEDED))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    halts = [e for e in stored if e.event_type == GovernanceEventType.HALT]
    assert len(halts) == 1  # exactly one halt; no cascade


# ── Multiple policy entries ───────────────────────────────────────────────────

async def test_multiple_policy_entries_fire_independently(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    kill = KillSwitchAgent("ks", bus, policy={
        GovernanceEventType.DEPTH_EXCEEDED:    halt_correlation,
        GovernanceEventType.TRUST_ESCALATION:  quarantine_source,
        GovernanceEventType.CONFIDENCE_BREACH: emit_human_override,
    })
    kill.subscribe("governance")
    bus.register(kill)

    await bus.start()
    await bus.publish(make_governance_event(GovernanceEventType.DEPTH_EXCEEDED))
    await bus.publish(make_governance_event(GovernanceEventType.TRUST_ESCALATION))
    await bus.publish(make_governance_event(GovernanceEventType.CONFIDENCE_BREACH))
    await bus.stop()

    stored = load_events("governance", store_dir=tmp_path)
    event_types = {str(e.event_type) for e in stored}
    assert GovernanceEventType.HALT       in event_types
    assert GovernanceEventType.QUARANTINE in event_types
    assert GovernanceEventType.HUMAN_OVERRIDE in event_types


# ── governance stream namespace check ────────────────────────────────────────

def test_halt_and_quarantine_in_governance_stream():
    """HALT and QUARANTINE must carry 'governance.' prefix (stream test)."""
    assert str(GovernanceEventType.HALT).startswith("governance.")
    assert str(GovernanceEventType.QUARANTINE).startswith("governance.")
