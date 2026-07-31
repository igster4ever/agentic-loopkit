# NOOA (NVIDIA Object-Oriented Agents) Carryforward — agentic-loopkit

**Status:** Design doc only — nothing implemented yet. One item (P72a) is scoped to design + audit; the sandboxed-CodeAct idea is explicitly deferred, not queued.
**Source:** Furgale, Klingler, Nolan, et al., "NVIDIA-labs OO Agents: Native Python Object-Oriented Agents" (arXiv:2607.20709v1, 22 Jul 2026).
**Master doc:** none yet — this is loopkit-specific; promote a cross-project summary to `~/.claude/skills/compass/docs/` only if the memorykit item below turns out to generalise to other namespaces (e.g. `agentic-memorykit`).

---

## Why this paper, for this repo specifically

NOOA is a model-agnostic Python agent framework whose central claim is "agent as Python object": methods are actions, docstrings are prompts, type annotations are contracts, and an ellipsis (`...`) method body is completed at runtime as an LLM-driven loop. It names six interface capabilities it claims to be first to combine on one surface — typed I/O, pass-by-reference, code-as-action, programmable loop engineering, explicit object state, model-callable harness APIs — and scores fourteen other frameworks against them (§5, Table 7).

Read against loopkit's own design (`CLAUDE.md`'s "Key design rules" + executor table), three of the six capabilities are already loopkit strengths (object state via `AgentState`/`AgentBase`, loop engineering via the bounded `RALFExecutor`/`ReActExecutor`/`PlanExecutor` family, typed I/O via `RALFResult`/dataclass returns validated before `learn()`). Two are gaps worth naming explicitly: **pass-by-reference/bounded-preview discipline** and **code-as-action (CodeAct)**. The sixth — harness APIs — is partially covered by `EventMeta`/`headlines.py` but not on the same explicit terms NOOA describes.

This doc covers the gaps in confidence order: memorykit verb/consolidation gap (do this), bounded-preview helper (worth a small ticket), CodeAct executor (deliberately not queued — see below).

---

## P72a — Memorykit: explicit memory verbs + consolidation pass

**Target:** `docs/memorykit-design.md` (design surface) and, once agreed, `agentic_loopkit/agents/base.py`'s `recall()`/`save_state()`/`load_state()` plus whatever memorykit exposes today for writes.
**Dependency:** none structurally — `AgentState(episodic, semantic, procedural)` and `agent.recall()` (P46, wired to `memorykit.query_iterative()`) already exist and are sufficient to build on.

### What NOOA does that loopkit doesn't yet (§3.7, Fig. 5)

NOOA's memory subsystem (`MemoryManager.install(agent)`) gives the model seven callable tools — `remember, recall, search, update_memory, forget, associate, deref` — rather than treating memory as a background extraction pipeline. Two mechanics stand out as absent from loopkit's current design:

1. **Spontaneous injection via a `BeforeTurn` hook**, separate from deliberate tool calls — the harness derives a query from recent events and injects associated memories into a dynamic context block *without counting as a reinforcing read* (the paper is explicit that injected memories are "not reinforced, so what the harness surfaces does not distort the usage signal" — a subtlety worth preserving if loopkit adds this).
2. **Asynchronous reflection/consolidation**, run after a task completes or while idle: near-duplicates merged, conflicting values reconciled (superseded ones archived, not deleted), related memories linked, importance re-scored, episodes distilled into higher-level records, decayed memories pruned — with an explicit guarantee that pruning never removes recent memories, protected types, or open todos.

Loopkit's `agent.recall()` is read-only and iterative (query → steps → results); there's no `forget`/`associate`/`deref` triad, no distinction between deliberate and spontaneous recall, and no consolidation pass. `AgentState.procedural` is reserved-but-unused, which is the natural home for "the store's own state" if a consolidation pass is added later.

### Audit before building

Per this repo's established discipline (see `docs/mem-harness-carryforward.md`'s Step 0), confirm before scoping implementation:

