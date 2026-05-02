from agentic_loopkit.events.models import Event, SystemEventType


def test_stream_derived_from_event_type():
    e = Event(event_type="gps.cycle_complete", source="test", payload={})
    assert e.stream == "gps"


def test_stream_explicit_overrides_derivation():
    e = Event(event_type="gps.cycle_complete", source="test", payload={}, stream="custom")
    assert e.stream == "custom"


def test_causation_chain_inherits_correlation_id():
    parent = Event(
        event_type="clickup.task_created", source="adapter",
        payload={}, correlation_id="CU-123",
    )
    child = parent.caused("analysis.task.received", "agent", {"detail": "x"})
    assert child.causation_id == parent.event_id
    assert child.correlation_id == "CU-123"


def test_causation_chain_propagates_none_correlation():
    parent = Event(event_type="test.event", source="test", payload={})
    child = parent.caused("test.child", "test", {})
    assert child.causation_id == parent.event_id
    assert child.correlation_id is None


def test_roundtrip_serialisation():
    e = Event(
        event_type="test.event", source="test", payload={"key": "val"},
        correlation_id="corr-1", causation_id="cause-1",
    )
    restored = Event.from_dict(e.to_dict())
    assert restored.event_id == e.event_id
    assert restored.event_type == e.event_type
    assert restored.stream == e.stream
    assert restored.source == e.source
    assert restored.payload == e.payload
    assert restored.correlation_id == e.correlation_id
    assert restored.causation_id == e.causation_id


def test_serialisation_timestamp_utc():
    e = Event(event_type="test.event", source="test", payload={})
    d = e.to_dict()
    assert d["timestamp"].endswith("Z")


def test_event_id_unique():
    e1 = Event(event_type="test.event", source="test", payload={})
    e2 = Event(event_type="test.event", source="test", payload={})
    assert e1.event_id != e2.event_id


def test_system_event_types_have_system_stream():
    for evt in SystemEventType:
        e = Event(event_type=evt, source="bus", payload={})
        assert e.stream == "system", f"{evt} should have stream 'system'"


def test_from_dict_handles_missing_optional_fields():
    d = {
        "event_id": "abc", "event_type": "test.event", "stream": "test",
        "source": "s", "timestamp": "2026-05-02T10:00:00Z", "payload": {},
    }
    e = Event.from_dict(d)
    assert e.causation_id is None
    assert e.correlation_id is None
