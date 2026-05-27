"""
agentic_govkit/loops/conflict.py — ConflictResolutionExecutor.

Mediates between two competing agent positions on the same entity.
Extends OutcomeExecutor — inherits isolated rubric evaluation.

Event flow::

    governance.dispute_opened     ← trigger event
            │
            ▼
    ConflictResolutionExecutor.run(event)
            │
            ├─ retrieve()   — load position_a, position_b (by correlation_id)
            ├─ act()        — LLM synthesises a reconciliation
            ├─ evaluate()   — isolated rubric check (no history)
            │
            ├─ satisfied=True  ──▶  governance.dispute_resolved
            └─ max_iterations  ──▶  governance.human_override

Isolation contract (inherited from OutcomeExecutor):
    evaluate(synthesis, rubric) receives ONLY the synthesis and rubric —
    no prior reasoning history from either competing agent.  This prevents
    the mediator anchoring on whichever position it encountered first.

Key distinction from ReflexionExecutor:
    Reflexion.critique() runs in the same context as act() — anchors to one position.
    ConflictResolutionExecutor.evaluate() is isolated — sees only the synthesis.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any, Optional

from agentic_loopkit import OutcomeExecutor, RALFResult, Event
from agentic_govkit.events.models import GovernanceEventType

log = logging.getLogger("agentic_govkit.conflict")


class ConflictResolutionExecutor(OutcomeExecutor):
    """
    Mediates between two competing agent positions on the same entity.

    Subclass and implement ``rubric``, ``retrieve()``, ``act()``, and
    ``evaluate()``.  The default ``follow_up()`` emits the appropriate
    governance event; override it for custom payload enrichment.

    ``retrieve()`` must return a context dict containing at minimum::

        {"position_a": <Event or payload>, "position_b": <Event or payload>}

    Use ``event.correlation_id`` in ``retrieve()`` to scope the store query —
    all events in a dispute share a correlation_id.

    ``act()`` receives both positions via ``context`` and the gap list from the
    previous ``evaluate()`` call via ``prior_result.output`` (when not None).
    The primary LLM call should present both positions symmetrically.

    ``evaluate()`` is inherited from ``OutcomeExecutor``.  It must call the LLM
    with ONLY ``(synthesis, rubric)`` — no prior reasoning history.

    Default ``follow_up()`` behaviour::

        complete  → governance.dispute_resolved  (synthesis in payload)
        error     → governance.human_override    (last_output + correlation_id)
        rejected  → governance.human_override    (confidence too low; escalate)
    """

    max_iterations: int = 3

    @property
    @abstractmethod
    def rubric(self) -> str:
        """
        Markdown criteria for a valid reconciliation.

        Write explicit, gradeable criteria.  Example:
        "The synthesis must not contradict either source position without
        explaining why the contradiction was resolved."
        """
        ...

    @abstractmethod
    async def retrieve(self, event: Event) -> dict:
        """
        Load both competing positions.

        Return context dict with at minimum::

            {"position_a": <Event or payload>, "position_b": <Event or payload>}

        Use ``event.correlation_id`` to scope the store query.
        No LLM calls here — deterministic context assembly.
        """
        ...

    @abstractmethod
    async def act(self, context: dict, prior_result: Optional[RALFResult]) -> RALFResult:
        """
        Produce a reconciled synthesis from both positions.

        Primary LLM call.  Present ``context["position_a"]`` and
        ``context["position_b"]`` symmetrically.  If ``prior_result`` is not
        None, ``prior_result.output`` contains the gap list from the previous
        ``evaluate()`` call — use it to revise the synthesis.
        """
        ...

    @abstractmethod
    async def evaluate(self, artifact: str, rubric: str) -> tuple[bool, list[str]]:
        """
        Evaluate the synthesis against the rubric in an isolated context.

        Must call the LLM with ONLY ``(artifact, rubric)`` — no prior
        reasoning history from either agent position.  This prevents anchoring.

        Returns (satisfied, gaps) where gaps is an empty list when satisfied.
        """
        ...

    async def follow_up(self, event: Event, result: RALFResult) -> Optional[Event]:
        """
        Emit governance.dispute_resolved on consensus,
        governance.human_override on exhaustion or confidence rejection.
        """
        if result.status == "complete":
            log.info("[%s] dispute resolved — correlation_id=%s", self.name, event.correlation_id)
            return event.caused(
                GovernanceEventType.DISPUTE_RESOLVED,
                self.name,
                {
                    "synthesis":      result.output,
                    "correlation_id": event.correlation_id,
                },
            )

        log.warning(
            "[%s] dispute unresolved (status=%s) — escalating to human_override",
            self.name, result.status,
        )
        return event.caused(
            GovernanceEventType.HUMAN_OVERRIDE,
            self.name,
            {
                "reason":         f"ConflictResolutionExecutor exhausted without consensus (status={result.status})",
                "last_output":    result.output,
                "correlation_id": event.correlation_id,
                "status":         result.status,
            },
        )
