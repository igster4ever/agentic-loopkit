# SkillOptExecutor — Design Brief

_Date: 2026-06-11_
_Status: Draft — v0.1_
_Source: SkillOpt: Executive Strategy for Self-Evolving Agent Skills (Microsoft / SJTU / Tongji / Fudan, May 2026) — arXiv:2605.23904_
_Informs: loopkit v5 — self-optimising skill layer_

---

## North Star

Agent skills (SKILL.md, system prompt procedures, behavioural policies) are currently hand-crafted
and hand-maintained. SkillOpt proves empirically that treating the skill document as the *trainable
external state* of a frozen agent — optimised via a bounded, validation-gated text-space update
loop — outperforms human-written, one-shot LLM-generated, and trajectory-distilled baselines on
all 52 evaluated (model, benchmark, harness) cells.

This document specifies a `SkillOptExecutor` module for agentic-loopkit that encodes SkillOpt's
core loop as a reusable executor, so any loopkit user can plug in skill self-improvement alongside
their existing OODA/RALF agents.

---

## SkillOpt Key Mechanisms

Five components make SkillOpt work (paper §3; ablation Table 3):

| Mechanism | What it does | Removing it costs |
|---|---|---|
| **Validation gate** | Accepts candidate skill only if selection-split score > current | ~4.6 pts (SpreadsheetBench) |
| **Rejected-edit buffer** | Records failed edit proposals as negative feedback for later steps | ~4.4 pts |
| **Bounded edit budget `L_t`** | Caps edits per step (learning-rate analogue) | ~3.5 pts (vs no LR) |
| **Slow/meta update** | Cross-epoch protected guidance block; fast edits cannot overwrite it | ~22.5 pts (largest ablation) |
| **Minibatch reflection** | Batches trajectories before drawing skill-edit conclusions | prevents anecdotal fixes |

The deployed skill is compact (median ~920 tokens, 1–4 accepted edits total) and portable across
models, harnesses, and related benchmarks without re-optimisation.

---

## Mapping to loopkit Concepts

| SkillOpt concept | loopkit equivalent |
|---|---|
| Skill document `s` | Any text artifact injected into agent context (SKILL.md, system prompt fragment, behavioural policy) |
| Target model `M` (frozen) | The agent using the skill — `AgentBase` or any executor |
| Optimizer model `O` | A second, potentially stronger, LLM instance — not the task agent |
| Rollout batch | `retrieve()` — execution traces from `EventStore` JSONL |
| Validation gate | `evaluate()` from `OutcomeExecutor` — skill accepted only if it beats current score |
| Rejected-edit buffer | `_step_buffer: list[RejectedEdit]` — analogous to `ReflexionExecutor` critique history |
| Slow/meta update | Protected `<!-- SLOW_UPDATE_START/END -->` section in the skill artifact |
| `best_skill.md` | `SkillOptExecutor.best_skill` property — the exported artifact |

---

## Proposed Interface

