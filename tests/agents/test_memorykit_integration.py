"""
End-to-end integration test: AgentBase._memory_store wired to a real
agentic_memorykit.MemoryStore instance (not the duck-type mock used
elsewhere in tests/agents/test_base.py).

Skips cleanly if agentic_memorykit is not installed — memorykit is an
optional [memory] extra, never a hard dependency of agentic_loopkit.
"""

import pytest

memorykit = pytest.importorskip("agentic_memorykit")
MemoryStore = memorykit.MemoryStore

from agentic_loopkit.agents.base import AgentBase, AgentState
from agentic_loopkit.bus import EventBus


class RememberingAgent(AgentBase):
    """Concrete agent that only exercises save_state/load_state."""

    async def orient(self, event, context):
        return None

    async def decide(self, event, orientation):
        return None


async def test_save_state_persists_to_real_memory_store(tmp_path):
    bus = EventBus(store_dir=tmp_path / "events")
    agent = RememberingAgent("rememberer", bus)
    agent._memory_store = MemoryStore(store_dir=tmp_path / "memory")

    await agent.save_state(AgentState(
        semantic={"favourite_stream": "gps"},
        world_model={"adapter_stalls_on": "502"},
    ))

    records = await agent._memory_store.list(agent_id="rememberer")
    by_key = {r.key: r.value for r in records}
    assert by_key["favourite_stream"] == "gps"
    assert by_key["adapter_stalls_on"] == "502"


async def test_load_state_roundtrips_through_real_memory_store(tmp_path):
    bus = EventBus(store_dir=tmp_path / "events")
    agent = RememberingAgent("rememberer", bus)
    agent._memory_store = MemoryStore(store_dir=tmp_path / "memory")

    await agent.save_state(AgentState(
        semantic={"favourite_stream": "gps"},
        world_model={"adapter_stalls_on": "502"},
    ))

    state = await agent.load_state()
    assert state.semantic == {"favourite_stream": "gps"}
    assert state.world_model == {"adapter_stalls_on": "502"}


async def test_load_state_isolates_by_agent_id(tmp_path):
    bus = EventBus(store_dir=tmp_path / "events")
    shared_dir = tmp_path / "memory"

    agent_a = RememberingAgent("agent-a", bus)
    agent_a._memory_store = MemoryStore(store_dir=shared_dir)
    await agent_a.save_state(AgentState(semantic={"key": "a-value"}))

    agent_b = RememberingAgent("agent-b", bus)
    agent_b._memory_store = MemoryStore(store_dir=shared_dir)
    await agent_b.save_state(AgentState(semantic={"key": "b-value"}))

    state_a = await agent_a.load_state()
    state_b = await agent_b.load_state()
    assert state_a.semantic == {"key": "a-value"}
    assert state_b.semantic == {"key": "b-value"}


async def test_recall_evidence_conditioning_uses_real_saved_state_tags(tmp_path):
    # Regression test for the P46/P59/P60 finding (2026-07-06): before this
    # fix, save_state() tagged every fact "semantic" or "world_model" only,
    # so recall()'s evidence-conditioning step always re-derived the same
    # single blanket tag and stopped after one redundant round-trip. With
    # key-derived tags, evidence_tags carry real vocabulary from the second
    # fact's key onward.
    bus = EventBus(store_dir=tmp_path / "events")
    agent = RememberingAgent("rememberer", bus)
    agent._memory_store = MemoryStore(store_dir=tmp_path / "memory")

    await agent.save_state(AgentState(semantic={
        "adapterErrorPattern": "ClickUp 429s cluster around poll interval",
        "clickupBackoffFix": "Exponential backoff resolves the 429 cluster",
    }))

    received = []

    async def _collect(event):
        received.append(event)
    bus.router.subscribe("system", _collect)

    await agent.recall("adapter error", max_steps=3, limit_per_step=1)

    from agentic_loopkit.events.models import SystemEventType
    step_events = [e for e in received if e.event_type == SystemEventType.MEMORY_QUERY_STEP]
    assert step_events
    all_evidence_tags = {t for e in step_events for t in e.payload["evidence_tags"]}
    # Real content vocabulary from the keys, not just the structural tag
    assert all_evidence_tags & {"adapter", "error", "pattern"}
    assert all_evidence_tags != {"semantic"}
