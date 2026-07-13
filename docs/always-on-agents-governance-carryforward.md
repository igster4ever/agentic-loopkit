# Always-On Agents Governance Carryforward — P64 (agentic-loopkit)

**P-code:** P64 (a/b/c below)
**Status:** Design doc only — nothing implemented yet.
**Source:** Ding, Nannapaneni, Liu, Zhang — "Always-On Agents: A Survey of Persistent Memory, State, and Governance in LLM Agents" (arXiv:2606.30306v1, 29 Jun 2026).
**Master doc:** `~/.claude/skills/compass/docs/always-on-agents-governance-design.md` (cross-project priority table and background — this doc is the loopkit-specific implementation plan referenced from there).

---

## P64a — `revoke()` on `FrontierSelector`/`BranchScore`

**Target:** `agentic_loopkit/loops/frontier.py`
**Dependency:** none — `frontier.py` (P62a) already ships `BranchScore`, `FrontierCandidate`, `FrontierSelector`

**Design.** The paper's falsifiable controlled-compounding criterion: *"adaptation is net-positive only when the system can identify, de-authorize, and revert the specific state update that later caused a regression."* `FrontierSelector` already tracks `utility_history` and `times_selected` per candidate and computes a weighted `utility + productivity + novelty` score — but a candidate that was selected, later shown to cause a regression, has no way to be marked as such; it can only fade naturally through the existing productivity estimator reacting to *future* utility history. That's down-ranking, not de-authorization — the paper's criterion specifically requires the latter.

Add `FrontierSelector.revoke(candidate_id: str, reason: str)`:
- Marks the candidate ineligible for future selection rounds (either removed from the pool or its score permanently floored — removal is cleaner and avoids an ever-growing dead-candidate list being re-scored every round for no purpose).
- Retains the `reason` string alongside the revocation (compass's own P61d finding this session was that keeping the *why* next to a removal, not just the fact of removal, is what makes the record useful to a later session — same principle applies here for debugging why a branch was killed).
- Does not retroactively alter any already-made selection decision — this is a forward-looking exclusion, matching the paper's own scope guard on `frontier.py` (a scoring primitive, not an automatic branch-forking/rollback pipeline).

**Effort:** 0.5–1 session — a new method + a small ineligibility-tracking field on the selector, no new subsystem.

---

## P64b — Self-confirmation risk in `ReflexionExecutor.critique()`

**Target:** `agentic_loopkit/loops/reflexion.py`
**Dependency:** none structurally — this is a documentation/design-guidance gap first, a possible helper second

**Confirmed this session, not hypothetical:** `ReflexionExecutor`'s own docstring explicitly states *"LLM calls are appropriate in BOTH `act()` and `critique()`"* — `act()` drafts, `critique()` evaluates and optionally revises, and the framework does not distinguish or warn about the case where `act()` and `critique()` are backed by the same model in the same context. That is precisely the paper's named **"self-confirmation trap"**: an agent judging its own trajectory writes a self-consistent-but-wrong critique/lesson into durable state because nothing decouples the writer from the judge.

This is distinct from what `calibration.py`'s `CalibrationRecord` (P60) already covers — `CalibrationRecord` scores the gap between a *stated confidence* and a *verified outcome* for `OutcomeExecutor` specifically, which is a calibration check, not a correctness-of-judgment check. A `ReflexionExecutor` subclass whose `critique()` uses the same model as `act()` could be perfectly calibrated (confident when right, unconfident when wrong, on average) while still being systematically wrong in a self-consistent way that pure calibration scoring wouldn't catch, because there's no independent second opinion in the loop at all.

**Design.** Two independent, non-exclusive additions:
1. **Documentation fix (near-zero cost, do regardless of anything else):** the `reflexion.py` module docstring should explicitly flag the self-confirmation risk next to the line permitting same-model `act()`/`critique()`, and recommend a heterogeneous critique (different model, different prompt framing, or at minimum a structurally distinct evaluation rubric rather than "does this look right to the same context that produced it") whenever `critique()`'s output is destined to be persisted as a durable lesson rather than used only within the current bounded loop.
2. **Optional helper (larger, only if a concrete consumer needs it):** a small `heterogeneous_critique` composition helper that wraps a `ReflexionExecutor` subclass's `critique()` to force a different model/prompt path — mirroring compass's own `reconcile: true` pre-write check pattern (a structurally separate check gates the write, rather than trusting the same process that produced the candidate). Only build this once a real `ReflexionExecutor` subclass exists that writes to genuinely durable (cross-session) memory rather than within-task scratch state — most current subclasses may only use `critique()` within a single bounded loop, in which case the durable-memory risk doesn't apply and this is lower priority than the documentation fix.

**Effort:** ~0 for the documentation fix; 0.5–1 session for the optional helper, gated on identifying a concrete executor that actually persists a self-judged lesson long-term.

---

## P64c — Skill-drift re-validation: already covered by `SelfHarnessExecutor` (no build needed)

**Target:** none — confirmation only, read `agentic_loopkit/loops/self_harness.py` and `skillopt.py`
**Design.** The paper names "skill drift" (a skill correct when written becomes wrong later) and cites typed-contract/write-time-verification-gate mechanisms (SkillOps, Skill-Pro, SkillNB) as the current best mitigations. **This session found `SelfHarnessExecutor` already implements exactly this pattern**: it wires `SkillOptExecutor` inside an `OutcomeExecutor` outer loop and gates every candidate skill edit through `AgentTestHarness.regression_gate(baseline, candidate)` — a deterministic (no-LLM), non-regressive acceptance rule (`∆in ≥ 0 AND ∆ho ≥ 0 AND max(∆in, ∆ho) > 0`, cited to arXiv:2606.09498 §3.4) before a skill edit is accepted. This is a write-time verification gate in the paper's own taxonomy, already shipped.

The only open question is one of **coverage, not design**: is `SelfHarnessExecutor` the mandatory path for all skill edits in practice, or can `SkillOptExecutor` be used standalone (bypassing the regression gate) by a caller who doesn't wire it through `SelfHarnessExecutor`? If `SkillOptExecutor` has callers that skip the harness, those edits have no re-validation gate at all. This is a confirmation task (grep for `SkillOptExecutor(` call sites and check whether any construct it outside a `SelfHarnessExecutor` wrapper), not a build — same shape as compass's own P61d (verify existing write path already does the right thing).

**Effort:** 0.2–0.3 session to audit call sites; likely a no-op if `SelfHarnessExecutor` is the only production path, in which case this closes as "confirmed already covered," not "shipped new code."

---

## Sequencing note

P64c is cheapest (confirmation only, may close as a no-op like compass's P61d) — do it first, since it may remove the need for any further work here entirely. P64a is the next cleanest (extends an existing, purpose-built module). P64b's documentation half should happen regardless of the other two; its optional-helper half depends on identifying a concrete consumer, which may only become clear once more `ReflexionExecutor` subclasses exist in practice.
