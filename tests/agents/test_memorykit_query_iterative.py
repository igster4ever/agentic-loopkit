"""
End-to-end test for AgentBase.recall() — the real query_iterative() consumer (P46).

Proves the repo-wide gap ("query_iterative shipped but no real consumer") is closed:
a concrete agent calls recall(), which drives agentic_memorykit.MemoryStore.query_iterative()
and emits an observable system.memory_query_step event per retrieval step.

Skips cleanly if agentic_memorykit is not installed — memorykit is an optional
[memory] extra, never a hard dependency of agentic_loopkit.
"""

import pytest

memorykit = pytest.importorskip("agentic_memorykit")
MemoryStore = memorykit.MemoryStore

from agentic_loopkit.agents.base import AgentBase
from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import SystemEventType
from agentic_loopkit.events.store import load_events


class RecallingAgent(AgentBase):
    """Concrete agent that only exercises recall()."""

    async def orient(self, event, context):
        return None

    async def decide(self, event, orientation):
        return None


async def _seed(store: MemoryStore, agent_id: str) -> None:
    await store.write(
        "adapter_error_pattern", "ClickUp 429s cluster around poll interval",
        agent_id=agent_id, tags=["rate-limit", "clickup"],
    )
    await store.write(
        "clickup_backoff", "Exponential backoff resolves the 429 cluster",
        agent_id=agent_id, tags=["rate-limit", "clickup", "fix"],
    )
    await store.write(
        "unrelated_fact", "Dashboard uses dagre for DAG layout",
        agent_id=agent_id, tags=["dashboard"],
    )


async def test_recall_returns_query_iterative_results(tmp_path):
    bus = EventBus(store_dir=tmp_path / "events")
    agent = RecallingAgent("recaller", bus)
    agent._memory_store = MemoryStore(store_dir=tmp_path / "memory")
    await _seed(agent._memory_store, "recaller")

    results = await agent.recall("rate-limit clickup", max_steps=3, limit_per_step=2)

    assert results
    keys = {record.key for record, _ in results}
    assert "adapter_error_pattern" in keys


async def test_recall_emits_memory_query_step_per_step(tmp_path):
    bus = EventBus(store_dir=tmp_path / "events")
    agent = RecallingAgent("recaller", bus)
    agent._memory_store = MemoryStore(store_dir=tmp_path / "memory")
    await _seed(agent._memory_store, "recaller")

    received = []

    async def _collect(event):
        received.append(event)

    bus.router.subscribe("system", _collect)

    await agent.recall("rate-limit clickup", max_steps=3, limit_per_step=2)

    step_events = [e for e in received if e.event_type == SystemEventType.MEMORY_QUERY_STEP]
    assert step_events, "expected at least one system.memory_query_step event"
    assert step_events[0].source == "recaller"
    assert "step" in step_events[0].payload
    assert "query_text" in step_events[0].payload
    assert "result_count" in step_events[0].payload


async def test_recall_query_step_events_are_persisted(tmp_path):
    store_dir = tmp_path / "events"
    bus = EventBus(store_dir=store_dir)
    agent = RecallingAgent("recaller", bus)
    agent._memory_store = MemoryStore(store_dir=tmp_path / "memory")
    await _seed(agent._memory_store, "recaller")

    await agent.recall("rate-limit clickup", max_steps=3, limit_per_step=2)

    persisted = load_events("system", store_dir=store_dir)
    step_events = [e for e in persisted if e.event_type == SystemEventType.MEMORY_QUERY_STEP]
    assert step_events


async def test_recall_returns_empty_without_memory_store(tmp_path):
    bus = EventBus(store_dir=tmp_path / "events")
    agent = RecallingAgent("recaller", bus)

    results = await agent.recall("anything")

    assert results == []
