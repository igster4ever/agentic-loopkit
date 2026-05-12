"""
tests/agents/test_projection.py — ProjectionAgent tests.

Uses a real EventBus + tmp_path store so load_all_events() round-trips
through the actual JSONL persistence layer — no store mocking.
"""

import pytest
from pathlib import Path

from agentic_loopkit import EventBus, Event, ProjectionAgent, ProjectionEventType
from agentic_loopkit.events.models import EventMeta, TrustLevel
from agentic_loopkit.events.store import append_event


# ── Concrete subclass for testing ─────────────────────────────────────────────

class EchoProjection(ProjectionAgent):
    """Materialises by echoing the event count — no LLM needed in tests."""
    async def materialise(self, events):
        return f"doc:{len(events)}"


class SelectiveProjection(ProjectionAgent):
    """Only materialises on 'things.important' events."""
    def should_materialise(self, event):
        return event.event_type == "things.important"

    async def materialise(self, events):
        return f"selective:{len(events)}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_event(
    event_type="things.happened",
    source="test",
    confidence=None,
    trust=TrustLevel.MEDIUM,
) -> Event:
    payload = {}
    if confidence is not None:
        payload["_meta"] = EventMeta(confidence=confidence).to_dict()
    return Event(event_type=event_type, source=source, payload=payload, trust_level=trust)


# ── should_materialise ────────────────────────────────────────────────────────

