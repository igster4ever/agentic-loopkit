import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from agentic_loopkit.bus import EventBus
from agentic_loopkit.adapters.clickup import ClickUpAdapter, ClickUpEventType
from agentic_loopkit.events.models import Event


def make_task(
    task_id="t1",
    date_created=1000,
    date_updated=2000,
    name="Test Task",
    status="open",
) -> dict:
    return {
        "id": task_id,
        "name": name,
        "status": {"status": status},
        "assignees": [{"username": "alice"}],
        "list": {"name": "Sprint 1", "id": "list-1"},
        "url": f"https://app.clickup.com/t/{task_id}",
        "date_created": str(date_created),
        "date_updated": str(date_updated),
        "priority": {"priority": "high"},
        "tags": [{"name": "backend"}],
        "custom_fields": [{"name": "Story Points", "value": 3}],
    }


def make_adapter(bus, list_ids=None, team_id=None) -> ClickUpAdapter:
    return ClickUpAdapter(
        bus=bus,
        api_token="test-token",
        list_ids=list_ids or ["list-1"],
        team_id=team_id,
    )


# ── Constructor validation ────────────────────────────────────────────────────

def test_requires_list_ids_or_team_id(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    with pytest.raises(ValueError):
        ClickUpAdapter(bus=bus, api_token="token")


def test_accepts_list_ids_only(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = ClickUpAdapter(bus=bus, api_token="token", list_ids=["abc"])
    assert adapter._list_ids == ["abc"]


def test_accepts_team_id_only(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = ClickUpAdapter(bus=bus, api_token="token", team_id="team-1")
    assert adapter._team_id == "team-1"


# ── Event type classification ─────────────────────────────────────────────────

def test_task_to_event_created_when_date_created_gte_since(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    task = make_task(date_created=1000, date_updated=2000)
    event = adapter._task_to_event(task, since_ms=500)
    assert event.event_type == ClickUpEventType.TASK_CREATED


def test_task_to_event_updated_when_date_created_lt_since(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    task = make_task(date_created=1000, date_updated=2000)
    event = adapter._task_to_event(task, since_ms=1500)
    assert event.event_type == ClickUpEventType.TASK_UPDATED


def test_task_to_event_created_at_exact_boundary(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    task = make_task(date_created=1000, date_updated=2000)
    event = adapter._task_to_event(task, since_ms=1000)
    assert event.event_type == ClickUpEventType.TASK_CREATED


# ── Payload mapping ───────────────────────────────────────────────────────────

def test_task_to_event_payload_fields(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    task = make_task()
    event = adapter._task_to_event(task, since_ms=0)

    assert event.correlation_id == "t1"
    assert event.payload["id"] == "t1"
    assert event.payload["name"] == "Test Task"
    assert event.payload["status"] == "open"
    assert event.payload["assignees"] == ["alice"]
    assert event.payload["list"] == "Sprint 1"
    assert event.payload["list_id"] == "list-1"
    assert event.payload["priority"] == "high"
    assert event.payload["tags"] == ["backend"]
    assert event.payload["custom_fields"] == {"Story Points": 3}
    assert event.payload["url"] == "https://app.clickup.com/t/t1"


def test_task_to_event_stream_is_clickup(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    event = adapter._task_to_event(make_task(), since_ms=0)
    assert event.stream == "clickup"


def test_task_to_event_handles_missing_optional_fields(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    minimal_task = {"id": "t1", "date_created": "1000", "date_updated": "2000"}
    event = adapter._task_to_event(minimal_task, since_ms=0)
    assert event.payload["status"] == ""
    assert event.payload["assignees"] == []
    assert event.payload["priority"] is None
    assert event.payload["custom_fields"] == {}


def test_task_to_event_priority_none_when_not_dict(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    task = make_task()
    task["priority"] = None
    event = adapter._task_to_event(task, since_ms=0)
    assert event.payload["priority"] is None


# ── Deduplication ─────────────────────────────────────────────────────────────

async def test_poll_deduplicates_same_task_across_lists(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus, list_ids=["list-1", "list-2"])
    duplicate_task = make_task(task_id="same-id")

    async def fake_fetch_list(list_id, since_ms):
        return [duplicate_task]

    adapter._fetch_list = fake_fetch_list
    events, _ = await adapter.poll(cursor=0)
    assert len(events) == 1


# ── Cursor calculation ────────────────────────────────────────────────────────

async def test_poll_cursor_is_max_date_updated(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    tasks = [
        make_task(task_id="t1", date_updated=1000),
        make_task(task_id="t2", date_updated=3000),
        make_task(task_id="t3", date_updated=2000),
    ]
    adapter._fetch_all = AsyncMock(return_value=tasks)
    _, new_cursor = await adapter.poll(cursor=0)
    assert new_cursor == 3000


async def test_poll_returns_empty_and_none_cursor_on_no_tasks(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    adapter._fetch_all = AsyncMock(return_value=[])
    events, cursor = await adapter.poll(cursor=0)
    assert events == []
    assert cursor is None


async def test_poll_default_cursor_24h_ago_when_none(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    adapter = make_adapter(bus)
    captured_since = []

    async def fake_fetch_all(since_ms):
        captured_since.append(since_ms)
        return []

    adapter._fetch_all = fake_fetch_all
    await adapter.poll(cursor=None)
    assert len(captured_since) == 1
    # Should be approximately 24h ago in ms
    import time
    now_ms = int(time.time() * 1000)
    assert abs(captured_since[0] - (now_ms - 86_400_000)) < 5000


# ── ClickUpEventType stream ───────────────────────────────────────────────────

def test_clickup_event_types_have_clickup_stream():
    for et in ClickUpEventType:
        e = Event(event_type=et, source="clickup", payload={})
        assert e.stream == "clickup"
