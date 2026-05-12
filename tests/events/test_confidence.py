import pytest

from agentic_loopkit.events.models import Event, EventMeta, TrustLevel
from agentic_loopkit.events.confidence import aggregate_confidence


def _event(confidence=None, trust=TrustLevel.MEDIUM, depth=0):
    payload = {}
    if confidence is not None:
        payload["_meta"] = EventMeta(confidence=confidence).to_dict()
    return Event(
        event_type="test.event",
        source="test",
        payload=payload,
        trust_level=trust,
        delegation_depth=depth,
    )


def test_empty_list_returns_none():
    assert aggregate_confidence([]) is None


def test_no_meta_returns_none():
    assert aggregate_confidence([_event(), _event()]) is None


def test_meta_without_confidence_returns_none():
    payload = {"_meta": EventMeta(phase="act").to_dict()}
    e = Event(event_type="test.event", source="test", payload=payload)
    assert aggregate_confidence([e]) is None


def test_single_event_returns_its_confidence():
    assert aggregate_confidence([_event(confidence=0.8)]) == pytest.approx(0.8)


def test_equal_trust_equal_depth_is_plain_mean():
    events = [_event(confidence=0.6), _event(confidence=0.8)]
    assert aggregate_confidence(events) == pytest.approx(0.7)


def test_high_trust_outweighs_low_trust():
    # HIGH weight=3, LOW weight=1
    # (0.9*3 + 0.1*1) / (3+1) = 2.8/4 = 0.7
    events = [
        _event(confidence=0.9, trust=TrustLevel.HIGH),
        _event(confidence=0.1, trust=TrustLevel.LOW),
    ]
    assert aggregate_confidence(events) == pytest.approx(0.7)


def test_untrusted_contributes_no_signal():
    events = [
        _event(confidence=0.8, trust=TrustLevel.MEDIUM),
        _event(confidence=0.0, trust=TrustLevel.UNTRUSTED),
    ]
    assert aggregate_confidence(events) == pytest.approx(0.8)


def test_all_untrusted_returns_none():
    events = [_event(confidence=0.5, trust=TrustLevel.UNTRUSTED)] * 3
    assert aggregate_confidence(events) is None


def test_deeper_delegation_decays_weight():
    # Both MEDIUM (trust_weight=2); depth 0 decay=1.0, depth 9 decay=0.1
    # effective weights: 2.0 and 0.2
    # (0.9*2.0 + 0.1*0.2) / (2.0+0.2) = 1.82/2.2 ≈ 0.8273
    shallow = _event(confidence=0.9, depth=0)
    deep    = _event(confidence=0.1, depth=9)
    expected = (0.9 * 2.0 + 0.1 * 0.2) / (2.0 + 0.2)
    assert aggregate_confidence([shallow, deep]) == pytest.approx(expected)


def test_events_without_meta_are_skipped():
    events = [_event(confidence=0.7), _event()]
    assert aggregate_confidence(events) == pytest.approx(0.7)


def test_result_within_valid_range():
    events = [_event(confidence=c) for c in [0.0, 0.5, 1.0]]
    result = aggregate_confidence(events)
    assert result is not None
    assert 0.0 <= result <= 1.0


def test_exported_from_public_api():
    from agentic_loopkit import aggregate_confidence as fn  # noqa: F401
    assert callable(fn)
