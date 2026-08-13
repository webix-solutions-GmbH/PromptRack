# Prompt versioning pivot — "git for your customers' prompts"

**Date:** 2026-08-13
**Status:** Approved design, pending implementation plan

## Premise

Webix sells agentic tools, workflows and RAG systems to customers. The prompts behind
them are the product's business logic and need a home: a place to store them, version
them, and prove they keep working — especially across model upgrades. modelfit already
has the "prove it works" half (test suites, runs, ratings, hardware-aware measurements);
this pivot adds the "git" half: immutable version history, diffs, and a record of what
is deployed where.

modelfit stays an **internal tool, developed publicly** as a demonstration of the
consultancy's AI/LLM competence. It is **not** a product competing with Braintrust or
Langfuse — those assume a product team owning one app and (for Braintrust) a US-cloud
control plane. modelfit's niche is the consultancy side: many customer engagements,
fully self-hosted, and "which model *and what hardware*" in one verdict. No existing
tool serves that.

## Decisions (settled during brainstorming)

1. **Registry + test bench, not runtime source of truth.** Prompt changes are moved to
   customer machines by hand. modelfit is never in a customer's serving path — no
   fetch-by-label API, no uptime obligation. (A read API could be bolted on later; the
   version rows would be the same.)
2. **The versioned asset is the system prompt text only** — not the model/params/tools
   bundle. Runs already snapshot the rest.
3. **Explicit commits, git-style.** The editor works on a mutable draft; a deliberate
   commit (with message) freezes an immutable version.
