"""
agentic_govkit/loops/consensus.py — ConsensusOutcomeExecutor.

Shared follow_up() logic for consensus-style governance executors
(ConflictResolutionExecutor, CouncilExecutor). Both emit a single
domain-specific "success" event on result.status == "complete" and
governance.human_override otherwise — this class factors that shape out.
"""

from __future__ import annotations

import logging
from typing import Optional

from agentic_govkit.events.models import GovernanceEventType
from agentic_loopkit import Event, OutcomeExecutor, RALFResult

log = logging.getLogger("agentic_govkit.consensus")


class ConsensusOutcomeExecutor(OutcomeExecutor):
    """
    Shared follow_up() for OutcomeExecutor subclasses that emit one "success"
    event on consensus and governance.human_override otherwise.

    Subclasses must set:
        success_event_type:  GovernanceEventType — emitted when result.status == "complete"
        success_payload_key: str — key under which result.output is stored on success
    """

    success_event_type: GovernanceEventType
    success_payload_key: str

    async def follow_up(self, event: Event, result: RALFResult) -> Optional[Event]:
        if result.status == "complete":
            log.info(
                "[%s] %s reached — correlation_id=%s",
                self.name, self.success_payload_key, event.correlation_id,
            )
            return event.caused(
                self.success_event_type,
                self.name,
                {
                    self.success_payload_key: result.output,
                    "correlation_id":         event.correlation_id,
                },
            )

        log.warning(
            "[%s] no consensus (status=%s) — escalating to human_override",
            self.name, result.status,
        )
        return event.caused(
            GovernanceEventType.HUMAN_OVERRIDE,
            self.name,
            {
                "reason":         f"{type(self).__name__} exhausted without consensus (status={result.status})",
                "last_output":    result.output,
                "correlation_id": event.correlation_id,
                "status":         result.status,
            },
        )
