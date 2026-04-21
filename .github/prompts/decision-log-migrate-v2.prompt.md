---
description: Decision Log schema migration. Walk the six steps exactly. Step 0 produces migration_request.yaml before any CLI fires. The CLI is the integrity layer; you conduct the dialog and write code-architecture decision blocks. You never edit decision_log_v{N+1}.yaml directly.
---

# Decision Log Schema Migration

You are the Decision Log migration conductor. Migration converts a v{N} Decision Log written under the legacy schema (untyped `reads`/`writes`, no `code_architecture`) into a v{N+1} log under the typed-IO + code-architecture schema.

The migration is non-destructive: v{N} stays on disk, the new log lands at v{N+1} with `parent_version=N` and `reason_for_revision="schema migration: typed agent I/O + code_architecture"`.

Walk this prompt top to bottom. Do not skip steps. Do not improvise sequencing. The `gate_migration_request` hook will reject Step 5 (`--apply`) if Step 3 (`--plan`) has not produced `proposal.yaml`.

## Prompt-as-Conductor Contract

Artifacts flow one direction: dialog → request artifact → CLI writes proposal → you review and refine → you author code-architecture blocks → CLI compiles → schema validator audits. You never edit `decision_log_v{N+1}.yaml` directly.

---

## Step 0 — Re-orient on the v{N} Decision Log (LLM + human)

Read, in order:

- The latest Decision Log at `workspace-artifacts/decision-logs/decision_log_v{N}.yaml`
- `PROBLEM_STATEMENT.md`
- `workspace-artifacts/wiki/v2/index.md` (for code-architecture context)

Establish:

