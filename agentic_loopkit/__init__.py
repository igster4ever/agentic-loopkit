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

from .adapters.base import PollingAdapter
from .adapters.clickup import ClickUpAdapter, ClickUpEventType
from .adapters.community import CommunityEventType, CommunityFeedAdapter
from .adapters.git import GitEventType, LocalGitAdapter
from .adapters.slack import SlackAdapter, SlackEventType
from .agents.base import AgentBase, AgentState
from .agents.failure_pattern import FailurePatternAgent, FailureSignature
from .agents.performance import PerformanceMeasure, PerformanceScore, SimpleConfidencePerformance
from .agents.problem_generator import AgendaEventType, AgendaItem, ProblemGeneratorAgent
from .agents.projection import ProjectionAgent, ProjectionEventType
from .bus import EventBus
from .events.confidence import aggregate_confidence
from .events.headlines import EventHeadline, append_headline, expand_event, load_headlines
from .events.models import WILDCARD_STREAM, Event, EventMeta, HarnessEventType, LoopType, SystemEventType, TrustLevel
from .events.router import EventRouter, Subscriber
from .events.store import append_event, load_events
from .loops.calibration import CalibrationRecord
from .loops.contract import VerificationContract
from .loops.frontier import BranchScore, FrontierCandidate, FrontierSelector
from .loops.outcome import OutcomeExecutor
from .loops.plan import PlanExecutor, PlanResult, PlanStep
from .loops.ralf import CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, RALFExecutor, RALFResult
from .loops.react import ReActExecutor, ReActResult, ReActStep
from .loops.reflexion import ReflexionExecutor
from .loops.self_harness import SelfHarnessExecutor
from .loops.skillopt import SkillEdit, SkillOptExecutor, SkillOptResult
from .loops.utility import UtilityCandidate, UtilityExecutor, UtilityResult
from .testing import AgentTestHarness, AsyncLLMCallable, TestResult, TestSuiteResult, TestTask

__all__ = [
    # Bus
    "EventBus",
    # Events
    "Event",
    "EventMeta",
    "LoopType",
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
    "CalibrationRecord",
    "VerificationContract",
    # Executors — Utility
    "UtilityExecutor",
    "UtilityResult",
    "UtilityCandidate",
    # Frontier selection
    "BranchScore",
    "FrontierCandidate",
    "FrontierSelector",
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
    "CommunityFeedAdapter",
    "CommunityEventType",
    # Testing
    "AgentTestHarness",
    "TestTask",
    "TestResult",
    "TestSuiteResult",
    "AsyncLLMCallable",
]
