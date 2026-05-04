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
from .events.models import Event, EventMeta, SystemEventType, WILDCARD_STREAM
from .events.router import EventRouter, Subscriber
from .events.store import append_event, load_events
from .agents.base import AgentBase
from .loops.ralf import RALFExecutor, RALFResult, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH
from .loops.react import ReActExecutor, ReActResult, ReActStep
from .adapters.base import PollingAdapter
from .adapters.clickup import ClickUpAdapter, ClickUpEventType

__all__ = [
    # Bus
    "EventBus",
    # Events
    "Event",
    "EventMeta",
    "SystemEventType",
    "WILDCARD_STREAM",
    "EventRouter",
    "Subscriber",
    "append_event",
    "load_events",
    # Agents
    "AgentBase",
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
    # Adapters
    "PollingAdapter",
    "ClickUpAdapter",
    "ClickUpEventType",
]
