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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from ..events.models import Event

if TYPE_CHECKING:
    from ..bus import EventBus

log = logging.getLogger("agentic_loopkit.agent")


@dataclass
class AgentState:
    """Agent state decomposed by CoALA memory type.

    episodic    — event_ids or summaries of what has happened
    semantic    — key→value facts the agent has learned or asserted
    procedural  — reserved for v4+ behavioural adjustments; always empty by default
    world_model — predictive/causal state: cause-effect relationships and transition
                  beliefs (e.g. "adapter stalls when API returns 502")
    """

    episodic:    list[str]       = field(default_factory=list)
    semantic:    dict[str, Any]  = field(default_factory=dict)
    procedural:  dict[str, Any]  = field(default_factory=dict)
    world_model: dict[str, Any]  = field(default_factory=dict)


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
        # Optional agentic_memorykit.MemoryStore — set externally; no hard dep.
        self._memory_store: Any = None

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

    # ── State persistence ─────────────────────────────────────────────────────

    async def save_state(self, state: AgentState) -> None:
        """Persist agent state decomposed by CoALA memory type.

        Base implementation writes semantic and world_model facts to _memory_store
        when set.  Override to customise episodic or procedural persistence.
        """
        if self._memory_store is not None:
            for key, value in state.semantic.items():
                await self._memory_store.write(
                    key, str(value), agent_id=self.name, tags=["semantic"]
                )
            for key, value in state.world_model.items():
                await self._memory_store.write(
                    key, str(value), agent_id=self.name, tags=["world_model"]
                )

    async def load_state(self) -> AgentState:
        """Load agent state decomposed by CoALA memory type.

        Base implementation reads semantic and world_model facts from _memory_store
        when set.  Override to customise episodic or procedural loading.
        """
        semantic:    dict[str, Any] = {}
        world_model: dict[str, Any] = {}
        if self._memory_store is not None:
            sem_records = await self._memory_store.list(agent_id=self.name, tags=["semantic"])
            semantic    = {r.key: r.value for r in sem_records}
            wm_records  = await self._memory_store.list(agent_id=self.name, tags=["world_model"])
            world_model = {r.key: r.value for r in wm_records}
        return AgentState(episodic=[], semantic=semantic, procedural={}, world_model=world_model)

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @property
    def subscriptions(self) -> list[str]:
        """Read-only view of the streams this agent is subscribed to."""
        return list(self._subscriptions)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, streams={self._subscriptions})"
