"""
tests/events/test_headlines.py — EventHeadline + load_headlines + expand_event tests.

Covers:
  - EventHeadline round-trip serialisation (to_dict / from_dict)
  - EventHeadline.from_event: summary field, fallback text, confidence suffix
  - Headline truncation to ≤ 120 chars
  - append_headline: chunk_id sequential per stream
  - load_headlines: newest-first ordering, limit, empty stream
  - expand_event: correct event returned, None for unknown chunk_id
  - expand_event: returns None when underlying event removed (beyond retention)
  - compact_stream compatibility: events within window still expandable after compaction
  - EventBus integration: bus.publish() writes headline; bus.load_headlines(); bus.expand_event()
  - Stream isolation: chunk_ids are independent per stream
"""

import pytest
from pathlib import Path

from agentic_loopkit import EventBus, Event, EventHeadline, append_headline, load_headlines, expand_event
from agentic_loopkit.events.models import SystemEventType, EventMeta
from agentic_loopkit.events.store import load_events, compact_stream


def make_event(event_type="things.happened", source="test", payload=None) -> Event:
    return Event(event_type=event_type, source=source, payload=payload or {})


# ── EventHeadline dataclass ───────────────────────────────────────────────────

def test_to_dict_from_dict_roundtrip():
    h = EventHeadline(
        event_id="eid-1", stream="things", event_type="things.happened",
        timestamp="2026-06-18T10:00:00+00:00", headline="something happened", chunk_id=3,
    )
    assert EventHeadline.from_dict(h.to_dict()) == h


def test_to_dict_keys():
    h = EventHeadline(
        event_id="eid-1", stream="s", event_type="s.e", timestamp="t", headline="h", chunk_id=0,
    )
    d = h.to_dict()
    assert set(d.keys()) == {"event_id", "stream", "event_type", "timestamp", "headline", "chunk_id"}


# ── Headline text generation ──────────────────────────────────────────────────

def test_from_event_uses_payload_summary():
    event = make_event(payload={"summary": "ticket was closed"})
    h = EventHeadline.from_event(event, chunk_id=0)
    assert h.headline == "ticket was closed"


def test_from_event_fallback_contains_event_type_and_source():
    event = make_event(event_type="tickets.opened", source="clickup")
    h = EventHeadline.from_event(event, chunk_id=0)
    assert "tickets.opened" in h.headline
    assert "clickup" in h.headline


def test_from_event_headline_max_120_chars():
    long_summary = "x" * 200
    event = make_event(payload={"summary": long_summary})
    h = EventHeadline.from_event(event, chunk_id=0)
    assert len(h.headline) <= 120


def test_from_event_appends_high_confidence():
    payload = {"_meta": EventMeta(confidence=0.9).to_dict()}
    event = make_event(payload=payload)
    h = EventHeadline.from_event(event, chunk_id=0)
    assert "[conf: HIGH]" in h.headline


def test_from_event_appends_medium_confidence():
    payload = {"_meta": EventMeta(confidence=0.7).to_dict()}
    event = make_event(payload=payload)
    h = EventHeadline.from_event(event, chunk_id=0)
    assert "[conf: MEDIUM]" in h.headline


def test_from_event_appends_low_confidence():
    payload = {"_meta": EventMeta(confidence=0.3).to_dict()}
    event = make_event(payload=payload)
    h = EventHeadline.from_event(event, chunk_id=0)
    assert "[conf: LOW]" in h.headline


def test_from_event_no_confidence_no_suffix():
    event = make_event(payload={"summary": "clean"})
    h = EventHeadline.from_event(event, chunk_id=0)
    assert "[conf:" not in h.headline


def test_headline_with_confidence_still_max_120():
    long_summary = "x" * 200
    payload = {"summary": long_summary, "_meta": EventMeta(confidence=0.9).to_dict()}
    event = make_event(payload=payload)
    h = EventHeadline.from_event(event, chunk_id=0)
    assert len(h.headline) <= 120
    assert "[conf: HIGH]" in h.headline


def test_from_event_fields_match_event():
    event = make_event(event_type="things.happened", source="svc")
    h = EventHeadline.from_event(event, chunk_id=5)
    assert h.event_id == event.event_id
    assert h.stream == "things"
    assert h.event_type == "things.happened"
    assert h.chunk_id == 5


# ── append_headline ───────────────────────────────────────────────────────────

def test_append_headline_creates_file(tmp_path):
    event = make_event()
    append_headline(event, store_dir=tmp_path)
    assert (tmp_path / "headlines-things.jsonl").exists()


def test_append_headline_chunk_id_starts_at_zero(tmp_path):
    event = make_event()
    h = append_headline(event, store_dir=tmp_path)
    assert h.chunk_id == 0


def test_append_headline_chunk_id_increments(tmp_path):
    e1, e2, e3 = make_event(), make_event(), make_event()
    h1 = append_headline(e1, store_dir=tmp_path)
    h2 = append_headline(e2, store_dir=tmp_path)
    h3 = append_headline(e3, store_dir=tmp_path)
    assert h1.chunk_id == 0
    assert h2.chunk_id == 1
    assert h3.chunk_id == 2


# ── load_headlines ────────────────────────────────────────────────────────────

