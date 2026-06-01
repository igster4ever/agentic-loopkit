"""
agentic_loopkit/dashboard/routes/memory.py

GET /api/memory                          — list all facts (across all agents)
GET /api/memory/{agent_id}               — list facts for one agent
GET /api/memory/{agent_id}/{key}/history — operation history for a specific key

Requires patch_bus() from agentic-memorykit to have been called at startup.
Routes return 503 if the memory store is not wired.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..constants import DEFAULT_LIMIT, MAX_LIMIT
from ..dependencies import get_bus
from ...bus import EventBus

log = logging.getLogger("agentic_loopkit.dashboard.routes.memory")
router = APIRouter()

_DEFAULT_LIMIT = DEFAULT_LIMIT
_MAX_LIMIT     = MAX_LIMIT


def _get_store(bus: EventBus):
    try:
        return bus.memory
    except AttributeError:
        raise HTTPException(
            status_code=503,
            detail=(
                "Memory store not wired — call patch_bus() from agentic-memorykit "
                "at startup before using /api/memory routes."
            ),
        )


@router.get("/memory")
async def list_all_facts(
    tag:            Optional[str] = Query(None, description="Filter to facts carrying this tag"),
    min_confidence: float         = Query(0.0, ge=0.0, le=1.0),
    limit:          int           = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    bus: EventBus = Depends(get_bus),
) -> dict:
    """
    Return facts across all agents.

    Response:
        {
          "facts": [ ...MemoryRecord dicts... ],
          "total": 42,
          "limit": 100
        }
    """
    store = _get_store(bus)
    tags = [tag] if tag else []
    records = await store.list(agent_id=None, tags=tags, min_confidence=min_confidence)
    paged = records[:limit]
    return {
        "facts": [r.to_dict() for r in paged],
        "total": len(records),
        "limit": limit,
    }


@router.get("/memory/{agent_id}")
async def list_agent_facts(
    agent_id:       str,
    tag:            Optional[str] = Query(None, description="Filter to facts carrying this tag"),
    min_confidence: float         = Query(0.0, ge=0.0, le=1.0),
    limit:          int           = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    bus: EventBus = Depends(get_bus),
) -> dict:
    """
    Return facts for a specific agent.

    Response:
        {
          "agent_id": "shared",
          "facts":    [ ...MemoryRecord dicts... ],
          "total":    15
        }
    """
    store = _get_store(bus)
    tags = [tag] if tag else []
    records = await store.list(agent_id=agent_id, tags=tags, min_confidence=min_confidence)
    paged = records[:limit]
    return {
        "agent_id": agent_id,
        "facts":    [r.to_dict() for r in paged],
        "total":    len(records),
    }


@router.get("/memory/{agent_id}/{key}/history")
async def get_key_history(
    agent_id: str,
    key:      str,
    bus: EventBus = Depends(get_bus),
) -> dict:
    """
    Return the full operation history for a specific fact key.

    404 if the key has never existed for this agent.

    Response:
        {
          "agent_id":  "shared",
          "key":       "user_language",
          "memory_id": "abc123",
          "history":   [ ...MemoryOperation dicts... ],
          "versions":  3
        }
    """
    store = _get_store(bus)
    record = await store.get(key=key, agent_id=agent_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No fact '{key}' found for agent '{agent_id}'.",
        )
    ops = await store.history(record.memory_id)
    return {
        "agent_id":  agent_id,
        "key":       key,
        "memory_id": record.memory_id,
        "history":   [op.to_dict() for op in ops],
        "versions":  len(ops),
    }
