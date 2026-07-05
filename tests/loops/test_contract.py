"""
tests/loops/test_contract.py — VerificationContract tests (P55e).

Covers:
  - to_rubric() renders criteria as a markdown bullet list under a fixed header
  - to_rubric() appends evidence type when present, omits when absent
  - to_rubric() appends stopping condition when present, omits when absent
  - to_rubric() with no criteria still renders the header
  - from_goal_contract() builds from a compass goal_contracts entry
  - from_goal_contract() defaults missing fields to None / empty list
"""

from agentic_loopkit.loops.contract import VerificationContract


def test_to_rubric_renders_criteria_as_bullets():
    contract = VerificationContract(criteria=["tests pass", "docs updated"])
    rubric = contract.to_rubric()
    assert rubric.startswith("## Verification Contract\n")
    assert "- tests pass" in rubric
    assert "- docs updated" in rubric


def test_to_rubric_includes_evidence_type_when_present():
    contract = VerificationContract(criteria=["a"], evidence_type="test_output")
    rubric = contract.to_rubric()
    assert "Evidence type: test_output" in rubric


def test_to_rubric_omits_evidence_type_when_absent():
    contract = VerificationContract(criteria=["a"])
    rubric = contract.to_rubric()
    assert "Evidence type" not in rubric


def test_to_rubric_includes_stopping_condition_when_present():
    contract = VerificationContract(criteria=["a"], stopping_condition="all unit tests pass")
    rubric = contract.to_rubric()
    assert "Stop when: all unit tests pass" in rubric


def test_to_rubric_omits_stopping_condition_when_absent():
    contract = VerificationContract(criteria=["a"])
    rubric = contract.to_rubric()
    assert "Stop when" not in rubric


def test_to_rubric_with_no_criteria_still_renders_header():
    contract = VerificationContract(criteria=[])
    rubric = contract.to_rubric()
    assert rubric.strip() == "## Verification Contract"


def test_from_goal_contract_builds_from_dict():
    d = {
        "criteria": ["tests pass", "docs updated"],
        "evidence_type": "test_output",
        "stopping_condition": "all unit tests pass",
    }
    contract = VerificationContract.from_goal_contract(d)
    assert contract.criteria == ["tests pass", "docs updated"]
    assert contract.evidence_type == "test_output"
    assert contract.stopping_condition == "all unit tests pass"


def test_from_goal_contract_defaults_missing_fields():
    contract = VerificationContract.from_goal_contract({})
    assert contract.criteria == []
    assert contract.evidence_type is None
    assert contract.stopping_condition is None
