import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.agents.base import AgentBase, AgentState
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


# ── AgentState ─────────────────────────────────────────────────────────────────


def test_agent_state_defaults():
    state = AgentState()
    assert state.episodic == []
    assert state.semantic == {}
    assert state.procedural == {}


def test_agent_state_explicit():
    state = AgentState(episodic=["evt-1"], semantic={"k": "v"}, procedural={"p": 1})
    assert state.episodic == ["evt-1"]
    assert state.semantic == {"k": "v"}
    assert state.procedural == {"p": 1}


# ── save_state / load_state — no memory store ─────────────────────────────────


async def test_save_state_no_memory_store_is_noop(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = RecordingAgent("agent", bus)
    # Must not raise; _memory_store is None
    await agent.save_state(AgentState(semantic={"key": "value"}))


async def test_load_state_no_memory_store_returns_empty(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = RecordingAgent("agent", bus)
    state = await agent.load_state()
    assert isinstance(state, AgentState)
    assert state.episodic == []
    assert state.semantic == {}
    assert state.procedural == {}


# ── save_state / load_state — with mock memory store ─────────────────────────


class _MockRecord:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _MockMemoryStore:
    """Minimal duck-type stand-in for agentic_memorykit.MemoryStore."""

    def __init__(self):
        self._written: list[tuple] = []
        self._records: list[_MockRecord] = []

    async def write(self, key, value, agent_id, *, tags=(), **_kwargs):
        self._written.append((key, value, agent_id, list(tags)))
        self._records.append(_MockRecord(key, value))

    async def list(self, agent_id=None, **_kwargs):
        return list(self._records)


async def test_save_state_writes_semantic_to_memory_store(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = RecordingAgent("agent", bus)
    store = _MockMemoryStore()
    agent._memory_store = store

    await agent.save_state(AgentState(semantic={"fact": "loopkit rules", "count": "42"}))

    assert len(store._written) == 2
    keys = {w[0] for w in store._written}
    assert keys == {"fact", "count"}
    # agent_id must match the agent name
    assert all(w[2] == "agent" for w in store._written)
    # tagged as semantic
    assert all("semantic" in w[3] for w in store._written)


async def test_save_state_skips_empty_semantic(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = RecordingAgent("agent", bus)
    store = _MockMemoryStore()
    agent._memory_store = store

    await agent.save_state(AgentState())  # no semantic facts

    assert store._written == []


async def test_load_state_reads_semantic_from_memory_store(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = RecordingAgent("agent", bus)
    store = _MockMemoryStore()
    store._records = [_MockRecord("x", "1"), _MockRecord("y", "hello")]
    agent._memory_store = store

    state = await agent.load_state()

    assert state.semantic == {"x": "1", "y": "hello"}
    assert state.episodic == []
    assert state.procedural == {}


async def test_save_then_load_roundtrip(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = RecordingAgent("agent", bus)
    store = _MockMemoryStore()
    agent._memory_store = store

    await agent.save_state(AgentState(semantic={"topic": "OODA", "version": "4"}))
    state = await agent.load_state()

    assert state.semantic["topic"] == "OODA"
    assert state.semantic["version"] == "4"
