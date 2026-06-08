# UtilityExecutor — Design Brief

_Date: 2026-06-08_
_Status: Draft — v0.1_
_Informs: loopkit v4 theme 6 — ranked candidate selection_

---

## North Star

`OutcomeExecutor` iterates to improve **one** artifact against a rubric (pass/fail).
`UtilityExecutor` generates **multiple** candidates and selects the best by utility score.
These are not competing patterns — they address different problems.

Use `OutcomeExecutor` when the goal is a single correct answer, refined iteratively.
Use `UtilityExecutor` when the goal is the best option from a space of valid alternatives.

---

## The Gap It Fills

The Utility-Based Agent in the AIMA taxonomy:
- Maintains goals **and** a utility function
- Ranks outcomes by preferences or probabilities
- Handles **conflicting goals and trade-offs**
- Aims for maximum satisfaction, not just threshold success
- Performs cost-benefit analysis

`OutcomeExecutor.evaluate()` returns `(satisfied: bool, gaps: list[str])` — a binary verdict.
This does not support: ranking competing solutions, multi-criteria weighting, or
trade-off resolution when multiple solutions all pass the rubric.

---

## Package Location

`agentic_loopkit/loops/utility.py`

Standalone executor — does **not** extend `RALFExecutor`. The generate-and-rank pattern
does not fit the iterative convergence model of the RALF family.

---

## Dataclasses

```python
@dataclass
class UtilityCandidate:
    artifact:      Any          # the generated artifact (text, dict, etc.)
    utility_score: float        # 0.0–1.0; higher = more preferred
    criteria_scores: dict[str, float] = field(default_factory=dict)  # per-criterion breakdown
    rationale:     str = ""     # why this score was assigned

@dataclass
class UtilityResult:
    status:         str         # "complete" | "no_candidates" | "below_threshold" | "error"
    winner:         Any | None  # artifact of the highest-scoring candidate; None if not complete
    winner_score:   float       # utility score of winner; 0.0 if not complete
    all_candidates: list[UtilityCandidate] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"
```

---

## Abstract Interface

```python
class UtilityExecutor(ABC):
    """
    Generate N candidates; rank by utility; select the winner.

    Pattern:
        retrieve(event)              → context (deterministic; no LLM)
        generate_candidates(context) → list[Any] (primary LLM phase)
        utility_score(artifact,      → UtilityCandidate (LLM or deterministic)
                      criteria,
                      context)
        follow_up(event, result)     → downstream Event or None

    Does not extend RALFExecutor — this is a single-pass generate-and-rank,
    not an iterative convergence loop.
    """

    #: Number of candidates to generate per run.
    max_candidates: int = 3

    #: Minimum utility score to accept a winner. Below this → "below_threshold".
    min_utility: float = 0.5

    def __init__(self, name: str, bus: "EventBus") -> None:
        self.name = name
        self._bus = bus

    @property
    @abstractmethod
    def criteria(self) -> dict[str, float]:
        """
        Scoring criteria and their weights. Weights must sum to 1.0.

        Example:
            {
                "correctness":  0.5,
                "conciseness":  0.3,
                "tone":         0.2,
            }

        The criteria dict is passed to utility_score() so the scorer can
        apply per-criterion weights explicitly.
        """

    async def retrieve(self, event: "Event") -> dict:
        """
        Assemble context before generation.  Default: return event payload.
        Override to load from store, call APIs, etc.  No LLM calls here.
        """
        return dict(event.payload)

    @abstractmethod
    async def generate_candidates(
        self, context: dict
    ) -> list[Any]:
        """
        PRIMARY LLM PHASE — generate max_candidates candidate artifacts.

        Prompt should request N distinct alternatives, not N variations of
        the same answer.  Returns a list; fewer than max_candidates is valid.
        Empty list → status="no_candidates".
        """

    @abstractmethod
    async def utility_score(
        self, artifact: Any, criteria: dict[str, float], context: dict
    ) -> UtilityCandidate:
        """
        Score a single candidate against the criteria.

        Two implementation styles:
            LLM-based:    call LLM with (artifact, criteria) — isolated context,
                          same isolation contract as OutcomeExecutor.evaluate().
            Rule-based:   compute scores programmatically from artifact properties.

        The method must return a UtilityCandidate.  `criteria_scores` should
        reflect per-criterion breakdown; `utility_score` is the weighted sum.
        """

    async def follow_up(
        self, event: "Event", result: UtilityResult
    ) -> "Optional[Event]":
        """Emit downstream event.  Default: no-op.  Use event.caused()."""
        return None

    async def run(self, event: "Event") -> UtilityResult:
        """
        Execute the generate-and-rank pass.

        1. retrieve(event) → context
        2. generate_candidates(context) → list of artifacts
        3. utility_score(artifact, criteria, context) for each → UtilityCandidate
        4. Sort by utility_score descending; select winner
        5. If winner.utility_score < min_utility → status="below_threshold"
        6. publish follow_up event if result.is_complete
        """
```

