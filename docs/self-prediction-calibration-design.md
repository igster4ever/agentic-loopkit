# Self-prediction calibration signal — design note (P60)

**Status:** Design — no implementation this session. Scoped per `~/.claude/skills/compass/docs/rlmf-metacognitive-calibration-design.md` (P59/P60), which explicitly deferred P60's shape to "its own session in the agentic-loopkit namespace."
**Origin:** compass P60 (RLMF-inspired metacognitive calibration) — sibling to compass-side P59 (which measures compass's own hypothesis-confidence calibration). P60 targets the same *is-stated-confidence-trustworthy* question, but for `OutcomeExecutor` task completion instead of LLM linguistic confidence.
**Dependency:** P55e (`VerificationContract`) — shipped 2026-07-05, `agentic_loopkit/loops/contract.py`.

---

## Finding: the self-prediction signal already exists, and `OutcomeExecutor` already throws it away

The compass-side sketch (design doc §P60, "Sketch" step 1) proposed a new self-report step: *"before calling the rubric/verification step, `OutcomeExecutor` prompts the executing agent for a self-predicted completion score per criterion."* Reading `agentic_loopkit/loops/outcome.py` and `ralf.py` shows this data already flows through the pipeline — it just isn't captured:

- `RALFResult.confidence` (`ralf.py:48`) is populated by `act()` on every iteration. This *is* the agent's self-predicted completion score — the field exists for exactly this purpose in `RALFExecutor`, where confidence gates the hard-reject/accept bands (`CONFIDENCE_LOW`/`MEDIUM`/`HIGH`).
- `OutcomeExecutor._post_act_hook()` (`outcome.py:157-197`) receives that `result` — self-predicted confidence included — calls the isolated `evaluate()`, then **overwrites** `result.confidence` with a hardcoded `1.0` (satisfied) or `0.5` (not satisfied), discarding the original value entirely.

So P60 needs **no new interface, no new abstract method, and no new required override** on `OutcomeExecutor` subclasses. The self-prediction is the `confidence` the subclass's `act()` already emits; the ground truth is `evaluate()`'s `satisfied` outcome, already computed one line earlier in the same hook. The only gap is that nothing captures the *pair* before the original value is discarded.

This mirrors the P55e finding almost exactly: the compass-side sketch invented a new lifecycle stage where the actual codebase already had the pattern — here, a self-report field already exists (`RALFResult.confidence` from `act()`) and just needs to survive one hook call instead of being clobbered.

### What the original sketch got right vs. what changes here

| RLMF doc sketch (step 1) | This design |
|---|---|
| "Prompt the executing agent for a self-predicted completion score" — implies a new prompt/call | No new call — `act()` already returns `confidence`; the hook already has it in scope |
| "Per criterion (0–1 or met/partial/not-met)" | Deferred to a future iteration (see Open questions) — v1 captures whole-artifact confidence only, since `RALFResult.confidence` is a single scalar, not per-criterion |
| New field on "the verification result" | New dataclass (below), not a change to `RALFResult` or `evaluate()`'s return shape |

---

## Proposed addition: `CalibrationRecord` (data shape) + capture point in `_post_act_hook`

```python
# agentic_loopkit/loops/calibration.py  (new, small file)

from __future__ import annotations
from dataclasses import dataclass

@dataclass
class CalibrationRecord:
    """
    Self-prediction vs. verified-outcome gap for one OutcomeExecutor iteration
    (P60). Diagnostic only — see "Non-goals" below.
    """
    executor_name: str
    iteration:     int
    self_predicted: float   # result.confidence from act(), before _post_act_hook overwrites it
    actual:         float   # 1.0 if evaluate() satisfied, else 0.0
    gap:            float   # 1 - (self_predicted - actual) ** 2  — RLMF's Z_g shape

    @classmethod
    def compute(
        cls, executor_name: str, iteration: int, self_predicted: float, satisfied: bool,
    ) -> "CalibrationRecord":
        actual = 1.0 if satisfied else 0.0
        return cls(
            executor_name=executor_name, iteration=iteration,
            self_predicted=self_predicted, actual=actual,
            gap=1.0 - (self_predicted - actual) ** 2,
        )
```

### Integration point — `_post_act_hook`

The capture happens in one place, before the existing overwrite:

```python
async def _post_act_hook(self, event, result, iteration):
    satisfied, gaps = await self.evaluate(result.output, self.rubric)

    calibration = CalibrationRecord.compute(
        executor_name=self.name, iteration=iteration,
        self_predicted=result.confidence, satisfied=satisfied,
    )
    await self._bus.publish(Event(
        event_type=SystemEventType.CALIBRATION_RECORDED,
        source=self.name,
        payload={"iteration": iteration, "self_predicted": calibration.self_predicted,
                  "actual": calibration.actual, "gap": calibration.gap},
    ))

    if satisfied:
        ...  # unchanged
```

No change to the abstract `evaluate()`/`rubric`/`act()` contracts. Subclasses opt in automatically — any existing `OutcomeExecutor` subclass starts emitting calibration events the moment this ships, since the data was already there.

### Event type

**Decision (resolves the design doc's open question "yes, but decide during implementation"):** add `SystemEventType.CALIBRATION_RECORDED` (stream: `system`), mirroring `MEMORY_QUERY_STEP` from P46. Rationale: calibration data is inherently a per-run diagnostic stream, not a request/response value — downstream consumers (a future `CalibrationAggregatorAgent`, or a compass-style periodic review) want to subscribe and accumulate, not poll a single executor instance. Same shape of decision as P46's `on_step` → event choice.

### Aggregation — explicitly out of scope for this design

Per the RLMF doc's own framing ("Aggregate calibration across runs the same way P59 does for compass hypotheses"), loopkit's job stops at emitting the per-run signal. Aggregation (flagging systematically over/under-confident executor types) is either:
- a `ProjectionAgent` subclass consuming `system.calibration_recorded` (same pattern as `FailurePatternAgent` clustering `system.*`/`governance.*`), or
- left to the consuming application (GPS·ADR, MPSM) to build its own view.

Not designed further here — this doc scopes the emission side only.

### Effort

~0.3–0.5 sessions for the dataclass + hook wiring + event type + tests (smaller than the original 1.5–2 session estimate in the RLMF doc, because — same lesson as P55e — the estimate was pricing in a self-report *prompt* mechanism that turns out to be unnecessary; the data already exists in `RALFResult.confidence`).

### Non-goals

- No per-criterion self-prediction in v1 — `RALFResult.confidence` is a single scalar. Per-criterion would require changing `RALFResult`'s shape or `evaluate()`'s return contract, which is a larger, separate change.
- No auto-correction of anything based on a poor calibration score — purely observational, per the RLMF doc's own design principle (surface, never silently mutate) and consistent with P56 zone inference / P12.2 contradiction detection in compass.
- No aggregation logic in loopkit itself (see above).
- No change to `RALFExecutor`, `ReflexionExecutor`, or any non-`OutcomeExecutor` loop — `RALFResult.confidence` is set by `act()` universally, but only `OutcomeExecutor` has an isolated ground-truth signal (`evaluate()`'s `satisfied`) to compare it against. `ReflexionExecutor.critique()` is same-context and would give a biased "ground truth" — not a fair calibration target.

## Open questions (carried into implementation, not resolved here)

- Should `CalibrationRecord.gap` use squared error (RLMF's `Z_g` shape, implemented above) or a simpler `abs(self_predicted - actual)`? Squared error penalizes confident-and-wrong more than the linear form — matches RLMF's intent, kept as the default, but worth a second look once real data exists.
- `self.name` as `executor_name` assumes one instance per executor "identity" for aggregation purposes — matches the existing convention (`AuditAgent`, `KillSwitchAgent` self-exclusion already keys off `event.source == self.name`).