- The project's `meta.project_type` (algorithm | hybrid | report).
- Every entry under `agents_needed`. For each agent, what each artifact in its `reads` and `writes` lists actually is — a markdown document, a YAML manifest, a Python source file, a test suite. Modality must be `document` or `code`; mixed artifacts pick the dominant modality.
- Whether the log already has a `code_architecture` section. If yes, the language/library/layout decisions carry forward and Step 4 is skipped.
- Whether `PROBLEM_STATEMENT.md` describes any code-architecture intent that should be captured (libraries the human has named, languages they've ruled out, runtime targets).

### 0a. Dialog with the human (only when there is real ambiguity)

For every artifact in any agent's `reads`/`writes` whose modality is not obvious from the artifact name alone, ask:

> "Agent `<role>` declares it `<reads|writes>` `<artifact_name>`. Is that artifact a document (markdown, YAML, decision log) or code (source files, tests)?"

Don't ask about artifacts that are unambiguous (`decision_log` → document, `code` → code, `tests` → code, `architecture` → document, etc.). Save the dialog for the genuinely unclear ones.

For algorithm/hybrid projects without a prior `code_architecture`, briefly probe:

> "What language and core libraries does this project commit to? We'll capture this as a `code-architecture` block in the migrated log."

The deeper code-architecture dialog happens in Step 4 — Step 0 just surfaces enough context to draft `migration_request.yaml`.

### 0b. Write `migration_request.yaml`

Write `workspace-artifacts/runtime/migration/migration_request.yaml`:

```yaml
decision_log_migration_request:
  generated_at: <current ISO timestamp>
  parent_version: <N>
  reason: "schema migration: typed agent I/O + code_architecture"
  modality_overrides:
    # Optional. Per-role overrides for inputs/outputs the heuristic CLI cannot
    # confidently classify. Keys are agent roles; values list typed entries.
    <role>:
      inputs:
        - {name: <artifact_name>, modality: <document|code>}
      outputs:
        - {name: <artifact_name>, modality: <document|code>}
  notes: |
    <free-form notes — what was discussed, what's still ambiguous>
```

Modality_overrides is the only place where you record the typings that came out of the dialog. Anything you do not override falls back to the CLI's default-modality heuristic and shows up in `unresolved_artifacts` for review in Step 3.

## Step 1 — Plan (CLI)

```bash
meta-compiler migrate-decision-log --plan
```

This:

- Reads the latest `decision_log_v{N}.yaml`.
- Reads `runtime/migration/migration_request.yaml` (if present) and merges `modality_overrides` over the heuristic defaults.
- Writes `runtime/migration/proposal.yaml` with every agent's typed `inputs`/`outputs`, an `unresolved_artifacts` list flagging artifacts the heuristic could not confidently classify, and a `needs_code_architecture` flag.
- For algorithm/hybrid projects without a prior `code_architecture`, also seeds `runtime/migration/code_architecture_blocks.md` with a starter skeleton.

On nonzero exit: **STOP**. Surface the failure (usually a missing parent log or a malformed migration_request) and return to Step 0.

## Step 2 — Review the proposal

Read `runtime/migration/proposal.yaml`. Walk every entry under `agents_needed`. For every artifact name listed in `unresolved_artifacts`, judge whether the heuristic's default (always `document`) is correct. If not:

- Edit the proposal directly to fix the modality on the offending entries, OR
- Edit `runtime/migration/migration_request.yaml` to add the override and re-run Step 1.

For `project_type=report`, double-check that every `outputs[].modality` is `document`. The validator will reject `code` outputs at --apply time, but catching it now is cheaper.

## Step 3 — Code-architecture dialog (algorithm/hybrid only)

Skip this step entirely for `project_type=report`.

For algorithm/hybrid projects without a prior `code_architecture`, walk the `code-architecture` probe library at `.github/docs/stage-2-probes.md`. Author at minimum:

- One block with `Aspect: language` (concrete version pin, alternatives rejected, citations).
- One block with `Aspect: libraries` (a `Libraries:` sublist with name + version + purpose for each library; alternatives rejected; constraints applied).

Author additional blocks for `Aspect: module_layout`, `Aspect: build_tooling`, and `Aspect: runtime` when those choices are load-bearing.

Write the blocks into `runtime/migration/code_architecture_blocks.md` using the standard decision-block format (see `.github/prompts/stage-2-dialog.prompt.md` § per-section required fields and § Code-architecture block format example).

If the parent log already has a `code_architecture` section, skip this step — Step 5 carries the existing entries forward unchanged.

## Step 4 — Verify (LLM)

Self-check before invoking the CLI:

- Every `agents_needed` entry in the proposal has at least one input and one output, each tagged with `modality ∈ {document, code}`.
- For `project_type=report`, no agent declares a `code` output.
- For algorithm/hybrid without prior `code_architecture`, `code_architecture_blocks.md` contains at least one Aspect=language and one Aspect=libraries block, and every block parses (run a quick eyeball against the format example in `.github/prompts/stage-2-dialog.prompt.md`).

Surface any inconsistencies to the human and loop back to the relevant step.

## Step 5 — Apply (CLI)

```bash
meta-compiler migrate-decision-log --apply
```

This:

- Reads `runtime/migration/proposal.yaml`.
- Validates every agent's typed inputs/outputs (modality enum, presence).
- Carries forward `meta`, `conventions`, `architecture`, `scope`, `requirements`, `open_items` from the parent log unchanged.
- For algorithm/hybrid: parses `code_architecture_blocks.md` (or carries forward the parent's existing `code_architecture` if present) and emits the `code_architecture` section.
- Compiles `workspace-artifacts/decision-logs/decision_log_v{N+1}.yaml` with `parent_version={N}` and `reason_for_revision="schema migration: typed agent I/O + code_architecture"`.
- Runs `validate_decision_log` on the result and refuses to write if the schema doesn't validate.

On nonzero exit: **STOP**. Surface the failure (usually a missing or malformed code-architecture block, or a residual `reads`/`writes` field) and return to Step 2 or Step 3 to fix.

The `gate_migration_request` hook will refuse `--apply` if Step 1 did not write `proposal.yaml`.

## Step 6 — Audit and handoff

```bash
meta-compiler audit-requirements
```

Confirm REQ traces still resolve against the migrated log. If scope or agents changed materially during migration (they shouldn't, this is a schema migration), recommend `meta-compiler scaffold` to regenerate Stage 3 outputs against the new typed I/O.

Record the audit output path in your final handoff message to the human.

---

## Out of scope

- You do not edit `decision_log_v{N+1}.yaml` directly. The transcript artifacts (`migration_request.yaml`, `proposal.yaml`, `code_architecture_blocks.md`) are the source; the CLI compiles.
- You do not author new conventions, architecture, scope, requirements, or open items during migration. Use Stage 2 re-entry (`/stage2-reentry`) for that.
- You do not run `meta-compiler scaffold`. That's Stage 3.

## On refusal

If the human asks you to skip Step 1 or invoke `--apply` directly, refuse. The integrity layer exists for a reason. The `gate_migration_request` hook is non-skippable except via documented overrides.

## Guiding principles

- **Document everything** — modality decisions, code-architecture choices, and the rationale for each are captured in artifacts, not chat.
- **Default to the wiki** — if the wiki names a library or language already, propose it; don't ask the human to re-derive it.
- **Prefer the safe heuristic** — when modality is genuinely ambiguous, default to `document` and surface in `unresolved_artifacts` for human review.
