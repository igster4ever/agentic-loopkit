"""
agentic_loopkit/loops/reflexion.py — ReflexionExecutor RALF + critique phase.

Reflexion pattern:
  retrieve(event)              → context (deterministic, no LLM)
  [act → critique → learn] × N → each iteration: act() drafts, critique()
                                  evaluates and optionally revises the result
  follow_up(event, result)     → downstream Event or None

Design rules:
  - Extends RALFExecutor — bounded iteration, confidence rejection, and
    follow_up() are all inherited; only the step interior changes
  - critique() sits between act() and learn() in every iteration
  - Confidence enforcement applies to the post-critique result — critique
    is the quality gate, not act()
  - learn() always receives the post-critique (possibly revised) result
  - LLM calls are appropriate in BOTH act() and critique():
      act()     — primary drafting phase
      critique() — evaluation and revision phase
  - No changes to retrieve(), learn(), or follow_up() semantics

Composition with RALF:

    class EssayReflexionExecutor(ReflexionExecutor):
        max_iterations = 3

        async def retrieve(self, event):
            return {"prompt": event.payload["prompt"]}

        async def act(self, context, prior):
            draft = await call_llm(context["prompt"], prior)
            return RALFResult(
                status="complete", step_summary="draft produced",
                output=draft, confidence=0.75,
            )

        async def critique(self, event, result):
            score, note = await evaluate_llm(result.output)
            if score < 0.6:
                return RALFResult(
                    status="in_progress", step_summary="needs revision",
                    output=result.output, confidence=0.55,
                ), note
            return result, "critique passed"

        async def follow_up(self, event, result):
            if result.status == "complete":
                return event.caused("essay.done", self.name, {"essay": result.output})
            return None
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Optional, TYPE_CHECKING

from ..events.models import Event
from .ralf import RALFExecutor, RALFResult, CONFIDENCE_LOW

if TYPE_CHECKING:
    from ..bus import EventBus

log = logging.getLogger("agentic_loopkit.reflexion")


class ReflexionExecutor(RALFExecutor):
    """
    RALFExecutor + explicit self-critique phase.

    Adds a ``critique()`` call between ``act()`` and ``learn()`` in each
    iteration.  The critique can revise the result — lowering confidence
    forces another iteration; raising it drives toward completion.

    Loop::

        retrieve(event)
        for i in range(max_iterations):
            result          = act(context, prior_result)
            result, note    = critique(event, result)   ← Reflexion addition
            if confidence < 0.40: hard reject and break
            learn(event, result)
            if result.is_terminal: break
        follow_up(event, result)

    Subclass and implement ``retrieve()``, ``act()``, and ``critique()``.
    ``learn()`` and ``follow_up()`` are optional overrides (default no-ops).

    Example::

        class ReviewExecutor(ReflexionExecutor):
            max_iterations = 3

            async def retrieve(self, event):
                return {"task": event.payload["task"]}

            async def act(self, context, prior):
                output = await call_llm(context["task"], prior)
                return RALFResult(
                    status="complete", step_summary="draft",
                    output=output, confidence=0.70,
                )

            async def critique(self, event, result):
                score, note = await evaluate_llm(result.output)
                if score < 0.65:
                    return RALFResult(
                        status="in_progress",
                        step_summary="revision needed",
                        output=result.output,
                        confidence=0.55,
                    ), note
                return result, "passed"

            async def follow_up(self, event, result):
                if result.status == "complete":
                    return event.caused("review.done", self.name,
                                        {"output": result.output})
                return None
    """

    # ── Critique phase ─────────────────────────────────────────────────────────

    @abstractmethod
    async def critique(
        self, event: Event, result: RALFResult
    ) -> tuple[RALFResult, str]:
        """
        CRITIQUE — evaluate the ``act()`` output and optionally revise it.

        Returns ``(revised_result, critique_note)``.

        Patterns:

        - **Force another iteration** — return a result with
          ``status="in_progress"`` and reduced confidence.
        - **Accept and complete** — return the result unchanged (or with
          raised confidence) and an explanatory note.
        - **Hard-reject immediately** — return a result with
          ``confidence < 0.40``; the loop runner enforces the rejection.

        The ``critique_note`` is logged at DEBUG level and is available for
        tracing and dashboard display.

        LLM calls are appropriate here — critique() is the evaluation phase.
        """
        ...

    # ── Loop runner ────────────────────────────────────────────────────────────

    async def run(self, event: Event) -> RALFResult:
        """
        Execute the bounded Reflexion loop.

        Identical to ``RALFExecutor.run()`` with one addition: after each
        ``act()`` call, ``critique()`` is invoked and the result it returns
        replaces the raw ``act()`` result before confidence enforcement and
        ``learn()`` are applied.

        Hard rejects if post-critique confidence < ``CONFIDENCE_LOW``.
        Caps at ``max_iterations`` regardless of status.
        Always calls ``learn()`` and ``follow_up()`` on the terminal result.
        """
        log.info("[%s] starting ReflexionExecutor for %s", self.name, event.event_type)

        context = await self.retrieve(event)
        result: Optional[RALFResult] = None

        for i in range(self.max_iterations):
            result = await self.act(context, result)

            # ── Critique phase ─────────────────────────────────────────────
            result, critique_note = await self.critique(event, result)
            log.debug(
                "[%s] critique %d/%d — status=%s confidence=%.2f note=%r",
                self.name, i + 1, self.max_iterations,
                result.status, result.confidence, critique_note,
            )

            # Hard reject on very low post-critique confidence
            if result.confidence < CONFIDENCE_LOW:
                log.warning(
                    "[%s] post-critique confidence %.2f below threshold %.2f — rejecting",
                    self.name, result.confidence, CONFIDENCE_LOW,
                )
                result = RALFResult(
                    status       = "rejected",
                    step_summary = result.step_summary,
                    output       = None,
                    confidence   = result.confidence,
                    uncertainty  = (
                        f"Confidence {result.confidence:.2f} below "
                        f"rejection threshold {CONFIDENCE_LOW}"
                    ),
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

        log.info("[%s] ReflexionExecutor done — status=%s", self.name, result.status)
        return result
