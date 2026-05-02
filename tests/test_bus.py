import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event, SystemEventType
from agentic_loopkit.events.store import load_events


def make_event(stream="gps") -> Event:
    return Event(event_type=f"{stream}.test", source="test", payload={})


async def test_publish_persists_before_fanout(tmp_path):
    """The store must be written before any subscriber fires."""
    bus = EventBus(store_dir=tmp_path)
    seen_in_store = []

    async def check_store(e):
        stored = load_events("gps", store_dir=tmp_path)
        seen_in_store.append(any(s.event_id == e.event_id for s in stored))

    bus.router.subscribe("gps", check_store)
    await bus.publish(make_event("gps"))
    assert seen_in_store == [True]


async def test_publish_routes_to_subscriber(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    received = []
    async def handler(e): received.append(e)
    bus.router.subscribe("gps", handler)
    event = make_event("gps")
    await bus.publish(event)
    assert received == [event]


async def test_publish_many(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    received = []
    async def handler(e): received.append(e)
    bus.router.subscribe("gps", handler)
    events = [make_event("gps") for _ in range(3)]
    await bus.publish_many(events)
    assert len(received) == 3


async def test_start_emits_bus_started(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()
    stored = load_events("system", store_dir=tmp_path)
    assert any(e.event_type == SystemEventType.BUS_STARTED for e in stored)


async def test_start_payload_includes_agent_and_adapter_names(tmp_path):
    from agentic_loopkit.agents.base import AgentBase
    from agentic_loopkit.adapters.base import PollingAdapter

    class StubAgent(AgentBase):
        async def orient(self, event, context): return None
        async def decide(self, event, orientation): return None

    class StubAdapter(PollingAdapter):
        name = "stub-adapter"
        async def poll(self, cursor): return [], None

    bus = EventBus(store_dir=tmp_path)
    bus.register(StubAgent("my-agent", bus))
    bus.add_adapter(StubAdapter(bus))
    await bus.start()

    stored = load_events("system", store_dir=tmp_path)
    started = next(e for e in stored if e.event_type == SystemEventType.BUS_STARTED)
    assert "my-agent" in started.payload["agents"]
    assert "stub-adapter" in started.payload["adapters"]


async def test_stop_emits_bus_stopped(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()
    await bus.stop()
    stored = load_events("system", store_dir=tmp_path)
    assert any(e.event_type == SystemEventType.BUS_STOPPED for e in stored)


async def test_is_running_lifecycle(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    assert not bus.is_running
    await bus.start()
    assert bus.is_running
    await bus.stop()
    assert not bus.is_running


async def test_store_dir_created_on_init(tmp_path):
    store = tmp_path / "new" / "dir"
    bus = EventBus(store_dir=store)
    assert store.exists()


async def test_status_reflects_registered_components(tmp_path):
    from agentic_loopkit.agents.base import AgentBase
    from agentic_loopkit.adapters.base import PollingAdapter

    class StubAgent(AgentBase):
        async def orient(self, event, context): return None
        async def decide(self, event, orientation): return None

    class StubAdapter(PollingAdapter):
        name = "stub"
        async def poll(self, cursor): return [], None

    bus = EventBus(store_dir=tmp_path)
    bus.register(StubAgent("agent-1", bus))
    bus.add_adapter(StubAdapter(bus))
    status = bus.status()
    assert len(status["agents"]) == 1
    assert len(status["adapters"]) == 1
