---
name: Hooks-based determinism enforcement + Stage 2 re-entry hardening
description: Replace informal "prompts describe CLI calls" discipline with VSCode Copilot hooks that gate, auto-fire, and audit the meta-compiler pipeline; apply prompt-as-conductor hardening to Stage 2 re-entry; promote Step 0 (problem-space re-ingestion) into an artifact-producing step.
status: Draft for review
last_updated: 2026-04-18
runtime_scope: GitHub Copilot Chat in VSCode
depends_on: .github/docs/stage-2-hardening.md
---

# Hooks-Based Determinism Enforcement + Stage 2 Re-entry Hardening

## 1. Context and Motivation

Meta-compiler's `.github/prompts/*.prompt.md` and `.github/agents/*.agent.md` embed ~35 `meta-compiler` and `python scripts/*.py` calls that the LLM is expected to run in the correct order with faithful handling of output. In practice this produces four failure modes observed during real use:

1. **LLM skips CLI calls entirely** — writes the command in chat but never executes it, or asserts the command "would run" and proceeds with fabricated state.
2. **LLM runs the CLI but ignores output** — the call executes, but the LLM paraphrases or hallucinates the result instead of reading actual stdout or the artifact the CLI wrote.
3. **LLM runs steps out of order** — e.g., invokes `scaffold` before `elicit-vision --finalize`, or skips a `validate-stage` between stages, breaking artifact preconditions.
4. **Stage 2 re-entry dialog does not happen** — the seeded transcript is generated, then `elicit-vision --finalize` runs immediately with no new decision blocks authored for the revised sections; the resulting `v{N+1}` claims to address a scope shift that was never actually elicited.

The intended contract — *prompt sequences the steps, agents perform bounded semantic judgment, CLI guarantees integrity* — is not enforced. This spec replaces the informal discipline with VSCode Copilot hooks that gate execution, auto-fire deterministic CLI steps at transition boundaries, and audit the flow; it also applies the prompt-as-conductor hardening pattern to Stage 2 re-entry (which was left prose when `stage-2-dialog.prompt.md` was hardened).

Runtime scope: **GitHub Copilot Chat in VSCode**. Claude Code hook support exists and uses the same event model, but this spec does not target it.

## 2. Architecture Overview

Four layered pieces, outermost (coarsest) to innermost (most specific):

1. **Workspace hooks** — `.github/hooks/main.json`. One file. Enforces state-machine ordering across the whole pipeline regardless of which prompt or agent is active. Fires on `SessionStart`, `UserPromptSubmit`, `PreToolUse` (Bash, Write/Edit), `PostToolUse` (Bash), `Stop`.
2. **Agent-scoped hooks** — declared in each `.agent.md` frontmatter under `hooks:`. Enforce invariants that only make sense while a specific custom agent is active. Layer additively on top of workspace hooks. Require `chat.useCustomAgentHooks: true` in VSCode settings.
3. **CLI hardening** — additions to `meta_compiler/stages/elicit_stage.py` and `meta_compiler/stages/stage2_reentry.py`. Mechanical checks that belong in code, not in hooks.
4. **Stage 2 re-entry prompt rewrite** — `.github/prompts/stage2-reentry.prompt.md` becomes a conductor prompt matching `stage-2-dialog.prompt.md`'s structure. Adds a hardened Step 0 that re-ingests the problem space and produces a machine-readable re-entry request.

**Shared machinery**: `.github/hooks/bin/meta_hook.py`, single-file, stdlib-only. All hook entries shell into `python3 .github/hooks/bin/meta_hook.py <check-name>`. Keeps `main.json` short and keeps check logic under pytest coverage.

## 3. Workspace Hooks — Event Matrix

`.github/hooks/main.json` registers the following:

