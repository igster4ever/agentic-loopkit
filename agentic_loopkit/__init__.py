"""
agentic-loopkit — local-first, event-driven agent runtime.

Core pattern:
    Event Bus (JSONL logs)
        ↓
    OODA Agents (reactive detection)   ← AgentBase
        ↓
    RALF Executors (task loops)        ← RALFExecutor
        ↓
    LLM (reasoning engine — not the orchestrator)

Quick start:
    from agentic_loopkit import EventBus, Event, AgentBase, RALFExecutor
    from agentic_loopkit.loops.ralf import RALFResult
"""

from .bus import EventBus
from .events.models import Event, SystemEventType, WILDCARD_STREAM
from .events.router import EventRouter, Subscriber
from .events.store import append_event, load_events
from .agents.base import AgentBase
from .loops.ralf import RALFExecutor, RALFResult, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH
from .adapters.base import PollingAdapter

__all__ = [
    "EventBus",
    "Event",
    "SystemEventType",
    "WILDCARD_STREAM",
    "EventRouter",
    "Subscriber",
    "append_event",
    "load_events",
    "AgentBase",
    "RALFExecutor",
    "RALFResult",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_HIGH",
    "PollingAdapter",
]
