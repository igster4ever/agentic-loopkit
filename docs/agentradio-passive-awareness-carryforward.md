# AgentRadio passive-awareness carryforward (agentic-loopkit)

**Status:** Step 0 audit complete (2026-08-13) — verdict: does not apply to `UtilityExecutor` as it exists today. Not implementing. See "Step 0 — audit findings" below; the original open questions are kept for the record.
**Source:** Ren et al., "AgentRadio: Passive Awareness for Long-Horizon Multi-Agent Collaboration" (arXiv:2607.28430, Jul 2026).
**Master doc:** `~/.claude/projects/-Users-ismith--claude-skills-compass/memory/project_agentradio_findings.md` (paper summary and cross-project priority notes — this doc is the loopkit-specific grounding referenced from there).

---

## Why loopkit specifically, and why not Claude Code's Workflow tool

The paper's finding: four Claude Code agents dividing a codebase-comprehension task, coordinated by three primitives (`create_thread`, `send_message`, `wait_for_mention`), beat the strongest single agent by 29.8 points. Controlled ablation isolates the gain to one variable — whether the "listen for teammates" step **blocks** foreground work or runs as a **non-blocking background watcher** that folds mentions in at the next step boundary. The paper explicitly names "parallel but isolated" fan-out (cites Anthropic's own research-system blog post: subagents run in parallel but the orchestrator waits for the whole batch and can't steer mid-task) as one of the patterns it beats.

Checked this session: **Claude Code's own `Workflow` orchestration tool is not part of this repo at all.** `rg -ni workflow` across `agentic-loopkit` turns up nothing related (only an unrelated "business workflow ID" mention in `CLAUDE.md:137`). If the target is really Claude Code's `Workflow.parallel()`/`pipeline()` primitives, this repo doesn't control that surface — the idea would need to go to Claude Code itself, not here.

What *is* in scope here: agentic-loopkit's own multi-agent/multi-candidate coordination, which has the identical structural weakness.

## What already exists — confirmed this session

**The one real concurrent fan-out in the codebase today is `UtilityExecutor.generate_candidates`** (`agentic_loopkit/loops/utility.py:33-35`): `asyncio.gather(*[llm.call(...) for _ in range(self.max_candidates)])` — N candidates generated in parallel, fully isolated from each other, ranked only after all finish. This is structurally identical to the paper's "parallel but isolated" baseline that passive awareness beats by ~11 points on top of division-of-labor + negotiation.

Everything else checked is single-agent, not multi-agent: `AgentBase` (`agents/base.py`, 261 lines) is a single reactive OODA agent handling one event at a time. `RALFExecutor` (`loops/ralf.py`) is a single bounded task loop, one LLM call per `act()`. `CompassSkillOptExecutor` (`integrations/compass_skillopt.py`, 312 lines) shells out to the compass CLI sequentially (`_run_compass_cli`, line 24) — no concurrency, no other agents.

**The substrate for a fix already exists and doesn't need building from scratch.** `agentic_loopkit/bus.py` (292 lines, `EventBus`) + `agentic_loopkit/events/router.py` (`EventRouter`, an async fan-out router — "publish one Event → call all matching subscribers", line 26) + `agentic_loopkit/events/store.py` (append-only JSONL event log) + `agentic_loopkit/events/headlines.py` (compacted views). Documented as the "Cheap Kafka" pattern (`docs/architecture.md:5-6`). Agents already publish `Event`s to a shared bus; subscribers pick them up via `router.subscribe(stream, fn)` with no blocking wait built into the primitive. **This is functionally AgentRadio's non-blocking `send_message` already** — what's missing is a `UtilityExecutor`-side consumer that checks the bus between candidate-generation steps (the `wait_for_mention`-as-background-watcher half), not a new messaging layer.

## Candidate design (pending Step 0's outcome — do not build yet)

