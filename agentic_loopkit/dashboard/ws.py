"""
agentic_loopkit/dashboard/ws.py — WebSocket live-tail endpoint.

Endpoint: WS /ws/tail

Query params:
    stream      subscribe to a specific stream (default: all via WILDCARD_STREAM)
    event_type  filter to a specific event_type string (default: no filter)

Protocol:
    Server → client: one JSON-serialised Event per message
    Client → server: heartbeat ping only; no inbound commands

On disconnect the server unsubscribes from the router immediately.
Events are queued with a maxsize of 500; on backpressure the newest
event is dropped (client can reconnect to resume).
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ..bus import EventBus
from ..events.models import WILDCARD_STREAM
from .dependencies import get_bus

log = logging.getLogger("agentic_loopkit.dashboard.ws")
router = APIRouter()


@router.websocket("/ws/tail")
async def live_tail(
    websocket:  WebSocket,
    stream:     str = WILDCARD_STREAM,
    event_type: str = "",
    bus:        EventBus = Depends(get_bus),
) -> None:
    """
    Stream events live to a connected WebSocket client.

    Each event is serialised as JSON and sent as a text frame.
    The client may optionally pass stream / event_type query params to filter.
    """
    await websocket.accept()

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    async def enqueue(event) -> None:
        if event_type and str(event.event_type) != event_type:
            return
        try:
            queue.put_nowait(event.to_dict())
        except asyncio.QueueFull:
            log.debug("[ws/tail] queue full — dropping event %s", event.event_type)

    bus.router.subscribe(stream, enqueue)
    log.debug("[ws/tail] client connected — stream=%s event_type=%r", stream, event_type)

    try:
        while True:
            item = await queue.get()
            await websocket.send_text(json.dumps(item, default=str))
    except WebSocketDisconnect:
        log.debug("[ws/tail] client disconnected")
    except Exception as exc:
        log.error("[ws/tail] unexpected error: %s", exc, exc_info=True)
    finally:
        bus.router.unsubscribe(stream, enqueue)
        log.debug("[ws/tail] unsubscribed — stream=%s", stream)
