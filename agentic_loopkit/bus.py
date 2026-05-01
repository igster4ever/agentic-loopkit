"""
agentic_loopkit/bus.py — EventBus coordinator.

The bus owns the router, store directory, and registered agents/adapters.
It is the single entry point for publishing events and the dependency
injected into all agents and executors.

Usage (e.g. in a FastAPI lifespan or asyncio main):

    bus = EventBus(store_dir=Path("~/.cache/my-app").expanduser())

    # Register agents
    bus.register(DiagramAgent("diagram-agent", bus))
    bus.register(AdrAgent("adr-agent", bus))

    # Register adapters (tick them on a schedule externally)
    bus.add_adapter(ClickUpAdapter(bus))

    await bus.start()

    # Publish events
    await bus.publish(Event(event_type="ticket.updated", source="clickup", payload={...}))

    await bus.stop()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .events.models import Event, SystemEventType
from .events.router import EventRouter
from .events.store import append_event

if TYPE_CHECKING:
    from .agents.base import AgentBase
    from .adapters.base import PollingAdapter

log = logging.getLogger("agentic_loopkit.bus")

_DEFAULT_STORE = Path("~/.cache/agentic-loopkit").expanduser()


class EventBus:
    """
    Coordinator: owns the event router, store path, and agent/adapter registry.

    Dependency-injected into every AgentBase and RALFExecutor so they can
    publish events without importing store or router directly.
    """

    def __init__(self, store_dir: Optional[Path] = None) -> None:
        self.store_dir: Path = (store_dir or _DEFAULT_STORE).expanduser()
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.router  = EventRouter()
        self._agents: list[AgentBase]       = []
        self._adapters: list[PollingAdapter] = []
        self._started = False

    # ── Registration ───────────────────────────────────────────────────────────

    def register(self, agent: "AgentBase") -> None:
        """Register an OODA agent with the bus."""
        self._agents.append(agent)
        log.debug("[bus] registered agent %s", agent.name)

    def add_adapter(self, adapter: "PollingAdapter") -> None:
        """Register a polling adapter.  Ticking is the caller's responsibility."""
        self._adapters.append(adapter)
        log.debug("[bus] registered adapter %s", adapter.name)

    # ── Publishing ─────────────────────────────────────────────────────────────

    async def publish(self, event: Event) -> None:
        """
        Persist then fan-out.

        Writing to the JSONL store before router fanout means a restart can
        replay missed events from disk — the bus never loses an event silently.
        """
        append_event(event, store_dir=self.store_dir)
        await self.router.publish(event)

    async def publish_many(self, events: list[Event]) -> None:
        for event in events:
            await self.publish(event)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Emit BUS_STARTED.  Call from application lifespan."""
        self._started = True
        await self.publish(Event(
            event_type = SystemEventType.BUS_STARTED,
            source     = "bus",
            payload    = {
                "agents":   [a.name for a in self._agents],
                "adapters": [a.name for a in self._adapters],
            },
        ))
        log.info("[bus] started — %d agent(s), %d adapter(s)", len(self._agents), len(self._adapters))

    async def stop(self) -> None:
        """Emit BUS_STOPPED and unsubscribe all agents.  Call from lifespan teardown."""
        await self.publish(Event(
            event_type = SystemEventType.BUS_STOPPED,
            source     = "bus",
            payload    = {},
        ))
        for agent in self._agents:
            agent.unsubscribe_all()
        self._started = False
        log.info("[bus] stopped")

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._started

    def status(self) -> dict:
        return {
            "started":  self._started,
            "agents":   [repr(a) for a in self._agents],
            "adapters": [repr(a) for a in self._adapters],
            "streams":  self.router.streams(),
            "store_dir": str(self.store_dir),
        }

    def __repr__(self) -> str:
        return f"EventBus(started={self._started}, agents={len(self._agents)}, adapters={len(self._adapters)})"