- `UtilityExecutor.generate_candidates` publishes an event per candidate as it completes a meaningful sub-step (not just at the end), e.g. `utility.candidate_progress` carrying whatever intermediate finding the candidate has made.
- Each in-flight candidate, between its own `llm.call()` steps, does a **non-blocking** check of the bus for `utility.candidate_progress` events from sibling candidates in the same `generate_candidates` batch (mirrors the paper's "checks between its own work steps, never stops to listen").
- If a sibling's finding is relevant (e.g. rules out an approach the current candidate is still pursuing), fold it into the current candidate's next step rather than continuing blind — this is the exact mechanism the paper's MinIO case study shows recovering a rubric that dies unvoiced under blocking receive (Figure 6).
- Ranking after `asyncio.gather` resolves stays as-is; the change is entirely about what happens *during* generation, not the selection step.

## Step 0 — audit findings (2026-08-13)

1. **`EventRouter.subscribe` is callback-on-publish only** (`agentic_loopkit/events/router.py`) — there is no cursor/poll API (no `get_new_since()`, no queue drain method). `publish()` directly `await`s every matching subscriber inline, on whichever coroutine called `publish()`. That is a meaningfully different — and more hazardous — shape than the paper's non-blocking poll: it is the *publisher's* call stack invoking the *subscriber's* registered function, not the subscriber checking for messages at a moment of its own choosing. Confirms the original suspicion: this is closer to interrupt-on-delivery (HANDRAISER's pattern) than to passive awareness. Implementing the paper's design against `EventRouter` as-is would require either (a) a new poll-style read (e.g. a per-candidate cursor over a buffered stream of `utility.candidate_progress` events) built alongside the router, not a `subscribe()` callback, or (b) accepting the callback-interrupt shape and dropping the "never stops to listen" property entirely.
2. **Every concrete `generate_candidates()` in this repo is one-shot per candidate.** The module docstring's canonical usage (`AdrSummarySelector`), both `tests/loops/test_utility.py` fixtures, and `docs/utility-executor-design.md`'s own usage example all call a single `llm.call(...)` per candidate via `asyncio.gather`. The abstract base doesn't forbid a multi-step candidate generator, but nothing in this codebase demonstrates or requires one. **As it stands, there is no "between steps" moment for a mid-generation mention check to land in — this idea does not apply to `UtilityExecutor` today.**
3. **`docs/utility-executor-design.md` read in full — orthogonal, not supporting or ruling out.** Its only isolation contract concerns `utility_score()` (scoring must not see sibling candidates or generation history, to avoid anchoring). It says nothing about candidate independence *during* `generate_candidates()` itself — that step is entirely unaddressed by the design doc.

**Verdict:** two independent negatives. (1) The substrate (`EventRouter`) is push/callback, not poll — the paper's primitive would need a different mechanism to build against. (2) No current `UtilityExecutor` usage has multi-step candidates to fold information into. Revisit only if a consumer builds a genuinely multi-step `generate_candidates()` — at that point, item 1's poll-vs-callback gap becomes the actual blocker to solve first.

## Explicitly out of scope for this item

- Wrapping or modifying Claude Code's own `Workflow` tool — confirmed not part of this repo.
- Any new message-bus infrastructure — `bus.py`/`events/router.py` already cover this; this item is purely about wiring a consumer into `UtilityExecutor`.
- Any change to `RALFExecutor`, `CompassSkillOptExecutor`, or other single-agent executors — none of them do concurrent fan-out today, so the paper's finding doesn't apply to them.

## Docs to update if this proceeds

Per `CLAUDE.md`'s documentation-hygiene section: `docs/architecture.md` (executors table), `docs/idioms-adoption-plan.md` (executor specs/build order), `docs/event-catalog.md` (new event type if `utility.candidate_progress` is added) — matches the `loopkit-docs-hygiene` skill compass's own CLAUDE.md references. Tests should follow the existing per-module split (`tests/loops/test_utility.py` presumably already exists — extend it, follow the same fixture pattern as `test_bus.py`/`test_bus_compaction.py`).