| Event | Matcher | Check | Purpose |
|---|---|---|---|
| `SessionStart` | — | `inject_state` | Read manifest; emit `additionalContext` with current stage, pending next action, any mid-flight re-entry state. |
| `UserPromptSubmit` | — | `inject_state` + prompt-trigger dispatch | Re-inject state (in case of context compaction); if prompt text matches a `PROMPT_TRIGGERS` entry (§ 5), run the auto-fire chain and inject its output. |
| `PreToolUse` | `Bash` | `gate_cli` | Inspect command; if `meta-compiler <X>`, check manifest precondition. Return `permissionDecision: deny` with remediation on mismatch. Honors `META_COMPILER_SKIP_HOOK=1` env (logged). |
| `PreToolUse` | `Write\|Edit` | `gate_artifact_writes` | Block direct writes to `workspace-artifacts/decision-logs/*.yaml` (compiled output, CLI-only) and `workspace-artifacts/seeds/**` (immutable). |
| `PreToolUse` | `Bash` | `gate_reentry_request` | If command is `meta-compiler stage2-reentry ...`, require `runtime/stage2/reentry_request.yaml` to exist and pass SHA validation. **Not overridable.** |
| `PostToolUse` | `Bash` | `capture_output` | If command was `meta-compiler *`, emit stdout as `additionalContext` so the LLM cannot paraphrase it. |
| `Stop` | — | `nudge_finalize` | If `last_completed_stage` is mid-flight (`2-reentry-seeded`, `2-dialog-started`), return `decision: block` with a reminder to either finalize or explicitly defer. |

**State-machine table (`STAGE_PRECONDITIONS`).** Derived from `run_all_stage.py`'s ordering; maps each `meta-compiler` subcommand to the required prior `last_completed_stage` value(s).

