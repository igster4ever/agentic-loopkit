"""
tests/dashboard/test_routes_streams.py — GET /api/streams
"""

import json
import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from agentic_loopkit.bus import EventBus
from agentic_loopkit.dashboard import create_app
from agentic_loopkit.events.models import Event


async def _make_client(tmp_path: Path):
    bus = EventBus(store_dir=tmp_path)
    app = create_app(bus)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_streams_empty_store(tmp_path):
    client = await _make_client(tmp_path)
    async with client as c:
        resp = await c.get("/api/streams")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_streams_returns_known_stream(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.publish(Event(event_type="things.happened", source="test", payload={}))
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/streams")
    data = resp.json()
    names = [s["stream"] for s in data]
    assert "things" in names


async def test_streams_counts_events(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    for _ in range(3):
        await bus.publish(Event(event_type="things.happened", source="test", payload={}))
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/streams")
    data = resp.json()
    things = next(s for s in data if s["stream"] == "things")
    assert things["event_count"] == 3


async def test_streams_has_last_event_at(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    await bus.publish(Event(event_type="things.happened", source="test", payload={}))
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/streams")
    data = resp.json()
    things = next(s for s in data if s["stream"] == "things")
    assert things["last_event_at"] is not None