---

## `run()` Implementation Sketch

```python
async def run(self, event: Event) -> UtilityResult:
    try:
        context = await self.retrieve(event)

        raw_candidates = await self.generate_candidates(context)
        if not raw_candidates:
            return UtilityResult(status="no_candidates", winner=None, winner_score=0.0)

        scored = [
            await self.utility_score(c, self.criteria, context)
            for c in raw_candidates
        ]
        scored.sort(key=lambda c: c.utility_score, reverse=True)
        winner = scored[0]

        if winner.utility_score < self.min_utility:
            result = UtilityResult(
                status="below_threshold",
                winner=None,
                winner_score=winner.utility_score,
                all_candidates=scored,
            )
        else:
            result = UtilityResult(
                status="complete",
                winner=winner.artifact,
                winner_score=winner.utility_score,
                all_candidates=scored,
            )

        downstream = await self.follow_up(event, result)
        if downstream is not None:
            await self._bus.publish(downstream)

        return result

    except Exception as exc:
        log.error("[%s] UtilityExecutor error: %s", self.name, exc, exc_info=True)
        return UtilityResult(status="error", winner=None, winner_score=0.0)
```

---

## Usage Example

```python
class AdrSummarySelector(UtilityExecutor):
    max_candidates = 4
    min_utility    = 0.6

    @property
    def criteria(self):
        return {
            "technical_accuracy": 0.50,
            "decision_clarity":   0.30,
            "brevity":            0.20,
        }

    async def retrieve(self, event):
        events = load_events("adr", hours=24)
        return {"events": events, "request": event.payload}

    async def generate_candidates(self, context):
        prompt = build_prompt(context["events"])
        responses = await asyncio.gather(*[
            llm.call(system="Write an ADR summary.", user=prompt)
            for _ in range(self.max_candidates)
        ])
        return list(responses)

    async def utility_score(self, artifact, criteria, context):
        # Isolated LLM call — no context from generate_candidates
        score_response = await llm.call(
            system="Score this ADR summary against the criteria.",
            user=f"Criteria: {criteria}\nArtifact: {artifact}",
        )
        scores = parse_scores(score_response)  # {"technical_accuracy": 0.9, ...}
        weighted = sum(criteria[k] * scores[k] for k in criteria)
        return UtilityCandidate(
            artifact=artifact,
            utility_score=weighted,
            criteria_scores=scores,
            rationale=score_response,
        )

    async def follow_up(self, event, result):
        if result.is_complete:
            return event.caused("adr.summary_selected", self.name, {
                "summary":  result.winner,
                "score":    result.winner_score,
            })
        return None
```

---

## Isolation Contract

`utility_score()` **must** be called with `(artifact, criteria, context)` only.
It must not receive the full `generate_candidates()` prompt, the prior candidates,
or any agent reasoning history. This mirrors `OutcomeExecutor.evaluate()`.

