import pytest
from agentic_loopkit.agents.failure_pattern import FailurePatternAgent, FailureSignature
from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event, SystemEventType, TrustLevel


# ── Concrete subclass ──────────────────────────────────────────────────────────

class ConcreteFailurePatternAgent(FailurePatternAgent):
    async def materialise(self, events):
        return f"pattern:{len(events)}"


# ── Helpers ────────────────────────────────────────────────────────────────────

def halt_event(source="killswitch") -> Event:
    return Event(event_type="governance.halt", source=source, payload={"correlation_id": "c1"})


def adapter_error_event(source="clickup-adapter") -> Event:
    return Event(event_type="system.adapter_error", source=source, payload={"error": "timeout"})


def normal_event() -> Event:
    return Event(event_type="gps.cycle_complete", source="scheduler", payload={})


def status_error_event(source="ralf-loop") -> Event:
    return Event(event_type="things.processed", source=source, payload={"status": "error"})


# ── _cluster_errors ────────────────────────────────────────────────────────────

def test_cluster_groups_by_terminal_cause_and_source():
    events = [halt_event(), halt_event()]
    sigs = FailurePatternAgent._cluster_errors(events)
    assert len(sigs) == 1
    assert sigs[0].count == 2
    assert sigs[0].terminal_cause == "halt_enforced"
    assert sigs[0].causal_status == "halted"
    assert sigs[0].agent_mechanism == "killswitch"


def test_cluster_separates_different_sources():
    events = [halt_event("ks-1"), halt_event("ks-2")]
    sigs = FailurePatternAgent._cluster_errors(events)
    assert len(sigs) == 2
    mechanisms = {s.agent_mechanism for s in sigs}
    assert mechanisms == {"ks-1", "ks-2"}


def test_cluster_separates_different_event_types():
    events = [halt_event(), adapter_error_event()]
    sigs = FailurePatternAgent._cluster_errors(events)
    assert len(sigs) == 2
    causes = {s.terminal_cause for s in sigs}
    assert "halt_enforced" in causes
    assert "adapter_error" in causes


def test_cluster_excludes_non_error_events():
    sigs = FailurePatternAgent._cluster_errors([normal_event()])
    assert sigs == []


def test_cluster_includes_payload_status_error():
    sigs = FailurePatternAgent._cluster_errors([status_error_event()])
    assert len(sigs) == 1
    assert sigs[0].agent_mechanism == "ralf-loop"


def test_cluster_event_ids_populated():
    e1, e2 = halt_event(), halt_event()
    sigs = FailurePatternAgent._cluster_errors([e1, e2])
    assert set(sigs[0].event_ids) == {e1.event_id, e2.event_id}


def test_cluster_known_governance_types():
    """All known governance types map to a (terminal_cause, causal_status) pair."""
    type_map = {
        "governance.quarantine":        ("source_quarantined",         "quarantined"),
        "governance.human_override":    ("human_override_required",    "overridden"),
        "governance.depth_exceeded":    ("delegation_depth_exceeded",  "flagged"),
        "governance.trust_escalation":  ("untrusted_source",           "flagged"),
        "governance.confidence_breach": ("confidence_below_threshold", "flagged"),
    }
    for event_type, (expected_cause, expected_status) in type_map.items():
        e = Event(event_type=event_type, source="audit", payload={})
        sigs = FailurePatternAgent._cluster_errors([e])
        assert len(sigs) == 1
        assert sigs[0].terminal_cause == expected_cause
        assert sigs[0].causal_status == expected_status


# ── should_materialise ─────────────────────────────────────────────────────────

async def test_should_materialise_triggers_on_error_keywords(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = ConcreteFailurePatternAgent("fp", bus)
    assert agent.should_materialise(halt_event()) is True
    assert agent.should_materialise(adapter_error_event()) is True
    assert agent.should_materialise(normal_event()) is False


async def test_should_materialise_triggers_on_payload_status(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = ConcreteFailurePatternAgent("fp", bus)
    assert agent.should_materialise(status_error_event()) is True


# ── OODA pipeline ──────────────────────────────────────────────────────────────

async def test_no_error_events_returns_none_from_orient(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = ConcreteFailurePatternAgent("fp", bus)
    # no events in store — orient should return None
    result = await agent.orient(normal_event(), {"trigger": normal_event()})
    assert result is None


async def test_orient_returns_signatures_when_errors_exist(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()
    agent = ConcreteFailurePatternAgent("fp", bus)
    agent.subscribe("system", "governance")
    bus.register(agent)

    await bus.publish(halt_event())
    result = await agent.orient(halt_event(), {"trigger": halt_event()})
    assert result is not None
    assert len(result["signatures"]) >= 1
    sig = result["signatures"][0]
    assert sig.pattern_summary.startswith("pattern:")
    await bus.stop()


async def test_act_emits_failure_pattern_detected(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()

    emitted: list[Event] = []

    from agentic_loopkit.agents.base import AgentBase

    class Collector(AgentBase):
        async def orient(self, event, context):
            return {}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            emitted.append(event)

    collector = Collector("col", bus)
    collector.subscribe("system")
    bus.register(collector)

    agent = ConcreteFailurePatternAgent("fp", bus)
    agent.subscribe("governance")
    bus.register(agent)

    await bus.publish(halt_event())

    matching = [
        e for e in emitted
        if e.event_type == SystemEventType.FAILURE_PATTERN_DETECTED
    ]
    assert len(matching) >= 1
    payload = matching[0].payload
    assert payload["terminal_cause"] == "halt_enforced"
    assert payload["agent_mechanism"] == "killswitch"
    assert "pattern_summary" in payload
    await bus.stop()


async def test_act_preserves_causation_chain(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()

    emitted: list[Event] = []

    from agentic_loopkit.agents.base import AgentBase

    class Collector(AgentBase):
        async def orient(self, event, context):
            return {}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            emitted.append(event)

    collector = Collector("col", bus)
    collector.subscribe("system")
    bus.register(collector)

    agent = ConcreteFailurePatternAgent("fp", bus)
    agent.subscribe("governance")
    bus.register(agent)

    trigger = halt_event()
    await bus.publish(trigger)

    matching = [
        e for e in emitted
        if e.event_type == SystemEventType.FAILURE_PATTERN_DETECTED
    ]
    assert matching[0].causation_id == trigger.event_id
    await bus.stop()


async def test_default_projection_streams(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = ConcreteFailurePatternAgent("fp", bus)
    assert agent.projection_streams == ["system", "governance"]


async def test_custom_projection_streams(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = ConcreteFailurePatternAgent("fp", bus, projection_streams=["system"])
    assert agent.projection_streams == ["system"]


async def test_meta_present_on_emitted_events(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()

    emitted: list[Event] = []

    from agentic_loopkit.agents.base import AgentBase

    class Collector(AgentBase):
        async def orient(self, event, context):
            return {}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            emitted.append(event)

    collector = Collector("col", bus)
    collector.subscribe("system")
    bus.register(collector)

    agent = ConcreteFailurePatternAgent("fp", bus)
    agent.subscribe("governance")
    bus.register(agent)

    await bus.publish(halt_event())

    matching = [
        e for e in emitted
        if e.event_type == SystemEventType.FAILURE_PATTERN_DETECTED
    ]
    assert matching[0].meta() is not None
    assert matching[0].meta()["loop_type"] == "ooda"
    await bus.stop()
