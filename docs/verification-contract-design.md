# VerificationContract — design note (P55e)

**Status:** ✅ Implemented 2026-07-05 — `agentic_loopkit/loops/contract.py`, exported from the package top-level alongside `OutcomeExecutor`, 8 tests (`tests/loops/test_contract.py`). Shipped exactly as designed below — no lifecycle change, no new event types.
**Origin:** compass P55 (Verification Contract / DoD 2.0) — `~/.claude/skills/compass/docs/verification-contract-design.md`.
**Source:** compass session 2026-07-03, re-derived against the actual loopkit codebase (the original compass-side sketch predated a codebase read and invented interfaces — `ExecutorAdapter`, `execute()`/`verify()`, new `LoopEventType` members — that do not exist here; see "What the original design got wrong" below).

---

## Finding: the pattern already exists

The compass-side design proposed adding verification as a brand-new lifecycle stage (`execute()` → `verify()`) with new event types. That's unnecessary — `OutcomeExecutor` (`agentic_loopkit/loops/outcome.py`) already implements exactly the "Planner → Builder → Verifier" pattern the DoD 2.0 analysis calls for:

- `act()` produces an artifact.
- `evaluate(artifact, rubric)` checks it **in an isolated context** (no agent reasoning history — the isolation contract this file's docstring already specifies, matching the Anthropic Managed Agents grader pattern).
- `_post_act_hook()` maps `evaluate()`'s `(satisfied, gaps)` onto `RALFResult.status`/`confidence`; unmet gaps feed back into the next `act()` call.
- The inherited `RALFExecutor.run()` loop already handles iteration, confidence-gated hard-rejection (`CONFIDENCE_LOW = 0.40`), and `max_iterations` exhaustion.

So P55e's actual job is much smaller than originally scoped: give `OutcomeExecutor` subclasses a structured way to build a `rubric` string from a compass-style goal contract — not a new lifecycle.

### What the original design got wrong

| Compass doc proposed | Reality |
|---|---|
| New `ExecutorAdapter` protocol with `execute()`/`verify()` | No such protocol exists. The real base is `AgentBase` (observe→orient→decide→act, for event-reactive agents) and `RALFExecutor`/`OutcomeExecutor` (retrieve→act→evaluate, for bounded iterative tasks). `OutcomeExecutor` already *is* the verify step. |
| New `VerificationResult` dataclass (`passed`, `verified_criteria`, `unmet_criteria`, `evidence`) | `evaluate()` already returns `tuple[bool, list[str]]` (satisfied, gaps) — same shape, different names, already shipped. |
| New `LoopEventType` members (`verification.skipped/passed/failed/exhausted`) | Not needed — `RALFResult.status` (`complete`/`in_progress`/`rejected`/`error`) already distinguishes these outcomes, and whatever event bus wiring an `OutcomeExecutor` subclass already has (e.g. `SelfHarnessExecutor` → `harness.edit_accepted`/`harness.edit_rejected` via `follow_up()`) already surfaces them. |
| `max_iterations` on the contract itself | Already a class attribute on `RALFExecutor`/`OutcomeExecutor` (default 3, matching Anthropic Managed Agents). No need to duplicate it per-contract. |

---

## Shipped addition: `VerificationContract` (data shape only)

A plain dataclass + one convenience method — no new executor, no new event types, no change to `OutcomeExecutor`'s existing contract:

```python
# agentic_loopkit/loops/contract.py  (new, small file)

from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class VerificationContract:
    """
    Structured verification criteria — the loopkit-side counterpart to compass's
    ``goal_contracts`` state field (P55b). Builds an ``OutcomeExecutor.rubric``
    string; carries no lifecycle behaviour of its own.
    """
    criteria: list[str]
    evidence_type: str | None = None          # test_output | metric | observation | document | sign_off | mix
    stopping_condition: str | None = None      # free text, e.g. "all unit tests pass"

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
```

### Integration point

An `OutcomeExecutor` subclass that wants to consume a compass contract just does:

```python
class MyExecutor(OutcomeExecutor):
    def __init__(self, name, bus, contract: VerificationContract):
        super().__init__(name, bus)
        self._contract = contract

    @property
    def rubric(self) -> str:
        return self._contract.to_rubric()

    async def evaluate(self, artifact, rubric):
        ...  # unchanged — isolated LLM call against (artifact, rubric)
```

`stopping_condition` is deliberately folded into the rubric text rather than given a separate mechanical check: it is typically not mechanically evaluable ("stop when the design is validated by two reviewers"), so it belongs in the same isolated-evaluator judgment as the other criteria, not a bolt-on Python conditional.

### Effort

~0.1 sessions (one dataclass, no lifecycle change, no new tests beyond the dataclass itself) — down from the original 1.0-session estimate, because the estimate was pricing in a lifecycle rewrite that turned out to be unnecessary.

### Non-goals

- No change to `AgentBase`, `RALFExecutor`, `OutcomeExecutor`, or the event bus.
- No new `LoopEventType` members.
- No `max_iterations` field on the contract — use the executor's existing class attribute.
