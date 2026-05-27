"""
agentic_loopkit/agents/projection.py — ProjectionAgent base class.

Subscribes to event streams and continuously materialises a structured
document (wiki page, governance summary, status report) from the event log.

The event log is the source of truth.  The materialised document is a
*projection* of it, regenerated whenever a trigger event arrives.  This
distinction matters: pages can evolve automatically, historical versions are
reconstructable from the log, and conflicting interpretations can coexist as
separate projections over the same source events.

Usage::

    class WikiPageAgent(ProjectionAgent):
        async def materialise(self, events):
            # LLM call: synthesise events into a wiki page
            return await my_llm.summarise(events)

    agent = WikiPageAgent("wiki-page", bus, projection_streams=["gps", "adr"])
    agent.subscribe("gps", "adr")
    bus.register(agent)

Every materialisation emits a ``projection.updated`` event carrying the
document content, page-level confidence, and provenance metadata.  Downstream
consumers subscribe to the ``projection`` stream to receive updates.
"""

from __future__ import annotations

from abc import abstractmethod
from enum import StrEnum
from typing import Any, Optional

from ..events.confidence import aggregate_confidence
from ..events.models import Event, EventMeta
from ..events.store import load_all_events, load_events
from .base import AgentBase


class ProjectionEventType(StrEnum):
    """Events emitted by ProjectionAgent.  Stream: 'projection'."""
    UPDATED = "projection.updated"


class ProjectionAgent(AgentBase):
    """
    Reactive agent that materialises a live document from the event log.

    Subclasses implement ``materialise()`` — the LLM phase that turns a
    list of source events into a document string.  Loading, confidence
    aggregation, and event emission are handled by the base class.

    OODA wiring:
      observe()     — calls should_materialise(); skips if False (deterministic)
      orient()      — loads events; calls materialise() (primary LLM phase)
      decide()      — pass-through; always act when orient succeeds
      act()         — publishes projection.updated (deterministic)

    Args:
        name:               Agent name registered on the bus.
        bus:                The EventBus instance.
        projection_streams: Streams to load when materialising.  Defaults to
                            the agent's subscription streams.  Set this
                            explicitly when load scope differs from trigger scope
                            (e.g. subscribe to "tickets", load from "tickets"
                            and "comments").
    """

    def __init__(
        self,
        name: str,
        bus: Any,
        projection_streams: Optional[list[str]] = None,
        hours: Optional[int] = None,
    ) -> None:
        super().__init__(name, bus)
        self._projection_streams = projection_streams
        self._hours = hours

    @property
    def projection_streams(self) -> list[str]:
        """Streams to load when materialising.  Defaults to subscriptions."""
        if self._projection_streams is not None:
            return list(self._projection_streams)
        return list(self._subscriptions)

    # ── Trigger filter ────────────────────────────────────────────────────────

    def should_materialise(self, event: Event) -> bool:
        """
        Return True to trigger a materialisation for this event.
        Override to gate on event_type, stream, source, etc.
        Default: materialise on every event received.
        """
        return True

    # ── OODA pipeline ─────────────────────────────────────────────────────────

    async def observe(self, event: Event) -> Optional[dict[str, Any]]:
        """Filter: skip if should_materialise returns False."""
        if not self.should_materialise(event):
            return None
        return {"trigger": event}

    async def orient(self, event: Event, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Load source events and call materialise() — the primary LLM phase."""
        streams = self.projection_streams
        source_events: list[Event] = []
        for stream in streams:
            if self._hours is not None:
                source_events.extend(load_events(stream, store_dir=self._bus.store_dir, hours=self._hours))
            else:
                source_events.extend(load_all_events(stream, store_dir=self._bus.store_dir))

        content    = await self.materialise(source_events)
        confidence = aggregate_confidence(source_events)

        return {
            "content":     content,
            "confidence":  confidence,
            "event_count": len(source_events),
            "streams":     streams,
        }

    async def decide(self, event: Event, orientation: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Pass-through — always act when orient produced a result."""
        return orientation

    async def act(self, event: Event, action: dict[str, Any]) -> None:
        """Publish a projection.updated event with the materialised content."""
        content    = action["content"]
        confidence = action["confidence"]
        streams    = action["streams"]
        count      = action["event_count"]

        await self._bus.publish(
            event.caused(
                ProjectionEventType.UPDATED,
                self.name,
                {
                    "projection_id": self.name,
                    "content":       content,
                    "confidence":    confidence,
                    "event_count":   count,
                    "streams":       streams,
                    "_meta": EventMeta(
                        phase="orient",
                        loop_type="ooda",
                        confidence=confidence,
                        context=(
                            f"Projection '{self.name}': "
                            f"{len(streams)} stream(s), {count} event(s)"
                        ),
                    ).to_dict(),
                },
            )
        )

    # ── Abstract ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def materialise(self, events: list[Event]) -> str:
        """
        Generate the projected document from source events.

        This is the primary LLM phase.  Receive all events for the configured
        projection_streams; return the materialised document as a string
        (markdown, JSON, plain text — consumer's choice).

        The result is embedded in a projection.updated event and persisted
        to the bus, making it queryable and replayable from the event log.
        """
        ...