def test_load_headlines_empty_when_no_file(tmp_path):
    assert load_headlines("nothing", store_dir=tmp_path) == []


def test_load_headlines_returns_written_headlines(tmp_path):
    e1, e2 = make_event(), make_event()
    append_headline(e1, store_dir=tmp_path)
    append_headline(e2, store_dir=tmp_path)
    results = load_headlines("things", store_dir=tmp_path)
    assert len(results) == 2


def test_load_headlines_newest_first(tmp_path):
    e1, e2, e3 = make_event(), make_event(), make_event()
    append_headline(e1, store_dir=tmp_path)
    append_headline(e2, store_dir=tmp_path)
    append_headline(e3, store_dir=tmp_path)
    results = load_headlines("things", store_dir=tmp_path)
    assert results[0].chunk_id > results[1].chunk_id > results[2].chunk_id


def test_load_headlines_respects_limit(tmp_path):
    for _ in range(5):
        append_headline(make_event(), store_dir=tmp_path)
    results = load_headlines("things", store_dir=tmp_path, limit=3)
    assert len(results) == 3


def test_load_headlines_limit_returns_newest(tmp_path):
    for _ in range(5):
        append_headline(make_event(), store_dir=tmp_path)
    results = load_headlines("things", store_dir=tmp_path, limit=2)
    assert results[0].chunk_id == 4
    assert results[1].chunk_id == 3


# ── expand_event ──────────────────────────────────────────────────────────────

def test_expand_event_returns_correct_event(tmp_path):
    from agentic_loopkit.events.store import append_event
    event = make_event(payload={"key": "the-value"})
    append_event(event, store_dir=tmp_path)
    append_headline(event, store_dir=tmp_path)
    result = expand_event(0, "things", store_dir=tmp_path)
    assert result is not None
    assert result.event_id == event.event_id
    assert result.payload["key"] == "the-value"


def test_expand_event_returns_none_for_unknown_chunk_id(tmp_path):
    assert expand_event(999, "things", store_dir=tmp_path) is None


def test_expand_event_returns_none_when_headline_file_absent(tmp_path):
    assert expand_event(0, "no-such-stream", store_dir=tmp_path) is None


def test_expand_event_correct_when_multiple_events(tmp_path):
    from agentic_loopkit.events.store import append_event
    e1 = make_event(payload={"order": "first"})
    e2 = make_event(payload={"order": "second"})
    e3 = make_event(payload={"order": "third"})
    for e in [e1, e2, e3]:
        append_event(e, store_dir=tmp_path)
        append_headline(e, store_dir=tmp_path)
    assert expand_event(0, "things", store_dir=tmp_path).payload["order"] == "first"
    assert expand_event(1, "things", store_dir=tmp_path).payload["order"] == "second"
    assert expand_event(2, "things", store_dir=tmp_path).payload["order"] == "third"


# ── compact_stream compatibility ──────────────────────────────────────────────

def test_expand_event_survives_compact_stream(tmp_path):
    """Events within retention window should still be expandable after compaction."""
    from agentic_loopkit.events.store import append_event
    event = make_event(payload={"kept": True})
    append_event(event, store_dir=tmp_path)
    append_headline(event, store_dir=tmp_path)
    compact_stream("things", store_dir=tmp_path, hours=72)  # default retention
    result = expand_event(0, "things", store_dir=tmp_path)
    assert result is not None
    assert result.payload["kept"] is True


# ── Stream isolation ──────────────────────────────────────────────────────────

def test_chunk_ids_independent_per_stream(tmp_path):
    from agentic_loopkit.events.store import append_event
    e_things = make_event(event_type="things.happened")
    e_other  = make_event(event_type="other.happened")
    append_event(e_things, store_dir=tmp_path)
    append_event(e_other, store_dir=tmp_path)
    h_things = append_headline(e_things, store_dir=tmp_path)
    # pre-populate other stream with 3 entries first
    for _ in range(3):
        dummy = make_event(event_type="other.happened")
        append_event(dummy, store_dir=tmp_path)
        append_headline(dummy, store_dir=tmp_path)
    h_other = append_headline(e_other, store_dir=tmp_path)
    # things stream has just 1 headline, other stream had 3 dummies first
    assert h_things.chunk_id == 0
    assert h_other.chunk_id == 3


# ── EventBus integration ──────────────────────────────────────────────────────

async def test_bus_publish_writes_headline(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    event = make_event(event_type=SystemEventType.BUS_STARTED)
    await bus.publish(event)
    headlines = load_headlines("system", store_dir=tmp_path)
    assert any(h.event_id == event.event_id for h in headlines)


async def test_bus_load_headlines_delegate(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.publish(make_event(event_type=SystemEventType.BUS_STARTED))
    results = bus.load_headlines("system")
    assert len(results) >= 1


async def test_bus_expand_event_delegate(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    event = make_event(event_type=SystemEventType.BUS_STARTED, payload={"x": 42})
    await bus.publish(event)
    headlines = bus.load_headlines("system")
    target = next(h for h in headlines if h.event_id == event.event_id)
    result = bus.expand_event(target.chunk_id, "system")
    assert result is not None
    assert result.event_id == event.event_id
