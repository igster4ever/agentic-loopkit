"""
Tests for periodic stream compaction wiring on EventBus.

compact_stream() (agentic_loopkit/events/store.py) previously had no caller
outside tests — an operational gap flagged in the 2026-07-09/07-21 code
reviews, since ProjectionAgent/FailurePatternAgent/SelfHarnessExecutor all
call load_events()/load_all_events() with no hours cutoff on every
triggering event, so cost scales with unbounded lifetime event count.
"""
import asyncio
from datetime import timedelta

from agentic_loopkit.bus import EventBus
from agentic_loopkit.events.models import Event
from agentic_loopkit.events.store import append_event, load_all_events
from agentic_loopkit.utils.time import utc_now


def _write_stale_event(store_dir, stream="gps", age_hours=100):
    event = Event(event_type=f"{stream}.thing", source="test", payload={})
    event.timestamp = utc_now() - timedelta(hours=age_hours)
    append_event(event, store_dir=store_dir)


# ── compact_all_streams() — manual trigger ────────────────────────────────────

async def test_compact_all_streams_removes_stale_events_across_streams(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    _write_stale_event(tmp_path, stream="gps", age_hours=100)
    _write_stale_event(tmp_path, stream="adr", age_hours=100)

    removed = bus.compact_all_streams()

    assert removed == {"gps": 1, "adr": 1}
    assert load_all_events("gps", store_dir=tmp_path) == []
    assert load_all_events("adr", store_dir=tmp_path) == []


async def test_compact_all_streams_keeps_fresh_events(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.publish(Event(event_type="gps.thing", source="test", payload={}))

    removed = bus.compact_all_streams()

    assert removed == {}
    assert len(load_all_events("gps", store_dir=tmp_path)) == 1


async def test_compact_all_streams_no_op_when_store_empty(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    assert bus.compact_all_streams() == {}


async def test_compact_all_streams_respects_custom_retention(tmp_path):
    bus = EventBus(store_dir=tmp_path, compaction_retention_hours=1)
    _write_stale_event(tmp_path, stream="gps", age_hours=2)

    removed = bus.compact_all_streams()

    assert removed == {"gps": 1}


# ── Background loop wiring ────────────────────────────────────────────────────

async def test_compaction_task_not_started_when_interval_unset(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.start()
    assert bus._compaction_task is None
    await bus.stop()


async def test_compaction_task_started_when_interval_set(tmp_path):
    bus = EventBus(store_dir=tmp_path, compaction_interval_hours=24)
    await bus.start()
    assert bus._compaction_task is not None
    assert not bus._compaction_task.done()
    await bus.stop()


async def test_compaction_task_cancelled_on_stop(tmp_path):
    bus = EventBus(store_dir=tmp_path, compaction_interval_hours=24)
    await bus.start()
    task = bus._compaction_task
    await bus.stop()
    assert task.cancelled() or task.done()
    assert bus._compaction_task is None


async def test_compaction_loop_fires_after_interval(tmp_path):
    """Use a near-zero interval (seconds, expressed as a fraction of an hour) to
    prove the loop actually calls compact_all_streams() on schedule, not just
    that the task object exists."""
    bus = EventBus(store_dir=tmp_path, compaction_interval_hours=0.01 / 3600)  # ~0.01s
    _write_stale_event(tmp_path, stream="gps", age_hours=100)

    await bus.start()
    await asyncio.sleep(0.1)
    await bus.stop()

    assert load_all_events("gps", store_dir=tmp_path) == []