Rationale: scoring the same artifact with knowledge of the alternatives anchors the
score toward the field average rather than the artifact's absolute merit.

For LLM-based scoring, each `utility_score()` call is a **fresh LLM context**.
For rule-based scoring, the constraint applies trivially.

---

## Comparison with Existing Executors

| | `OutcomeExecutor` | `ReflexionExecutor` | `UtilityExecutor` |
|---|---|---|---|
| Number of artifacts | 1 (iteratively improved) | 1 (self-critiqued) | N (simultaneously generated) |
| Selection method | Rubric pass/fail | Confidence convergence | Utility ranking |
| Evaluation context | Isolated | Same as act() | Isolated per candidate |
| Conflicting goal handling | No | No | Yes — via weighted criteria |
| Iteration model | Iterative (converge) | Iterative (converge) | Single pass (select) |
| Extends | `RALFExecutor` | `RALFExecutor` | Standalone |
| Best for | Single correct answer | Progressive quality | Best of multiple alternatives |

---

## Multi-Criteria Weighting

The `criteria` dict carries both the dimension names and their weights. This means:

- Different executor subclasses can express different goal priorities without changing
  the scoring logic
- A `governance.policy_recommendation` event could propose new weights at runtime
  (the agent re-reads criteria from state rather than hardcoding)
- The per-criterion breakdown in `UtilityCandidate.criteria_scores` is dashboard-inspectable

---

## Design Decisions

1. **Standalone, not a RALF subclass** — RALF's `run()` loop is built around iterative
   convergence with confidence gating. UtilityExecutor is a single-pass generate-and-rank.
   Forcing it into `_post_act_hook()` would contort both patterns. Standalone is cleaner.

2. **`utility_score()` called per candidate, not once for the whole list** — this enforces
   the isolation contract and makes scoring parallelisable (callers may `asyncio.gather`).

3. **`criteria` as dict, not a rubric string** — the rubric string in `OutcomeExecutor` is
   passed to an LLM for a pass/fail verdict. UtilityExecutor criteria need to produce
   numeric weights, so a typed dict is the correct representation. Implementations can
   still pass the dict to an LLM formatted as a string.

4. **`below_threshold` is a distinct status from `no_candidates`** — callers may want to
   surface "all candidates generated but none good enough" differently from "generation
   returned nothing." Both are non-error, non-complete states.

5. **`max_candidates` drives generate, not iterate** — candidates are generated once, not
   accumulated across iterations. If quality is insufficient, the caller should re-run or
   fall back to `OutcomeExecutor`.

6. **`follow_up()` not emitted on `below_threshold`** — matches `OutcomeExecutor` convention
   where `follow_up()` is only called on complete. Callers wanting to handle `below_threshold`
   should check `result.status` after `run()` returns.

---

## Govkit Integration Opportunity

`ConflictResolutionExecutor` currently evaluates a single synthesis (pass/fail).
A `UtilityExecutor`-backed variant could generate N synthesis candidates and select the one
that best satisfies both competing positions by utility score. This would be a
`ConflictResolutionUtilityExecutor` in `agentic_govkit/loops/` — a govkit concern,
not a loopkit change.

---

## Public API Addition

```python
# agentic_loopkit/__init__.py
from .loops.utility import UtilityExecutor, UtilityResult, UtilityCandidate
```

---

## Test Targets

`tests/loops/test_utility.py`

- Happy path: generate 3 candidates, score all, winner selected
- `min_utility` gate: all candidates below threshold → `below_threshold`
- Empty `generate_candidates()` → `no_candidates`
- `utility_score()` exception → `error` status
- `follow_up()` called on `complete`, not on `below_threshold` or error
- Candidates sorted descending by `utility_score` in `all_candidates`
- Isolation: `utility_score()` called with `(artifact, criteria, context)` only
- `criteria` weights reflected in `criteria_scores` keys

---

## Estimated Scope

~100 lines implementation, ~90 lines tests.
