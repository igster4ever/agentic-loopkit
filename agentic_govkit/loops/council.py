"""
agentic_govkit/loops/council.py — CouncilExecutor.

Fan-out governance executor: submits a question to N specialist agents in
parallel, gathers opinions, synthesises weighted consensus, emits
governance.council_decision.

Distinct from:
    ConflictResolutionExecutor — two-party mediation (position_a vs position_b)
    UtilityExecutor            — single-agent generate-and-rank

Event flow::

    trigger event (any)
            │
            ▼
    CouncilExecutor.run(event)
            │
            ├─ retrieve()          — calls gather_opinions() → list[CouncilOpinion]
            ├─ act()               — LLM synthesises weighted consensus from opinions
            ├─ evaluate()          — isolated rubric check (no history)
            │
            ├─ satisfied=True  ──▶  governance.council_decision
            └─ max_iterations  ──▶  governance.human_override

Isolation contract (inherited from OutcomeExecutor):
    evaluate(consensus, rubric) receives ONLY the consensus text and rubric —
    no prior reasoning history from any specialist.  This prevents the evaluator
    anchoring on whichever specialist opinion it encountered first.

Key distinction from ConflictResolutionExecutor:
    ConflictResolutionExecutor resolves *two* competing positions on the *same*
    entity.  CouncilExecutor aggregates *N* independent specialist opinions on a
    shared question and synthesises a single decision.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from agentic_loopkit import OutcomeExecutor, RALFResult, Event
from agentic_govkit.events.models import GovernanceEventType

log = logging.getLogger("agentic_govkit.council")


@dataclass
class CouncilOpinion:
    """
    A single specialist contribution to the council.

    Attributes:
        source:     Identifier for the specialist (agent name, role label, etc.)
        opinion:    The specialist's free-text opinion on the question.
        weight:     Relative weight during synthesis (default 1.0 = equal weight).
        confidence: Specialist's declared confidence in their own opinion (0–1).
    """
    source: str
    opinion: str
    weight: float = 1.0
    confidence: float = 1.0


class CouncilExecutor(OutcomeExecutor):
    """
    Fan-out governance executor.

    Submits a question to N specialist agents in parallel via
    ``gather_opinions()``, collects their ``CouncilOpinion`` responses,
    synthesises a weighted consensus in ``act()``, and gate-checks the consensus
    with an isolated ``evaluate()`` call.  Emits ``governance.council_decision``
    on consensus or ``governance.human_override`` on exhaustion.

    Subclass and implement ``rubric``, ``gather_opinions()``, ``act()``, and
    ``evaluate()``.  ``retrieve()`` calls ``gather_opinions()`` by default —
    override it if you need custom context loading beyond the opinions.

    ``act()`` receives context from ``retrieve()``::

        context["question"]  — from event.payload.get("question", "")
        context["opinions"]  — list[CouncilOpinion]

    Gap feedback from ``evaluate()`` is passed to the next ``act()`` iteration
    via ``prior_result.output`` — exactly as in OutcomeExecutor.

    Example::

        class TechCouncil(CouncilExecutor):
            max_iterations = 3

            @property
            def rubric(self):
                return (
                    "## Consensus Rubric\\n"
                    "- Decision is actionable\\n"
                    "- Rationale references at least two specialist opinions\\n"
                )

            async def gather_opinions(self, event):
                return [
                    CouncilOpinion("security", await ask_security(event), weight=1.5),
                    CouncilOpinion("perf",     await ask_perf(event),     weight=1.0),
                    CouncilOpinion("ux",       await ask_ux(event),       weight=1.0),
                ]

            async def act(self, context, prior):
                consensus = await synthesise_llm(context["opinions"], prior)
                return RALFResult(
                    status="complete", step_summary="consensus reached",
                    output=consensus, confidence=0.85,
                )

            async def evaluate(self, artifact, rubric):
                gaps = await check_isolated(artifact, rubric)
                return not gaps, gaps
    """

    max_iterations: int = 3

    # ── Abstract interface ────────────────────────────────────────────────────

    @property
    @abstractmethod
    def rubric(self) -> str:
        """
        Markdown rubric defining acceptance criteria for the consensus.

        Write explicit, gradeable criteria.  Example:
        "The decision must cite evidence from at least two specialist opinions
        and include a concrete next step."
        """
        ...

    @abstractmethod
    async def gather_opinions(self, event: Event) -> list[CouncilOpinion]:
        """
        Fan out to N specialist agents and collect their opinions.

        Called from ``retrieve()`` on every run.  Implementations should
        parallelise specialist calls (e.g. via ``asyncio.gather``).

        Returns a list of ``CouncilOpinion`` objects, one per specialist.
        No LLM synthesis here — only collection.
        """
        ...

    @abstractmethod
    async def act(self, context: dict, prior_result: Optional[RALFResult]) -> RALFResult:
        """
        Synthesise a weighted consensus from the gathered opinions.

        Primary LLM call.  ``context["opinions"]`` is a ``list[CouncilOpinion]``.
        If ``prior_result`` is not None, ``prior_result.output`` contains the gap
        list from the previous ``evaluate()`` call — use it to refine the
        consensus in the next iteration.
        """
        ...

    @abstractmethod
    async def evaluate(self, artifact: Any, rubric: str) -> tuple[bool, list[str]]:
        """
        Evaluate the consensus in an isolated context.

        Must call the LLM with ONLY ``(artifact, rubric)`` — no specialist
        opinions, no prior synthesis history.  This prevents anchoring on the
        loudest opinion.

        Returns (satisfied, gaps) where gaps is empty when satisfied.
        """
        ...

    # ── Default retrieve ──────────────────────────────────────────────────────

    async def retrieve(self, event: Event) -> dict:
        """
        Gather specialist opinions and return context for ``act()``.

        Default implementation calls ``gather_opinions()`` and returns::

            {
                "question": event.payload.get("question", ""),
                "opinions": list[CouncilOpinion],
            }

        Override if you need additional context beyond the opinions themselves.
        """
        opinions = await self.gather_opinions(event)
        return {
            "question": event.payload.get("question", ""),
            "opinions": opinions,
        }

    # ── follow_up ─────────────────────────────────────────────────────────────

    async def follow_up(self, event: Event, result: RALFResult) -> Optional[Event]:
        """
        Emit governance.council_decision on consensus,
        governance.human_override on exhaustion or confidence rejection.
        """
        if result.status == "complete":
            log.info(
                "[%s] council decision reached — correlation_id=%s",
                self.name, event.correlation_id,
            )
            return event.caused(
                GovernanceEventType.COUNCIL_DECISION,
                self.name,
                {
                    "decision":       result.output,
                    "correlation_id": event.correlation_id,
                },
            )

        log.warning(
            "[%s] council did not reach consensus (status=%s) — escalating to human_override",
            self.name, result.status,
        )
        return event.caused(
            GovernanceEventType.HUMAN_OVERRIDE,
            self.name,
            {
                "reason":         (
                    f"CouncilExecutor exhausted without consensus (status={result.status})"
                ),
                "last_output":    result.output,
                "correlation_id": event.correlation_id,
                "status":         result.status,
            },
        )
