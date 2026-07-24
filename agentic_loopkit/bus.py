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

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .events.models import Event, SystemEventType
from .events.router import EventRouter
from .events.store import append_event, compact_stream
from .events.headlines import EventHeadline, append_headline, load_headlines, expand_event

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

    def __init__(
        self,
        store_dir: Optional[Path] = None,
        backpressure_threshold: int = 100,
        drain_timeout: float = 5.0,
        compaction_interval_hours: Optional[float] = None,
        compaction_retention_hours: Optional[int] = None,
    ) -> None:
        self.store_dir: Path = (store_dir or _DEFAULT_STORE).expanduser()
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.router  = EventRouter()
        self._agents: list[AgentBase]       = []
        self._adapters: list[PollingAdapter] = []
        self._started = False
        self.backpressure_threshold = backpressure_threshold
        self.drain_timeout          = drain_timeout
        self._event_counter         = 0   # non-system events since last pressure signal
        self._stopping              = False
        self._active_ticks          = 0   # ticks currently in-flight

        # Compaction: opt-in periodic background task (None = disabled — the
        # historical default; compact_stream() otherwise has no caller outside
        # tests, so lifetime event count grows unboundedly for long-running
        # deployments). compaction_retention_hours=None defers to compact_stream()'s
        # own default retention window.
        self.compaction_interval_hours  = compaction_interval_hours
        self.compaction_retention_hours = compaction_retention_hours
        self._compaction_task: Optional[asyncio.Task] = None

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

        Backpressure: every ``backpressure_threshold`` non-system events a
        ``system.bus_pressure`` event is emitted.  System events are excluded
        from the count so the pressure signal cannot trigger itself.
        """
        append_event(event, store_dir=self.store_dir)
        append_headline(event, store_dir=self.store_dir)
        await self.router.publish(event)
        if event.stream != "system":
            self._event_counter += 1
            if self._event_counter >= self.backpressure_threshold:
                self._event_counter = 0
                await self.publish(Event(
                    event_type = SystemEventType.BUS_PRESSURE,
                    source     = "bus",
                    payload    = {"threshold": self.backpressure_threshold},
                ))

    async def publish_many(self, events: list[Event]) -> None:
        for event in events:
            await self.publish(event)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Emit BUS_STARTED.  Call from application lifespan."""
        self._started = True
        if self.compaction_interval_hours:
            self._compaction_task = asyncio.create_task(self._compaction_loop())
        await self.publish(Event(
            event_type = SystemEventType.BUS_STARTED,
            source     = "bus",
            payload    = {
                "agents":   [a.name for a in self._agents],
                "adapters": [a.name for a in self._adapters],
            },
        ))
        log.info("[bus] started — %d agent(s), %d adapter(s)", len(self._agents), len(self._adapters))

    async def stop(self, drain_timeout: Optional[float] = None) -> None:
        """
        Drain in-flight adapter ticks, emit BUS_STOPPED, unsubscribe all agents.

        Sets ``is_stopping`` immediately so new ticks skip themselves.  Waits up
        to ``drain_timeout`` seconds for any already-running ticks to complete
        before forcing shutdown.  Call from lifespan teardown.
        """
        self._stopping = True
        if self._compaction_task is not None:
            self._compaction_task.cancel()
            try:
                await self._compaction_task
            except asyncio.CancelledError:
                pass
            self._compaction_task = None
        timeout = drain_timeout if drain_timeout is not None else self.drain_timeout
        waited, step = 0.0, 0.05
        while self._active_ticks > 0 and waited < timeout:
            await asyncio.sleep(step)
            waited += step
        if self._active_ticks > 0:
            log.warning(
                "[bus] drain timeout — %d tick(s) still active after %.1fs",
                self._active_ticks, timeout,
            )
        await self.publish(Event(
            event_type = SystemEventType.BUS_STOPPED,
            source     = "bus",
            payload    = {},
        ))
        for agent in self._agents:
            agent.unsubscribe_all()
        self._started = False
        log.info("[bus] stopped")

    # ── Public read-only accessors ────────────────────────────────────────────

    @property
    def agents(self) -> list["AgentBase"]:
        """Registered agents (read-only snapshot)."""
        return list(self._agents)

    @property
    def adapters(self) -> list["PollingAdapter"]:
        """Registered adapters (read-only snapshot)."""
        return list(self._adapters)

    # ── Headline access (LCLM-inspired corpus skimming) ──────────────────────

    def load_headlines(self, stream: str, limit: int = 50) -> list[EventHeadline]:
        """Return the most recent ``limit`` headlines for the stream, newest first."""
        return load_headlines(stream, store_dir=self.store_dir, limit=limit)

    def expand_event(self, chunk_id: int, stream: str) -> "Event | None":
        """Resolve a chunk_id to the full ``Event``.  Returns None if not found."""
        return expand_event(chunk_id, stream, store_dir=self.store_dir)

    # ── Compaction (opt-in periodic maintenance) ──────────────────────────────

    async def _compaction_loop(self) -> None:
        """Background task: compact every stream file every ``compaction_interval_hours``.

        Runs until cancelled by ``stop()``.  A plain ``asyncio.create_task()``
        loop is sufficient here — no external scheduler/cron dependency needed,
        since the bus already owns an event loop for its entire lifetime.
        """
        interval_seconds = self.compaction_interval_hours * 3600
        while True:
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                return
            self.compact_all_streams()

    def compact_all_streams(self) -> dict[str, int]:
        """
        Compact every ``events-*.jsonl`` file in ``store_dir``.

        Returns ``{stream: removed_count}`` for streams with at least one
        event removed.  Safe to call directly (e.g. from a management CLI
        or an ad-hoc maintenance task) — it does not require the background
        loop to be running.
        """
        kwargs: dict = {}
        if self.compaction_retention_hours is not None:
            kwargs["hours"] = self.compaction_retention_hours

        removed_by_stream: dict[str, int] = {}
        for path in sorted(self.store_dir.glob("events-*.jsonl")):
            stream = path.stem[len("events-"):]
            removed = compact_stream(stream, store_dir=self.store_dir, **kwargs)
            if removed:
                removed_by_stream[stream] = removed
        return removed_by_stream

    # ── Tick registration (called by PollingAdapter) ──────────────────────────

    def _register_tick(self) -> None:
        """Signal that an adapter tick has started."""
        self._active_ticks += 1

    def _release_tick(self) -> None:
        """Signal that an adapter tick has completed (success or error)."""
        self._active_ticks -= 1

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def is_stopping(self) -> bool:
        return self._stopping

    def adapter_states(self) -> list[dict]:
        """Liveness snapshot for all registered adapters."""
        return [a.liveness_state() for a in self._adapters]

    def status(self) -> dict:
        return {
            "started":   self._started,
            "stopping":  self._stopping,
            "agents":    [repr(a) for a in self._agents],
            "adapters":  [repr(a) for a in self._adapters],
            "streams":   self.router.streams(),
            "store_dir": str(self.store_dir),
        }

    def __repr__(self) -> str:
        return f"EventBus(started={self._started}, agents={len(self._agents)}, adapters={len(self._adapters)})"
