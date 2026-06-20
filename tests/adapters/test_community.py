"""
tests/adapters/test_community.py — CommunityFeedAdapter unit tests.
"""

import json
from pathlib import Path

import pytest

from agentic_loopkit.bus import EventBus
from agentic_loopkit.adapters.community import CommunityFeedAdapter, CommunityEventType
from agentic_loopkit.events.models import TrustLevel


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_adapter(bus, feed_path, **kwargs) -> CommunityFeedAdapter:
    return CommunityFeedAdapter(bus=bus, feed_path=feed_path, **kwargs)


def write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def append_jsonl(path: Path, entry: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Constructor ───────────────────────────────────────────────────────────────

def test_adapter_stores_feed_path(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    adapter = make_adapter(bus, feed_path=feed)
    assert adapter._feed_path == feed


def test_adapter_default_name(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, feed_path=tmp_path / "feed.jsonl")
    assert adapter.name == "community"


def test_adapter_custom_name(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, feed_path=tmp_path / "feed.jsonl", name="gps-insights")
    assert adapter.name == "gps-insights"


# ── poll — basic ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_missing_file_returns_empty(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, feed_path=tmp_path / "nonexistent.jsonl")
    events, new_cursor = await adapter.poll(None)
    assert events == []
    assert new_cursor is None


@pytest.mark.asyncio
async def test_poll_empty_file_returns_empty(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    feed.write_text("")
    events, new_cursor = await adapter.poll(None) if False else await make_adapter(bus, feed).poll(None)
    assert events == []
    assert new_cursor is None


@pytest.mark.asyncio
async def test_poll_reads_entries_from_start(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"id": "a", "msg": "hello"}, {"id": "b", "msg": "world"}])

    adapter = make_adapter(bus, feed)
    events, new_cursor = await adapter.poll(None)

    assert len(events) == 2
    assert events[0].payload["id"] == "a"
    assert events[1].payload["id"] == "b"
    assert new_cursor is not None
    assert new_cursor > 0


@pytest.mark.asyncio
async def test_poll_advances_cursor_on_second_call(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"id": "a"}])

    adapter = make_adapter(bus, feed)
    events1, cursor1 = await adapter.poll(None)
    assert len(events1) == 1

    # Append a new entry
    append_jsonl(feed, {"id": "b"})

    events2, cursor2 = await adapter.poll(cursor1)
    assert len(events2) == 1
    assert events2[0].payload["id"] == "b"
    assert cursor2 > cursor1


@pytest.mark.asyncio
async def test_poll_no_new_entries_returns_none_cursor(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"id": "a"}])

    adapter = make_adapter(bus, feed)
    _, cursor1 = await adapter.poll(None)

    # No new entries
    events, cursor2 = await adapter.poll(cursor1)
    assert events == []
    assert cursor2 is None


# ── TrustLevel ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_events_are_untrusted(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"id": "x"}, {"id": "y"}])

    adapter = make_adapter(bus, feed)
    events, _ = await adapter.poll(None)

    assert all(e.trust_level == TrustLevel.UNTRUSTED for e in events)


# ── Event type and fields ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_type_is_entry_received(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"id": "z"}])

    adapter = make_adapter(bus, feed)
    events, _ = await adapter.poll(None)

    assert events[0].event_type == CommunityEventType.ENTRY_RECEIVED


@pytest.mark.asyncio
async def test_correlation_id_from_id_field(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"id": "entry-42", "data": "x"}])

    adapter = make_adapter(bus, feed)
    events, _ = await adapter.poll(None)

    assert events[0].correlation_id == "entry-42"


@pytest.mark.asyncio
async def test_correlation_id_from_correlation_id_field(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"correlation_id": "flow-99", "data": "x"}])

    adapter = make_adapter(bus, feed)
    events, _ = await adapter.poll(None)

    assert events[0].correlation_id == "flow-99"


@pytest.mark.asyncio
async def test_correlation_id_none_when_absent(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"data": "no-id"}])

    adapter = make_adapter(bus, feed)
    events, _ = await adapter.poll(None)

    assert events[0].correlation_id is None


@pytest.mark.asyncio
async def test_source_matches_adapter_name(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"id": "1"}])

    adapter = make_adapter(bus, feed, name="my-feed")
    events, _ = await adapter.poll(None)

    assert events[0].source == "my-feed"


# ── Malformed JSON ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_malformed_line_skipped(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    feed.write_text('{"id": "good"}\nnot-json\n{"id": "also-good"}\n')

    adapter = make_adapter(bus, feed)
    events, new_cursor = await adapter.poll(None)

    assert len(events) == 2
    assert events[0].payload["id"] == "good"
    assert events[1].payload["id"] == "also-good"
    assert new_cursor is not None


# ── Truncation / rotation ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_truncated_file_resets_to_start(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    feed = tmp_path / "feed.jsonl"
    write_jsonl(feed, [{"id": "old1"}, {"id": "old2"}])

    adapter = make_adapter(bus, feed)
    _, old_cursor = await adapter.poll(None)

    # Simulate file rotation: rewrite with new content
    feed.write_text(json.dumps({"id": "new1"}) + "\n")

    # old_cursor > new file size → should reset
    events, new_cursor = await adapter.poll(old_cursor)

    assert len(events) == 1
    assert events[0].payload["id"] == "new1"
    assert new_cursor is not None


# ── Public API import ──────────────────────────────────────────────────────────

def test_public_api_exports():
    from agentic_loopkit import CommunityFeedAdapter, CommunityEventType
    assert CommunityFeedAdapter is not None
    assert CommunityEventType.ENTRY_RECEIVED == "community.entry_received"
