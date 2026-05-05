"""
agentic_loopkit/dashboard/routes/streams.py

GET /api/streams
    List stream names present in the JSONL store, with event counts and
    timestamp of the most recent event.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends

from ..dependencies import get_bus
from ...bus import EventBus

log = logging.getLogger("agentic_loopkit.dashboard.routes.streams")
router = APIRouter()


@router.get("/streams")
async def list_streams(bus: EventBus = Depends(get_bus)) -> list[dict]:
    """
    Return all event streams present in the store.

    Response:
        [
          {
            "stream":        "clickup",
            "event_count":   142,
            "last_event_at": "2026-05-02T10:15:44Z"
          },
          ...
        ]
    """
    store_dir: Path = bus.store_dir
    result: list[dict] = []

    for path in sorted(store_dir.glob("events-*.jsonl")):
        stream_name = path.stem.removeprefix("events-")
        count       = 0
        last_ts     = None

        try:
            with path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        count += 1
                        ts = d.get("timestamp")
                        if ts and (last_ts is None or ts > last_ts):
                            last_ts = ts
                    except json.JSONDecodeError:
                        pass
        except OSError as exc:
            log.warning("[streams] could not read %s: %s", path.name, exc)
            continue

        result.append({
            "stream":        stream_name,
            "event_count":   count,
            "last_event_at": last_ts,
        })

    return result
