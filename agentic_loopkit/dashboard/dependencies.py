"""
agentic_loopkit/dashboard/dependencies.py — FastAPI dependency helpers.
"""

from __future__ import annotations

from starlette.requests import HTTPConnection

from ..bus import EventBus


def get_bus(conn: HTTPConnection) -> EventBus:
    """
    FastAPI dependency: extract the EventBus from app state.

    Accepts ``HTTPConnection`` (the common base of ``Request`` and
    ``WebSocket``) so the same dependency works for both HTTP routes
    and WebSocket endpoints without duplication.
    """
    return conn.app.state.bus
