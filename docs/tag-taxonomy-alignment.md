# Tag Taxonomy Alignment — agentic-loopkit

_P34 seed doc — 2026-06-19_

Compass uses `~/.claude/skills/compass/scripts/compass/taxonomy.json` as the canonical tag
vocabulary. All learning tags should map to a canonical name (or a declared alias) in that
file. This doc records the alignment for loopkit-domain tags.

---

## Tags already in taxonomy.json

These loopkit learning tags resolve correctly today (canonical names and aliases):

| Tag used in learnings | Resolves to | Via |
|---|---|---|
| `architecture` | `architecture` | canonical |
| `design-constraint` | `architecture` | alias |
| `executor-design` | `architecture` | alias |
| `modulith` | `architecture` | alias |
| `boundaries` | `architecture` | alias |
| `process` | `process` | canonical |
| `intent` | `process` | alias |
| `scope` | `process` | alias |
| `confidence` | `process` | alias |
| `tooling` | `tooling` | canonical |
| `testing` | `testing` | canonical |
| `technical-debt` | `technical-debt` | canonical |
| `performance` | `performance` | canonical |
| `gps-wiki` | `gps` | alias |

---

## Tags pending taxonomy.json seeding

These tags appear in loopkit learnings but have no entry in taxonomy.json. They should be
added when the taxonomy seeding script runs (P34 Phase 1 — compass).

| Proposed canonical | Current aliases in learnings | Domain | Description |
|---|---|---|---|
| `govkit` | `governance` | `architecture` | Governance layer (agentic_govkit package) — AuditAgent, KillSwitchAgent, ConflictResolutionExecutor, CouncilExecutor, GovernanceLearningAgent |
| `lclm` | `headlines` | `architecture` | LCLM event headline compression (arXiv:2606.09659) — EventHeadline, load_headlines, expand_event |
| `bus` | `event-bus`, `storage` | `architecture` | EventBus event routing, JSONL store, compact_stream, backpressure |
| `self-improvement` | `skillopt`, `extension-pattern` | `architecture` | Agent skill self-improvement patterns — SkillOptExecutor, SelfHarnessExecutor, SkillEdit |
| `dashboard` | _(none yet)_ | `tooling` | Management dashboard (FastAPI backend + React frontend) |
| `design-decision` | _(add as alias of `architecture`)_ | `architecture` | Already an alias candidate — complement to `design-constraint` |

---

## Tags to clean up

These one-off tags in learnings should be normalised to a canonical on next log:

| Raw tag | Should use |
|---|---|
| `OutcomeExecutor` | `architecture` (too specific; executor class names are not tags) |
| `v6` | _(drop — version labels decay quickly and add no retrieval value)_ |
| `executors` | `architecture` (executor patterns are an architecture concern) |

---

## Next steps

1. **Taxonomy.json update** — add the five canonical entries above to
   `~/.claude/skills/compass/scripts/compass/taxonomy.json` (P34 Phase 1, compass skill).
2. **Alias normalisation in compass** — once P34 Phase 1 lands, `cmd_log_learning` will warn
   on unknown tags; re-tag any raw entries flagged.
3. **Loopkit contributor note** — when adding learnings with tags from the "pending" list above,
   use the proposed canonical name now (they will validate once P34 Phase 1 ships).
