"""
agentic_loopkit/dashboard/routes/chains.py

GET /api/chains/{correlation_id}
    Full causation graph for a workflow — all events sharing the correlation_id,
    plus the edges needed to render the event chain DAG.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..constants import DEFAULT_HOURS
from ..dependencies import get_bus
from ...bus import EventBus
from ...events.models import WILDCARD_STREAM, Event
from ...events.store import load_events

log = logging.getLogger("agentic_loopkit.dashboard.routes.chains")
router = APIRouter()

_DEFAULT_HOURS = DEFAULT_HOURS


@router.get("/chains/{correlation_id}")
async def get_chain(
    correlation_id: str,
    bus: EventBus = Depends(get_bus),
) -> dict:
    """
    Return the full event chain for a correlation_id.

    Response:
        {
          "correlation_id": "a2f8e7b1-...",
          "events":  [ ...full Event dicts, timestamp order... ],
          "edges":   [ {"from": "cause_event_id", "to": "effect_event_id"} ],
          "summary": {
            "stream_count": 3,
            "agent_count":  2,
            "ralf_loops":   1,
            "ooda_events":  5,
            "error_count":  0,
            "duration_ms":  12000,
            "status":       "completed"
          }
        }
    """
    events = load_events(
        stream         = WILDCARD_STREAM,
        store_dir      = bus.store_dir,
        hours          = _DEFAULT_HOURS,
        correlation_id = correlation_id,
    )

    if not events:
        raise HTTPException(
            status_code=404,
            detail=f"No events found for correlation_id {correlation_id!r}",
        )

    # Sort oldest-first for DAG layout
    ordered = sorted(events, key=lambda e: e.timestamp)

    edges   = _build_edges(ordered)
    summary = _build_summary(ordered)

    return {
        "correlation_id": correlation_id,
        "events":         [e.to_dict() for e in ordered],
        "edges":          edges,
        "summary":        summary,
    }


# ── Chain analysis ─────────────────────────────────────────────────────────────


def _build_edges(events: list[Event]) -> list[dict]:
    """
    Derive causation edges from event.causation_id links.

    Each edge goes from the *cause* (causation_id) to the *effect* (event_id).
    Only edges where the cause event is also in the chain are included;
    orphaned causation_ids (pointing outside the chain) are silently skipped.
    """
    ids_in_chain = {e.event_id for e in events}
    edges: list[dict] = []

    for event in events:
        if event.causation_id and event.causation_id in ids_in_chain:
            edges.append({"from": event.causation_id, "to": event.event_id})

    return edges


def _build_summary(events: list[Event]) -> dict[str, Any]:
    """
    Derive summary statistics for a chain of events.

    Loop type counts come from payload["_meta"]["loop_type"] where present.
    Status is derived from presence/absence of error/rejected events and
    recency of the most recent event.
    """
    if not events:
        return {
            "stream_count": 0, "agent_count": 0, "ralf_loops": 0,
            "ooda_events": 0,  "error_count": 0, "duration_ms": 0,
            "status": "completed",
        }

    streams   = {e.stream for e in events}
    sources   = {e.source for e in events}
    error_count = sum(
        1 for e in events
        if (e.meta() or {}).get("status") in ("error", "rejected")
    )

    ralf_loops  = 0
    ooda_events = 0
    for e in events:
        meta = e.meta() or {}
        lt   = meta.get("loop_type", "")
        if lt in ("ralf", "reflexion"):
            ralf_loops  += 1
        elif lt == "ooda":
            ooda_events += 1

    # Duration
    first_ts = events[0].timestamp
    last_ts  = events[-1].timestamp
    if first_ts.tzinfo is None:
        first_ts = first_ts.replace(tzinfo=timezone.utc)
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    duration_ms = int((last_ts - first_ts).total_seconds() * 1000)

    # Status derivation
    now = datetime.now(tz=timezone.utc)
    if error_count > 0:
        status = "error"
    elif (now - last_ts) < timedelta(seconds=60):
        status = "in_progress"
    else:
        status = "completed"

    return {
        "stream_count": len(streams),
        "agent_count":  len(sources),
        "ralf_loops":   ralf_loops,
        "ooda_events":  ooda_events,
        "error_count":  error_count,
        "duration_ms":  duration_ms,
        "status":       status,
    }
