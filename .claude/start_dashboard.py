import asyncio
import uvicorn
from pathlib import Path


async def main():
    from agentic_loopkit import EventBus
    from agentic_loopkit.dashboard import create_app

    store = Path.home() / ".cache" / "squad-gps-radar" / "events"
    bus = EventBus(store_dir=store)
    await bus.start()

    app = create_app(bus)
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


asyncio.run(main())
