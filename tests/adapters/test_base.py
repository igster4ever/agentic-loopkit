import json
from unittest.mock import AsyncMock, MagicMock

from agentic_loopkit.adapters.base import PollingAdapter, paginate_get
from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import load_events


def make_response(status=200, json_data=None):
    resp = AsyncMock()
    resp.status = status
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value=json_data or {})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


# ── paginate_get ───────────────────────────────────────────────────────────────

async def test_paginate_get_single_page():
    session = MagicMock()
    session.get = MagicMock(return_value=make_response(json_data={"items": [1, 2]}))

    results = await paginate_get(
        session, "http://x", {}, {},
        log_prefix="test", rate_limit_label="page",
        extract_batch=lambda d: d["items"],
        advance=lambda d, params: None,
    )
    assert results == [1, 2]


async def test_paginate_get_follows_advance_across_pages():
    session = MagicMock()
    session.get = MagicMock(side_effect=[
        make_response(json_data={"items": [1], "more": True}),
        make_response(json_data={"items": [2], "more": False}),
    ])

    def advance(data, params):
        return {"page": "2"} if data["more"] else None

    results = await paginate_get(
        session, "http://x", {}, {"page": "1"},
        log_prefix="test", rate_limit_label="page",
        extract_batch=lambda d: d["items"],
        advance=advance,
    )
    assert results == [1, 2]


async def test_paginate_get_stops_on_429_keeping_partial_results():
    session = MagicMock()
    session.get = MagicMock(side_effect=[
        make_response(json_data={"items": [1], "more": True}),
        make_response(status=429),
    ])

    results = await paginate_get(
        session, "http://x", {}, {},
        log_prefix="test", rate_limit_label="page",
        extract_batch=lambda d: d["items"],
        advance=lambda d, params: {"page": "2"} if d.get("more") else None,
    )
    assert results == [1]


async def test_paginate_get_stops_when_is_ok_returns_false():
    session = MagicMock()
    session.get = MagicMock(return_value=make_response(json_data={"ok": False, "items": [1]}))

    results = await paginate_get(
        session, "http://x", {}, {},
        log_prefix="test", rate_limit_label="page",
        extract_batch=lambda d: d["items"],
        advance=lambda d, params: None,
        is_ok=lambda d: d["ok"],
    )
    assert results == []


async def test_paginate_get_invokes_on_page_callback_per_page():
    session = MagicMock()
    session.get = MagicMock(side_effect=[
        make_response(json_data={"items": [1], "more": True}),
        make_response(json_data={"items": [2], "more": False}),
    ])
    seen_batches = []

    await paginate_get(
        session, "http://x", {}, {},
        log_prefix="test", rate_limit_label="page",
        extract_batch=lambda d: d["items"],
        advance=lambda d, params: {"page": "2"} if d["more"] else None,
        on_page=lambda batch, data: seen_batches.append(list(batch)),
    )
    assert seen_batches == [[1], [2]]


def make_event() -> Event:
    return Event(event_type="test.item", source="stub", payload={})


class StubAdapter(PollingAdapter):
    name = "stub"

    def __init__(self, bus, events=None, new_cursor=None):
        self._events_to_emit = events or []
        self._new_cursor = new_cursor
        super().__init__(bus)

    async def poll(self, cursor):
        return self._events_to_emit, self._new_cursor


class ErrorAdapter(PollingAdapter):
    name = "error-adapter"

    async def poll(self, cursor):
        raise RuntimeError("API is down")


async def test_tick_publishes_events(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus, events=[make_event(), make_event()], new_cursor=1)
    count = await adapter.tick()
    assert count == 2
    stored = load_events("test", store_dir=tmp_path)
    assert len(stored) == 2


async def test_tick_empty_poll_returns_zero(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus, events=[], new_cursor=None)
    count = await adapter.tick()
    assert count == 0


async def test_cursor_persisted_on_tick(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus, new_cursor=99999)
    await adapter.tick()
    cursor_file = tmp_path / "cursor-stub.json"
    assert cursor_file.exists()
    assert json.loads(cursor_file.read_text()) == 99999


async def test_cursor_not_written_when_none_returned(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus, new_cursor=None)
    await adapter.tick()
    assert not (tmp_path / "cursor-stub.json").exists()


async def test_cursor_loaded_from_file_on_init(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    (tmp_path / "cursor-stub.json").write_text(json.dumps(42))
    adapter = StubAdapter(bus)
    assert adapter._cursor == 42


async def test_cursor_updated_in_memory_after_tick(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus, new_cursor=500)
    await adapter.tick()
    assert adapter._cursor == 500


async def test_poll_error_emits_adapter_error_event(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = ErrorAdapter(bus)
    count = await adapter.tick()
    assert count == 0
    stored = load_events("system", store_dir=tmp_path)
    error_events = [e for e in stored if e.event_type == "system.adapter_error"]
    assert len(error_events) == 1
    assert error_events[0].payload["adapter"] == "error-adapter"
    assert "API is down" in error_events[0].payload["error"]


async def test_poll_error_does_not_update_cursor(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    (tmp_path / "cursor-error-adapter.json").write_text(json.dumps(100))
    adapter = ErrorAdapter(bus)
    await adapter.tick()
    assert adapter._cursor == 100


async def test_null_cursor_not_persisted_on_empty_poll(tmp_path):
    """When poll() returns new_cursor=None the cursor must not advance."""
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus, events=[], new_cursor=42)
    await adapter.tick()
    assert adapter._cursor == 42

    # Second tick: poll returns None cursor — cursor must stay at 42
    adapter._events_to_emit = []
    adapter._new_cursor = None
    await adapter.tick()
    assert adapter._cursor == 42