4. **One `deployed` pointer per prompt.** A bookkeeping claim ("this version is live at
   the customer"), set by a human in the UI. No staging/production multiplicity.
5. **One `baseline` run pointer per version.** The known-good run that justified
   deploying it; the reference point for regression checks after a model swap.
6. **No automated pass/fail.** Verification stays: re-run the suite, compare in
   `/results`, rate by hand (or by an external judge over MCP, as today). Auto-scoring
   is the treadmill this tool deliberately stays off.
7. **Fresh start on migrations.** No data to preserve; the migration history collapses
   to a new baseline and the SQLite importer is deleted.

## The rename

The pivot inverts which prompt-like thing is central, so the names flip:

| today             | after the pivot      | role                              |
| ----------------- | -------------------- | --------------------------------- |
| `system_prompts`  | `prompts`            | the versioned asset               |
| —                 | `prompt_versions`    | immutable history (new)           |
| `prompts`         | `test_cases`         | input + expected output + tools   |
| `prompt_groups`   | `test_groups`        | suite grouping                    |
| `prompt_toolsets` | `test_case_toolsets` | tool links                        |

UI language follows ("Prompts" vs "Test cases"), routes move (`/system-prompts` →
`/prompts`, old `/prompts` → `/test-cases`), and MCP tool names change accordingly
(`create_system_prompt` → `create_prompt`, `create_prompt` → `create_test_case`, …) —
a breaking MCP change, acceptable for an internal tool starting from zero.

## Data model

Three additions; no existing semantics change.

### `prompt_versions` (new)

A child table of `prompts`, scope inherited through the parent FK like every other
child table.

| column           | type / behavior                                              |
| ---------------- | ------------------------------------------------------------ |
| `id`             | pk                                                            |
| `prompt_id`      | FK → `prompts`, **cascade** — history dies with the asset; runs keep their snapshots regardless |
| `version`        | int, sequential per prompt; `max + 1` computed inside the commit transaction; unique index `(prompt_id, version)` as backstop |
| `content`        | the frozen text                                               |
| `message`        | commit message                                                |
| `created_at`     | timestamp                                                     |
| `created_by`     | FK → `user`                                                   |
| `baseline_run_id`| nullable FK → `runs`, **SET NULL** on run delete              |

Versions are never edited or deleted individually. A commit whose content is
byte-identical to the head version is refused ("no changes").

### `prompts` (formerly `system_prompts`) gains

| column                | behavior                                                  |
| --------------------- | --------------------------------------------------------- |
| `deployed_version_id` | nullable FK → `prompt_versions`; must belong to this prompt (app-level check) |
| `deployed_at`         | timestamp, set when the pointer is set                     |
| `deployed_by`         | FK → `user`                                                |

The existing `content` column **is the draft** — unchanged.

### `run_results` gains

| column                     | behavior                                            |
| -------------------------- | --------------------------------------------------- |
| `prompt_version_id`        | nullable FK → `prompt_versions`, SET NULL           |

Set at run creation **only when the draft text is byte-equal to a committed version**
(a "clean working tree"); null means the run tested a dirty draft or used no prompt.
There is no version selection at run creation: a run always tests the current draft,
exactly as today — the column is *attribution*, not selection. `createRunRecord`, the
snapshot invariant, and `resolveEffectiveSystemPrompt` are untouched; attribution is
one extra lookup inside the existing scoped read.

### Cross-reference guards

`deployed_version_id` and `baseline_run_id` get the `assertSameCustomer` treatment
(and same-prompt for the deployed pointer), enforced inside the repo functions so no
call site can forget it — the established pattern for cross-root references.

## Workflow & UI

**Prompt editor** (the asset):

- Draft textarea as today, plus a **dirty indicator** when draft ≠ head version.
- **Commit** button → message dialog → new immutable version.
- **History panel**: version list (number, message, author, date) with badges —
  `deployed`, `baseline: run #N`. Per-version actions: view, **diff** (against draft,
  deployed, or any other version — unified text diff), **mark deployed**, **restore to
  draft** (rollback = copy content into the draft, then commit).

**Deployed signal**: when the deployed version is not the head, the prompt list and
editor show e.g. "deployed v3, head is v5" — the one-glance answer to "is what's live
at the customer what we last verified."

**Verify flow** (the baseline payoff): a version with a baseline run gets a **Verify**
button that opens `/runs/new` prefilled with the baseline run's test cases; the user
picks the new model/machine. The finished run's page links "compare against baseline" →
`/results?mode=runs&runs=<baseline>,<new>`. No new comparison UI — the existing
run-mode matrix is the behavior diff viewer.

**Attribution surfaced**: run detail and results cells show which version a result
tested (`v4` or `draft`).

**Baseline selection**: the UI only offers runs whose results are attributed to that
version; MCP's `set_baseline` validates workspace + attribution the same way.

**Test cases**: editor unchanged except terminology; a test case still references one
prompt plus an append/override mode.

## MCP surface

Versions are content (not credentials), so they are writable over MCP:

- `commit_prompt` — commit the draft with a message
- `list_prompt_versions`
- `get_prompt_version`
- `set_baseline` — attach a run to a version as its baseline

`mark_deployed` is deliberately **UI-only**: it is a human claim about a customer's
production system, the same reasoning that keeps customer workspaces unwritable over
MCP. Existing tools are renamed per the table above.

## Rename fallout

Schema + repos + actions + components + routes, MCP tool names,
`docs/example-suite/` terminology sweep, README, CLAUDE.md. Migrations collapse to one
fresh baseline; `scripts/migrate-sqlite-to-pg.mjs` and its assumptions are deleted.

## Testing

- **Pure suite**: the version-attribution matcher (clean/dirty detection), the diff
  helper, commit-refusal rule ("no changes").
- **Integration suite**: commit flow + version-numbering race, version scope isolation
  (the workspaces fixture grows a versions case), deployed/baseline cross-reference
  guards, `baseline_run_id` SET NULL on run delete, `prompt_version_id` attribution on
  run creation.

## Out of scope (explicitly)

- Runtime prompt delivery (fetch-by-label API) — possible later, not now.
- Branches, merges, multiple environment pointers — one linear history, one deployed
  pointer.
- Automated scoring / LLM-as-judge inside the app — stays external via MCP.
- Versioning of test cases, toolsets, machines — runs already snapshot them.
