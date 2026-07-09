"""
agentic_loopkit/loops/calibration.py — CalibrationRecord (P60).

Self-prediction vs. verified-outcome gap for one OutcomeExecutor iteration.
The self-predicted signal is ``RALFResult.confidence`` from ``act()`` — it
already exists; this module captures it before ``OutcomeExecutor._post_act_hook``
overwrites it, and pairs it against ``evaluate()``'s ``satisfied`` ground truth.

Diagnostic only. No aggregation, no auto-correction — see
docs/self-prediction-calibration-design.md "Non-goals".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CalibrationRecord:
    """Self-prediction vs. verified-outcome gap for one OutcomeExecutor iteration."""

    executor_name:  str
    iteration:      int
    self_predicted: float   # result.confidence from act(), before _post_act_hook overwrites it
    actual:         float   # 1.0 if evaluate() satisfied, else 0.0
    gap:            float   # 1 - (self_predicted - actual) ** 2 — RLMF's Z_g shape

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