```python
# agentic_loopkit/loops/skillopt.py

from dataclasses import dataclass, field
from abc import abstractmethod
from .ralf import RALFExecutor, RALFResult


@dataclass
class SkillEdit:
    op: str          # "append" | "insert_after" | "replace" | "delete"
    target: str      # exact text to locate (for insert_after/replace/delete)
    content: str     # new content (empty for delete)
    source_type: str # "failure" | "success"
    support_count: int = 1


@dataclass
class RejectedEdit:
    edit: SkillEdit
    failure_patterns: list[str]  # why it was rejected
    epoch: int


@dataclass
class SkillOptResult:
    skill: str                      # current best skill text
    accepted_edits: list[SkillEdit]
    rejected_edits: list[SkillEdit]
    selection_score: float
    epoch: int
    status: str  # "improved" | "unchanged" | "error"


class SkillOptExecutor(RALFExecutor):
    """
    Bounded, validation-gated skill optimiser.

    Override retrieve(), score(), and reflect() to adapt to any skill domain.
    The core loop (bounded edits → validation gate → rejected-edit buffer →
    slow/meta update) is provided by this base class.
    """

    max_iterations: int = 4         # epochs
    edit_budget: int = 4            # L_t — max edits accepted per epoch
    reflection_minibatch_size: int = 8

    def __init__(self, name, bus, initial_skill: str):
        super().__init__(name, bus)
        self._current_skill = initial_skill
        self._best_skill = initial_skill
        self._best_score: float = 0.0
        self._step_buffer: list[RejectedEdit] = []
        self._meta_skill: str = ""  # optimizer-side only; never deployed

    @property
    def best_skill(self) -> str:
        return self._best_skill

    @abstractmethod
    async def retrieve(self, event) -> dict:
        """
        Load execution traces as rollout evidence.
        Typically: read EventStore JSONL filtered by time window and agent.
        Return: {"train": [...], "selection": [...]} — trajectory splits.
        """

    @abstractmethod
    async def score(self, skill: str, trajectories: list) -> float:
        """
        Score skill on the selection split.
        Deterministic — no LLM. Use agent confidence, goal hit-rate,
        or task-specific metrics. Must be comparable across epochs.
        """

    @abstractmethod
    async def reflect(
        self,
        skill: str,
        failures: list,
        successes: list,
        rejected_buffer: list["RejectedEdit"],
        meta_skill: str,
    ) -> list[SkillEdit]:
        """
        Optimizer model pass.
        Propose bounded add/delete/replace edits from trajectory evidence.
        Receives: current skill, failure minibatch, success minibatch,
        rejected-edit buffer (negative feedback), and optimizer meta-skill.
        Returns: at most self.edit_budget proposals.
        IMPORTANT: protect <!-- SLOW_UPDATE_START --> content — never propose
        edits inside the protected section.
        """

    async def act(self, context: dict, prior_result) -> RALFResult:
        """One optimisation epoch."""
        train = context["train"]
        selection = context["selection"]

        failures = [t for t in train if t.get("outcome") == "failure"]
        successes = [t for t in train if t.get("outcome") == "success"]

        proposals = await self.reflect(
            self._current_skill, failures, successes,
            self._step_buffer, self._meta_skill,
        )
        proposals = proposals[:self.edit_budget]

        candidate = _apply_edits(self._current_skill, proposals)
        candidate_score = await self.score(candidate, selection)

        if candidate_score > self._best_score:
            self._best_skill = candidate
            self._best_score = candidate_score
            self._current_skill = candidate
            accepted = proposals
            rejected = []
        else:
            accepted = []
            rejected = proposals
            for p in proposals:
                self._step_buffer.append(
                    RejectedEdit(edit=p, failure_patterns=[], epoch=context.get("epoch", 0))
                )

        return RALFResult(
            answer=SkillOptResult(
                skill=self._current_skill,
                accepted_edits=accepted,
                rejected_edits=rejected,
                selection_score=candidate_score,
                epoch=context.get("epoch", 0),
                status="improved" if accepted else "unchanged",
            ),
            confidence=0.9 if accepted else 0.6,
            status="complete" if context.get("epoch", 0) >= self.max_iterations else "in_progress",
        )

    async def learn(self, event, result):
        """Persist best skill to state after every epoch."""
        state = await self.load_state()
        state.procedural["best_skill"] = self._best_skill
        state.procedural["best_score"] = self._best_score
        await self.save_state(state)
```

---

## Slow/Meta Update Hook

The epoch-wise slow update writes longitudinal guidance into the skill's protected block.
This is optimizer-side only — the deployed `best_skill` artifact carries the protected
block but the optimizer writes it; the task agent reads it but never modifies it.

```python
# Extension of SkillOptExecutor

    async def slow_update(
        self,
        prev_epoch_skill: str,
        curr_epoch_skill: str,
        longitudinal_evidence: list,
    ) -> str:
        """
        Write protected guidance block into current skill.
        Called once per epoch boundary (iteration N-1 → N).
        Must return the updated skill text with SLOW_UPDATE markers preserved.
        Default: no-op (returns skill unchanged).
        Override with an LLM call that writes the protected block.
        """
        return curr_epoch_skill

    async def update_meta_skill(
        self,
        prev_epoch_skill: str,
        curr_epoch_skill: str,
    ) -> str:
        """
        Update optimizer-side meta skill (teacher-facing; never deployed).
        Summarises which edit patterns helped, which were rejected,
        which failures persisted. Used to prime reflect() next epoch.
        Default: no-op.
        """
        return self._meta_skill
```

---

## Integration Pattern

