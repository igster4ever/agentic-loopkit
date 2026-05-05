"""
agentic_loopkit/dashboard — Optional FastAPI management API for agentic-loopkit.

Install with:
    pip install agentic-loopkit[dashboard]

Usage:
    from agentic_loopkit.dashboard import create_app
    dashboard = create_app(bus)              # bound to a running EventBus

    # Mount inside an existing FastAPI app:
    app.mount("/dashboard", dashboard)

    # Or run standalone:
    import uvicorn
    uvicorn.run(create_app(bus), host="0.0.0.0", port=8765)
"""

from .app import create_app

__all__ = ["create_app"]
