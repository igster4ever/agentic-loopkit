"""
agentic_loopkit/loops/skillopt.py — SkillOptExecutor: bounded, validation-gated skill optimiser.

Encodes the SkillOpt loop (arXiv:2605.23904) as a reusable RALFExecutor subclass.
The skill document (SKILL.md, system-prompt fragment, behavioural policy) is treated
as trainable external state; the frozen task agent is never modified.

Five mechanisms from the paper are preserved:
  Validation gate          — accepts edits only when selection-split score strictly improves
  Rejected-edit buffer     — negative feedback fed to subsequent reflect() calls
  Bounded edit budget L_t  — edit_budget cap per epoch
  Slow/meta update hooks   — optional protected block + optimizer memory (no-op by default)
  Minibatch reflection     — reflect() receives train split, not single trajectories

LLM placement: reflect() only (optimizer model). score() is deterministic — no LLM.
"""

from __future__ import annotations

import re
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from .ralf import RALFExecutor, RALFResult


@dataclass
class SkillEdit:
    op: str           # "append" | "insert_after" | "replace" | "delete"
    target: str       # exact text to locate (insert_after/replace/delete)
    content: str      # new content ("" for delete)
    source_type: str  # "failure" | "success"
    support_count: int = 1


@dataclass
class RejectedEdit:
    edit: SkillEdit
    failure_patterns: list[str]
    epoch: int


@dataclass
class SkillOptResult:
    skill: str
    accepted_edits: list[SkillEdit]
    rejected_edits: list[SkillEdit]
    selection_score: float
    epoch: int
    status: str  # "improved" | "unchanged" | "error"


_SLOW_START = "<!-- SLOW_UPDATE_START -->"
_SLOW_END = "<!-- SLOW_UPDATE_END -->"


def _apply_edits(skill: str, edits: list[SkillEdit]) -> str:
    """Apply a list of SkillEdit operations to a skill document in order."""
    result = skill
    for edit in edits:
        if edit.op == "append":
            result = result.rstrip("\n") + "\n\n" + edit.content
        elif edit.op == "insert_after":
            idx = result.find(edit.target)
            if idx != -1:
                insert_at = idx + len(edit.target)
                result = result[:insert_at] + "\n" + edit.content + result[insert_at:]
        elif edit.op == "replace":
            if edit.target in result:
                result = result.replace(edit.target, edit.content, 1)
        elif edit.op == "delete":
            result = result.replace(edit.target, "", 1)
    return result


def _is_protected(edit: SkillEdit, skill: str) -> bool:
    """Return True if the edit target falls inside a SLOW_UPDATE protected block."""
    start = skill.find(_SLOW_START)
    end = skill.find(_SLOW_END)
    if start == -1 or end == -1:
        return False
    if edit.op == "append":
        return False
    idx = skill.find(edit.target)
    if idx == -1:
        return False
    return start < idx < end


class SkillOptExecutor(RALFExecutor):
    """
    Bounded, validation-gated skill optimiser.

    Subclasses must implement:
      retrieve(event)     → dict with "train" and "selection" trajectory lists
      score(skill, traj)  → float (deterministic — no LLM)
      reflect(...)        → list[SkillEdit] (primary LLM phase — optimizer model)

    Optionally override:
      slow_update(...)         → write protected guidance block once per epoch
      update_meta_skill(...)   → update optimizer-side meta skill (never deployed)
    """

    max_iterations: int = 4
    edit_budget: int = 4
    reflection_minibatch_size: int = 8

    def __init__(self, name: str, bus, initial_skill: str) -> None:
        super().__init__(name, bus)
        self._current_skill = initial_skill
        self._best_skill = initial_skill
        self._best_score: float = 0.0
        self._step_buffer: list[RejectedEdit] = []
        self._meta_skill: str = ""
        self._epoch: int = 0

    @property
    def best_skill(self) -> str:
        return self._best_skill

    @abstractmethod
    async def retrieve(self, event) -> dict:
        """
        Load execution traces as rollout evidence.
        Return: {"train": [...], "selection": [...]} — trajectory splits.
        """

    @abstractmethod
    async def score(self, skill: str, trajectories: list) -> float:
        """
        Score skill on the selection split. Deterministic — no LLM.
        Must be comparable across epochs.
        """

    @abstractmethod
    async def reflect(
        self,
        skill: str,
        failures: list,
        successes: list,
        rejected_buffer: list[RejectedEdit],
        meta_skill: str,
    ) -> list[SkillEdit]:
        """
        Optimizer LLM pass. Propose bounded edits from trajectory evidence.
        Must return at most self.edit_budget proposals.
        Must NOT propose edits inside <!-- SLOW_UPDATE_START --> ... <!-- SLOW_UPDATE_END -->.
        """

    async def slow_update(
        self,
        prev_epoch_skill: str,
        curr_epoch_skill: str,
        longitudinal_evidence: list,
    ) -> str:
        """
        Write protected guidance block into current skill (once per epoch boundary).
        Default: no-op. Override with LLM call that writes the protected block.
        """
        return curr_epoch_skill

    async def update_meta_skill(
        self,
        prev_epoch_skill: str,
        curr_epoch_skill: str,
    ) -> str:
        """
        Update optimizer-side meta skill (never deployed; primes reflect() next epoch).
        Default: no-op.
        """
        return self._meta_skill

    async def act(self, context: dict, prior_result: Optional[RALFResult]) -> RALFResult:
        """One optimisation epoch."""
        self._epoch += 1
        train = context.get("train", [])
        selection = context.get("selection", [])

        failures = [t for t in train if t.get("outcome") == "failure"]
        successes = [t for t in train if t.get("outcome") == "success"]

        proposals = await self.reflect(
            self._current_skill,
            failures,
            successes,
            list(self._step_buffer),
            self._meta_skill,
        )

        # Strip proposals that target the protected block
        proposals = [p for p in proposals if not _is_protected(p, self._current_skill)]
        proposals = proposals[: self.edit_budget]

        candidate = _apply_edits(self._current_skill, proposals)
        candidate_score = await self.score(candidate, selection)

        prev_skill = self._current_skill

        if candidate_score > self._best_score:
            self._best_skill = candidate
            self._best_score = candidate_score
            self._current_skill = candidate
            accepted = proposals
            rejected_this_epoch: list[SkillEdit] = []
            opt_status = "improved"
        else:
            accepted = []
            rejected_this_epoch = proposals
            for p in proposals:
                self._step_buffer.append(
                    RejectedEdit(edit=p, failure_patterns=[], epoch=self._epoch)
                )
            opt_status = "unchanged"

        # Slow/meta update at epoch boundary
        self._current_skill = await self.slow_update(
            prev_skill, self._current_skill, train
        )
        self._meta_skill = await self.update_meta_skill(prev_skill, self._current_skill)

        is_final = self._epoch >= self.max_iterations

        opt_result = SkillOptResult(
            skill=self._current_skill,
            accepted_edits=accepted,
            rejected_edits=rejected_this_epoch,
            selection_score=candidate_score,
            epoch=self._epoch,
            status=opt_status,
        )
        return RALFResult(
            status="complete" if is_final else "in_progress",
            step_summary=f"epoch {self._epoch}: {opt_status} (score={candidate_score:.3f})",
            output=opt_result,
            confidence=0.9 if accepted else 0.6,
        )

    async def learn(self, event, result: RALFResult) -> None:
        """
        Persist best skill after every epoch.
        Default: no-op — state is maintained in _best_skill / _best_score.
        Override to wire durable storage (e.g. write to state.procedural via memorykit).
        """
