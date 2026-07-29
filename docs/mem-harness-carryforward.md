# MemoHarness Carryforward — P71 (agentic-loopkit)

**P-code:** P71
**Status:** Design doc only — nothing implemented yet. Audit step required before implementation (see below).
**Source:** Huang et al., "MemoHarness: Agent Harnesses That Learn from Experience" (arXiv:2607.14159v1, 14 Jul 2026).
**Master doc:** `~/.claude/skills/compass/docs/mem-harness-carryforward.md` (cross-project priority table and background — this doc is the loopkit-specific implementation plan referenced from there).

---

## Why loopkit specifically

MemoHarness's whole point of difference from ordinary reflection/experience-writing (Reflexion, Self-Refine, etc., discussed in the paper's own Related Work §F.1) is that it closes the loop: distilled patterns aren't just detected and logged, they're retrieved and used to **adapt the next case's behaviour**, without a feedback signal or retraining (§2.6, "Test-Time Case Adaptation Without Feedback").

Confirmed this session: loopkit already has the detection half of this, and arguably a *more* structured version than the paper's own six-dimension tag. `FailurePatternAgent` (`agentic_loopkit/agents/failure_pattern.py:65`) tags failures with a `FailureSignature` — `(terminal_cause, causal_status, agent_mechanism)` — via a deterministic map from known event types (lines 50–59, e.g. `governance.halt → ("halt_enforced", "halted")`), clusters matching signatures, and emits `system.failure_pattern_detected` on the event bus. `CalibrationRecord` (`loops/calibration.py`, P60) separately pairs an executor's self-predicted confidence against `evaluate()`'s actual verdict.

What's missing is the return path: nothing in the executor set (`react.py`, `plan.py`, `reflexion.py`, `outcome.py`, `utility.py`, `skillopt.py`, `self_harness.py`, `contract.py`, `frontier.py`) was confirmed this session to *subscribe* to `system.failure_pattern_detected` and adapt its own next-iteration configuration in response. The clustering exists; the adaptation consuming it is unconfirmed and most likely absent.

---

## P71 — Wire `failure_pattern_detected` into executor adaptation

**Target:** most likely `agentic_loopkit/loops/self_harness.py` (the executor whose stated purpose — self-tuning harness behaviour — most directly matches MemoHarness's Phase B adaptation role); `agentic_loopkit/bus.py` for the subscription mechanics if a push-style subscribe API doesn't already exist alongside `publish()`/`publish_many()`
**Dependency:** none structurally — `agents/failure_pattern.py`'s `FailureSignature`/`FailurePatternAgent` and `bus.py`'s `EventBus` (`publish`, `load_headlines`/`expand_event`) are both already shipped and sufficient to build on

### Step 0 — audit before building (do not skip)

This session did not confirm whether `self_harness.py` (or any other executor) already reads `system.failure_pattern_detected` events. Per the same discipline already established in this repo's own always-on-agents-governance-carryforward.md (items P64b/P64c were explicitly left as "audit first, do not implement against an assumed target"): before writing any adaptation code, check —

1. Does `self_harness.py` (or `skillopt.py`) already subscribe to any `system.*` event from the bus, and if so, what's the subscription mechanism (`EventBus` doesn't expose an explicit `subscribe()` in this session's read — confirm whether one exists elsewhere, e.g. a consumer-loop pattern reading `load_headlines`/`expand_event`)?
2. Is there already a config-driven adaptation path in any executor that changes its behaviour based on accumulated diagnostic state (as opposed to a fixed prompt/config per run)?

If either is already true, this item's scope shrinks to "extend the existing consumption to also read `FailureSignature` clusters" rather than "build a new subscription path from scratch" — closer in shape to the always-on-agents doc's P64c ("likely closes as confirmed-already-covered").

### Design (pending Step 0's outcome)

Following MemoHarness §2.6's test-time adaptation shape as closely as loopkit's architecture allows:

- On each new iteration, the adapting executor queries recent `FailureSignature` clusters (via `load_headlines`/`expand_event` over the relevant event stream, or a direct read of whatever store `FailurePatternAgent` persists clusters to) filtered to signatures relevant to the current task/mechanism.
- If a cluster's `agent_mechanism` matches the executor's own identity and `count` exceeds a small threshold, the executor adapts a **bounded, pre-declared** set of its own next-iteration parameters (mirrors MemoHarness's harness bundle being edited along fixed dimensions, not arbitrary code mutation) — e.g. widening a retry budget, switching a tool-call ordering, or tightening an output validator, depending on what `terminal_cause` indicates.
- This adaptation happens **once per case/iteration, without a feedback loop** — matching the paper's explicit "no test-time labels, no gradient updates, no additional search rounds" constraint. It is not the same mechanism as `SkillOptExecutor`'s search-time optimization (P53-style holdout-gated skill edits) — that's the loopkit analogue of MemoHarness's Phase A (training-time search); this item is the analogue of Phase B (per-case adaptation of an already-settled configuration).
- Retain the causal record: every adaptation should itself emit an event (e.g. `system.harness_adapted`) carrying which `FailureSignature` triggered it and what changed — this is the same "keep the why, not just the fact of the change" discipline already established for `decay_history.jsonl` (compass) and proposed for `FrontierSelector.revoke()` (P64a, always-on-agents-governance-carryforward.md) — do not adapt silently.

### Explicitly out of scope for this item

- Any new failure-detection mechanism — `FailurePatternAgent`/`FailureSignature` already cover this well; this item is purely about consumption.
- LLM-judged adaptation decisions — matching the paper's own Appendix B choice ("practical heuristics, not learned controllers") and this repo's existing no-LLM-judge discipline for deterministic checks, the mapping from `FailureSignature` to a bounded parameter change should be a deterministic lookup, not a model call.
