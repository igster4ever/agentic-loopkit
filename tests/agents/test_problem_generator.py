"""Tests for ProblemGeneratorAgent — v4-5 proactive exploration agent."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agentic_loopkit import (
    Event, EventBus,
    ProblemGeneratorAgent, AgendaEventType, AgendaItem,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_event(**kwargs) -> Event:
    defaults = dict(event_type="system.test", source="test", payload={})
    return Event(**{**defaults, **kwargs})


def make_bus(tmp_path: Path) -> EventBus:
    return EventBus(store_dir=tmp_path)


class ConcreteExplorer(ProblemGeneratorAgent):
    """Minimal concrete subclass for testing."""

    def __init__(self, name, bus, items=None, min_priority=0.3):
        super().__init__(name, bus)
        self._items = items or []
        self.min_priority = min_priority
        self.explore_called_with: list | None = None

    async def explore(self, events):
        self.explore_called_with = events
        return list(self._items)


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_observe_returns_context_by_default(tmp_path):
    bus = make_bus(tmp_path)
    agent = ConcreteExplorer("explorer", bus)
    event = make_event()
    result = await agent.observe(event)
    assert result is not None
    assert result["trigger"] == event


@pytest.mark.asyncio
async def test_observe_returns_none_when_should_explore_false(tmp_path):
    bus = make_bus(tmp_path)

    class GatedExplorer(ConcreteExplorer):
        def should_explore(self, event):
            return False

    agent = GatedExplorer("explorer", bus)
    result = await agent.observe(make_event())
    assert result is None


@pytest.mark.asyncio
async def test_explore_called_with_loaded_events(tmp_path):
    bus = make_bus(tmp_path)
    item = AgendaItem(
        description="Investigate gap",
        priority=0.8,
        rationale="Pattern detected",
        context_streams=["test"],
    )
    agent = ConcreteExplorer("explorer", bus, items=[item])
    agent.subscribe("test")

    trigger = make_event()
    context = await agent.observe(trigger)
    # No events in store — explore called with empty list
    orientation = await agent.orient(trigger, context)
    assert orientation is not None
    assert len(orientation["items"]) == 1
    assert agent.explore_called_with is not None


@pytest.mark.asyncio
async def test_min_priority_filter_discards_low_priority_items(tmp_path):
    bus = make_bus(tmp_path)
    items = [
        AgendaItem("High priority", priority=0.9, rationale="r", context_streams=[]),
        AgendaItem("Below threshold", priority=0.1, rationale="r", context_streams=[]),
    ]
    agent = ConcreteExplorer("explorer", bus, items=items, min_priority=0.3)
    agent.subscribe("test")

    trigger = make_event()
    context = await agent.observe(trigger)
    orientation = await agent.orient(trigger, context)
    decision = await agent.decide(trigger, orientation)

    assert decision is not None
    assert len(decision["items"]) == 1
    assert decision["items"][0].description == "High priority"


@pytest.mark.asyncio
async def test_decide_returns_none_when_all_items_below_threshold(tmp_path):
    bus = make_bus(tmp_path)
    items = [
        AgendaItem("Low", priority=0.1, rationale="r", context_streams=[]),
        AgendaItem("Also low", priority=0.2, rationale="r", context_streams=[]),
    ]
    agent = ConcreteExplorer("explorer", bus, items=items, min_priority=0.5)
    agent.subscribe("test")

    trigger = make_event()
    context = await agent.observe(trigger)
    orientation = await agent.orient(trigger, context)
    decision = await agent.decide(trigger, orientation)
    assert decision is None


@pytest.mark.asyncio
async def test_act_emits_one_event_per_agenda_item(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    items = [
        AgendaItem("Item A", priority=0.8, rationale="r1", context_streams=["gps"], tags=["x"]),
        AgendaItem("Item B", priority=0.6, rationale="r2", context_streams=["adr"]),
    ]
    agent = ConcreteExplorer("explorer", bus, items=items)
    agent.subscribe("test")

    trigger = make_event(correlation_id="corr-123")
    context = await agent.observe(trigger)
    orientation = await agent.orient(trigger, context)
    decision = await agent.decide(trigger, orientation)
    await agent.act(trigger, decision)

    assert len(published) == 2
    assert all(e.event_type == AgendaEventType.ITEM_ADDED for e in published)


@pytest.mark.asyncio
async def test_act_payload_correctness(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    item = AgendaItem(
        description="Check GPS cycles",
        priority=0.75,
        rationale="12 of 15 cycles before 09:00",
        context_streams=["gps"],
        tags=["timing"],
        ttl_hours=24,
    )
    agent = ConcreteExplorer("explorer", bus, items=[item])
    agent.subscribe("test")

    trigger = make_event(correlation_id="corr-42")
    context = await agent.observe(trigger)
    orientation = await agent.orient(trigger, context)
    decision = await agent.decide(trigger, orientation)
    await agent.act(trigger, decision)

    assert len(published) == 1
    payload = published[0].payload
    assert payload["description"] == "Check GPS cycles"
    assert payload["priority"] == 0.75
    assert payload["rationale"] == "12 of 15 cycles before 09:00"
    assert payload["context_streams"] == ["gps"]
    assert payload["tags"] == ["timing"]
    assert payload["ttl_hours"] == 24


@pytest.mark.asyncio
async def test_act_meta_present_with_correct_phase(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    item = AgendaItem("Test", priority=0.8, rationale="r", context_streams=[])
    agent = ConcreteExplorer("explorer", bus, items=[item])
    agent.subscribe("test")

    trigger = make_event()
    context = await agent.observe(trigger)
    orientation = await agent.orient(trigger, context)
    decision = await agent.decide(trigger, orientation)
    await agent.act(trigger, decision)

    meta = published[0].payload.get("_meta")
    assert meta is not None
    assert meta["phase"] == "orient"
    assert meta["loop_type"] == "ooda"


@pytest.mark.asyncio
async def test_causation_and_correlation_inherited_from_trigger(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    item = AgendaItem("Test", priority=0.8, rationale="r", context_streams=[])
    agent = ConcreteExplorer("explorer", bus, items=[item])
    agent.subscribe("test")

    trigger = make_event(correlation_id="flow-99")
    context = await agent.observe(trigger)
    orientation = await agent.orient(trigger, context)
    decision = await agent.decide(trigger, orientation)
    await agent.act(trigger, decision)

    emitted = published[0]
    assert emitted.causation_id == trigger.event_id
    assert emitted.correlation_id == "flow-99"


@pytest.mark.asyncio
async def test_empty_explore_result_no_events_emitted(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    agent = ConcreteExplorer("explorer", bus, items=[])  # explore returns []
    agent.subscribe("test")

    trigger = make_event()
    context = await agent.observe(trigger)
    orientation = await agent.orient(trigger, context)
    decision = await agent.decide(trigger, orientation)

    # decide returns None when list is empty — act never called
    assert decision is None
    assert len(published) == 0


@pytest.mark.asyncio
async def test_context_streams_defaults_to_subscriptions(tmp_path):
    bus = make_bus(tmp_path)
    agent = ConcreteExplorer("explorer", bus, items=[])
    agent.subscribe("gps")
    agent.subscribe("adr")

    # context_streams not set — should fall back to subscriptions
    assert agent.context_streams == []
    trigger = make_event()
    context = await agent.observe(trigger)
    orientation = await agent.orient(trigger, context)
    # explore_called_with comes from loaded events for "gps" + "adr" streams
    assert agent.explore_called_with is not None


@pytest.mark.asyncio
async def test_should_explore_override_throttles_exploration(tmp_path):
    bus = make_bus(tmp_path)

    class OnlyOnProjection(ConcreteExplorer):
        def should_explore(self, event):
            return event.event_type == "projection.updated"

    agent = OnlyOnProjection("explorer", bus, items=[])
    agent.subscribe("projection")

    irrelevant = make_event(event_type="system.test")
    relevant = make_event(event_type="projection.updated")

    assert await agent.observe(irrelevant) is None
    assert await agent.observe(relevant) is not None
