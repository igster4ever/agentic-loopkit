"""
tests/dashboard/test_routes_chains.py — GET /api/chains/{correlation_id}
"""

import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from agentic_loopkit.bus import EventBus
from agentic_loopkit.dashboard import create_app
from agentic_loopkit.dashboard.routes.chains import _build_edges, _build_summary
from agentic_loopkit.events.models import Event


def _make_event(event_type="things.a", source="svc", correlation_id="wf-001",
                causation_id=None, payload=None) -> Event:
    return Event(
        event_type=event_type, source=source, payload=payload or {},
        correlation_id=correlation_id, causation_id=causation_id,
    )


# ── _build_edges (pure) ───────────────────────────────────────────────────────

def test_build_edges_no_causation():
    e1 = _make_event(causation_id=None)
    e2 = _make_event(causation_id=None)
    assert _build_edges([e1, e2]) == []


def test_build_edges_simple_chain():
    e1 = _make_event()
    e2 = _make_event(causation_id=e1.event_id)
    edges = _build_edges([e1, e2])
    assert len(edges) == 1
    assert edges[0] == {"from": e1.event_id, "to": e2.event_id}


def test_build_edges_orphaned_causation_id_excluded():
    """causation_id pointing outside the chain is silently dropped."""
    e = _make_event(causation_id="ghost-id-not-in-chain")
    assert _build_edges([e]) == []


def test_build_edges_multi_step_chain():
    e1 = _make_event(event_type="a.start")
    e2 = _make_event(event_type="b.mid",  causation_id=e1.event_id)
    e3 = _make_event(event_type="c.end",  causation_id=e2.event_id)
    edges = _build_edges([e1, e2, e3])
    assert len(edges) == 2


# ── _build_summary (pure) ─────────────────────────────────────────────────────

def test_build_summary_empty():
    s = _build_summary([])
    assert s["status"] == "completed"
    assert s["stream_count"] == 0


def test_build_summary_counts_streams():
    e1 = _make_event(event_type="foo.a")
    e2 = _make_event(event_type="bar.b")
    s = _build_summary([e1, e2])
    assert s["stream_count"] == 2


def test_build_summary_error_status_on_meta_status_error():
    e = _make_event(payload={"_meta": {"status": "error"}})
    s = _build_summary([e])
    assert s["status"] == "error"
    assert s["error_count"] == 1


def test_build_summary_error_status_on_meta_status_rejected():
    e = _make_event(payload={"_meta": {"status": "rejected"}})
    s = _build_summary([e])
    assert s["status"] == "error"
    assert s["error_count"] == 1


def test_build_summary_event_type_with_error_in_name_not_counted():
    """Event type names containing 'error' are not counted — only _meta.status is."""
    e = _make_event(event_type="things.error")
    s = _build_summary([e])
    assert s["error_count"] == 0


def test_build_summary_counts_meta_loop_types():
    e1 = _make_event(payload={"_meta": {"loop_type": "ooda"}})
    e2 = _make_event(payload={"_meta": {"loop_type": "ralf"}})
    e3 = _make_event(payload={"_meta": {"loop_type": "reflexion"}})
    s = _build_summary([e1, e2, e3])
    assert s["ooda_events"] == 1
    assert s["ralf_loops"]  == 2    # ralf + reflexion both count


# ── GET /api/chains/{correlation_id} ─────────────────────────────────────────

async def test_get_chain_returns_events(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    e1 = _make_event(event_type="a.start", correlation_id="wf-001")
    e2 = _make_event(event_type="b.done",  correlation_id="wf-001", causation_id=e1.event_id)
    await bus.publish(e1)
    await bus.publish(e2)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/chains/wf-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["correlation_id"] == "wf-001"
    assert len(body["events"]) == 2


async def test_get_chain_returns_edges(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    e1 = _make_event(event_type="a.start", correlation_id="wf-002")
    e2 = _make_event(event_type="b.done",  correlation_id="wf-002", causation_id=e1.event_id)
    await bus.publish(e1)
    await bus.publish(e2)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/chains/wf-002")
    body = resp.json()
    assert len(body["edges"]) == 1
    assert body["edges"][0]["from"] == e1.event_id
    assert body["edges"][0]["to"]   == e2.event_id


async def test_get_chain_not_found_returns_404(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/chains/no-such-correlation-id")
    assert resp.status_code == 404


async def test_get_chain_summary_present(tmp_path):
    bus = EventBus(store_dir=tmp_path)
    e = _make_event(correlation_id="wf-003")
    await bus.publish(e)
    app = create_app(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/chains/wf-003")
    summary = resp.json()["summary"]
    assert "stream_count" in summary
    assert "status" in summary