```python
from agentic_loopkit import EventBus, Event
from agentic_loopkit.loops.skillopt import SkillOptExecutor, SkillEdit
from pathlib import Path

class CompassSkillOptExecutor(SkillOptExecutor):
    """
    Optimises SKILL.md using compass session history snapshots as trajectories.
    """

    async def retrieve(self, event) -> dict:
        history_dir = Path("~/.claude/loop/compass/history/").expanduser()
        snapshots = sorted(history_dir.glob("*.md"))[-20:]  # last 20 sessions
        train, selection = snapshots[:-4], snapshots[-4:]   # 4:1 split
        return {
            "train": [_parse_snapshot(p) for p in train],
            "selection": [_parse_snapshot(p) for p in selection],
        }

    async def score(self, skill: str, trajectories: list) -> float:
        hit_rates = [t["hit_rate"] for t in trajectories if t.get("hit_rate") is not None]
        return sum(hit_rates) / len(hit_rates) if hit_rates else 0.0

    async def reflect(self, skill, failures, successes, rejected_buffer, meta_skill):
        # Call LLM with analyst_error + analyst_success prompts (see SkillOpt §C.2)
        # Return list[SkillEdit] proposals
        ...
```

---

## Prompt Contract Reference

SkillOpt Appendix C.2 specifies six prompt contracts used by the optimizer. All are
available in the paper and translate directly to Python string templates:

| Prompt | Purpose | Maps to |
|---|---|---|
| `analyst_error.md` | Identify common failure patterns across minibatch | `reflect()` failures pass |
| `analyst_success.md` | Identify generalizable success patterns | `reflect()` successes pass |
| `merge_failure.md` | Consolidate failure patch proposals (dedup + priority) | Pre-apply merge step |
| `merge_success.md` | Consolidate success patch proposals | Pre-apply merge step |
| `merge_final.md` | Final merge: failure-priority combined patch | `reflect()` return value |
| `ranking.md` | Rank edits by systematic impact, complementarity, generality | Edit budget clip |
| `slow_update.md` | Cross-epoch longitudinal guidance (protected block) | `slow_update()` |
| `meta_skill.md` | Optimizer memory (teacher-facing only; never deployed) | `update_meta_skill()` |

---

## Where It Lives

```
agentic_loopkit/loops/skillopt.py     # SkillOptExecutor + SkillEdit + RejectedEdit + SkillOptResult
tests/loops/test_skillopt.py          # unit tests
```

Export via `agentic_loopkit/__init__.py`:
```python
from .loops.skillopt import SkillOptExecutor, SkillEdit, SkillOptResult
```

---

## Key Design Rules (inherited from loopkit)

- **LLM belongs in `reflect()` and optional `slow_update()`/`update_meta_skill()` only** — `score()` is deterministic, no LLM
- **Loops are bounded** — `max_iterations` hard cap (default 4, matching SkillOpt default epochs)
- **Validation gate is strict** — ties are rejected (paper §3.5: "strictly greater than")
- **Deployed artifact stays compact** — `best_skill` is the only output; `_meta_skill` and `_step_buffer` are optimizer-internal
- **Backwards-compatible with existing executors** — `SkillOptExecutor` extends `RALFExecutor`; all existing loopkit patterns compose

---

## Test Targets

`tests/loops/test_skillopt.py`:

- `retrieve()` returns train/selection split
- `score()` is deterministic — same inputs → same float
- `reflect()` proposals bounded by `edit_budget`
- Accepted edit: `_best_skill` updated, `_step_buffer` unchanged
- Rejected edit: `_best_skill` unchanged, `RejectedEdit` appended to buffer
- Buffer negative feedback reaches subsequent `reflect()` calls
- `learn()` persists `best_skill` to `state.procedural`
- `slow_update()` no-op by default; override is called at epoch boundary
- `max_iterations` cap exits with `status="complete"`

---

## Estimated Scope

- `SkillOptExecutor` base class: ~120 lines implementation
- `_apply_edits()` helper (append/insert_after/replace/delete): ~40 lines
- Tests: ~80 lines
- Prompt contracts (string templates): ~200 lines
- Total: ~440 lines implementation + tests

**Prerequisite:** P27 (slow-update protected block in compass SKILL.md) should ship first
so there is a concrete skill artifact to validate the executor against.

---

## References

- Yang et al., _SkillOpt: Executive Strategy for Self-Evolving Agent Skills_, arXiv:2605.23904, May 2026
- Code: https://aka.ms/SkillOpt
