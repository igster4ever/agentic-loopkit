import json
import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.adapters.base import PollingAdapter
from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import load_events


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
