"""
agentic_loopkit/adapters/base.py — PollingAdapter base class.

Adapters bridge external systems (ClickUp, Slack, git, etc.) into the event
bus.  They are NOT agents — they don't reason or call LLMs.  Their only job is:

    poll external system → deduplicate → emit typed events

Cursor management:
  Each adapter maintains a last-seen cursor (stored in the event store) so
  repeat polls don't re-emit stale events.  The cursor is source-specific —
  a timestamp, a page token, or a sequence number depending on the API.

Scheduling:
  Adapters are tick-driven — call tick() from APScheduler, asyncio, or any
  scheduler.  The base class does NOT own a scheduler; the consuming app does.

Example:

    class ClickUpAdapter(PollingAdapter):
        name = "clickup"

        async def poll(self, cursor):
            tasks = await clickup_api.get_updated_since(cursor)
            events = []
            for task in tasks:
                events.append(Event(
                    event_type     = "ticket.updated",
                    source         = self.name,
                    payload        = task,
                    correlation_id = task["id"],
                ))
            return events, new_cursor

    adapter = ClickUpAdapter(bus=bus)
    await adapter.tick()
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from ..events.models import Event, SystemEventType
from ..utils.time import iso_format

if TYPE_CHECKING:
    from ..bus import EventBus

log = logging.getLogger("agentic_loopkit.adapter")


class PollingAdapter(ABC):
    """
    Base class for polling-based source adapters.

    Subclass and implement poll().  Call tick() on a schedule.
    """

    #: Override in subclass — used as the adapter identifier in logs + cursor key
    name: str = "adapter"

    #: Consecutive failures before a ``system.adapter_stalled`` event is emitted.
    stall_threshold: int = 3

    def __init__(self, bus: "EventBus") -> None:
        self._bus = bus
        self._cursor: Optional[Any] = self._load_cursor()
        self._consecutive_failures: int = 0
        self._last_tick_at:    Optional[datetime] = None
        self._last_success_at: Optional[datetime] = None

    # ── Contract ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def poll(self, cursor: Optional[Any]) -> tuple[list[Event], Optional[Any]]:
        """
        Fetch new events from the external system since cursor.

        Returns:
            events  — list of new Event objects to emit (may be empty)
            cursor  — updated cursor to persist (None = unchanged)

        The cursor is opaque — use whatever the source API provides:
        a timestamp, a page token, a sequence number, a set of seen IDs.
        """
        ...

    # ── Tick ──────────────────────────────────────────────────────────────────

    async def tick(self) -> int:
        """
        Run one poll cycle.  Returns the number of new events emitted.

        Skips immediately if the bus is stopping.  Registers with the bus so
        ``stop()`` can drain in-flight ticks before halting.  Emits
        ``system.adapter_alive`` on success and ``system.adapter_stalled`` when
        consecutive failures reach ``stall_threshold``.
        """
        if self._bus.is_stopping:
            return 0

        self._bus._register_tick()
        now = datetime.now(tz=timezone.utc)
        self._last_tick_at = now

        try:
            try:
                events, new_cursor = await self.poll(self._cursor)
            except Exception as exc:
                log.error("[%s] poll error: %s", self.name, exc, exc_info=True)
                self._consecutive_failures += 1
                await self._bus.publish(Event(
                    event_type = SystemEventType.ADAPTER_ERROR,
                    source     = self.name,
                    payload    = {"error": str(exc), "adapter": self.name},
                ))
                if self._consecutive_failures >= self.stall_threshold:
                    await self._bus.publish(Event(
                        event_type = SystemEventType.ADAPTER_STALLED,
                        source     = self.name,
                        payload    = {
                            "adapter":              self.name,
                            "consecutive_failures": self._consecutive_failures,
                        },
                    ))
                return 0

            for event in events:
                await self._bus.publish(event)

            if new_cursor is not None:
                self._cursor = new_cursor
                self._save_cursor(new_cursor)

            self._consecutive_failures = 0
            self._last_success_at = now

            await self._bus.publish(Event(
                event_type = SystemEventType.ADAPTER_ALIVE,
                source     = self.name,
                payload    = {"adapter": self.name, "events_emitted": len(events)},
            ))

            if events:
                log.info("[%s] emitted %d event(s)", self.name, len(events))
            else:
                log.debug("[%s] tick — no new events", self.name)

            return len(events)

        finally:
            self._bus._release_tick()

    # ── Cursor persistence ─────────────────────────────────────────────────────

    def _cursor_path(self) -> Path:
        return self._bus.store_dir / f"cursor-{self.name}.json"

    def _load_cursor(self) -> Optional[Any]:
        path = self._cursor_path()
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception as exc:
                log.warning("[%s] could not load cursor: %s", self.name, exc)
        return None

    def _save_cursor(self, cursor: Any) -> None:
        path = self._cursor_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(cursor))
        except Exception as exc:
            log.warning("[%s] could not save cursor: %s", self.name, exc)

    def cursor_state(self) -> Any:
        """
        Return the current cursor value in a form safe to expose externally.

        Default: returns ``self._cursor`` as-is.  Override in subclasses to
        redact credential-adjacent content (e.g. tokens embedded in cursor dicts).
        """
        return self._cursor

    def liveness_state(self) -> dict:
        """Snapshot of this adapter's health — safe to expose via the dashboard."""
        return {
            "name":                 self.name,
            "alive":                self._consecutive_failures == 0,
            "consecutive_failures": self._consecutive_failures,
            "last_tick_at":         iso_format(self._last_tick_at) if self._last_tick_at else None,
            "last_success_at":      iso_format(self._last_success_at) if self._last_success_at else None,
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
