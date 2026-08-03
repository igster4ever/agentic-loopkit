"""
tests/dashboard/test_routes_memory.py — GET /api/memory routes

Covers both the 503-unwired path (bus.memory is None, the default) and a
wired-store happy path using a minimal fake store matching the interface
documented in docs/memorykit-design.md (list/get/history).
"""

from pathlib import Path

from httpx import ASGITransport, AsyncClient

from agentic_loopkit.bus import EventBus
from agentic_loopkit.dashboard import create_app


class _FakeRecord:
    def __init__(self, key, value, agent_id, memory_id="mem-1"):
        self.key = key
        self.value = value
        self.agent_id = agent_id
        self.memory_id = memory_id

    def to_dict(self):
        return {"key": self.key, "value": self.value, "agent_id": self.agent_id, "memory_id": self.memory_id}


class _FakeOp:
    def __init__(self, op, new_value):
        self.op = op
        self.new_value = new_value

    def to_dict(self):
        return {"op": self.op, "new_value": self.new_value}


class _FakeStore:
    def __init__(self, records):
        self._records = records

    async def list(self, agent_id=None, tags=(), min_confidence=0.0):
        if agent_id is None:
            return list(self._records)
        return [r for r in self._records if r.agent_id == agent_id]

    async def get(self, key, agent_id):
        for r in self._records:
            if r.key == key and r.agent_id == agent_id:
                return r
        return None

    async def history(self, memory_id):
        return [_FakeOp("ADD", "hello")]


async def _make_client(tmp_path: Path, *, wired: bool):
    bus = EventBus(store_dir=tmp_path)
    if wired:
        bus.memory = _FakeStore([_FakeRecord("greeting", "hello", "shared")])
    app = create_app(bus)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_memory_not_wired_returns_503(tmp_path):
    client = await _make_client(tmp_path, wired=False)
    async with client as c:
        resp = await c.get("/api/memory")
    assert resp.status_code == 503
    assert "patch_bus" in resp.json()["detail"]


async def test_memory_agent_route_not_wired_returns_503(tmp_path):
    client = await _make_client(tmp_path, wired=False)
    async with client as c:
        resp = await c.get("/api/memory/shared")
    assert resp.status_code == 503


async def test_memory_history_route_not_wired_returns_503(tmp_path):
    client = await _make_client(tmp_path, wired=False)
    async with client as c:
        resp = await c.get("/api/memory/shared/greeting/history")
    assert resp.status_code == 503


async def test_list_all_facts_wired(tmp_path):
    client = await _make_client(tmp_path, wired=True)
    async with client as c:
        resp = await c.get("/api/memory")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["facts"][0]["key"] == "greeting"


async def test_list_agent_facts_wired(tmp_path):
    client = await _make_client(tmp_path, wired=True)
    async with client as c:
        resp = await c.get("/api/memory/shared")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "shared"
    assert data["total"] == 1


async def test_get_key_history_wired(tmp_path):
    client = await _make_client(tmp_path, wired=True)
    async with client as c:
        resp = await c.get("/api/memory/shared/greeting/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["memory_id"] == "mem-1"
    assert data["versions"] == 1


async def test_get_key_history_unknown_key_404s(tmp_path):
    client = await _make_client(tmp_path, wired=True)
    async with client as c:
        resp = await c.get("/api/memory/shared/does-not-exist/history")
    assert resp.status_code == 404
