import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.agents.base import AgentBase
from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import load_events


def make_event(stream="gps") -> Event:
    return Event(event_type=f"{stream}.test", source="test", payload={})


class RecordingAgent(AgentBase):
    """Concrete agent that records act() calls."""

    def __init__(self, name, bus):
        super().__init__(name, bus)
        self.acted: list[tuple] = []

    async def orient(self, event, context):
        return {"ok": True}

    async def decide(self, event, orientation):
        return orientation

    async def act(self, event, action):
        self.acted.append((event, action))


async def test_full_ooda_pipeline(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = RecordingAgent("agent", bus)
    agent.subscribe("gps")
    event = make_event("gps")
    await bus.publish(event)
    assert len(agent.acted) == 1
    assert agent.acted[0][0] == event


async def test_observe_none_short_circuits(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    acted = []

    class FilteringAgent(AgentBase):
        async def observe(self, event):
            return None if event.event_type == "gps.unwanted" else {}

        async def orient(self, event, context):
            return {"ok": True}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            acted.append(event)

    agent = FilteringAgent("filter", bus)
    agent.subscribe("gps")
    await bus.publish(Event(event_type="gps.unwanted", source="test", payload={}))
    await bus.publish(Event(event_type="gps.wanted", source="test", payload={}))
    assert len(acted) == 1
    assert acted[0].event_type == "gps.wanted"


async def test_orient_none_short_circuits(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    decided = []

    class OrientNoneAgent(AgentBase):
        async def orient(self, event, context):
            return None

        async def decide(self, event, orientation):
            decided.append(orientation)
            return orientation

    agent = OrientNoneAgent("agent", bus)
    agent.subscribe("gps")
    await bus.publish(make_event("gps"))
    assert decided == []


async def test_decide_none_short_circuits(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    acted = []

    class DecideNoneAgent(AgentBase):
        async def orient(self, event, context):
            return {"ok": True}

        async def decide(self, event, orientation):
            return None

        async def act(self, event, action):
            acted.append(action)

    agent = DecideNoneAgent("agent", bus)
    agent.subscribe("gps")
    await bus.publish(make_event("gps"))
    assert acted == []


async def test_handler_exception_does_not_crash_bus(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class BrokenAgent(AgentBase):
        async def orient(self, event, context):
            raise RuntimeError("orient exploded")

        async def decide(self, event, orientation):
            return orientation

    agent = BrokenAgent("broken", bus)
    agent.subscribe("gps")
    await bus.publish(make_event("gps"))  # must not raise


async def test_agent_publishes_child_event(tmp_path):
    bus = EventBus(store_dir=tmp_path)

    class PublishingAgent(AgentBase):
        async def orient(self, event, context):
            return {"ok": True}

        async def decide(self, event, orientation):
            return orientation

        async def act(self, event, action):
            await self._bus.publish(
                event.caused("analysis.result", self.name, {"result": "done"})
            )

    agent = PublishingAgent("agent", bus)
    agent.subscribe("gps")
    trigger = Event(event_type="gps.test", source="test", payload={}, correlation_id="CU-1")
    await bus.publish(trigger)

    stored = load_events("analysis", store_dir=tmp_path)
    assert len(stored) == 1
    assert stored[0].causation_id == trigger.event_id
    assert stored[0].correlation_id == "CU-1"


async def test_subscribe_multiple_streams(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = RecordingAgent("agent", bus)
    agent.subscribe("gps", "adr")
    await bus.publish(make_event("gps"))
    await bus.publish(make_event("adr"))
    assert len(agent.acted) == 2


async def test_unsubscribe_all(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = RecordingAgent("agent", bus)
    agent.subscribe("gps")
    agent.unsubscribe_all()
    await bus.publish(make_event("gps"))
    assert agent.acted == []
