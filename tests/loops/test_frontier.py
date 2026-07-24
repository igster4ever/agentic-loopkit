"""
tests/loops/test_frontier.py — BranchScore + FrontierSelector tests (P62a).

Covers:
  - BranchScore.score() weighted sum (default and custom weights)
  - Default productivity estimator (mean utility delta over history)
  - Default novelty estimator (visit-cooling)
  - FrontierSelector.rank() ordering
  - FrontierSelector.select() returns top candidate, None on empty pool
  - Custom productivity_fn / novelty_fn override
  - FrontierSelector.revoke() de-authorization (P64a)
"""

import pytest

from agentic_loopkit.loops.frontier import BranchScore, FrontierCandidate, FrontierSelector

# ── BranchScore ───────────────────────────────────────────────────────────────

def test_branch_score_default_weights_sum():
    score = BranchScore(utility=0.5, productivity=0.2, novelty=0.1)
    assert score.score() == 0.5 + 0.2 + 0.1


def test_branch_score_custom_weights():
    score = BranchScore(utility=1.0, productivity=1.0, novelty=1.0, weights=(2.0, 0.5, 0.1))
    assert score.score() == 2.0 * 1.0 + 0.5 * 1.0 + 0.1 * 1.0


def test_branch_score_zero_weight_excludes_term():
    score = BranchScore(utility=1.0, productivity=100.0, novelty=0.0, weights=(1.0, 0.0, 1.0))
    assert score.score() == 1.0


# ── Default productivity estimator ───────────────────────────────────────────

def test_productivity_insufficient_history_is_zero():
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.5, utility_history=[0.5])
    selector = FrontierSelector()
    assert selector.score(candidate).productivity == 0.0


def test_productivity_no_history_is_zero():
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.5)
    selector = FrontierSelector()
    assert selector.score(candidate).productivity == 0.0


def test_productivity_is_mean_delta():
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.7, utility_history=[0.4, 0.5, 0.7])
    selector = FrontierSelector()
    # deltas: 0.1, 0.2 → mean 0.15
    assert selector.score(candidate).productivity == pytest.approx(0.15)


def test_productivity_negative_when_declining():
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.3, utility_history=[0.6, 0.4, 0.3])
    selector = FrontierSelector()
    assert selector.score(candidate).productivity < 0.0


# ── Default novelty estimator ────────────────────────────────────────────────

def test_novelty_never_selected_is_one():
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.5, times_selected=0)
    selector = FrontierSelector()
    assert selector.score(candidate).novelty == 1.0


def test_novelty_decreases_with_selections():
    fresh    = FrontierCandidate(id="a", artifact="x", utility=0.5, times_selected=0)
    selected = FrontierCandidate(id="b", artifact="y", utility=0.5, times_selected=5)
    selector = FrontierSelector()
    assert selector.score(selected).novelty < selector.score(fresh).novelty


def test_novelty_visit_cooling_formula():
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.5, times_selected=3)
    selector = FrontierSelector()
    assert selector.score(candidate).novelty == 1.0 / (1.0 + 3)


# ── FrontierSelector.rank() / select() ───────────────────────────────────────

def test_rank_orders_by_score_descending():
    low  = FrontierCandidate(id="low", artifact="x", utility=0.1)
    high = FrontierCandidate(id="high", artifact="y", utility=0.9)
    selector = FrontierSelector()
    ranked = selector.rank([low, high])
    assert [c.id for c, _ in ranked] == ["high", "low"]


def test_select_returns_top_candidate():
    low  = FrontierCandidate(id="low", artifact="x", utility=0.1)
    high = FrontierCandidate(id="high", artifact="y", utility=0.9)
    selector = FrontierSelector()
    winner, score = selector.select([low, high])
    assert winner.id == "high"
    assert isinstance(score, BranchScore)


def test_select_returns_none_on_empty_pool():
    selector = FrontierSelector()
    assert selector.select([]) is None


def test_novelty_can_favour_less_utility_but_fresher_candidate():
    """High utility but heavily-visited candidate can lose to a fresher, lower-utility one."""
    stale = FrontierCandidate(id="stale", artifact="x", utility=0.9, times_selected=20)
    fresh = FrontierCandidate(id="fresh", artifact="y", utility=0.6, times_selected=0)
    # Weight novelty heavily to surface the effect
    selector = FrontierSelector(weights=(1.0, 0.0, 2.0))
    winner, _ = selector.select([stale, fresh])
    assert winner.id == "fresh"


# ── Custom estimator overrides ────────────────────────────────────────────────

def test_custom_productivity_fn_override():
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.5)
    selector = FrontierSelector(productivity_fn=lambda c: 42.0)
    assert selector.score(candidate).productivity == 42.0


def test_custom_novelty_fn_override():
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.5)
    selector = FrontierSelector(novelty_fn=lambda c: -1.0)
    assert selector.score(candidate).novelty == -1.0


# ── revoke() de-authorization (P64a) ─────────────────────────────────────────

def test_revoke_sets_flag_and_reason():
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.5)
    FrontierSelector.revoke(candidate, "traced to production regression")
    assert candidate.revoked is True
    assert candidate.revocation_reason == "traced to production regression"


def test_revoked_candidate_excluded_from_rank():
    high = FrontierCandidate(id="high", artifact="y", utility=0.9)
    low  = FrontierCandidate(id="low", artifact="x", utility=0.1)
    FrontierSelector.revoke(high, "regression")
    selector = FrontierSelector()
    ranked = selector.rank([high, low])
    assert [c.id for c, _ in ranked] == ["low"]


def test_revoked_candidate_excluded_from_select():
    only = FrontierCandidate(id="only", artifact="x", utility=0.9)
    FrontierSelector.revoke(only, "regression")
    selector = FrontierSelector()
    assert selector.select([only]) is None


def test_revocation_survives_future_utility_growth():
    """Unlike novelty/productivity, revocation is not overturned by later utility_history."""
    candidate = FrontierCandidate(id="a", artifact="x", utility=0.9, utility_history=[0.1, 0.9])
    FrontierSelector.revoke(candidate, "regression")
    selector = FrontierSelector()
    assert selector.rank([candidate]) == []


def test_unrevoked_candidates_unaffected_by_sibling_revocation():
    revoked = FrontierCandidate(id="revoked", artifact="x", utility=0.9)
    kept    = FrontierCandidate(id="kept", artifact="y", utility=0.5)
    FrontierSelector.revoke(revoked, "regression")
    selector = FrontierSelector()
    winner, _ = selector.select([revoked, kept])
    assert winner.id == "kept"