1. What does memorykit (the sibling package, not yet vendored into this repo per `CLAUDE.md`'s "no hard dep" note) actually expose for *writes*, today — is there already a `remember()`-equivalent, or is `save_state()` the only write path?
2. Does anything already distinguish a deliberate-recall read from a context-injection read, or would every `recall()` call currently count toward whatever usage/importance signal exists?
3. Is there a scheduling point that already resembles "after a task completes or while idle" (e.g. a `learn()` hook, an executor's terminal state) that a consolidation pass could hang off, or would this need new scaffolding in the bus/executor lifecycle?

### Explicitly out of scope for this item

- Vector-index or embedding infrastructure — NOOA's retrieval unions embedding + keyword candidates and ranks by ACT-R activation; loopkit's "zero runtime deps" stance means this would need to stay pluggable/optional exactly as memorykit's own design already intends, not a new hard dependency.
- Multi-agent store sharing / owner scoping (NOOA's `kind:key` typed references resolved against live agent state at recall time) — interesting, but a separate concern from the verb/consolidation gap and not evidenced as needed by any current loopkit consumer.

---

## Bounded-preview helper (smaller, no P-code assigned)

NOOA's pass-by-reference design (§3.2) renders large arguments as a compact preview — concrete type, true length, head/tail sample (e.g. `records = list(len=100, [:5]=[42, 17, 89, 33, 8], [-5:]=[56, 71, 12, 45, 28])`) — rather than serializing the full value into the prompt, while the underlying variable stays fully intact in the execution environment. `headlines.py` already does the analogous thing at the *event* granularity (LCLM-inspired skim/expand), but nothing today truncates an individual large payload *value* before it lands in `EventMeta.context` or a dashboard `_meta.context` render.

Worth a small, self-contained helper (`agentic_loopkit/utils/preview.py`?) if any executor or the dashboard starts handling genuinely large field values — not urgent today since nothing in the current codebase was observed constructing oversized single-field payloads. Flagging so it's not rediscovered from scratch later.

---

## CodeAct executor — deliberately not queued

NOOA's default strategy (`CodeActStrategy`) is a REPL loop: the model writes Python that calls `execute_python(...)` to inspect state, call helper methods, or invoke other generation methods, and terminates with `return_result(...)` once the harness type-validates the output. It is the single largest capability gap against loopkit's current executor family (`ReAct`/`Plan`/`Reflexion`/`Outcome` are all tool-call- or step-shaped, not arbitrary-code-shaped) — and also the one I'd actively hold off on, for a reason NOOA's own paper states outright.

**The tradeoff:** NOOA runs the model's code in-process, which is what preserves pass-by-reference (live objects, not serialized copies) — but the paper's own Limitations section (§7) says plainly that "the validator... protects the agent loop, not the host" and that "sandboxing... goes around the agent process, and a shell tool is no safer than in-process Python; most harnesses... ship one" — their own preferred deployment is an external sandbox (OpenShell). That's a materially different trust boundary than everything else in `agentic_loopkit`: `PollingAdapter` subclasses are explicitly non-reasoning (`CLAUDE.md`: "Adapters are not agents — no reasoning, no LLM calls"), and the bounded-loop executors constrain *what* the model can do at each step, not *arbitrary code execution* at all. Building CodeAct as a first-class loopkit executor would either (a) require accepting an in-process-exec trust model that cuts against "zero runtime deps, pure stdlib" and every other executor's determinism boundary, or (b) require a real sandbox dependency, which is a different package's problem (`agentic-codekit`?) rather than a loopkit core addition.

**Recommendation:** leave this as a noted idea, not a backlog item, unless a concrete consumer need shows up (e.g. GPS·ADR Radar or another consumer wants arbitrary-code tool composition badly enough to justify a sandboxed sibling package). If it ever gets picked up, start from NOOA's own admission about the isolation boundary rather than assuming in-process exec is safe by default.

---

## Nudge for next `/compass orient` in this namespace

A research signal has been recorded via `record-research` so the next OODA-half orient in the `agentic-loopkit` namespace surfaces this doc rather than requiring it to be rediscovered from session context.
