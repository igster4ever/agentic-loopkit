"""
tests/loops/test_calibration.py — CalibrationRecord tests (P60).

Covers:
  - compute() maps satisfied → actual=1.0, not satisfied → actual=0.0
  - gap uses the squared-error (RLMF Z_g) shape
  - perfect calibration (self_predicted matches actual) yields gap=1.0
  - executor_name / iteration pass through unchanged
"""

from agentic_loopkit.loops.calibration import CalibrationRecord


def test_compute_satisfied_sets_actual_one():
    record = CalibrationRecord.compute("e", 0, self_predicted=0.8, satisfied=True)
    assert record.actual == 1.0


def test_compute_not_satisfied_sets_actual_zero():
    record = CalibrationRecord.compute("e", 0, self_predicted=0.8, satisfied=False)
    assert record.actual == 0.0


def test_gap_is_squared_error_shape():
    record = CalibrationRecord.compute("e", 0, self_predicted=0.7, satisfied=True)
    assert record.gap == 1.0 - (0.7 - 1.0) ** 2


def test_perfect_calibration_confident_and_satisfied_yields_gap_one():
    record = CalibrationRecord.compute("e", 0, self_predicted=1.0, satisfied=True)
    assert record.gap == 1.0


def test_perfect_calibration_unconfident_and_unsatisfied_yields_gap_one():
    record = CalibrationRecord.compute("e", 0, self_predicted=0.0, satisfied=False)
    assert record.gap == 1.0


def test_confident_but_wrong_yields_low_gap():
    """High self-predicted confidence paired with an unsatisfied outcome — worst case."""
    record = CalibrationRecord.compute("e", 0, self_predicted=1.0, satisfied=False)
    assert record.gap == 0.0


def test_executor_name_and_iteration_pass_through():
    record = CalibrationRecord.compute("my-executor", 3, self_predicted=0.5, satisfied=True)
    assert record.executor_name == "my-executor"
    assert record.iteration == 3


def test_self_predicted_preserved_unchanged():
    record = CalibrationRecord.compute("e", 0, self_predicted=0.63, satisfied=False)
    assert record.self_predicted == 0.63
