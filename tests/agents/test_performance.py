"""
Tests for PerformanceMeasure protocol and SimpleConfidencePerformance.
"""
import pytest
from agentic_loopkit.events.models import Event, EventMeta
from agentic_loopkit.agents.performance import PerformanceMeasure, PerformanceScore, SimpleConfidencePerformance


def make_event(source="agent-a", event_type="work.done", confidence=None, stream=None):
    payload = {}
    if confidence is not None:
        payload["_meta"] = EventMeta(confidence=confidence, phase="act", loop_type="ooda").to_dict()
    et = event_type if stream is None else f"{stream}.event"
    return Event(event_type=et, source=source, payload=payload)


def make_gov_event(flagged_source="agent-a"):
    return Event(
        event_type="governance.confidence_breach",
        source="audit",
        payload={"flagged_source": flagged_source, "confidence": 0.3, "threshold": 0.4},
    )


pm = SimpleConfidencePerformance()


# ── event_count ────────────────────────────────────────────────────────────────

def test_event_count_correct():
    events = [make_event("agent-a") for _ in range(5)] + [make_event("agent-b")]
    score = pm.score("agent-a", events)
    assert score.event_count == 5


def test_event_count_filters_by_agent_name():
    events = [make_event("other") for _ in range(10)]
    score = pm.score("agent-a", events)
    assert score.event_count == 0


def test_empty_event_list():
    score = pm.score("agent-a", [])
    assert score.event_count == 0
    assert score.mean_confidence is None
    assert score.governance_flags == 0
    assert score.trend == "insufficient_data"


# ── mean_confidence ───────────────────────────────────────────────────────────

def test_mean_confidence_computed_correctly():
    events = [make_event(confidence=c) for c in [0.8, 0.6, 0.9, 0.7]]
    score = pm.score("agent-a", events)
    assert score.mean_confidence == pytest.approx(0.75, abs=1e-4)


def test_mean_confidence_none_when_no_meta():
    events = [make_event() for _ in range(3)]
    score = pm.score("agent-a", events)
    assert score.mean_confidence is None


def test_mean_confidence_ignores_events_without_confidence_key():
    events_with = [make_event(confidence=0.9)]
    events_without = [make_event()]  # no _meta
    score = pm.score("agent-a", events_with + events_without)
    # mean_confidence is only over events that carry confidence
    assert score.mean_confidence == pytest.approx(0.9, abs=1e-4)
    assert score.event_count == 2  # both events counted


# ── governance_flags ──────────────────────────────────────────────────────────

def test_governance_flags_counted():
    events = [make_gov_event("agent-a"), make_gov_event("agent-a")]
    score = pm.score("agent-a", events)
    assert score.governance_flags == 2


def test_governance_flags_excludes_other_agents():
    events = [make_gov_event("agent-b"), make_gov_event("agent-b")]
    score = pm.score("agent-a", events)
    assert score.governance_flags == 0


def test_governance_flags_mixed():
    events = [make_gov_event("agent-a"), make_gov_event("agent-b"), make_gov_event("agent-a")]
    score = pm.score("agent-a", events)
    assert score.governance_flags == 2


# ── trend ─────────────────────────────────────────────────────────────────────

def test_trend_improving():
    # first half low, second half high
    events = [make_event(confidence=c) for c in [0.5, 0.5, 0.9, 0.9]]
    score = pm.score("agent-a", events)
    assert score.trend == "improving"


def test_trend_degrading():
    events = [make_event(confidence=c) for c in [0.9, 0.9, 0.5, 0.5]]
    score = pm.score("agent-a", events)
    assert score.trend == "degrading"


def test_trend_stable():
    events = [make_event(confidence=c) for c in [0.8, 0.8, 0.8, 0.8]]
    score = pm.score("agent-a", events)
    assert score.trend == "stable"


def test_trend_insufficient_data_below_four():
    events = [make_event(confidence=0.8) for _ in range(3)]
    score = pm.score("agent-a", events)
    assert score.trend == "insufficient_data"


def test_trend_insufficient_data_no_confidence():
    events = [make_event() for _ in range(10)]
    score = pm.score("agent-a", events)
    assert score.trend == "insufficient_data"


# ── misc ──────────────────────────────────────────────────────────────────────

def test_follow_up_rate_is_none():
    events = [make_event(confidence=0.8)]
    score = pm.score("agent-a", events)
    assert score.follow_up_rate is None


def test_score_metadata():
    score = pm.score("agent-a", [], window_hours=48)
    assert score.agent_name == "agent-a"
    assert score.window_hours == 48
    assert score.computed_at != ""


def test_performance_measure_is_abstract():
    with pytest.raises(TypeError):
        PerformanceMeasure()  # cannot instantiate abstract class