**What hooks deliberately do NOT do:**
- Validate semantic content (that's the orchestrator agents).
- Auto-fire CLI calls that require human input (e.g., `meta-init`'s `--project-name`, `elicit-vision --finalize`'s "dialog is done" judgment).

## 4. Agent-Scoped Hooks

Declared in each agent's `.agent.md` frontmatter under `hooks:`. Four agents get hooks; pure-reasoning agents do not.

| Agent | Event | Check | Purpose |
|---|---|---|---|
| `stage2-orchestrator` | `PreToolUse` (any) | `gate_orchestrator_mode` | Require `precheck_request.yaml` (preflight) or `postcheck_request.yaml` (postflight) to exist at agent start. |
| `stage2-orchestrator` | `SubagentStop` | `require_verdict` | Require `precheck_verdict.yaml` or `postcheck_verdict.yaml` written with a valid `verdict` field. |
| `ingest-orchestrator` | `PreToolUse` (any) | `gate_ingest_workplan` | Require `runtime/ingest/work_plan.yaml` before fan-out. |
| `ingest-orchestrator` | `SubagentStop` | `require_ingest_report` | Require `wiki/reports/ingest_report.yaml` written. |
| `seed-reader` | `PostToolUse` (Write) | `validate_findings_schema` | Validate JSON against findings schema on every write under `wiki/findings/`; deny with schema error if malformed. |
| `stage-1a2-orchestrator` | `SubagentStop` | `require_handoff` | Require `wiki/reviews/1a2_handoff.yaml` with a `decision` field. |

**Design principle**: each agent owns an artifact contract. The hook enforces "I must be given X; I must produce Y" at entry and exit. Subagent cannot "succeed" without producing its output artifact.

**Excluded agents** (pure reasoning; no deterministic artifact contract the CLI depends on): `academic-researcher`, `schema-auditor`, `adversarial-questioner`, `optimistic-reviewer`, `pessimistic-reviewer`, `pragmatic-reviewer`, `domain-ontologist`, `debate-synthesizer`, `gap-remediator`, `requirements-auditor`.

**Requires**: `chat.useCustomAgentHooks: true` in `.vscode/settings.json`. `meta-init` is updated to write this value into provisioned workspaces; this repo's `.vscode/settings.json` is updated in-place.

## 5. Auto-Firing CLI at Transition Boundaries

**Principle.** Hooks own the *between-step glue*. The LLM owns the *reasoning inside a step*.

A CLI call qualifies for auto-fire when:

1. It is pure CLI (no LLM interpretation between inputs and outputs).
2. It fires at a well-defined transition (prompt invocation, subagent stop, stage boundary).
3. Its output is context for the LLM's next action, not a decision the LLM must make.

### Auto-fire map

| Prompt | Auto-fire on `UserPromptSubmit` | Auto-fire on relevant `SubagentStop` | Stays LLM-invoked |
|---|---|---|---|
| `stage-0-init` | — | — | `meta-init` (needs user-supplied args) |
| `stage-1a-breadth` | `ingest --scope all` | (on `ingest-orchestrator`) `ingest-validate`, `research-breadth`, `validate-stage --stage 1a` | — |
| `stage-1b-evaluators` | — | (on `stage-1a2-orchestrator`) `research-depth`, `validate-stage --stage 1b` | — |
| `stage-1c-review` | — | (on `stage-1a2-orchestrator`) `review`, `validate-stage --stage 1c` | — |
| `stage-2-dialog` | `elicit-vision --start` | — | `elicit-vision --finalize` (LLM judges dialog end), `audit-requirements` |
| `stage2-reentry` | — | — | All CLI calls gated through Step 0 artifact (§ 7) |
| `stage-3-scaffold` | `scaffold`, `validate-stage --stage 3` | — | — |
| `stage-4-finalize` | `phase4-finalize`, `validate-stage --stage 4` | — | — |

**Why `stage2-reentry` doesn't auto-fire its CLI on prompt invocation.** Step 0's problem-space dialog (§ 7) is the entire reason the prompt exists. Auto-firing `stage2-reentry` on prompt submit would reproduce today's flakiness. The CLI fires after Step 0 has produced `reentry_request.yaml`; `gate_reentry_request` (§ 3) enforces this ordering non-skippably.

### Detection

`UserPromptSubmit` hook matches the user's prompt text against `PROMPT_TRIGGERS` in `meta_hook.py`. Match is a simple startswith check for `/<prompt-name>` — no prompt-file parsing. Unrelated user prompts are a no-op.

### Chain execution

`SubagentStop` chains run in order via a small runner in `meta_hook.py`. Each step is `(cmd, precondition_check)`. On failure, chain stops; failure is surfaced as `systemMessage`; remaining steps are skipped. Chain runner sets `META_COMPILER_SKIP_HOOK=1` on child processes so `gate_cli` does not re-gate its own parent's chain.

### Prompt body changes

When a CLI call moves to auto-fire, its bullet is **removed** from the prompt body in the same commit that adds the auto-fire registration. Keeping both produces duplicate execution. The prompt body becomes semantic-only for auto-fired steps (e.g., stage-1a-breadth reads: "conduct seed fan-out via `ingest-orchestrator`").

## 6. CLI Hardening — Stage 2 Re-entry Block-Freshness

`run_elicit_vision_finalize` in `meta_compiler/stages/elicit_stage.py` gains a mechanical check, invoked when re-entry state is detected (`research.last_completed_stage == "2-reentry-seeded"` in the manifest).

### New helper

```python
def _check_reentry_block_freshness(
    transcript_blocks: list[DecisionBlock],
    cascade_report: dict[str, Any],
    parent_log: dict[str, Any],
) -> list[str]:
    """For each section listed as revised in the cascade report, require
    >= 1 decision block in the transcript that did NOT appear in the
    parent Decision Log. Identity checked by (block title + section).

    Returns list of issue strings, one per empty revised section.
    Empty list means pass.
    """
```

`scope` in the cascade report maps to both `scope-in` and `scope-out` block sections; a fresh block in either satisfies the `scope` revision. All other revisable sections (`conventions`, `architecture`, `requirements`, `open_items`, `agents_needed`) map 1:1.

### Integration

`run_elicit_vision_finalize`:

1. Parse blocks (existing).
2. If re-entry state detected, load the cascade report and the parent Decision Log, run `_check_reentry_block_freshness`. Nonempty result → exit nonzero with issue list in stderr. The `PostToolUse capture_output` hook surfaces this to the LLM.
3. Run existing `_mechanical_fidelity_checks`.
4. Compile and write the Decision Log.
5. On success in re-entry mode, clear `research.reentry_version` and set `research.last_completed_stage = "2"`.

### Why this check lives in the CLI, not a hook

1. The check parses the transcript, loads the cascade report, and loads the prior Decision Log — enough logic that placing it in a hook moves it further from `parse_decision_blocks` and `compile_decision_log`, which already have pytest coverage.
2. Hardening spec's principle: CLI owns mechanical validation; agents own semantic judgment; hooks own execution discipline. Block-freshness is mechanical.
3. Users running `meta-compiler elicit-vision --finalize` directly (outside Copilot — e.g., from `.vscode/tasks.json` or a migration script) still get the protection.

## 7. Stage 2 Re-entry Prompt Rewrite

`.github/prompts/stage2-reentry.prompt.md` is replaced wholesale. The current prose prompt references a stale `reentry_context_v{N}.md` file that the CLI no longer writes, has no orchestrator invocations, and leaves Step 0 underspecified.

### Six-step conductor structure

**Step 0 — Re-ingest the problem space (LLM + human).** Produces `workspace-artifacts/runtime/stage2/reentry_request.yaml` before any CLI fires. Detailed below in § 7.1.

**Step 1 — Seed the transcript (CLI).**
```
meta-compiler stage2-reentry --from-request workspace-artifacts/runtime/stage2/reentry_request.yaml
```
Hook `gate_reentry_request` enforces the artifact exists. `PostToolUse capture_output` surfaces the cascade report and seeded transcript paths.

**Step 2 — Orchestrator preflight (semantic readiness).**
```
@stage2-orchestrator mode=preflight
```
Agent-scoped hook requires `precheck_request.yaml`, which `stage2-reentry` now writes (§ 8.2). On `BLOCK`: surface to human; iterate Stage 1B if the cascade opened new coverage gaps.

**Step 3 — Scoped dialog (LLM + human).** Read `PROBLEM_STATEMENT.md`, `brief.md`, the seeded `transcript.md`, and the cascade report. Discuss **only** sections listed in `cascade_report.revised_sections`. For each: present the prior decision (already in the transcript as reference prose), query the wiki for alternatives not previously considered, converse with the human, append a new decision block. For unchanged sections: note "Retained from v{N}" in prose; the carried-forward blocks are already in the transcript.

**Step 4 — Finalize (CLI).**
```
meta-compiler elicit-vision --finalize
```
CLI enforces re-entry block-freshness (§ 6). On nonzero exit: stop, return to Step 3, address the named empty sections.

**Step 5 — Orchestrator postflight (fidelity audit).**
```
@stage2-orchestrator mode=postflight
```
Same as the fresh flow, with one re-entry-specific addition: carried-forward decisions from `parent_version` must remain consistent with the newly authored ones.

**Step 6 — Audit and handoff.**
```
meta-compiler audit-requirements
```
If scope or requirements changed, recommend re-running `meta-compiler scaffold`.

### 7.1 Step 0 — Hardened Problem-Space Re-ingestion

Current failure: the LLM takes a one-sentence `--reason` and fires the CLI. The cascade analysis runs against a possibly-stale `PROBLEM_STATEMENT.md`; the orchestrator audits the wrong problem; v{N+1} claims to address a scope change the system never actually ingested.

Fix: promote Step 0 into an artifact-producing step. The LLM must write `reentry_request.yaml` before any CLI fires. `gate_reentry_request` enforces this non-skippably.

**Artifact schema** (`workspace-artifacts/runtime/stage2/reentry_request.yaml`):

```yaml
stage2_reentry_request:
  generated_at: <iso>
  parent_version: <N>
  problem_change_summary: |
    <LLM's synthesis of what the human said changed, in their own words.
     Written during the dialog, not boilerplate.>
  problem_statement:
    previously_ingested_sha256: <sha of PROBLEM_STATEMENT.md at parent_version>
    current_sha256: <sha at request time>
    updated: <true | false>
    update_rationale: |
      <if updated=true: why the edits were made.
       If updated=false: affirmation that the problem still stands
       despite the scope shift, with the human's reasoning.>
  revised_sections:
    - <one of: conventions | architecture | scope | requirements | open_items | agents_needed>
  reason: <short string; becomes the --reason arg to stage2-reentry>
  carried_consistency_risks:
    - prior_decision: <title from parent log>
      section: <section>
      concern: <why carrying it forward may be unsafe given the new problem context>
```

**Step 0 procedure**:

- **0a.** Read `PROBLEM_STATEMENT.md`, the latest Decision Log, and the current wiki index. Establish what `v{N}` captured.
- **0b.** Dialog with the human:
  - "What changed in your problem space since `v{N}`?"
  - "Does `PROBLEM_STATEMENT.md` still describe what you're trying to build? Walk me through the parts that no longer fit."
  - For each identified change: "Which decision areas does this touch — conventions, architecture, scope, requirements, agents, or open items?"
  - "Are there carried-forward decisions from `v{N}` that might no longer be safe given this shift?"
- **0c.** If `PROBLEM_STATEMENT.md` needs edits, edit it in-session with the human's explicit approval. Record the change in `problem_change_summary`. Never edit seeds or the Decision Log directly.
- **0d.** Write `reentry_request.yaml` per the schema.

**Hook enforcement.** `gate_reentry_request` (PreToolUse on Bash) denies any `meta-compiler stage2-reentry ...` command unless:

- The request file exists and parses against the schema.
- `problem_statement.current_sha256` matches the live file's SHA at check time.
- If `problem_statement.updated == true`: current SHA differs from `previously_ingested_sha256`.
- If `updated == false`: current SHA matches `previously_ingested_sha256` (the human affirmed no edit, so none should have happened).

**Not honored by `META_COMPILER_SKIP_HOOK`.** Non-skippable by design — the whole point is closing this gap.

**Orchestrator preflight in re-entry mode** additionally checks:

- Does `problem_change_summary` map plausibly to `revised_sections`? (E.g., summary about "new compliance requirement" with `revised_sections: [conventions]` and no `requirements` is suspicious → `WARN`.)
- Do any `carried_consistency_risks` flagged by the LLM suggest sections the human did not list as revised?

## 8. CLI Changes

### 8.1 `elicit-vision --finalize` (§ 6)

Add `_check_reentry_block_freshness`. Wire into `run_elicit_vision_finalize` when re-entry state is detected. Nonzero exit on any empty revised section, with names surfaced.

### 8.2 `stage2-reentry` (§ 7)

- New flag: `--from-request <path>`. When passed, `--reason` and `--sections` are derived from the artifact's `reason` and `revised_sections` fields. Conflicts between explicit flags and artifact values → error.
- Write `workspace-artifacts/runtime/stage2/brief.md` (re-entry variant — same structure as the fresh flow's brief, but with a `## Re-entry context` section summarizing `problem_change_summary`, `revised_sections`, and `carried_consistency_risks`).
- Write `workspace-artifacts/runtime/stage2/precheck_request.yaml`. Same shape as the fresh flow's precheck request, plus a `reentry:` block containing `parent_version`, `revised_sections`, `problem_change_summary`, `carried_consistency_risks`.
- Update the `next_step` message in `run_stage2_reentry`'s return value to point at `.github/prompts/stage2-reentry.prompt.md` (not `stage-2-dialog.prompt.md`).

Backward compat: the old `--reason` / `--sections` flag signature continues to work, for scripts and tests that predate this spec.

### 8.3 Removed from prompt bodies

When a CLI call moves to auto-fire per § 5, its bullet is deleted from the prompt body. Prompts affected: `stage-1a-breadth`, `stage-1b-evaluators`, `stage-1c-review`, `stage-2-dialog` (only `elicit-vision --start` removed; `--finalize` and `audit-requirements` remain), `stage-3-scaffold`, `stage-4-finalize`.

## 9. Shared Machinery — `meta_hook.py`

### 9.1 Location

```
.github/hooks/
├── main.json
├── overrides.json         # optional, gitignored, time-bounded kill-switch
└── bin/
    ├── meta_hook.py       # dispatch + all checks
    └── tests/
        ├── __init__.py
        ├── test_gate_cli.py
        ├── test_gate_artifact_writes.py
        ├── test_capture_output.py
        ├── test_inject_state.py
        ├── test_gate_reentry_request.py
        ├── test_require_verdict.py
        ├── test_prompt_triggers.py
        ├── test_chain_runner.py
        ├── test_overrides.py
        └── fixtures/      # sample hook-input JSON, sample manifests
```

### 9.2 Hook protocol

Each hook entry invokes:

```json
{
  "type": "command",
  "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py <check-name>",
  "timeout": 10
}
```

`meta_hook.py` reads JSON from stdin per the hook protocol, dispatches on `sys.argv[1]`, writes JSON to stdout, exits 0.

### 9.3 Checks

| Function | Reads | Emits |
|---|---|---|
| `inject_state` | Manifest | `additionalContext` |
| `gate_cli` | Manifest + `STAGE_PRECONDITIONS` | `permissionDecision` |
| `gate_artifact_writes` | `tool_input.file_path` | `permissionDecision` |
| `capture_output` | `tool_result.stdout` | `additionalContext` |
| `user_prompt_submit_dispatch` | `tool_input.prompt` + `PROMPT_TRIGGERS` | `additionalContext` (chain output) |
| `subagent_stop_dispatch` | Agent identity + chain config | `additionalContext` |
| `nudge_finalize` | Manifest | `decision: block` + reason |
| `gate_reentry_request` | `runtime/stage2/reentry_request.yaml` + live file SHAs | `permissionDecision` |
| `gate_orchestrator_mode` | `runtime/stage2/{precheck,postcheck}_request.yaml` | `permissionDecision` |
| `require_verdict` | `runtime/stage2/*verdict.yaml` | `permissionDecision` |
| `gate_ingest_workplan` | `runtime/ingest/work_plan.yaml` | `permissionDecision` |
| `require_ingest_report` | `wiki/reports/ingest_report.yaml` | `permissionDecision` |
| `require_handoff` | `wiki/reviews/1a2_handoff.yaml` | `permissionDecision` |
| `validate_findings_schema` | Hook-provided `tool_input.file_path` | `permissionDecision` |

### 9.4 Internal utilities

- `read_manifest() -> dict` — cached per invocation.
- `resolve_workspace_root() -> Path` — from `$PWD` or hook input `cwd`.
- `STAGE_PRECONDITIONS: dict[str, dict]` — state-machine table (one row per `meta-compiler` subcommand).
- `PROMPT_TRIGGERS: dict[str, list[ChainStep]]` — the § 5 auto-fire table in code form.
- `emit(obj)` — JSON-dump to stdout, flush.

### 9.5 Dependencies

Stdlib only. The hook does not use `PyYAML`. Rationale: requiring the project venv to invoke `meta_hook.py` complicates hook invocation from VSCode (each hook would need `.venv/bin/python3` resolution). A short internal parser handles the narrow YAML subset the manifest and verdict files use (key/scalar/list/nested dict). Machine-readable hook-state artifacts (findings, cascade reports, requests) are JSON where possible; where YAML exists, the subset parser handles it.

## 10. Error Handling and Overrides

### 10.1 Hook outcomes

1. **`allow`** (default). Check passes. Silent unless the hook also emits `additionalContext`.
2. **`deny`**. Precondition unmet. LLM sees `reason` + `remediation` + `audit_ref` in `systemMessage`; tool does not run.
3. **Hook error** (Python exception, malformed stdin, filesystem unavailable). **Fail-open with loud warning.** Hook emits `{"continue": true, "systemMessage": "Hook <name> crashed: <tb>. Proceeding anyway; workspace integrity not enforced for this call."}`. A broken hook must not freeze the pipeline.

### 10.2 Denial message shape

```json
{
  "permissionDecision": "deny",
  "reason": "<one sentence: what precondition failed>",
  "remediation": "<exact command or action that would unblock>",
  "audit_ref": "workspace-artifacts/runtime/hook_audit.log:<line>"
}
```

### 10.3 Override tiers

**Tier 1 — per-call env flag.** `META_COMPILER_SKIP_HOOK=1` in the tool call env. Honored by: `gate_cli`, `gate_artifact_writes`, and the auto-fire chain runner's child invocations. **Not honored by**: `gate_reentry_request`, `gate_orchestrator_mode`, `require_verdict`, `validate_findings_schema`. Those are the contracts the whole system relies on.

**Tier 2 — config file.** `.github/hooks/overrides.json` (optional, gitignored, human-authored):

```json
{
  "disable_checks": ["nudge_finalize"],
  "disable_until": "2026-05-01T00:00:00Z",
  "reason": "Temporarily disabling mid-flight nudge while refactoring Stage 2.",
  "approved_by": "human"
}
```

Listed checks short-circuit with `systemMessage` noting the override and expiry. Expired overrides are treated as absent. Use case: "the hook is correct but annoying during active development."

### 10.4 Audit log

`workspace-artifacts/runtime/hook_audit.log`, append-only, one JSON line per event:

```
{"ts": "2026-04-18T14:52:03Z", "check": "gate_cli", "event": "PreToolUse", "matcher": "Bash", "command": "meta-compiler scaffold", "decision": "deny", "reason": "last_completed_stage is 2-reentry-seeded, scaffold requires 2", "override": null}
{"ts": "2026-04-18T14:52:18Z", "check": "gate_cli", "event": "PreToolUse", "matcher": "Bash", "command": "meta-compiler scaffold", "decision": "allow_override", "reason": "META_COMPILER_SKIP_HOOK=1", "override": "env"}
{"ts": "2026-04-18T14:53:41Z", "check": "capture_output", "event": "PostToolUse", "matcher": "Bash", "command": "meta-compiler ingest --scope all", "decision": "inject", "stdout_bytes": 1842}
```

Suppressed from audit: `inject_state` firing on `UserPromptSubmit` with no mid-flight state to surface. Too noisy, no signal.

Log is cleaned only by `meta-compiler clean-workspace --target-stage 0`.

### 10.5 Test mode

Setting `META_COMPILER_HOOK_TEST=1` suppresses audit log writes. Keeps the test suite hermetic.

## 11. Testing

### 11.1 Layer 1 — `meta_hook.py` unit tests

Location: `.github/hooks/bin/tests/`. CI runs `pytest tests/ .github/hooks/bin/tests/ -v`.

- `test_gate_cli.py` — parametrized across `STAGE_PRECONDITIONS`: (command, manifest-state, expected-decision). Covers correct order, wrong order, env override, missing manifest.
- `test_gate_artifact_writes.py` — deny on `decision-logs/*.yaml` and `seeds/**`; allow on `wiki/v2/*.md` and `runtime/stage2/transcript.md`.
- `test_capture_output.py` — well-formed CLI JSON → parsed and re-emitted; non-JSON stdout → wrapped in code fence and emitted.
- `test_inject_state.py` — fresh / mid-flight-reentry / post-stage-2 workspaces.
- `test_gate_reentry_request.py` — missing request, stale SHA, `updated=false` with matching SHA, `updated=true` with identical SHA (should deny).
- `test_require_verdict.py` — missing / malformed / valid verdicts.
- `test_prompt_triggers.py` — `/stage-1a-breadth` triggers chain; unrelated prompt is no-op.
- `test_chain_runner.py` — successful chain; middle-step failure stops chain.
- `test_overrides.py` — env-flag honored where allowed, rejected where not; expired `overrides.json` treated as absent; future expiry disables check.

Each check has ≥ 3 test cases (happy / denial / edge).

### 11.2 Layer 2 — CLI hardening tests

Additions to `tests/`:

- `tests/test_stage2_reentry_freshness.py` — all-carried → nonzero with names; ≥1 fresh per revised → success; mixed → fails naming only the stale sections.
- `tests/test_stage2_reentry_request.py` — `--from-request` derives `--reason` / `--sections`; conflicts between artifact and explicit flags → error; missing artifact when `--from-request` passed → error. Also asserts `brief.md` + `precheck_request.yaml` are written.
- `tests/test_stage2_reentry.py` (existing) — update to assert new artifacts.

### 11.3 Layer 3 — integration smoke test

`tests/test_hooks_integration.py` — creates a workspace fixture at Stage 1C, invokes `meta_hook.py` as a subprocess with simulated hook-input JSON for a realistic event sequence, asserts the audit log contains the expected decisions. Does not require VSCode. The hook binary IS the contract.

### 11.4 Layer 4 — agent hook frontmatter sanity

`tests/test_agent_hooks_frontmatter.py` — parses every `.github/agents/*.agent.md`, extracts the `hooks:` block, validates it against the hook-config schema, and asserts each referenced check name resolves to a function in `meta_hook.py`. Catches typos and drift.

### 11.5 Not tested (by design)

- Actual Copilot runtime behavior. Covered by the manual smoke tests in § 12.
- Claude Code interaction — out of scope.

## 12. Rollout

Each step leaves the system runnable. If a step breaks, revert only that step.

1. **Add `meta_hook.py` + unit tests.** No hook registrations yet. No behavior change.
2. **Register `SessionStart` + `UserPromptSubmit` → `inject_state`.** Safest — only injects context. Smoke-test in Copilot.
3. **Register `PostToolUse` → `capture_output` on `Bash`.** Also safe — no gating.
4. **Add CLI hardening (§ 6).** Re-entry block-freshness check in `elicit_stage.py`. Independent of hooks.
5. **Add `--from-request` flag + `brief.md` / `precheck_request.yaml` emission to `stage2-reentry` (§ 8.2).** Old flag surface preserved.
6. **Rewrite `.github/prompts/stage2-reentry.prompt.md` (§ 7).** Prose change; no code risk.
7. **Register `PreToolUse` → `gate_cli` + `gate_artifact_writes`.** First denying hooks. Soft-launch via `overrides.json` if needed, then remove.
8. **Register `PreToolUse` → `gate_reentry_request`.** Non-skippable; closes the Stage 2 re-entry gap.
9. **Add agent-scoped hooks (§ 4).** One agent at a time: `stage2-orchestrator` → `ingest-orchestrator` → `seed-reader` → `stage-1a2-orchestrator`. Each merge after manual walk. Also: `meta-init` writes `chat.useCustomAgentHooks: true`; this repo's `.vscode/settings.json` updated in-place.
10. **Register auto-fire chains (§ 5).** `UserPromptSubmit` triggers for stage-1a-breadth, stage-2-dialog, stage-3-scaffold, stage-4-finalize. `SubagentStop` chains for `ingest-orchestrator`, `stage-1a2-orchestrator`. Strip corresponding CLI bullets from prompt bodies in the same commit.
11. **Register `Stop` → `nudge_finalize`.** Last — most disruptive.
12. **Docs pass.** Update `README.md`, `LLM_INSTRUCTIONS.md`, `CLAUDE.md`. Add `.github/docs/hooks.md` documenting the hook layer, overrides, audit log, per-check semantics.

One commit per step. Each revertable.

### 12.1 Manual smoke tests in VSCode Copilot Chat

No automation can prove Copilot honored a hook; these are the sign-off gates.

- **After step 2**: open workspace, observe SessionStart context injection in chat.
- **After step 7**: attempt `meta-compiler scaffold` from a Stage 1A workspace; confirm denial with readable remediation.
- **After step 8**: attempt `meta-compiler stage2-reentry --reason X --sections Y` without authoring `reentry_request.yaml`; confirm denial.
- **After step 10**: invoke `/stage-1a-breadth`; confirm `ingest --scope all` fired automatically and the LLM proceeded to the orchestrator fan-out without touching any CLI itself.
- **End-to-end after step 12**: complete a full Stage 0 → Stage 2 → Stage 2 re-entry → Stage 3 → Stage 4 walk against a real problem statement. Verify audit log captures the expected sequence.

### 12.2 Migration for in-flight workspaces

No migration required. Hooks read the existing manifest. A workspace mid-Stage-2-re-entry when hooks ship will have `last_completed_stage: "2-reentry-seeded"` set; `nudge_finalize` will reveal itself on next session end, which is correct.

### 12.3 Rollback

Any step revertable via one commit. `overrides.json` is the in-production kill-switch for an individual check without requiring a VSCode restart.

## 13. Relation to Existing Docs

- **`.github/docs/stage-2-hardening.md`** — the original Stage 2 prompt-as-conductor spec. This spec extends that pattern in two ways: (a) applies it to re-entry (which was left prose); (b) adds a hook layer that enforces the pattern's discipline mechanically.
- **`CLAUDE.md`** — the stage pipeline description needs a new note about the hook layer and the re-entry hardening. Updated in rollout step 12.
- **`README.md`** — needs a new "Hooks and Determinism" section documenting the `meta_hook.py` module, the `overrides.json` kill-switch, and the audit log. Updated in rollout step 12.
- **Future `.github/docs/prompt-as-conductor.md`** — the hardening spec mentions extracting the pattern after it's validated against a second roadmap item. This spec validates it against re-entry, which is close but not identical (re-entry adds Step 0). After ingest-orchestrator gets the same treatment, the extraction is worth doing.

## 14. Open Questions

- **`PyYAML` vs stdlib parser.** Committed to stdlib to keep hook invocation from VSCode simple. If the internal YAML subset parser turns out to be fragile in practice, revisit: invoke hooks via `.venv/bin/python3` and depend on PyYAML directly.
- **Agent hooks in provisioned workspaces.** `meta-init` provisions `.github/agents/` into downstream workspaces. Agent-scoped hooks will come along via the frontmatter. But `meta_hook.py` itself lives in the meta-compiler repo's `.github/hooks/bin/`. Provisioned workspaces would need their own copy (or a symlink). Decision deferred — this spec targets meta-compiler itself first.
- **Interaction with `meta-compiler run-all`.** `run-all` is a CLI orchestrator that spawns multiple `meta-compiler` subprocesses. Each subprocess's `PreToolUse` gate would fire. Current expectation: `run-all` sets `META_COMPILER_SKIP_HOOK=1` for its children, because `run-all` itself is the ordering authority inside its own run. Confirm during rollout step 7.

## 15. Non-Goals

- Replacing the existing orchestrator agents (`stage2-orchestrator`, `ingest-orchestrator`, etc.). Their semantic-judgment role is unchanged.
- Replacing the `meta-compiler` CLI's own validation (`validate-stage`, `validate_decision_log`). Hooks sit above the CLI; the CLI's integrity layer is unchanged.
- Enforcing determinism in runtimes other than VSCode Copilot Chat.
- Making the `meta_hook.py` module extensible by downstream workspaces. Deferred to a later spec once the pattern is proven.
