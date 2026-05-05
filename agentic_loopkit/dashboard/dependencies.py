"""
agentic_loopkit/dashboard/dependencies.py — FastAPI dependency helpers.
"""

from __future__ import annotations

from fastapi import Request

from ..bus import EventBus


def get_bus(request: Request) -> EventBus:
    """FastAPI dependency: extract the EventBus from app state."""
    return request.app.state.bus
