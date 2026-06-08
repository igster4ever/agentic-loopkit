"""
Tests for v4 theme 3 — adapter liveness tracking:
  - system.adapter_alive on successful tick
  - system.adapter_stalled after consecutive failures
  - liveness_state() snapshot
  - tick registration / release with EventBus
"""
import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.adapters.base import PollingAdapter
from agentic_loopkit.events.models import Event, SystemEventType
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


# ── system.adapter_alive ──────────────────────────────────────────────────────

async def test_tick_emits_adapter_alive_on_success(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus)
    await adapter.tick()
    stored = load_events("system", store_dir=tmp_path)
    assert any(e.event_type == SystemEventType.ADAPTER_ALIVE for e in stored)


async def test_adapter_alive_payload(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus, events=[make_event()])
    await adapter.tick()
    stored = load_events("system", store_dir=tmp_path)
    alive = next(e for e in stored if e.event_type == SystemEventType.ADAPTER_ALIVE)
    assert alive.payload["adapter"] == "stub"
    assert alive.payload["events_emitted"] == 1


async def test_adapter_alive_emitted_even_on_empty_poll(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus, events=[])
    await adapter.tick()
    stored = load_events("system", store_dir=tmp_path)
    assert any(e.event_type == SystemEventType.ADAPTER_ALIVE for e in stored)


async def test_adapter_alive_not_emitted_on_error(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = ErrorAdapter(bus)
    await adapter.tick()
    stored = load_events("system", store_dir=tmp_path)
    assert not any(e.event_type == SystemEventType.ADAPTER_ALIVE for e in stored)


# ── system.adapter_stalled ───────────────────────────────────────────────────

async def test_adapter_stalled_emitted_after_threshold_failures(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = ErrorAdapter(bus)
    adapter.stall_threshold = 3
    for _ in range(3):
        await adapter.tick()
    stored = load_events("system", store_dir=tmp_path)
    assert any(e.event_type == SystemEventType.ADAPTER_STALLED for e in stored)


async def test_adapter_stalled_not_emitted_before_threshold(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = ErrorAdapter(bus)
    adapter.stall_threshold = 3
    for _ in range(2):
        await adapter.tick()
    stored = load_events("system", store_dir=tmp_path)
    assert not any(e.event_type == SystemEventType.ADAPTER_STALLED for e in stored)


async def test_adapter_stalled_payload(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = ErrorAdapter(bus)
    adapter.stall_threshold = 2
    for _ in range(2):
        await adapter.tick()
    stored = load_events("system", store_dir=tmp_path)
    stalled = next(e for e in stored if e.event_type == SystemEventType.ADAPTER_STALLED)
    assert stalled.payload["adapter"] == "error-adapter"
    assert stalled.payload["consecutive_failures"] == 2


async def test_consecutive_failures_reset_on_success(tmp_path):
    """After a successful tick, consecutive_failures resets — no stall signal."""
    bus = EventBus(store_dir=tmp_path)

    class FlakyAdapter(PollingAdapter):
        name = "flaky"
        def __init__(self, bus):
            self._fail = True
            super().__init__(bus)
        async def poll(self, cursor):
            if self._fail:
                raise RuntimeError("boom")
            return [], None

    adapter = FlakyAdapter(bus)
    adapter.stall_threshold = 3
    await adapter.tick()  # failure 1
    await adapter.tick()  # failure 2

    adapter._fail = False
    await adapter.tick()  # success — resets counter

    assert adapter._consecutive_failures == 0

    await adapter.tick()  # failure 1 again — should NOT stall (counter reset)
    adapter._fail = True
    # only 0 consecutive failures at this point in a success path
    stored = load_events("system", store_dir=tmp_path)
    assert not any(e.event_type == SystemEventType.ADAPTER_STALLED for e in stored)


# ── liveness_state() ─────────────────────────────────────────────────────────

async def test_liveness_state_alive_after_success(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus)
    await adapter.tick()
    state = adapter.liveness_state()
    assert state["alive"] is True
    assert state["consecutive_failures"] == 0
    assert state["last_tick_at"] is not None
    assert state["last_success_at"] is not None


async def test_liveness_state_not_alive_after_failure(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = ErrorAdapter(bus)
    await adapter.tick()
    state = adapter.liveness_state()
    assert state["alive"] is False
    assert state["consecutive_failures"] == 1
    assert state["last_success_at"] is None


async def test_liveness_state_before_any_tick(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus)
    state = adapter.liveness_state()
    assert state["alive"] is True  # no failures yet
    assert state["last_tick_at"] is None
    assert state["last_success_at"] is None


async def test_bus_adapter_states_delegates_to_liveness_state(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus)
    bus.add_adapter(adapter)
    states = bus.adapter_states()
    assert len(states) == 1
    assert states[0]["name"] == "stub"


# ── Tick registration ─────────────────────────────────────────────────────────

async def test_active_ticks_zero_before_and_after_tick(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = StubAdapter(bus)
    assert bus._active_ticks == 0
    await adapter.tick()
    assert bus._active_ticks == 0


async def test_active_ticks_zero_after_error_tick(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = ErrorAdapter(bus)
    await adapter.tick()
    assert bus._active_ticks == 0
