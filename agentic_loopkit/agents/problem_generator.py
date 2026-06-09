"""
agentic_loopkit/agents/problem_generator.py — ProblemGeneratorAgent.

Proactive exploration agent.  Where every other agent in the loopkit is
reactive (observe event → respond), ProblemGeneratorAgent asks what should
we be looking at that we are not?

This is the Problem Generator module from the AIMA Learning Agent taxonomy —
the fourth and only previously missing module in the loopkit's AIMA coverage.

Usage::

    class GpsExplorationAgent(ProblemGeneratorAgent):
        context_streams = ["gps", "adr", "projection"]

        def should_explore(self, event):
            return event.event_type == "projection.updated"

        async def explore(self, events):
            result = await llm.call(
                system="Identify 2–4 unexplored angles in this event history.",
                user=format_events(events),
            )
            return parse_agenda_items(result)

    agent = GpsExplorationAgent("gps-explorer", bus)
    agent.subscribe("projection")
    bus.register(agent)

Downstream agents subscribe to the ``agenda`` stream and act on
``agenda.item_added`` events.  When an item is acted on they emit
``agenda.item_addressed``; the ProblemGeneratorAgent emits
``agenda.item_expired`` for items not addressed within ``ttl_hours``.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional

from ..events.models import Event, EventMeta
from ..events.store import load_all_events, load_events
from .base import AgentBase


class AgendaEventType(StrEnum):
    """Events on the ``agenda`` stream."""
    ITEM_ADDED     = "agenda.item_added"      # exploration target identified
    ITEM_ADDRESSED = "agenda.item_addressed"  # downstream agent acted on item
    ITEM_EXPIRED   = "agenda.item_expired"    # item not addressed within ttl_hours


@dataclass
class AgendaItem:
    """A single exploration target emitted by ProblemGeneratorAgent."""
    description:     str
    priority:        float              # 0.0–1.0; caller-set; not confidence
    rationale:       str
    context_streams: list[str]
    tags:            list[str] = field(default_factory=list)
    ttl_hours:       int = 48


class ProblemGeneratorAgent(AgentBase):
    """
    Proactive exploration agent.  Subscribes to trigger streams; on each
    qualifying event loads system state and generates AgendaItems for
    downstream agents.

    OODA wiring:
      observe()  — calls should_explore(); returns None if False      (deterministic)
      orient()   — loads context_streams; calls explore()             (LLM phase)
      decide()   — filters items below min_priority; None if empty    (deterministic)
      act()      — emits one agenda.item_added per AgendaItem         (deterministic)

    Class attributes:
        context_streams: Streams to load when generating agenda items.
                         Defaults to the agent's subscription streams when empty.
        min_priority:    Items below this threshold are discarded silently.
    """

    context_streams: list[str] = []
    min_priority: float = 0.3

    # ── Trigger gate ──────────────────────────────────────────────────────────

    def should_explore(self, event: Event) -> bool:
        """
        Return True to trigger exploration for this event.
        Default: always explore.  Override to throttle (e.g. only on
        system.bus_started, projection.updated, or a scheduler tick).
        """
        return True

    # ── OODA pipeline ─────────────────────────────────────────────────────────

    async def observe(self, event: Event) -> Optional[dict[str, Any]]:
        """Skip exploration if should_explore returns False."""
        if not self.should_explore(event):
            return None
        return {"trigger": event}

    async def orient(
        self, event: Event, context: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Load context streams and call explore() — the primary LLM phase."""
        streams = list(self.context_streams) if self.context_streams else list(self._subscriptions)
        source_events: list[Event] = []
        for stream in streams:
            source_events.extend(
                load_all_events(stream, store_dir=self._bus.store_dir)
            )

        items = await self.explore(source_events)

        return {
            "items":          items,
            "context_streams": streams,
            "event_count":    len(source_events),
        }

    async def decide(
        self, event: Event, orientation: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Filter items below min_priority; short-circuit if none remain."""
        items = [i for i in orientation["items"] if i.priority >= self.min_priority]
        if not items:
            return None
        return {**orientation, "items": items}

    async def act(self, event: Event, action: dict[str, Any]) -> None:
        """Emit one agenda.item_added event per AgendaItem."""
        items: list[AgendaItem] = action["items"]
        count = len(items)
        context_streams = action["context_streams"]
        event_count = action["event_count"]

        for item in items:
            await self._bus.publish(
                event.caused(
                    AgendaEventType.ITEM_ADDED,
                    self.name,
                    {
                        "description":     item.description,
                        "priority":        item.priority,
                        "rationale":       item.rationale,
                        "context_streams": item.context_streams,
                        "tags":            item.tags,
                        "ttl_hours":       item.ttl_hours,
                        "_meta": EventMeta(
                            phase="orient",
                            loop_type="ooda",
                            confidence=item.priority,
                            context=(
                                f"Exploration pass — {count} agenda item(s) "
                                f"from {event_count} event(s) across {context_streams}"
                            ),
                        ).to_dict(),
                    },
                )
            )

    # ── Abstract ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def explore(self, events: list[Event]) -> list[AgendaItem]:
        """
        PRIMARY LLM PHASE — generate agenda items from the loaded event history.

        Receives all events from context_streams.  Returns a list of AgendaItems
        representing unexplored angles, underweighted signals, or blind spots.

        Guidelines:
        - Surface absence: what is missing or not acted on, not what is present
        - Compare what happened vs what the goal demands
        - Prefer 2–5 focused items over a long undifferentiated list
        - Each item should be independently actionable by a downstream agent
        """
        ...
