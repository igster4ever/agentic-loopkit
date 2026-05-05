"""
tests/dashboard/test_routes_agents_adapters.py — GET /api/agents and GET /api/adapters
"""

import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock

from agentic_loopkit.bus import EventBus
from agentic_loopkit.dashboard import create_app
from agentic_loopkit.agents.base import AgentBase
from agentic_loopkit.events.models import Event


# ── Minimal concrete agent ────────────────────────────────────────────────────

class StubAgent(AgentBase):
    async def observe(self, event):        return {}
    async def orient(self, event, ctx):    return {}
    async def decide(self, event, orient): return orient
    async def act(self, event, action):    pass


# ── /api/agents ───────────────────────────────────────────────────────────────

async def test_agents_empty(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_agents_lists_registered_agents(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    agent = StubAgent("my-agent", bus)
    agent.subscribe("things")
    bus.register(agent)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/agents")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "my-agent"
    assert data[0]["type"] == "OODA"
    assert "things" in data[0]["streams"]


# ── /api/adapters ─────────────────────────────────────────────────────────────

async def test_adapters_empty(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/adapters")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_adapters_lists_registered_adapters(tmp_path):
    from agentic_loopkit.adapters.git import LocalGitAdapter
    bus = EventBus(store_dir=tmp_path)
    adapter = LocalGitAdapter(bus=bus, repo_path=tmp_path)
    bus.add_adapter(adapter)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/adapters")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "git"
    assert data[0]["type"] == "LocalGitAdapter"
    assert "cursor" in data[0]
