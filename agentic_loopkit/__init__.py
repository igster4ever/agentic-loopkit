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
from .events.models import Event, EventMeta, SystemEventType, HarnessEventType, TrustLevel, WILDCARD_STREAM
from .events.router import EventRouter, Subscriber
from .events.store import append_event, load_events
from .events.headlines import EventHeadline, append_headline, load_headlines, expand_event
from .events.confidence import aggregate_confidence
from .agents.base import AgentBase, AgentState
from .agents.projection import ProjectionAgent, ProjectionEventType
from .agents.performance import PerformanceMeasure, PerformanceScore, SimpleConfidencePerformance
from .agents.problem_generator import ProblemGeneratorAgent, AgendaEventType, AgendaItem
from .agents.failure_pattern import FailurePatternAgent, FailureSignature
from .loops.ralf import RALFExecutor, RALFResult, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH
from .loops.react import ReActExecutor, ReActResult, ReActStep
from .loops.plan import PlanExecutor, PlanResult, PlanStep
from .loops.reflexion import ReflexionExecutor
from .loops.outcome import OutcomeExecutor
from .loops.utility import UtilityExecutor, UtilityResult, UtilityCandidate
from .loops.skillopt import SkillOptExecutor, SkillEdit, SkillOptResult
from .loops.self_harness import SelfHarnessExecutor
from .adapters.base import PollingAdapter
from .adapters.clickup import ClickUpAdapter, ClickUpEventType
from .adapters.slack import SlackAdapter, SlackEventType
from .adapters.git import LocalGitAdapter, GitEventType
from .testing import AgentTestHarness, TestTask, TestResult, TestSuiteResult, AsyncLLMCallable

__all__ = [
    # Bus
    "EventBus",
    # Events
    "Event",
    "EventMeta",
    "SystemEventType",
    "HarnessEventType",
    "TrustLevel",
    "WILDCARD_STREAM",
    "EventRouter",
    "Subscriber",
    "append_event",
    "load_events",
    "EventHeadline",
    "append_headline",
    "load_headlines",
    "expand_event",
    "aggregate_confidence",
    # Agents
    "AgentBase",
    "AgentState",
    "ProjectionAgent",
    "ProjectionEventType",
    "PerformanceMeasure",
    "PerformanceScore",
    "SimpleConfidencePerformance",
    "ProblemGeneratorAgent",
    "AgendaEventType",
    "AgendaItem",
    "FailurePatternAgent",
    "FailureSignature",
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
    # Executors — Utility
    "UtilityExecutor",
    "UtilityResult",
    "UtilityCandidate",
    # Executors — SkillOpt
    "SkillOptExecutor",
    "SkillEdit",
    "SkillOptResult",
    # Executors — SelfHarness
    "SelfHarnessExecutor",
    # Adapters
    "PollingAdapter",
    "ClickUpAdapter",
    "ClickUpEventType",
    "SlackAdapter",
    "SlackEventType",
    "LocalGitAdapter",
    "GitEventType",
    # Testing
    "AgentTestHarness",
    "TestTask",
    "TestResult",
    "TestSuiteResult",
    "AsyncLLMCallable",
]
