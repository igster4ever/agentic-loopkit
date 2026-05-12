"""
agentic-loopkit — local-first, event-driven agent runtime.

Core pattern:
    Event Bus (JSONL logs)
        ↓
    OODA Agents (reactive detection)   ← AgentBase
        ↓
    RALF / ReAct Executors (task loops) ← RALFExecutor, ReActExecutor
        ↓
    LLM (reasoning engine — not the orchestrator)

Quick start:
    from agentic_loopkit import EventBus, Event, AgentBase, RALFExecutor
    from agentic_loopkit import ReActExecutor, ReActResult, ReActStep
    from agentic_loopkit import EventMeta
"""

from .bus import EventBus
from .events.models import Event, EventMeta, SystemEventType, TrustLevel, WILDCARD_STREAM
from .events.router import EventRouter, Subscriber
from .events.store import append_event, load_events
from .events.confidence import aggregate_confidence
from .agents.base import AgentBase
from .agents.projection import ProjectionAgent, ProjectionEventType
from .loops.ralf import RALFExecutor, RALFResult, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH
from .loops.react import ReActExecutor, ReActResult, ReActStep
from .loops.plan import PlanExecutor, PlanResult, PlanStep
from .loops.reflexion import ReflexionExecutor
from .loops.outcome import OutcomeExecutor
from .adapters.base import PollingAdapter
from .adapters.clickup import ClickUpAdapter, ClickUpEventType
from .adapters.slack import SlackAdapter, SlackEventType
from .adapters.git import LocalGitAdapter, GitEventType

__all__ = [
    # Bus
    "EventBus",
    # Events
    "Event",
    "EventMeta",
    "SystemEventType",
    "TrustLevel",
    "WILDCARD_STREAM",
    "EventRouter",
    "Subscriber",
    "append_event",
    "load_events",
    "aggregate_confidence",
    # Agents
    "AgentBase",
    "ProjectionAgent",
    "ProjectionEventType",
    # Executors — RALF
    "RALFExecutor",
    "RALFResult",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_HIGH",
    # Executors — ReAct
    "ReActExecutor",
    "ReActResult",
    "ReActStep",
    # Executors — Plan
    "PlanExecutor",
    "PlanResult",
    "PlanStep",
    # Executors — Reflexion
    "ReflexionExecutor",
    # Executors — Outcome
    "OutcomeExecutor",
    # Adapters
    "PollingAdapter",
    "ClickUpAdapter",
    "ClickUpEventType",
    "SlackAdapter",
    "SlackEventType",
    "LocalGitAdapter",
    "GitEventType",
]
