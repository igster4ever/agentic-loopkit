"""
tests/dashboard/test_ws.py — WebSocket /ws/tail endpoint tests.

Mix of sync tests (via starlette.testclient.TestClient for connection
lifecycle) and async unit tests (for the enqueue closure logic).
"""

import asyncio
from pathlib import Path

from starlette.testclient import TestClient

from agentic_loopkit.bus import EventBus
from agentic_loopkit.dashboard import create_app
from agentic_loopkit.events.models import Event, WILDCARD_STREAM


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_app(tmp_path: Path):
    bus = EventBus(store_dir=tmp_path)
    app = create_app(bus)
    return app, bus


def _make_event(event_type: str = "things.happened") -> Event:
    return Event(event_type=event_type, source="test", payload={})


# ── Connection lifecycle ──────────────────────────────────────────────────────

def test_ws_connect_and_disconnect(tmp_path):
    """WebSocket endpoint accepts a connection and disconnects cleanly."""
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/tail"):
            pass  # clean disconnect on context exit


def test_ws_stream_query_param_accepted(tmp_path):
    """stream query parameter is accepted without error."""
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/tail?stream=things"):
            pass


def test_ws_event_type_query_param_accepted(tmp_path):
    """event_type query parameter is accepted without error."""
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/tail?event_type=things.happened"):
            pass


# ── Router subscribe / unsubscribe lifecycle ──────────────────────────────────

def test_ws_subscribes_on_connect_unsubscribes_on_disconnect(tmp_path):
    """
    Subscriber count on WILDCARD_STREAM increases while a client is connected
    and returns to its baseline after disconnect.
    """
    app, bus = _make_app(tmp_path)
    before = bus.router.subscriber_count(WILDCARD_STREAM)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/tail"):
            during = bus.router.subscriber_count(WILDCARD_STREAM)

    after = bus.router.subscriber_count(WILDCARD_STREAM)

    assert during > before, "subscriber should be registered while connected"
    assert after == before, "subscriber should be removed after disconnect"


def test_ws_stream_filter_subscribes_to_named_stream(tmp_path):
    """Connecting with stream=things subscribes to 'things', not WILDCARD_STREAM."""
    app, bus = _make_app(tmp_path)
    wildcard_before = bus.router.subscriber_count(WILDCARD_STREAM)
    things_before   = bus.router.subscriber_count("things")

    with TestClient(app) as client:
        with client.websocket_connect("/ws/tail?stream=things"):
            things_during   = bus.router.subscriber_count("things")
            wildcard_during = bus.router.subscriber_count(WILDCARD_STREAM)

    assert things_during > things_before,     "named stream should gain a subscriber"
    assert wildcard_during == wildcard_before, "wildcard stream should be unchanged"


# ── enqueue closure — event-type filter ───────────────────────────────────────

async def test_enqueue_puts_matching_event_on_queue(tmp_path):
    """enqueue() puts a matching event onto the queue as a dict."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    event = _make_event("things.happened")

    async def enqueue(e: Event) -> None:
        try:
            queue.put_nowait(e.to_dict())
        except asyncio.QueueFull:
            pass

    await enqueue(event)
    assert queue.qsize() == 1
    item = queue.get_nowait()
    assert item["event_type"] == "things.happened"


async def test_enqueue_event_type_filter_drops_non_matching(tmp_path):
    """enqueue() with an event_type filter discards non-matching events."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    filter_type = "things.happened"

    async def enqueue(e: Event) -> None:
        if filter_type and str(e.event_type) != filter_type:
            return
        try:
            queue.put_nowait(e.to_dict())
        except asyncio.QueueFull:
            pass

    await enqueue(_make_event("other.event"))
    assert queue.qsize() == 0


async def test_enqueue_event_type_filter_passes_matching(tmp_path):
    """enqueue() with an event_type filter passes matching events through."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    filter_type = "things.happened"

    async def enqueue(e: Event) -> None:
        if filter_type and str(e.event_type) != filter_type:
            return
        try:
            queue.put_nowait(e.to_dict())
        except asyncio.QueueFull:
            pass

    await enqueue(_make_event("things.happened"))
    assert queue.qsize() == 1


async def test_enqueue_empty_filter_passes_all_events(tmp_path):
    """Empty event_type filter (default) allows all event types through."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    filter_type = ""  # default — no filter

    async def enqueue(e: Event) -> None:
        if filter_type and str(e.event_type) != filter_type:
            return
        try:
            queue.put_nowait(e.to_dict())
        except asyncio.QueueFull:
            pass

    await enqueue(_make_event("things.happened"))
    await enqueue(_make_event("other.event"))
    assert queue.qsize() == 2


# ── enqueue closure — backpressure / queue-full drop ─────────────────────────

async def test_enqueue_queue_full_drops_silently(tmp_path):
    """A full queue drops the next event without raising an exception."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    queue.put_nowait({})
    queue.put_nowait({})   # queue is now at capacity

    async def enqueue(e: Event) -> None:
        try:
            queue.put_nowait(e.to_dict())
        except asyncio.QueueFull:
            pass  # expected: drop silently

    # Should not raise even though the queue is full
    await enqueue(_make_event())
    assert queue.qsize() == 2  # still at cap — new event was dropped


async def test_enqueue_queue_full_still_accepts_after_drain(tmp_path):
    """After draining the queue, subsequent events are enqueued normally."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({})   # fill

    async def enqueue(e: Event) -> None:
        try:
            queue.put_nowait(e.to_dict())
        except asyncio.QueueFull:
            pass

    await enqueue(_make_event("first.dropped"))  # dropped
    assert queue.qsize() == 1

    queue.get_nowait()     # drain one slot

    await enqueue(_make_event("second.accepted"))  # now accepted
    assert queue.qsize() == 1
    item = queue.get_nowait()
    assert item["event_type"] == "second.accepted"