async def test_should_materialise_defaults_true(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = EchoProjection("proj", bus)
    assert agent.should_materialise(make_event()) is True


async def test_should_materialise_can_filter(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = SelectiveProjection("proj", bus)
    assert agent.should_materialise(make_event("things.ignored")) is False
    assert agent.should_materialise(make_event("things.important")) is True


# ── observe ───────────────────────────────────────────────────────────────────

async def test_observe_returns_context_when_should_materialise_true(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = EchoProjection("proj", bus)
    event = make_event()
    result = await agent.observe(event)
    assert result is not None
    assert result["trigger"] is event


async def test_observe_returns_none_when_should_materialise_false(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = SelectiveProjection("proj", bus)
    result = await agent.observe(make_event("things.ignored"))
    assert result is None


# ── projection_streams ────────────────────────────────────────────────────────

async def test_projection_streams_defaults_to_subscriptions(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = EchoProjection("proj", bus)
    agent.subscribe("things", "other")
    assert set(agent.projection_streams) == {"things", "other"}


async def test_projection_streams_explicit_overrides_subscriptions(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = EchoProjection("proj", bus, projection_streams=["custom"])
    agent.subscribe("things")
    assert agent.projection_streams == ["custom"]


# ── orient: loads events and calls materialise ────────────────────────────────

async def test_orient_loads_events_from_store_and_calls_materialise(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    # Pre-populate the store with source events
    for _ in range(3):
        append_event(make_event("things.happened"), store_dir=tmp_path)

    agent = EchoProjection("proj", bus, projection_streams=["things"])
    trigger = make_event("things.happened")
    context = {"trigger": trigger}

    result = await agent.orient(trigger, context)
    assert result is not None
    assert result["content"] == "doc:3"
    assert result["event_count"] == 3
    assert result["streams"] == ["things"]


async def test_orient_aggregates_confidence_from_source_events(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    append_event(make_event("things.happened", confidence=0.8), store_dir=tmp_path)
    append_event(make_event("things.happened", confidence=0.6), store_dir=tmp_path)

    agent = EchoProjection("proj", bus, projection_streams=["things"])
    trigger = make_event("things.happened")
    result = await agent.orient(trigger, {"trigger": trigger})

    assert result["confidence"] == pytest.approx(0.7)


async def test_orient_confidence_is_none_when_no_meta(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    append_event(make_event("things.happened"), store_dir=tmp_path)

    agent = EchoProjection("proj", bus, projection_streams=["things"])
    trigger = make_event("things.happened")
    result = await agent.orient(trigger, {"trigger": trigger})

    assert result["confidence"] is None


async def test_orient_loads_from_multiple_streams(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    append_event(make_event("alpha.x"), store_dir=tmp_path)
    append_event(make_event("alpha.x"), store_dir=tmp_path)
    append_event(make_event("beta.y"),  store_dir=tmp_path)

    agent = EchoProjection("proj", bus, projection_streams=["alpha", "beta"])
    trigger = make_event("alpha.x")
    result = await agent.orient(trigger, {"trigger": trigger})

    assert result["event_count"] == 3
    assert result["content"] == "doc:3"


# ── decide ────────────────────────────────────────────────────────────────────

async def test_decide_passes_through_orientation(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = EchoProjection("proj", bus)
    orientation = {"content": "x", "confidence": 0.8, "event_count": 1, "streams": ["s"]}
    result = await agent.decide(make_event(), orientation)
    assert result is orientation


# ── act: publishes projection.updated ────────────────────────────────────────

async def test_act_publishes_projection_updated(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    published: list[Event] = []
    original_publish = bus.publish

    async def capture(event):
        published.append(event)
        await original_publish(event)

    bus.publish = capture

    agent = EchoProjection("proj", bus)
    trigger = make_event()
    action = {"content": "hello", "confidence": 0.75, "event_count": 2, "streams": ["things"]}
    await agent.act(trigger, action)

    assert len(published) == 1
    evt = published[0]
    assert evt.event_type == str(ProjectionEventType.UPDATED)
    assert evt.stream == "projection"
    assert evt.payload["projection_id"] == "proj"
    assert evt.payload["content"] == "hello"
    assert evt.payload["confidence"] == pytest.approx(0.75)
    assert evt.payload["event_count"] == 2
    assert evt.payload["streams"] == ["things"]


async def test_act_event_has_meta(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    published: list[Event] = []
    original_publish = bus.publish

    async def capture(event):
        published.append(event)
        await original_publish(event)

    bus.publish = capture

    agent = EchoProjection("proj", bus)
    trigger = make_event()
    action = {"content": "x", "confidence": 0.9, "event_count": 1, "streams": ["s"]}
    await agent.act(trigger, action)

    meta = published[0].meta()
    assert meta is not None
    assert meta["phase"]     == "orient"
    assert meta["loop_type"] == "ooda"
    assert meta["confidence"] == pytest.approx(0.9)
    assert "proj" in meta["context"]


async def test_act_event_traces_back_to_trigger(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    published: list[Event] = []
    original_publish = bus.publish

    async def capture(event):
        published.append(event)
        await original_publish(event)

    bus.publish = capture

    agent = EchoProjection("proj", bus)
    trigger = make_event()
    trigger_id = trigger.event_id
    action = {"content": "x", "confidence": None, "event_count": 0, "streams": []}
    await agent.act(trigger, action)

    assert published[0].causation_id == trigger_id


# ── full integration ──────────────────────────────────────────────────────────

async def test_full_pipeline_trigger_to_projection_event(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    # Pre-populate store
    for _ in range(2):
        append_event(make_event("things.happened"), store_dir=tmp_path)

    projection_events: list[Event] = []

    class CaptureProjection(ProjectionAgent):
        async def materialise(self, events):
            return f"summary:{len(events)}"

    agent = CaptureProjection("wiki", bus, projection_streams=["things"])
    agent.subscribe("things")
    bus.register(agent)

    # Subscribe to the projection stream to capture output
    async def on_projection(event):
        if event.stream == "projection":
            projection_events.append(event)

    bus.router.subscribe("projection", on_projection)

    # Trigger by publishing to the subscribed stream
    await bus.publish(make_event("things.happened"))

    assert len(projection_events) == 1
    assert projection_events[0].payload["content"] == "summary:3"  # 2 pre-populated + 1 just published


async def test_projection_event_type_lands_on_projection_stream():
    e = Event(event_type=ProjectionEventType.UPDATED, source="test", payload={})
    assert e.stream == "projection"


# ── public API export ─────────────────────────────────────────────────────────

def test_exported_from_public_api():
    from agentic_loopkit import ProjectionAgent as PA, ProjectionEventType as PET  # noqa: F401
    assert PA is not None
    assert PET.UPDATED == "projection.updated"
