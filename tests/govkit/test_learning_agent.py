"""Tests for GovernanceLearningAgent — v4-8 governance Learning Element."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from agentic_loopkit import Event, EventBus, TrustLevel, SystemEventType
from agentic_govkit import GovernanceLearningAgent, GovernanceEventType, PolicyRecommendation


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_event(**kwargs) -> Event:
    defaults = dict(event_type="governance.depth_exceeded", source="audit", payload={})
    return Event(**{**defaults, **kwargs})


def make_bus(tmp_path: Path) -> EventBus:
    return EventBus(store_dir=tmp_path)


class ConcreteLearner(GovernanceLearningAgent):
    """Minimal concrete subclass for testing."""

    def __init__(self, name, bus, recs=None, analysis_window=20, min_confidence=0.65):
        super().__init__(name, bus)
        self.analysis_window = analysis_window
        self.min_confidence = min_confidence
        self._recs = recs or []
        self.analyse_called_with: list | None = None

    async def analyse(self, events):
        self.analyse_called_with = events
        return list(self._recs)


# ── observe() tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_observe_accumulates_governance_events(tmp_path):
    bus = make_bus(tmp_path)
    agent = ConcreteLearner("learner", bus, analysis_window=3)

    e1 = make_event()
    e2 = make_event()

    assert await agent.observe(e1) is None  # window: 1, not yet full
    assert await agent.observe(e2) is None  # window: 2, not yet full


@pytest.mark.asyncio
async def test_observe_triggers_when_window_full(tmp_path):
    bus = make_bus(tmp_path)
    agent = ConcreteLearner("learner", bus, analysis_window=2)

    assert await agent.observe(make_event()) is None
    ctx = await agent.observe(make_event())

    assert ctx is not None
    assert ctx["trigger"] == "window_full"
    assert len(ctx["window"]) == 2


@pytest.mark.asyncio
async def test_observe_self_excludes_own_events(tmp_path):
    bus = make_bus(tmp_path)
    agent = ConcreteLearner("learner", bus, analysis_window=1)

    own_event = make_event(source="learner")
    result = await agent.observe(own_event)
    assert result is None
    assert len(agent._window) == 0


@pytest.mark.asyncio
async def test_observe_excludes_policy_recommendation_events(tmp_path):
    bus = make_bus(tmp_path)
    agent = ConcreteLearner("learner", bus, analysis_window=1)

    rec_event = make_event(event_type=GovernanceEventType.POLICY_RECOMMENDATION)
    result = await agent.observe(rec_event)
    assert result is None
    assert len(agent._window) == 0


@pytest.mark.asyncio
async def test_observe_excludes_policy_applied_events(tmp_path):
    bus = make_bus(tmp_path)
    agent = ConcreteLearner("learner", bus, analysis_window=1)

    applied_event = make_event(event_type=GovernanceEventType.POLICY_APPLIED)
    result = await agent.observe(applied_event)
    assert result is None
    assert len(agent._window) == 0


@pytest.mark.asyncio
async def test_observe_bus_started_triggers_regardless_of_window(tmp_path):
    bus = make_bus(tmp_path)
    agent = ConcreteLearner("learner", bus, analysis_window=20)

    bus_started = Event(event_type=SystemEventType.BUS_STARTED, source="bus", payload={})
    ctx = await agent.observe(bus_started)

    assert ctx is not None
    assert ctx["trigger"] == "bus_started"


# ── orient() tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orient_calls_analyse_with_events(tmp_path):
    bus = make_bus(tmp_path)
    rec = PolicyRecommendation(
        policy_key="confidence_threshold",
        current_value=0.4,
        recommended_value=0.55,
        rationale="test",
        confidence=0.8,
    )
    agent = ConcreteLearner("learner", bus, recs=[rec], analysis_window=1)

    event = make_event()
    ctx = {"window": [event], "trigger": "window_full"}
    orientation = await agent.orient(event, ctx)

    assert orientation is not None
    assert len(orientation["recommendations"]) == 1
    assert agent.analyse_called_with is not None


# ── decide() tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_decide_filters_low_confidence_recommendations(tmp_path):
    bus = make_bus(tmp_path)
    recs = [
        PolicyRecommendation("key_a", 0.4, 0.55, "r", confidence=0.9),
        PolicyRecommendation("key_b", 0.4, 0.55, "r", confidence=0.3),  # below threshold
    ]
    agent = ConcreteLearner("learner", bus, recs=recs, analysis_window=1, min_confidence=0.65)

    event = make_event()
    ctx = {"window": [event], "trigger": "window_full"}
    orientation = await agent.orient(event, ctx)
    decision = await agent.decide(event, orientation)

    assert decision is not None
    assert len(decision["recommendations"]) == 1
    assert decision["recommendations"][0].policy_key == "key_a"


@pytest.mark.asyncio
async def test_decide_returns_none_when_all_recs_below_threshold(tmp_path):
    bus = make_bus(tmp_path)
    recs = [PolicyRecommendation("key", 0.4, 0.55, "r", confidence=0.2)]
    agent = ConcreteLearner("learner", bus, recs=recs, analysis_window=1, min_confidence=0.65)

    event = make_event()
    ctx = {"window": [event], "trigger": "window_full"}
    orientation = await agent.orient(event, ctx)
    decision = await agent.decide(event, orientation)

    assert decision is None


# ── act() tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_act_emits_policy_recommendation_events(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    recs = [
        PolicyRecommendation("confidence_threshold", 0.4, 0.55, "17 breaches", confidence=0.8, tags=["confidence"]),
        PolicyRecommendation("max_delegation_depth", 5, 3, "chains too deep", confidence=0.7, tags=["depth"]),
    ]
    agent = ConcreteLearner("learner", bus, recs=recs, analysis_window=1)
    agent._window = [make_event()]

    trigger = make_event()
    action = {"recommendations": recs, "event_count": 20, "trigger": "window_full"}
    await agent.act(trigger, action)

    assert len(published) == 2
    assert all(e.event_type == GovernanceEventType.POLICY_RECOMMENDATION for e in published)


@pytest.mark.asyncio
async def test_act_sets_trust_level_high(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    rec = PolicyRecommendation("key", 0.4, 0.55, "r", confidence=0.8)
    agent = ConcreteLearner("learner", bus, recs=[rec], analysis_window=1)
    agent._window = [make_event()]

    trigger = make_event()
    action = {"recommendations": [rec], "event_count": 5, "trigger": "window_full"}
    await agent.act(trigger, action)

    assert published[0].trust_level == TrustLevel.HIGH


@pytest.mark.asyncio
async def test_act_payload_correctness(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    rec = PolicyRecommendation(
        policy_key="confidence_threshold",
        current_value=0.4,
        recommended_value=0.55,
        rationale="17 breaches observed",
        confidence=0.82,
        evidence_event_ids=["evt-001", "evt-002"],
        tags=["confidence", "gps"],
    )
    agent = ConcreteLearner("learner", bus, recs=[rec], analysis_window=1)
    agent._window = [make_event()]

    trigger = make_event(correlation_id="flow-42")
    action = {"recommendations": [rec], "event_count": 20, "trigger": "window_full"}
    await agent.act(trigger, action)

    payload = published[0].payload
    assert payload["policy_key"] == "confidence_threshold"
    assert payload["current_value"] == 0.4
    assert payload["recommended_value"] == 0.55
    assert payload["rationale"] == "17 breaches observed"
    assert payload["confidence"] == 0.82
    assert payload["evidence_event_ids"] == ["evt-001", "evt-002"]
    assert payload["tags"] == ["confidence", "gps"]


@pytest.mark.asyncio
async def test_act_meta_present_with_correct_phase(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    rec = PolicyRecommendation("key", 0.4, 0.55, "r", confidence=0.8)
    agent = ConcreteLearner("learner", bus, recs=[rec], analysis_window=1)
    agent._window = [make_event()]

    trigger = make_event()
    action = {"recommendations": [rec], "event_count": 5, "trigger": "window_full"}
    await agent.act(trigger, action)

    meta = published[0].payload.get("_meta")
    assert meta is not None
    assert meta["phase"] == "orient"
    assert meta["loop_type"] == "ooda"


@pytest.mark.asyncio
async def test_act_clears_rolling_window(tmp_path):
    bus = make_bus(tmp_path)
    bus.publish = AsyncMock()

    rec = PolicyRecommendation("key", 0.4, 0.55, "r", confidence=0.8)
    agent = ConcreteLearner("learner", bus, recs=[rec], analysis_window=1)

    # Pre-load the window
    agent._window = [make_event(), make_event()]
    assert len(agent._window) == 2

    trigger = make_event()
    action = {"recommendations": [rec], "event_count": 2, "trigger": "window_full"}
    await agent.act(trigger, action)

    assert len(agent._window) == 0


@pytest.mark.asyncio
async def test_evidence_event_ids_in_payload(tmp_path):
    bus = make_bus(tmp_path)
    published = []
    bus.publish = AsyncMock(side_effect=lambda e: published.append(e))

    evidence_ids = ["evt-abc", "evt-def", "evt-ghi"]
    rec = PolicyRecommendation("key", 0.4, 0.55, "r", confidence=0.8, evidence_event_ids=evidence_ids)
    agent = ConcreteLearner("learner", bus, recs=[rec], analysis_window=1)
    agent._window = [make_event()]

    trigger = make_event()
    action = {"recommendations": [rec], "event_count": 5, "trigger": "window_full"}
    await agent.act(trigger, action)

    assert published[0].payload["evidence_event_ids"] == evidence_ids


# ── Module boundary test ──────────────────────────────────────────────────────

def test_module_boundary_learning_agent_imports_only_public_loopkit_api():
    """GovernanceLearningAgent must only import from agentic_loopkit public API."""
    import inspect
    import agentic_govkit.agents.learning as learning_module

    source = inspect.getsource(learning_module)

    # Must not import from sub-modules directly
    assert "from agentic_loopkit.agents" not in source
    assert "from agentic_loopkit.events.models" not in source
    assert "from agentic_loopkit.loops" not in source

    # Public API imports are allowed
    assert "from agentic_loopkit import" in source
