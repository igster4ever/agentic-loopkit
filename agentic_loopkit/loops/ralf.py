"""
agentic_loopkit/loops/ralf.py — RALFExecutor bounded task loop.

RALF = Retrieve → Act → Learn → Follow-up

Design rules (from the architecture spec):
  - Loops MUST be bounded (max_iterations hard cap)
  - LLM is called in act() — not the orchestrator
  - Each step produces a RALFResult with explicit status + confidence
  - Confidence < 0.40 → hard reject (do not proceed)
  - learn() persists state after every step (crash-safe)
  - follow_up() emits a downstream event on completion

Confidence bands:
  0.85 – 1.0   high      → proceed
  0.65 – 0.84  medium    → proceed with uncertainty noted
  0.40 – 0.64  low       → recommend clarification
  < 0.40       very low  → REJECT (mandatory)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from ..events.models import Event

if TYPE_CHECKING:
    from ..bus import EventBus

log = logging.getLogger("agentic_loopkit.ralf")

# Confidence thresholds
CONFIDENCE_HIGH   = 0.85
CONFIDENCE_MEDIUM = 0.65
CONFIDENCE_LOW    = 0.40   # hard reject below this


@dataclass
class RALFResult:
    """Output of one RALF step."""

    status:              str            # "complete" | "in_progress" | "rejected" | "error"
    step_summary:        str
    output:              Any            # domain-specific result (e.g. ADR draft, diagram spec)
    confidence:          float          = 1.0
    next_step:           Optional[str]  = None
    missing_information: list[str]      = field(default_factory=list)
    uncertainty:         Optional[str]  = None

    @property
    def is_terminal(self) -> bool:
        return self.status in ("complete", "rejected", "error")

    @property
    def confidence_band(self) -> str:
        if self.confidence >= CONFIDENCE_HIGH:   return "high"
        if self.confidence >= CONFIDENCE_MEDIUM: return "medium"
        if self.confidence >= CONFIDENCE_LOW:    return "low"
        return "very_low"


class RALFExecutor(ABC):
    """
    Bounded task execution loop.

    Subclass and implement retrieve() and act().
    The loop runs until result.is_terminal or max_iterations is reached.

    Example:

        class AdrDraftExecutor(RALFExecutor):
            max_iterations = 3

            async def retrieve(self, event):
                return {"ticket": event.payload, "related_events": [...]}

            async def act(self, context, prior):
                draft = await call_llm(context, prior)
                return RALFResult(
                    status="complete",
                    step_summary="ADR draft generated",
                    output=draft,
                    confidence=0.78,
                )

            async def follow_up(self, result):
                return event.caused("adr.draft.created", "AdrDraftExecutor", result.output)
    """

    max_iterations: int = 5

    def __init__(self, name: str, bus: "EventBus") -> None:
        self.name = name
        self._bus = bus

    # ── RALF phases ────────────────────────────────────────────────────────────

    @abstractmethod
    async def retrieve(self, event: Event) -> Any:
        """
        RETRIEVE — build context for the task.
        Load related events, wiki articles, git history, prior outputs.
        No LLM calls here — deterministic context assembly.
        """
        ...

    @abstractmethod
    async def act(self, context: Any, prior_result: Optional[RALFResult]) -> RALFResult:
        """
        ACT — execute one step (primary LLM phase).
        Return RALFResult with explicit status and confidence.
        Return status="in_progress" to continue the loop.
        Return status="complete" or "rejected" to terminate.
        """
        ...

    async def learn(self, event: Event, result: RALFResult) -> None:
        """
        LEARN — persist progress after each step.
        Override to save intermediate state so a restart can resume.
        Default: no-op.
        """
        pass

    async def follow_up(self, event: Event, result: RALFResult) -> Optional[Event]:
        """
        FOLLOW-UP — return a downstream event to publish, or None.
        Called once on terminal result.  Use event.caused() for traceability.
        Default: no-op.
        """
        return None

    # ── Loop runner ────────────────────────────────────────────────────────────

    async def run(self, event: Event) -> RALFResult:
        """
        Execute the bounded RALF loop.

        Hard reject if confidence < CONFIDENCE_LOW at any step.
        Caps at max_iterations regardless of status.
        Always calls learn() and follow_up() on the final result.
        """
        log.info("[%s] starting RALF loop for %s", self.name, event.event_type)

        context = await self.retrieve(event)
        result: Optional[RALFResult] = None

        for i in range(self.max_iterations):
            result = await self.act(context, result)

            log.debug(
                "[%s] step %d/%d — status=%s confidence=%.2f",
                self.name, i + 1, self.max_iterations,
                result.status, result.confidence,
            )

            # Hard reject on very low confidence
            if result.confidence < CONFIDENCE_LOW:
                log.warning(
                    "[%s] confidence %.2f below threshold %.2f — rejecting",
                    self.name, result.confidence, CONFIDENCE_LOW,
                )
                result = RALFResult(
                    status       = "rejected",
                    step_summary = result.step_summary,
                    output       = None,
                    confidence   = result.confidence,
                    uncertainty  = f"Confidence {result.confidence:.2f} below rejection threshold {CONFIDENCE_LOW}",
                    missing_information = result.missing_information,
                )

            await self.learn(event, result)

            if result.is_terminal:
                break
        else:
            # Loop exhausted without terminal status
            result = RALFResult(
                status       = "error",
                step_summary = f"Max iterations ({self.max_iterations}) reached without completion",
                output       = result.output if result else None,
                confidence   = 0.0,
            )
            await self.learn(event, result)

        follow_up_event = await self.follow_up(event, result)
        if follow_up_event is not None:
            await self._bus.publish(follow_up_event)

        log.info("[%s] RALF loop done — status=%s", self.name, result.status)
        return result
