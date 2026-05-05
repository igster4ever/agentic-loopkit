"""
tests/adapters/test_slack.py — SlackAdapter unit tests.

All tests mock the aiohttp HTTP layer so no real Slack API is called.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agentic_loopkit.bus import EventBus
from agentic_loopkit.adapters.slack import SlackAdapter, SlackEventType, _ts_to_iso
from agentic_loopkit.events.models import Event


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_adapter(bus, channel_ids=None) -> SlackAdapter:
    return SlackAdapter(
        bus=bus,
        bot_token="xoxb-test",
        channel_ids=channel_ids or ["C001"],
    )


def make_message(ts="1715000001.000000", user="U001", text="Hello") -> dict:
    return {
        "ts":       ts,
        "user":     user,
        "text":     text,
        "type":     "message",
        "subtype":  None,
        "thread_ts": None,
        "attachments": [],
        "blocks":   [],
        "reactions": [],
    }


# ── Constructor validation ────────────────────────────────────────────────────

def test_requires_channel_ids(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    with pytest.raises(ValueError):
        SlackAdapter(bus=bus, bot_token="xoxb-test", channel_ids=[])


def test_page_size_capped_at_200(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = SlackAdapter(bus=bus, bot_token="xoxb-test", channel_ids=["C001"], page_size=999)
    assert adapter._page_size == 200


def test_accepts_valid_config(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, channel_ids=["C001", "C002"])
    assert adapter._channel_ids == ["C001", "C002"]
    assert adapter._bot_token == "xoxb-test"


# ── Event mapping ─────────────────────────────────────────────────────────────

def test_message_to_event_type(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    event = adapter._message_to_event(make_message(), "C001")
    assert event.event_type == SlackEventType.MESSAGE_RECEIVED


def test_message_to_event_stream_is_slack(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    event = adapter._message_to_event(make_message(), "C001")
    assert event.stream == "slack"


def test_message_to_event_correlation_id(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    event = adapter._message_to_event(make_message(ts="1715000001.000000"), "C001")
    assert event.correlation_id == "C001:1715000001.000000"


def test_message_to_event_payload_fields(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    msg = make_message(ts="1715000001.000000", user="U999", text="hi there")
    event = adapter._message_to_event(msg, "C001")
    assert event.payload["channel_id"] == "C001"
    assert event.payload["ts"] == "1715000001.000000"
    assert event.payload["user"] == "U999"
    assert event.payload["text"] == "hi there"


def test_message_to_event_timestamp_iso(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    event = adapter._message_to_event(make_message(ts="1715000000.000000"), "C001")
    assert event.payload["timestamp"] is not None
    assert "T" in event.payload["timestamp"]


def test_message_to_event_handles_bot_message(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    msg = {"ts": "1715000001.000000", "bot_id": "B001", "text": "bot says hi",
           "subtype": "bot_message", "attachments": [], "blocks": [], "reactions": []}
    event = adapter._message_to_event(msg, "C001")
    assert event.payload["user"] == "B001"


# ── Poll logic ────────────────────────────────────────────────────────────────

async def test_poll_no_messages_returns_empty_and_none_cursor(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    adapter._fetch_channel = AsyncMock(return_value=([], "1715000000.000000"))
    events, cursor = await adapter.poll(cursor=None)
    assert events == []
    assert cursor is None


async def test_poll_returns_events_and_new_cursor(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    msgs = [make_message(ts="1715000002.000000"), make_message(ts="1715000001.000000")]
    adapter._fetch_channel = AsyncMock(return_value=(msgs, "1715000002.000000"))
    events, cursor = await adapter.poll(cursor=None)
    assert len(events) == 2
    assert cursor == {"C001": "1715000002.000000"}


async def test_poll_uses_per_channel_cursor(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, channel_ids=["C001", "C002"])
    captured_oldest: list = []

    async def fake_fetch(channel_id, oldest):
        captured_oldest.append((channel_id, oldest))
        return ([], oldest)

    adapter._fetch_channel = fake_fetch
    await adapter.poll(cursor={"C001": "111.0", "C002": "222.0"})

    assert ("C001", "111.0") in captured_oldest
    assert ("C002", "222.0") in captured_oldest


async def test_poll_merges_cursors_across_channels(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, channel_ids=["C001", "C002"])

    async def fake_fetch(channel_id, oldest):
        if channel_id == "C001":
            return ([make_message(ts="999.0")], "999.0")
        else:
            return ([make_message(ts="888.0")], "888.0")

    adapter._fetch_channel = fake_fetch
    events, cursor = await adapter.poll(cursor={})
    assert len(events) == 2
    assert cursor["C001"] == "999.0"
    assert cursor["C002"] == "888.0"


# ── Timestamp helper ──────────────────────────────────────────────────────────

def test_ts_to_iso_valid():
    result = _ts_to_iso("0.0")
    assert result == "1970-01-01T00:00:00Z"


def test_ts_to_iso_invalid_returns_none():
    assert _ts_to_iso("not-a-number") is None


def test_ts_to_iso_empty_returns_none():
    assert _ts_to_iso("") is None
