import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import (
    append_event, load_events, load_all_events, compact_stream,
)


def make_event(stream="gps", event_type=None, correlation_id=None) -> Event:
    return Event(
        event_type=event_type or f"{stream}.test",
        source="test",
        payload={},
        correlation_id=correlation_id,
    )


def backdate(event: Event, hours: int) -> dict:
    d = event.to_dict()
    d["timestamp"] = (
        datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return d


def test_append_and_load(tmp_path):
    e = make_event("gps")
    append_event(e, store_dir=tmp_path)
    loaded = load_events("gps", store_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].event_id == e.event_id


def test_load_missing_stream_returns_empty(tmp_path):
    assert load_events("nonexistent", store_dir=tmp_path) == []


def test_load_multiple_events_newest_first(tmp_path):
    for _ in range(3):
        append_event(make_event("gps"), store_dir=tmp_path)
    loaded = load_events("gps", store_dir=tmp_path)
    assert len(loaded) == 3
    timestamps = [e.timestamp for e in loaded]
    assert timestamps == sorted(timestamps, reverse=True)


def test_filter_by_event_type(tmp_path):
    append_event(make_event("gps", event_type="gps.cycle_complete"), store_dir=tmp_path)
    append_event(make_event("gps", event_type="gps.record_new"), store_dir=tmp_path)
    loaded = load_events("gps", store_dir=tmp_path, event_type="gps.cycle_complete")
    assert len(loaded) == 1
    assert loaded[0].event_type == "gps.cycle_complete"


def test_filter_by_correlation_id(tmp_path):
    append_event(make_event("gps", correlation_id="corr-A"), store_dir=tmp_path)
    append_event(make_event("gps", correlation_id="corr-B"), store_dir=tmp_path)
    loaded = load_events("gps", store_dir=tmp_path, correlation_id="corr-A")
    assert len(loaded) == 1
    assert loaded[0].correlation_id == "corr-A"


def test_wildcard_loads_all_streams(tmp_path):
    append_event(make_event("gps"), store_dir=tmp_path)
    append_event(make_event("adr"), store_dir=tmp_path)
    append_event(make_event("system"), store_dir=tmp_path)
    loaded = load_events("*", store_dir=tmp_path)
    assert len(loaded) == 3


def test_hours_filter_excludes_old_events(tmp_path):
    path = tmp_path / "events-gps.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(backdate(make_event("gps"), hours=100)) + "\n")
    append_event(make_event("gps"), store_dir=tmp_path)
    loaded = load_events("gps", store_dir=tmp_path, hours=72)
    assert len(loaded) == 1


def test_load_all_events_ignores_time_filter(tmp_path):
    path = tmp_path / "events-gps.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(backdate(make_event("gps"), hours=200)) + "\n")
    loaded = load_all_events("gps", store_dir=tmp_path)
    assert len(loaded) == 1


def test_compact_removes_old_keeps_recent(tmp_path):
    path = tmp_path / "events-gps.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(backdate(make_event("gps"), hours=100)) + "\n")
    append_event(make_event("gps"), store_dir=tmp_path)
    removed = compact_stream("gps", store_dir=tmp_path, hours=72)
    assert removed == 1
    assert len(load_events("gps", store_dir=tmp_path)) == 1


def test_compact_nothing_to_remove(tmp_path):
    append_event(make_event("gps"), store_dir=tmp_path)
    removed = compact_stream("gps", store_dir=tmp_path, hours=72)
    assert removed == 0


def test_compact_nonexistent_stream(tmp_path):
    assert compact_stream("nonexistent", store_dir=tmp_path) == 0


def test_malformed_line_is_skipped(tmp_path):
    path = tmp_path / "events-gps.jsonl"
    with path.open("w") as f:
        f.write("not valid json\n")
        f.write(json.dumps(make_event("gps").to_dict()) + "\n")
    loaded = load_events("gps", store_dir=tmp_path)
    assert len(loaded) == 1


def test_store_dir_created_if_absent(tmp_path):
    store = tmp_path / "deep" / "nested" / "dir"
    append_event(make_event("gps"), store_dir=store)
    assert (store / "events-gps.jsonl").exists()
