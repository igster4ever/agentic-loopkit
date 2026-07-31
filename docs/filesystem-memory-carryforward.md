# Filesystem-Memory Carryforward — P73 (watch item, not scoped)

**P-code:** P73
**Status:** Watch item only. No implementation, no design doc beyond this pointer.
**Source:** Zhou et al., "Filesystem-Based Memory for LLM Agents: Organization, Evolution, and Sustainability" (arXiv:2607.26637v1, 29 Jul 2026).
**Master doc:** `~/.claude/skills/compass/docs/filesystem-memory-carryforward.md` (full background, priority rationale, and the compass-side item that *was* shipped, P72).

---

## Why this is a watch item, not a build

The paper's procedural-memory setting found no single compression policy serves every consumer: a verbatim raw episode log serves a *strong* execution agent best; curated/distilled guidance serves a *weak* execution agent best and is nearly insensitive to how much experience has accumulated. Neither wins unconditionally — which model reads the store determines which representation pays off.

Loopkit's always-on-agents governance work (`docs/always-on-agents-governance-carryforward.md`) already establishes a pattern where cron/always-on executors may run a cheaper backbone than interactive sessions against shared state. **If** that pattern extends to loopkit's own experience-bank or pattern-store reads — i.e. if a cheap always-on executor and a strong interactive session both read the same stored patterns/traces — this paper's finding becomes directly actionable: the cheap executor would benefit from curated/distilled patterns where the strong session could consume raw traces directly without a quality loss.

**No evidence yet that this is actually happening.** Before scoping any work here, confirm: do any of loopkit's executors currently vary in backbone strength while reading the *same* memory/pattern store as another executor? If not, this stays a watch item — building a tiering mechanism speculatively repeats the mistake compass's P66 Phase 2 and P69 explicitly flagged (don't build ahead of a concrete trigger).
