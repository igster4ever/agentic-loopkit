"""
agentic_loopkit/loops/plan.py — PlanExecutor front-loaded task decomposition loop.

Plan-and-Execute pattern:
  1. plan(event)        → LLM decomposes task into ordered named steps (one call)
  2. execute_step() × N → each step executed in sequence; wire ReActExecutor here

Design rules (from the architecture spec):
  - plan() is the primary LLM call — front-loads all reasoning
  - execute_step() is deterministic orchestration; LLM calls belong inside a
    ReActExecutor wired within execute_step(), not here directly
  - No max_iterations on the plan itself — the step list is fixed at plan time;
    each ReActExecutor inside execute_step() carries its own max_steps cap
  - follow_up() emits a downstream event on completion (mirrors RALF + ReAct)

Composition with ReAct:

    class ResearchExecutor(PlanExecutor):

        async def plan(self, event):
            # One LLM call to decompose
            descriptions = await call_llm(event.payload["task"])
            return [PlanStep(index=i, description=d)
                    for i, d in enumerate(descriptions)]

        async def execute_step(self, event, step, prior_outputs):
            reactor = SearchReActExecutor("search", self._bus)
            result  = await reactor.run(
                event.caused("plan.step", self.name,
                             {"step": step.description,
                              "prior": prior_outputs})
            )
            return result.answer, result.is_complete

Overall result status:
  "complete" — all steps succeeded (or plan produced zero steps)
  "partial"  — some steps failed, at least one succeeded
  "failed"   — plan() raised, or every step failed
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from ..events.models import Event

if TYPE_CHECKING:
    from ..bus import EventBus

log = logging.getLogger("agentic_loopkit.plan")


@dataclass
class PlanStep:
    """One step in a decomposed task plan."""

    index:       int
    description: str
    status:      str = "pending"   # "pending" | "complete" | "failed"


@dataclass
class PlanResult:
    """Final output of a PlanExecutor run."""

    status:  str              # "complete" | "partial" | "failed"
    steps:   list[PlanStep]
    outputs: list[Any]


class PlanExecutor(ABC):
    """
    Front-loaded task decomposition executor (Plan-and-Execute pattern).

    Subclass and implement plan() and execute_step().

    plan() decomposes the task into ordered PlanStep instances (one LLM call).
    execute_step() executes each step — wire a ReActExecutor here for any
    step that needs tool use.

    Example::

        class AnalysisExecutor(PlanExecutor):

            async def plan(self, event):
                raw = await call_llm(f"Decompose: {event.payload['task']}")
                return [PlanStep(index=i, description=d)
                        for i, d in enumerate(raw)]

            async def execute_step(self, event, step, prior_outputs):
                reactor = SearchReActExecutor("search", self._bus)
                r = await reactor.run(
                    event.caused("plan.step", self.name,
                                 {"step": step.description})
                )
                return r.answer, r.is_complete

            async def follow_up(self, event, result):
                if result.status in ("complete", "partial"):
                    return event.caused("analysis.done", self.name,
                                        {"outputs": result.outputs})
                return None
    """

    def __init__(self, name: str, bus: "EventBus") -> None:
        self.name = name
        self._bus = bus

    # ── Plan phases ────────────────────────────────────────────────────────────

    @abstractmethod
    async def plan(self, event: Event) -> list[PlanStep]:
        """
        PLAN — decompose the task into an ordered list of steps.
        Primary LLM call.  Returns a list of PlanStep instances.
        Raise an exception on hard failure; the loop captures it as status="failed".
        """
        ...

    @abstractmethod
    async def execute_step(
        self,
        event:         Event,
        step:          PlanStep,
        prior_outputs: list[Any],
    ) -> tuple[Any, bool]:
        """
        EXECUTE STEP — execute one step of the plan.
        Returns (output, success).
          - output: domain-specific result; appended to prior_outputs on success
          - success: False marks the step "failed" and excludes it from outputs
        Wire a ReActExecutor here for tool-using steps.
        Raise an exception on hard failure; the step is marked "failed" and
        execution continues with the remaining steps.
        """
        ...

    async def follow_up(self, event: Event, result: PlanResult) -> Optional[Event]:
        """
        FOLLOW-UP — return a downstream event to publish, or None.
        Called once after all steps are processed.
        Use event.caused() for full traceability.
        Default: no-op.
        """
        return None

    # ── Loop runner ────────────────────────────────────────────────────────────

    async def run(self, event: Event) -> PlanResult:
        """
        Execute the plan-and-execute loop.

        1. Calls plan(event) to get an ordered list of steps.
        2. Executes each step in sequence via execute_step().
        3. Marks each step "complete" or "failed" based on the success flag.
        4. Determines overall status:
             "complete" — all steps succeeded (or plan produced zero steps)
             "partial"  — at least one step succeeded and at least one failed
             "failed"   — plan() raised, or every step failed
        5. Calls follow_up() on the terminal result and publishes if not None.
        """
        log.info("[%s] starting PlanExecutor for %s", self.name, event.event_type)

        steps: list[PlanStep] = []
        outputs: list[Any] = []

        # ── 1. Plan ────────────────────────────────────────────────────────────
        try:
            steps = await self.plan(event)
        except Exception as exc:
            log.error("[%s] plan() raised: %s", self.name, exc, exc_info=True)
            result = PlanResult(status="failed", steps=[], outputs=[])
            follow_up_event = await self.follow_up(event, result)
            if follow_up_event is not None:
                await self._bus.publish(follow_up_event)
            return result

        log.debug("[%s] plan produced %d step(s)", self.name, len(steps))

        # ── 2. Execute each step ───────────────────────────────────────────────
        for step in steps:
            try:
                output, success = await self.execute_step(event, step, list(outputs))
            except Exception as exc:
                log.error(
                    "[%s] execute_step() raised on step %d: %s",
                    self.name, step.index, exc, exc_info=True,
                )
                step.status = "failed"
                continue

            if success:
                step.status = "complete"
                outputs.append(output)
                log.debug("[%s] step %d complete", self.name, step.index)
            else:
                step.status = "failed"
                log.warning("[%s] step %d failed (success=False)", self.name, step.index)

        # ── 3. Determine overall status ────────────────────────────────────────
        if not steps:
            overall = "complete"   # vacuously complete — nothing to fail
        else:
            n_complete = sum(1 for s in steps if s.status == "complete")
            n_failed   = sum(1 for s in steps if s.status == "failed")
            if n_failed == 0:
                overall = "complete"
            elif n_complete == 0:
                overall = "failed"
            else:
                overall = "partial"

        result = PlanResult(status=overall, steps=steps, outputs=outputs)

        follow_up_event = await self.follow_up(event, result)
        if follow_up_event is not None:
            await self._bus.publish(follow_up_event)

        log.info("[%s] PlanExecutor done — status=%s", self.name, overall)
        return result
