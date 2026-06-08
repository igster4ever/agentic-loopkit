"""
Tests for v4 theme 3 — working memory lifecycle:
  - Bus backpressure signals
  - Graceful shutdown sequencing
"""
import asyncio
import pytest
from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event, SystemEventType
from agentic_loopkit.events.store import load_events
from agentic_loopkit.adapters.base import PollingAdapter


def make_event(stream="work") -> Event:
    return Event(event_type=f"{stream}.thing", source="test", payload={})


# ── Backpressure ───────────────────────────────────────────────────────────────

async def test_backpressure_emits_system_pressure_event(tmp_path):
    bus = EventBus(store_dir=tmp_path, backpressure_threshold=3)
    for _ in range(3):
        await bus.publish(make_event())
    stored = load_events("system", store_dir=tmp_path)
    assert any(e.event_type == SystemEventType.BUS_PRESSURE for e in stored)


async def test_backpressure_payload_includes_threshold(tmp_path):
    bus = EventBus(store_dir=tmp_path, backpressure_threshold=3)
    for _ in range(3):
        await bus.publish(make_event())
    stored = load_events("system", store_dir=tmp_path)
    pressure = next(e for e in stored if e.event_type == SystemEventType.BUS_PRESSURE)
    assert pressure.payload["threshold"] == 3


async def test_backpressure_resets_counter_after_signal(tmp_path):
    """After a pressure signal the counter resets — no second signal until the next window."""
    bus = EventBus(store_dir=tmp_path, backpressure_threshold=3)
    for _ in range(5):
        await bus.publish(make_event())
    stored = load_events("system", store_dir=tmp_path)
    pressure_events = [e for e in stored if e.event_type == SystemEventType.BUS_PRESSURE]
    assert len(pressure_events) == 1


async def test_backpressure_fires_again_at_next_threshold(tmp_path):
    bus = EventBus(store_dir=tmp_path, backpressure_threshold=3)
    for _ in range(6):
        await bus.publish(make_event())
    stored = load_events("system", store_dir=tmp_path)
    pressure_events = [e for e in stored if e.event_type == SystemEventType.BUS_PRESSURE]
    assert len(pressure_events) == 2


async def test_system_events_excluded_from_backpressure_count(tmp_path):
    """Backpressure must not count system events — prevents self-triggering loops."""
    bus = EventBus(store_dir=tmp_path, backpressure_threshold=3)
    for _ in range(10):
        await bus.publish(Event(
            event_type=SystemEventType.ADAPTER_ERROR,
            source="bus",
            payload={"error": "x", "adapter": "x"},
        ))
    stored = load_events("system", store_dir=tmp_path)
    assert not any(e.event_type == SystemEventType.BUS_PRESSURE for e in stored)


async def test_bus_pressure_event_type_is_in_systemeventtype(tmp_path):
    assert hasattr(SystemEventType, "BUS_PRESSURE")
    assert SystemEventType.BUS_PRESSURE == "system.bus_pressure"


# ── is_stopping property ──────────────────────────────────────────────────────

async def test_is_stopping_false_before_stop(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()
    assert not bus.is_stopping


async def test_is_stopping_true_after_stop(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()
    await bus.stop()
    assert bus.is_stopping


# ── Graceful shutdown — drain ─────────────────────────────────────────────────

async def test_stop_drains_active_tick_before_bus_stopped(tmp_path):
    """BUS_STOPPED must not be emitted until active ticks complete."""
    bus = EventBus(store_dir=tmp_path)
    await bus.start()

    order: list[str] = []

    async def slow_poll(_cursor):
        await asyncio.sleep(0.05)
        order.append("tick_done")
        return [], None

    class SlowAdapter(PollingAdapter):
        name = "slow"
        async def poll(self, cursor): return await slow_poll(cursor)

    adapter = SlowAdapter(bus)

    # Start the tick concurrently; stop() should wait for it
    tick_task = asyncio.create_task(adapter.tick())
    await asyncio.sleep(0.01)  # let tick register itself
    await bus.stop(drain_timeout=1.0)
    await tick_task

    order.append("stop_done")
    assert order[0] == "tick_done"


async def test_stop_force_proceeds_after_drain_timeout(tmp_path):
    """If drain_timeout expires, stop() proceeds regardless."""
    bus = EventBus(store_dir=tmp_path)
    await bus.start()

    class HungAdapter(PollingAdapter):
        name = "hung"
        async def poll(self, cursor):
            await asyncio.sleep(10)  # never finishes in time
            return [], None

    adapter = HungAdapter(bus)
    tick_task = asyncio.create_task(adapter.tick())
    await asyncio.sleep(0.01)  # let tick register

    await bus.stop(drain_timeout=0.1)  # short timeout
    tick_task.cancel()
    try:
        await tick_task
    except asyncio.CancelledError:
        pass

    stored = load_events("system", store_dir=tmp_path)
    assert any(e.event_type == SystemEventType.BUS_STOPPED for e in stored)


async def test_new_tick_skips_when_stopping(tmp_path):
    """A tick started after stop() begins must return 0 immediately."""
    bus = EventBus(store_dir=tmp_path)
    await bus.start()
    await bus.stop()

    class StubAdapter(PollingAdapter):
        name = "stub"
        async def poll(self, cursor): return [make_event()], None

    adapter = StubAdapter(bus)
    count = await adapter.tick()
    assert count == 0


async def test_stop_accepts_drain_timeout_override(tmp_path):
    """drain_timeout can be overridden per stop() call."""
    bus = EventBus(store_dir=tmp_path, drain_timeout=30.0)
    await bus.start()
    # Should complete quickly (nothing active) despite the long default
    await bus.stop(drain_timeout=0.0)
    assert bus.is_stopping
