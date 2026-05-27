"""
agentic_loopkit/dashboard/routes/events.py

GET /api/events                    — filtered event list
GET /api/events/{event_id}         — single event + related events in same chain
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..constants import DEFAULT_HOURS, DEFAULT_LIMIT, MAX_LIMIT
from ..dependencies import get_bus
from ...bus import EventBus
from ...events.models import WILDCARD_STREAM
from ...events.store import load_events
from ...utils.time import iso_format

log = logging.getLogger("agentic_loopkit.dashboard.routes.events")
router = APIRouter()

_DEFAULT_LIMIT = DEFAULT_LIMIT
_MAX_LIMIT     = MAX_LIMIT
_DEFAULT_HOURS = DEFAULT_HOURS


@router.get("/events")
async def list_events(
    stream:         Optional[str] = Query(None),
    event_type:     Optional[str] = Query(None),
    correlation_id: Optional[str] = Query(None),
    source:         Optional[str] = Query(None),
    since:          Optional[str] = Query(None, description="ISO 8601 UTC — e.g. 2026-05-01T00:00:00Z"),
    limit:          int           = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    bus: EventBus = Depends(get_bus),
) -> dict:
    """
    Return a filtered list of events from the JSONL store.

    All query parameters are optional.  Results are newest-first up to limit.

    Response:
        {
          "events": [ ...Event dicts... ],
          "total":  87,
          "limit":  100
        }
    """
    target_stream = stream or WILDCARD_STREAM

    events = load_events(
        stream         = target_stream,
        store_dir      = bus.store_dir,
        hours          = _DEFAULT_HOURS,
        event_type     = event_type,
        correlation_id = correlation_id,
    )

    # Additional filters not handled by load_events
    if source:
        events = [e for e in events if e.source == source]
    if since:
        events = [e for e in events if iso_format(e.timestamp) >= since]

    total  = len(events)
    paged  = events[:limit]

    return {
        "events": [e.to_dict() for e in paged],
        "total":  total,
        "limit":  limit,
    }


@router.get("/events/{event_id}")
async def get_event(
    event_id: str,
    bus: EventBus = Depends(get_bus),
) -> dict:
    """
    Return a single event by event_id, plus related events in the same
    correlation chain (lightweight — just type, id, timestamp).

    Response:
        {
          "event":   { ...full Event dict... },
          "related": [
            {"event_id": "...", "event_type": "...", "timestamp": "..."},
            ...
          ]
        }
    """
    # Targeted ID lookup — short-circuits on first match rather than full scan
    matches = load_events(
        WILDCARD_STREAM, store_dir=bus.store_dir,
        hours=_DEFAULT_HOURS, event_id=event_id,
    )
    if not matches:
        raise HTTPException(status_code=404, detail=f"Event {event_id!r} not found")
    target = matches[0]

    related = []
    if target.correlation_id:
        chain_events = load_events(
            WILDCARD_STREAM, store_dir=bus.store_dir,
            hours=_DEFAULT_HOURS, correlation_id=target.correlation_id,
        )
        related = [
            {
                "event_id":   e.event_id,
                "event_type": str(e.event_type),
                "timestamp":  iso_format(e.timestamp),
            }
            for e in chain_events
            if e.event_id != event_id
        ]

    return {"event": target.to_dict(), "related": related}


# ── Helpers ────────────────────────────────────────────────────────────────────
