"""
agentic_loopkit/loops/outcome.py — OutcomeExecutor rubric-governed iteration.

Outcome pattern:
  retrieve(event)          → context (deterministic, no LLM)
  [act → evaluate] × N    → each iteration: act() produces an artifact,
                             evaluate() checks it against a rubric in an
                             isolated context; gaps feed the next act()
  follow_up(event, result) → downstream Event or None

Key design principle — evaluation isolation:
  evaluate() is called with ONLY (artifact, rubric) — no agent reasoning history.
  This prevents anchoring: the evaluator cannot rationalise the agent's choices.
  In practice this means the LLM call inside evaluate() should use a fresh context
  rather than the conversation history accumulated during act().

Comparison with ReflexionExecutor:
  ReflexionExecutor.critique() — same-context self-critique; the model has seen
  its own reasoning trail and may defend its choices.

  OutcomeExecutor.evaluate()   — isolated evaluation; called with only
  (artifact, rubric); no prior chain; equivalent to the Anthropic Managed Agents
  grader contract.

Exit conditions (all inherited unless noted):
  satisfied (evaluate returns True)    → status="complete",   confidence=1.0
  max_iterations_reached               → inherited,           status="error"
  confidence < CONFIDENCE_LOW          → inherited hard reject, status="rejected"
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any, Optional

from ..events.models import Event
from .ralf import RALFExecutor, RALFResult

log = logging.getLogger("agentic_loopkit.outcome")


class OutcomeExecutor(RALFExecutor):
    """
    RALFExecutor with rubric-governed, isolated evaluation.

    Each iteration: ``act()`` produces an artifact, then ``evaluate()`` checks
    it against the rubric in an *isolated* context (no agent reasoning history).
    If satisfied the loop terminates; if not, the specific gaps are fed back
    into the next ``act()`` call via ``prior_result.output`` so the agent can
    revise accordingly.

    Loop::

        retrieve(event)
        for i in range(max_iterations):
            result                 = act(context, prior_result)
            satisfied, gaps        = evaluate(result.output, rubric)  ← isolated
            if satisfied: status="complete", confidence=1.0 → break
            else:         status="in_progress", gaps appended to output
            if confidence < 0.40: hard reject and break
            learn(event, result)
            if result.is_terminal: break
        follow_up(event, result)

    Subclass and implement ``retrieve()``, ``act()``, ``rubric``, and
    ``evaluate()``.  ``learn()`` and ``follow_up()`` are optional overrides.

    The ``evaluate()`` isolation contract:
        The implementation should call the LLM with *only* the artifact and
        rubric — no conversation history, no prior act() chain.  A minimal
        invocation looks like::

            messages = [
                {"role": "user", "content": f"Rubric:\\n{rubric}\\n\\nArtifact:\\n{artifact}"}
            ]
            response = await call_llm(messages=messages)  # fresh context

        This mirrors the Anthropic Managed Agents grader: a separate context
        window that cannot be influenced by the agent's reasoning choices.

    Example::

        class CsvOutcomeExecutor(OutcomeExecutor):
            max_iterations = 3

            @property
            def rubric(self) -> str:
                return (
                    "## CSV Rubric\\n"
                    "- Contains a 'price' column with numeric values\\n"
                    "- Contains a 'name' column\\n"
                    "- Has at least 10 rows\\n"
                )

            async def retrieve(self, event):
                return {"spec": event.payload["spec"]}

            async def act(self, context, prior):
                csv = await call_llm(context["spec"], prior)
                return RALFResult(
                    status="complete", step_summary="CSV generated",
                    output=csv, confidence=0.8,
                )

            async def evaluate(self, artifact, rubric):
                # Fresh LLM context — no agent history
                gaps = await check_rubric_isolated(artifact, rubric)
                return len(gaps) == 0, gaps

            async def follow_up(self, event, result):
                if result.status == "complete":
                    return event.caused("csv.done", self.name, {"csv": result.output})
                return None
    """

    max_iterations: int = 3   # matches Anthropic Managed Agents default

    # ── Rubric + evaluation ────────────────────────────────────────────────────

    @property
    @abstractmethod
    def rubric(self) -> str:
        """
        Markdown rubric defining per-criterion acceptance criteria.

        Write explicit, gradeable criteria: "The CSV contains a price column
        with numeric values" rather than "The data looks good."  The evaluator
        scores each criterion independently, so vague criteria produce noisy
        evaluations.
        """
        ...

    @abstractmethod
    async def evaluate(
        self, artifact: Any, rubric: str
    ) -> tuple[bool, list[str]]:
        """
        EVALUATE — assess the artifact against the rubric in an isolated context.

        Called with **only** ``(artifact, rubric)`` — no prior agent reasoning.
        The implementation should use a fresh LLM context to prevent anchoring.

        Returns ``(satisfied, gaps)`` where:

        - ``satisfied`` — True if all rubric criteria are met; False otherwise.
        - ``gaps``      — list of specific unmet criteria (empty when satisfied).
          Each gap string should be concrete enough for ``act()`` to act on in
          the next iteration, e.g. "Missing 'price' column" not "data is wrong".

        LLM calls are appropriate here (isolated, fresh context only).
        """
        ...

    # ── Post-act hook (wires evaluation into the inherited RALF runner) ────────

    async def _post_act_hook(
        self, event: Event, result: RALFResult, iteration: int
    ) -> RALFResult:
        """
        Run ``evaluate()`` after ``act()`` and map the outcome to confidence.

        Satisfied → ``status="complete"``, ``confidence=1.0`` (original output
        preserved unchanged).

        Needs revision → ``status="in_progress"``, ``confidence=0.5``; gap
        feedback is prepended to the output so the next ``act()`` call receives
        it via ``prior_result.output``.
        """
        satisfied, gaps = await self.evaluate(result.output, self.rubric)

        if satisfied:
            log.debug(
                "[%s] evaluate %d/%d — satisfied",
                self.name, iteration + 1, self.max_iterations,
            )
            return RALFResult(
                status       = "complete",
                step_summary = result.step_summary,
                output       = result.output,
                confidence   = 1.0,
            )

        gap_lines = "\n".join(f"- {g}" for g in gaps)
        log.debug(
            "[%s] evaluate %d/%d — needs revision, %d gap(s):\n%s",
            self.name, iteration + 1, self.max_iterations, len(gaps), gap_lines,
        )
        return RALFResult(
            status       = "in_progress",
            step_summary = f"Rubric not yet satisfied — {len(gaps)} gap(s)",
            output       = (
                f"Gaps identified by evaluator:\n{gap_lines}"
                f"\n\nPrevious output:\n{result.output}"
            ),
            confidence   = 0.5,   # above CONFIDENCE_LOW; keeps the loop going
        )
