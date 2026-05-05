"""
tests/dashboard/test_routes_events.py — GET /api/events and GET /api/events/{event_id}
"""

import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from agentic_loopkit.bus import EventBus
from agentic_loopkit.dashboard import create_app
from agentic_loopkit.events.models import Event


def _make_event(event_type="things.happened", source="svc", correlation_id=None) -> Event:
    return Event(event_type=event_type, source=source, payload={}, correlation_id=correlation_id)


async def _publish_and_app(tmp_path, events):
    bus = EventBus(store_dir=tmp_path)
    for e in events:
        await bus.publish(e)
    return create_app(bus)


# ── /api/events ───────────────────────────────────────────────────────────────

async def test_events_empty(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/events")
    body = resp.json()
    assert resp.status_code == 200
    assert body["events"] == []
    assert body["total"] == 0


async def test_events_returns_published_events(tmp_path):
    e1 = _make_event("things.a")
    e2 = _make_event("other.b")
    app = await _publish_and_app(tmp_path, [e1, e2])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/events")
    body = resp.json()
    assert body["total"] >= 2
    types = [e["event_type"] for e in body["events"]]
    assert "things.a" in types
    assert "other.b" in types


async def test_events_filter_by_stream(tmp_path):
    e1 = _make_event("things.a")
    e2 = _make_event("other.b")
    app = await _publish_and_app(tmp_path, [e1, e2])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/events?stream=things")
    body = resp.json()
    assert all(e["stream"] == "things" for e in body["events"])


async def test_events_filter_by_source(tmp_path):
    e1 = _make_event(source="svc-a")
    e2 = _make_event(source="svc-b")
    app = await _publish_and_app(tmp_path, [e1, e2])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/events?source=svc-a")
    body = resp.json()
    sources = [e["source"] for e in body["events"]]
    assert all(s == "svc-a" for s in sources)


async def test_events_filter_by_correlation_id(tmp_path):
    e1 = _make_event(correlation_id="wf-001")
    e2 = _make_event(correlation_id="wf-002")
    app = await _publish_and_app(tmp_path, [e1, e2])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/events?correlation_id=wf-001")
    body = resp.json()
    assert all(e["correlation_id"] == "wf-001" for e in body["events"])


async def test_events_limit(tmp_path):
    events = [_make_event() for _ in range(5)]
    app = await _publish_and_app(tmp_path, events)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/events?limit=2")
    body = resp.json()
    assert len(body["events"]) <= 2
    assert body["limit"] == 2


# ── /api/events/{event_id} ────────────────────────────────────────────────────

async def test_get_event_by_id(tmp_path):
    e = _make_event()
    app = await _publish_and_app(tmp_path, [e])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(f"/api/events/{e.event_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["event"]["event_id"] == e.event_id


async def test_get_event_returns_related_in_chain(tmp_path):
    e1 = _make_event(correlation_id="wf-001")
    e2 = _make_event(correlation_id="wf-001")
    app = await _publish_and_app(tmp_path, [e1, e2])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(f"/api/events/{e1.event_id}")
    body = resp.json()
    related_ids = [r["event_id"] for r in body["related"]]
    assert e2.event_id in related_ids
    assert e1.event_id not in related_ids


async def test_get_event_not_found_returns_404(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/events/no-such-id")
    assert resp.status_code == 404
