"""
agentic_loopkit/events/router.py — async callback fanout router.

Subscribers are Callable[[Event], Awaitable[None]] — zero coupling to any
transport layer.  The bus passes ws.send_json as a lambda; adapters pass
their own handlers.  This module never imports FastAPI, WebSocket, or any
framework.

Each handler is wrapped in try/except so one failing subscriber cannot
silently swallow events for all subsequent subscribers.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Awaitable, Callable

from .models import Event, WILDCARD_STREAM

log = logging.getLogger("agentic_loopkit.router")

Subscriber = Callable[[Event], Awaitable[None]]


class EventRouter:
    """Fan-out router: publish one Event → call all matching subscribers."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Subscriber]] = defaultdict(list)

    # ── Subscription management ────────────────────────────────────────────────

    def subscribe(self, stream: str, fn: Subscriber) -> None:
        """Register fn for stream.  Use WILDCARD_STREAM ('*') for all events."""
        if fn not in self._subs[stream]:
            self._subs[stream].append(fn)
            log.debug("[router] +sub %s → %s", stream, _fname(fn))

    def unsubscribe(self, stream: str, fn: Subscriber) -> None:
        try:
            self._subs[stream].remove(fn)
        except ValueError:
            pass

    def subscriber_count(self, stream: str) -> int:
        return len(self._subs.get(stream, []))

    # ── Publishing ─────────────────────────────────────────────────────────────

    async def publish(self, event: Event) -> None:
        """
        Dispatch to all subscribers on event.stream and on WILDCARD_STREAM.
        Sequential delivery; handler exceptions are logged, not re-raised.
        """
        targets = (
            list(self._subs.get(event.stream, []))
            + list(self._subs.get(WILDCARD_STREAM, []))
        )
        if not targets:
            log.debug("[router] no subscribers — stream=%s type=%s", event.stream, event.event_type)
            return

        for fn in targets:
            try:
                await fn(event)
            except Exception as exc:
                log.error(
                    "[router] subscriber %s raised on %s: %s",
                    _fname(fn), event.event_type, exc,
                    exc_info=True,
                )

    async def publish_many(self, events: list[Event]) -> None:
        for event in events:
            await self.publish(event)

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def streams(self) -> list[str]:
        return [s for s, fns in self._subs.items() if fns]

    def __repr__(self) -> str:
        summary = ", ".join(f"{s}={len(fns)}" for s, fns in self._subs.items() if fns)
        return f"EventRouter({summary or 'no subscribers'})"


def _fname(fn: Subscriber) -> str:
    return getattr(fn, "__name__", repr(fn))
