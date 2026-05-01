"""
agentic_loopkit/agents/base.py — AgentBase OODA reactive agent.

Subclass AgentBase and implement orient() and decide().
The observe() hook is a pre-filter — return None to silently ignore an event.
The act() hook emits the decided action, typically publishing new events.

LLM usage:
  orient() is the primary reasoning phase — call your LLM here.
  decide() is secondary — light rules or structured output parsing.
  observe() and act() are deterministic — no LLM calls.

Example:

    class DiagramAgent(AgentBase):
        async def observe(self, event):
            if event.event_type != "ticket.updated": return None
            return {"ticket": event.payload}

        async def orient(self, event, context):
            # call LLM: given ticket context, what diagram is needed?
            return {"diagram_type": "sequence", "confidence": 0.82}

        async def decide(self, event, orientation):
            if orientation["confidence"] < 0.65: return None
            return orientation

        async def act(self, event, action):
            await self._bus.publish(
                event.caused("command.generate_diagram", self.name, action)
            )
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from ..events.models import Event

if TYPE_CHECKING:
    from ..bus import EventBus

log = logging.getLogger("agentic_loopkit.agent")


class AgentBase(ABC):
    """
    OODA reactive agent.

    Agents subscribe to one or more event streams via subscribe().
    On each event the pipeline runs: observe → orient → decide → act.
    Any phase can short-circuit by returning None.
    """

    def __init__(self, name: str, bus: "EventBus") -> None:
        self.name = name
        self._bus = bus
        self._subscriptions: list[str] = []

    def subscribe(self, *streams: str) -> None:
        """Subscribe this agent to one or more event streams."""
        for stream in streams:
            self._bus.router.subscribe(stream, self._handle)
            self._subscriptions.append(stream)
            log.debug("[%s] subscribed to stream=%s", self.name, stream)

    def unsubscribe_all(self) -> None:
        for stream in self._subscriptions:
            self._bus.router.unsubscribe(stream, self._handle)
        self._subscriptions.clear()

    # ── OODA pipeline ──────────────────────────────────────────────────────────

    async def _handle(self, event: Event) -> None:
        """Internal dispatch — runs the full OODA pipeline."""
        try:
            context = await self.observe(event)
            if context is None:
                return

            orientation = await self.orient(event, context)
            if orientation is None:
                return

            action = await self.decide(event, orientation)
            if action is None:
                return

            await self.act(event, action)

        except Exception as exc:
            log.error("[%s] unhandled error on %s: %s", self.name, event.event_type, exc, exc_info=True)

    async def observe(self, event: Event) -> Optional[Any]:
        """
        OBSERVE — filter and gather initial context.
        Return a context dict to continue processing, or None to skip.
        Default: pass all events through with empty context.
        """
        return {}

    @abstractmethod
    async def orient(self, event: Event, context: Any) -> Optional[Any]:
        """
        ORIENT — interpret the event (primary LLM phase).
        Return an orientation object, or None to skip.
        Express uncertainty here: include confidence scores in the return value.
        """
        ...

    @abstractmethod
    async def decide(self, event: Event, orientation: Any) -> Optional[Any]:
        """
        DECIDE — choose an action based on orientation (secondary phase).
        Return an action spec, or None to do nothing.
        Apply confidence thresholds here — reject low-confidence orientations.
        """
        ...

    async def act(self, event: Event, action: Any) -> None:
        """
        ACT — execute the decision.
        Default: emit a new event using event.caused() for full traceability.
        Override to publish events, write state, or call external APIs.
        """
        pass

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, streams={self._subscriptions})"
