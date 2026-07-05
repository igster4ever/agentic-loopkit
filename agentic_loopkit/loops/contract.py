"""
agentic_loopkit/loops/contract.py — VerificationContract (P55e).

Structured verification criteria — the loopkit-side counterpart to compass's
``goal_contracts`` state field (P55b). Builds an ``OutcomeExecutor.rubric``
string; carries no lifecycle behaviour of its own. See
docs/verification-contract-design.md for the design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VerificationContract:
    """
    Structured verification criteria — the loopkit-side counterpart to compass's
    ``goal_contracts`` state field (P55b). Builds an ``OutcomeExecutor.rubric``
    string; carries no lifecycle behaviour of its own.
    """

    criteria: list[str]
    evidence_type: str | None = None  # test_output | metric | observation | document | sign_off | mix
    stopping_condition: str | None = None  # free text, e.g. "all unit tests pass"

    def to_rubric(self) -> str:
        """Render as a markdown rubric string for OutcomeExecutor.rubric."""
        lines = [f"- {c}" for c in self.criteria]
        rubric = "## Verification Contract\n" + "\n".join(lines)
        if self.evidence_type:
            rubric += f"\n\nEvidence type: {self.evidence_type}"
        if self.stopping_condition:
            rubric += f"\nStop when: {self.stopping_condition}"
        return rubric

    @classmethod
    def from_goal_contract(cls, d: dict) -> "VerificationContract":
        """Build from a compass goal_contracts[goal_text] entry verbatim."""
        return cls(
            criteria=d.get("criteria", []),
            evidence_type=d.get("evidence_type"),
            stopping_condition=d.get("stopping_condition"),
        )
