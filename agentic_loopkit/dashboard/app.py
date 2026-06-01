"""
agentic_loopkit/dashboard/app.py — FastAPI app factory.

Creates and configures the dashboard FastAPI application, bound to a running
EventBus.  Mounts all route groups and the WebSocket live-tail endpoint.
Optionally serves the compiled React frontend if dashboard/ui/dist/ exists.

Usage:

    from agentic_loopkit.dashboard import create_app

    # Inside a FastAPI lifespan, after bus.start():
    dashboard = create_app(bus)
    app.mount("/dashboard", dashboard)

    # Or standalone:
    import uvicorn
    uvicorn.run(create_app(bus), host="0.0.0.0", port=8765)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .routes import events, chains, streams, agents, adapters, memory
from .ws import router as ws_router
from ..bus import EventBus

_UI_DIST = Path(__file__).parent.parent.parent / "dashboard" / "ui" / "dist"


def create_app(bus: EventBus) -> FastAPI:
    """
    Instantiate the dashboard FastAPI app, bound to bus.

    The EventBus is stored in ``app.state.bus`` so all route handlers can
    access it via the ``get_bus`` dependency.
    """
    dashboard = FastAPI(
        title       = "agentic-loopkit dashboard",
        description = "Event inspector for agentic-loopkit runtimes.",
        version     = "0.1.0",
        docs_url    = "/api/docs",
        redoc_url   = "/api/redoc",
    )
    dashboard.state.bus = bus

    dashboard.include_router(events.router,   prefix="/api")
    dashboard.include_router(chains.router,   prefix="/api")
    dashboard.include_router(streams.router,  prefix="/api")
    dashboard.include_router(agents.router,   prefix="/api")
    dashboard.include_router(adapters.router, prefix="/api")
    dashboard.include_router(memory.router,   prefix="/api")
    dashboard.include_router(ws_router)

    if _UI_DIST.exists():
        from fastapi.staticfiles import StaticFiles  # fastapi optional dep
        dashboard.mount("/", StaticFiles(directory=_UI_DIST, html=True), name="ui")

    return dashboard
