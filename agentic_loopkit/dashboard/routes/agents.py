"""
agentic_loopkit/dashboard/routes/agents.py

GET /api/agents
    List registered agents and their subscribed streams.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_bus
from ...bus import EventBus

router = APIRouter()


@router.get("/agents")
async def list_agents(bus: EventBus = Depends(get_bus)) -> list[dict]:
    """
    Return registered agents and their subscription streams.

    Response:
        [
          {
            "name":    "AnalysisAgent",
            "type":    "OODA",
            "streams": ["clickup", "analysis"]
          }
        ]
    """
    return [
        {
            "name":    agent.name,
            "type":    "OODA",
            "streams": agent.subscriptions,
        }
        for agent in bus.agents
    ]
