"""
agentic_loopkit/dashboard/routes/adapters.py

GET /api/adapters
    List registered adapters with their name, type, and cursor state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_bus
from ...bus import EventBus

router = APIRouter()


@router.get("/adapters")
async def list_adapters(bus: EventBus = Depends(get_bus)) -> list[dict]:
    """
    Return registered adapters and their current cursor state.

    Response:
        [
          {
            "name":        "clickup",
            "type":        "ClickUpAdapter",
            "cursor":      "1746180942000",
            "last_tick":   null,
            "error_count": 0
          }
        ]

    Note: last_tick is not currently tracked by PollingAdapter.  It will
    remain null until tracked in a future release.
    """
    return [
        {
            "name":        adapter.name,
            "type":        type(adapter).__name__,
            "cursor":      _serialise_cursor(adapter._cursor),
            "last_tick":   None,   # not tracked in current PollingAdapter
            "error_count": 0,      # not tracked in current PollingAdapter
        }
        for adapter in bus._adapters
    ]


def _serialise_cursor(cursor) -> object:
    """Return cursor in a JSON-safe form."""
    if cursor is None:
        return None
    if isinstance(cursor, (str, int, float, bool)):
        return cursor
    if isinstance(cursor, dict):
        return cursor
    return str(cursor)
