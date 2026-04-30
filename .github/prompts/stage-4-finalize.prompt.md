# Stage 4: Execute + Pitch — Prompt-as-Conductor

## Intent

**Build an LLM-accessible knowledge base to make an LLM a domain and problem-space
expert before any task is posited.** Stage 4 proves the knowledge base worked: the
capability graph executes, the deliverables are real, and the pitch tells the story
of how research became a product.

Stage 4 follows the same prompt-as-conductor pattern as Stage 2 ingest:

1. CLI mechanical prep (`phase4-finalize --start`)
2. Preflight verdict (`@execution-orchestrator mode=preflight`)
3. Ralph loop: planner decomposes, implementer fan-out, reviewer fan-out,
   revision cycles — all against the capability-keyed dispatch plan
4. CLI mechanical compile (`phase4-finalize --finalize`)
5. Postflight verdict (`@execution-orchestrator mode=postflight`)

The conductor is *this prompt*. The CLI never holds the loop — the LLM does.
Step 3 batches must be dispatched in a single message with multiple tool
calls, the same way `ingest-orchestrator.prompt.md` fans out its readers.

## Your Role
Conduct the Stage 4 ralph loop against the static agent palette
(`planner`, `implementer`, `reviewer`, `researcher`). You will:
- Trigger the dispatch-plan write
- Invoke the orchestrator agent for preflight readiness
- Fan out implementers (one per capability in `dispatch_plan.yaml`)
- Fan out reviewers (one per implementer output)
- Trigger the final manifest + pitch compile
- Invoke the orchestrator for postflight fidelity audit

You do NOT inline-execute implementer work yourself. You delegate to the
four-agent palette.

## Step 1 — Mechanical prep (CLI)

Run:

```bash
meta-compiler phase4-finalize --start
```

This writes:
- `workspace-artifacts/executions/v{N}/dispatch_plan.yaml` — the
  capability-keyed assignment list, derived from the scaffold's
  `DISPATCH_HINTS.yaml`. Each entry carries `capability`,
  `assigned_agent: planner`, `skill_path`, `contract_ref`, and
  `expected_work_dir: executions/v{N}/work/<capability_id>/`.
- `workspace-artifacts/runtime/phase4/execution_request.yaml` — the
  preflight request the orchestrator agent will read.
- `workspace-artifacts/executions/v{N}/work/` — the directory each
  capability execution will populate.

If the scaffold is missing or the decision-log version cannot be resolved,
this step fails. Fix the root cause (re-run `meta-compiler scaffold`) before
proceeding.

## Step 2 — Preflight (orchestrator agent)

Invoke the execution orchestrator in preflight mode:

```
@execution-orchestrator mode=preflight
```

It reads `runtime/phase4/execution_request.yaml`, confirms every dispatch
assignment resolves to a real skill file + contract, and writes its verdict
to `runtime/phase4/preflight_verdict.yaml` with `verdict: PROCEED | BLOCK`.

If `verdict: BLOCK`: read the verdict's `remediation` field, fix the issue
(usually by re-running the scaffold compiler chain), re-run Step 1, then
re-invoke the orchestrator. Do not proceed to fan-out with a BLOCK verdict.

## Step 3.0 — Capability coverage check

Before any fan-out, read `scaffolds/v{N}/verification/REQ_TRACE.yaml` and
confirm every Stage 2 `REQ-NNN` maps to at least one capability + hook_id.
The `validate_capability_coverage` hook would have failed at
`validate-stage --stage 3`, but re-check here because re-entry can revise
the Decision Log without re-running Stage 3.

If any requirement is uncovered, halt with the specific REQ-IDs and re-run
`meta-compiler scaffold` (or `compile-capabilities` standalone) after
adding citations/capabilities.

## Step 3 — Ralph loop fan-out (capability-keyed)

Read `executions/v{N}/dispatch_plan.yaml`. Fan-out runs in three sub-steps.
The batch sub-steps (3a, 3b) MUST use a single message with multiple `@agent`
tool calls — not a chain of one-per-message invocations.

### Step 3a — Planner decomposes (1 invocation, sequential)

Invoke `@planner` once, passing the task description (or "implement every
capability in dispatch_plan.yaml" if running the default end-to-end flow).

The planner:
- Reads `capabilities.yaml`, `skills/INDEX.md`, and `dispatch_plan.yaml`.
- Produces an ordered `planner_plan` in
  `executions/v{N}/work/_plan.yaml` that topologically orders capabilities
  by the `composes` graph.
- Assigns each capability to an agent role:
  - `implementer` for code/artifact outputs
  - `researcher` for document outputs that need evidence gathering
  - `reviewer` for verification-only capabilities

### Step 3b — Implementer + researcher fan-out (parallel, up to 4)

For every capability in `_plan.yaml` with `assigned_agent: implementer` or
`assigned_agent: researcher`, spawn one subagent. **Run up to 4 in parallel
using a single message with multiple tool calls.** Each subagent:

- Reads its `skills/<capability>/SKILL.md`, the referenced contract file,
  and the cited findings (resolved via the SKILL's `findings:` frontmatter).
- Writes deliverables under `executions/v{N}/work/<capability>/`.
- Fails its own capability with `_issue.yaml` if a required input is
  missing, rather than emitting a placeholder.

Implementers do not depend on each other's `work/` outputs; they each read
the Decision Log + findings independently.

If an agent returns errors, retry once; on second failure, mark the
assignment `status: failed` in `dispatch_plan.yaml` and continue.

### Step 3c — Reviewer fan-out (parallel, up to 4)

For every capability produced in 3b, spawn a `@reviewer` invocation. **Run
up to 4 in parallel using a single message with multiple tool calls.** Each
reviewer:

- Runs in fresh context — it did not write the artifact it reviews.
- Executes the `verification/<hook_id>.py` pytest stubs and upgrades the
  `pytest.xfail` markers into real assertions against the implementer's
  work dir.
- Confirms every contract invariant holds against the outputs.
- Writes `_verdict.yaml` in the work dir with
  `verdict: PROCEED | ITERATE | BLOCK`.

### Step 3d — Revision batch (iterative, parallel within each cycle)

Collect all `ITERATE` verdicts. Re-dispatch the failing implementers **in a
single message with multiple tool calls**, each receiving its reviewer's
`failed_hooks` and `violated` invariants. After revision, re-run the
corresponding reviewers (also as a single batched message).

Cap at 3 cycles per capability. On cycle 3 force-advance and append an
`open_item` to `executions/v{N}/ralph_loop_log.yaml`.

`BLOCK` verdicts indicate the contract needs to change upstream — halt and
surface the issue rather than forcing a cycle.

## Step 4 — Final synthesis (assemble per-capability fragments into ONE deliverable)

The ralph loop fills `work/<capability>/` with per-capability fragments —
useful for per-capability review, not yet a deliverable. The
final-synthesis sub-stage takes those fragments and assembles them into a
project-type-aware artifact under `executions/v{N}/final/<bucket>/`:

| project_type | assembled subtree |
|---|---|
| `algorithm` (code) | `final/library/<package>/...` + tests + README + optional pyproject |
| `report` (document) | `final/document/<slug>.md` + `references.md` + `.docx` |
| `hybrid` | both `final/library/` and `final/document/` |
| `workflow` | `final/application/run.py` + bucketed layout + `requirements.txt` + `README.md` |

This sub-stage uses the canonical preflight CLI → orchestrator fan-out →
postflight CLI pattern. Walk the three sub-steps in order.

### Step 4a — Final-synthesis preflight (CLI)

```bash
meta-compiler final-synthesize-start
```

Walks `executions/v{N}/work/`, classifies every fragment, and writes
`runtime/final_synthesis/work_plan.yaml` plus `synthesis_request.yaml`.
Branches on `project_type` from the EXECUTION_MANIFEST to determine which
modalities (`library` / `document` / `application`) the orchestrator must
fan out for.

### Step 4b — Per-modality fan-out (LLM)

Invoke `.github/prompts/final-synthesis.prompt.md`. The conductor prompt
fans out one synthesizer per modality (≤2 modalities ⇒ both in parallel)
and persists each return JSON verbatim to
`runtime/final_synthesis/subagent_returns/<modality>.json`.

The synthesizers do NOT write files in `final/`. They return structured
JSON describing layouts, exports, section orderings, and entry points.

### Step 4c — Final-synthesis postflight (CLI)

```bash
meta-compiler final-synthesize-finalize
```

Validates each return against schema. Materializes
`executions/v{N}/final/.tmp/` from the proposals. Runs the REQ-trace
continuity check (every `REQ-NNN` that appeared in work/ fragments must
still appear under final/). Atomically swaps `.tmp/` into
`executions/v{N}/final/`. Emits `executions/v{N}/final_synthesis_report.yaml`.

If the REQ-trace check fails with `synthesis_drops`, your options are:

1. Re-run the synthesizers (re-invoke the conductor prompt) — the
   subagent prompts demand REQ preservation, so a retry usually fixes
   it.
2. Pass `--allow-req-drop REQ-007,REQ-012` to the postflight to
   acknowledge the drop. Each allowed drop is logged in the report.

If `final/` already exists and contains files edited after the last
report, the postflight refuses to overwrite. Pass `--force` to override.

## Step 5 — Mechanical compile + pitch sub-loop

The deck is built in three sub-steps. Walk them in order; do not skip.

### Step 5a — Evidence and final manifest (CLI)

```bash
meta-compiler phase4-finalize --pitch-step=evidence
```

This:
- Compiles `executions/v{N}/FINAL_OUTPUT_MANIFEST.yaml`. When Step 4 ran
  successfully, `deliverables[]` lists the assembled artifacts under
  `final/<bucket>/` and `synthesis_status: synthesized`. Per-capability
  files are demoted to `fragments[]` for audit. When Step 4 was skipped,
  the manifest falls back to the legacy `synthesis_status:
  fragments_only` shape.
- Refreshes `workspace-artifacts/wiki/provenance/what_i_built.md`.
- Builds `workspace-artifacts/runtime/phase4/evidence_pack.yaml` — typed
  facts (problem, architecture, code-architecture, deliverables,
  REQ-traced vs REQ-orphan, open items, citations, execution summary).
  Every fact gets a stable `ev-...` ID. When `final/` exists, the pack
  additionally exposes `assembled_deliverables[]` keyed by `ev-final-NNN`
  — the pitch-writer should prefer those on the `built` slide.
- Writes `workspace-artifacts/runtime/phase4/pitch_request.yaml` — the
  entry point for the `@pitch-writer` agent.

If `executions/v{N}/work/` is empty when this CLI runs, `phase4-finalize`
raises a clear error pointing back at Step 3. (There is no legacy
`orchestrator/run_stage4.py` subprocess fallback — it was removed when
the scaffold flipped to the capability-keyed shape.)

### Step 5b — Draft the deck (LLM)

Invoke:

```
@pitch-writer
```

The agent reads the evidence pack and writes `runtime/phase4/slides.yaml`.
Every bullet cites at least one evidence ID; every required slide role
(`title | problem | approach | built | evidence | why | cta`) is present;
orphan REQs and force-advanced capabilities are surfaced honestly. Spot-check
the draft for evidence-anchored claims; do not edit `slides.yaml` by hand.

### Step 5c — Verify and render (CLI)

```bash
meta-compiler phase4-finalize --pitch-step=render
```

Optionally pass a `.pptx` or `.potx` template:

```bash
meta-compiler phase4-finalize --pitch-step=render --pptx-template ./brand/template.potx
```

This runs the fidelity gate, loads the optional template, renders each slide
with strict layout guards, and writes `pitches/pitch_v{N}.pptx`,
`pitches/pitch_v{N}.md`, and `pitches/pitch_v{N}.yaml`. It also writes
`runtime/phase4/postcheck_request.yaml` for Step 5.

`gate_phase4_finalize` refuses `--pitch-step=render` when `slides.yaml` is
missing or older than `evidence_pack.yaml`.

Full sub-loop docs: `.github/prompts/pitch-writer.prompt.md`.

## Step 6 — Postflight (orchestrator agent)

Invoke the orchestrator a second time:

```
@execution-orchestrator mode=postflight
```

It reads `FINAL_OUTPUT_MANIFEST.yaml` and the dispatch plan and issues
`verdict: PROCEED | REVISE` against concrete criteria. Vague "deliverable
fidelity" is not enough; the postflight MUST enforce the following and name
specific files on failure.

**For final synthesis (when `synthesis_status: synthesized`):**
- `executions/v{N}/final/<bucket>/` exists and is non-empty for every
  modality the project_type requires.
- `executions/v{N}/final_synthesis_report.yaml` is fresh (mtime newer
  than the dispatch plan) and reports `req_trace_diff.synthesis_drops`
  empty OR fully covered by `allowed_req_drops[]`.
- The pitch deck cites at least one `ev-final-*` evidence ID on the
  `built` slide.

**For every capability:**
- The work dir `executions/v{N}/work/<capability>/` contains at least one
  non-empty deliverable matching the contract's `outputs[].modality`.
- Every contract invariant is cited by the reviewer's `_verdict.yaml`.
- The cited findings in the SKILL.md frontmatter match files under
  `wiki/findings/` (or resolve via the v1 bootstrap citation-index path).

**For `algorithm|hybrid` project types:**
- `executions/v{N}/work/**/code/main.py` (or equivalent) contains
  `def`/`class` definitions beyond a bare `return None` / `pass` body.
- At least one file under `**/tests/` imports the implementer's code and
  invokes something — not just `assert True`.
- Every REQ-NNN in `verification/REQ_TRACE.yaml` appears in at least one
  code file or test.

**For `report|hybrid` project types:**
- `executions/v{N}/work/**/report/DRAFT.md` contains section headers and
  citation markers (`[src-*]` or `[REQ-*]`).
- `executions/v{N}/work/**/report/OUTLINE.md` covers every REQ-NNN.

**For every project type:**
- Every `assignment` in `dispatch_plan.yaml` with `status: completed` has
  at least one non-empty file in its `work/<capability>/` directory.
- `pitches/pitch_v{N}.pptx` was written and its slide content references
  files that actually exist in `work/`.

`REVISE` means at least one criterion failed. The verdict MUST list the
specific file(s) and criterion. Re-run Step 3 (Tier 1 first) for the failing
capabilities, then re-run Step 4 + Step 5.

## Validation

After PROCEED, run:

```bash
meta-compiler validate-stage --stage 4
```

This is the final gate. If it returns issues, address them and re-run.

## Output
- `executions/v{N}/dispatch_plan.yaml` — preflight assignment record
- `executions/v{N}/work/<capability>/` — per-capability fragments
- `executions/v{N}/final/<bucket>/` — assembled deliverable (post-synthesis)
- `executions/v{N}/final_synthesis_report.yaml` — synthesis audit + REQ trace diff
- `executions/v{N}/FINAL_OUTPUT_MANIFEST.yaml` — compiled manifest
- `runtime/final_synthesis/{work_plan,synthesis_request}.yaml` + `subagent_returns/<modality>.json`
- `wiki/provenance/what_i_built.md` — refreshed product summary
- `pitches/pitch_v{N}.md` + `pitch_v{N}.pptx` + `pitch_v{N}.yaml`
- `runtime/phase4/preflight_verdict.yaml` + `postcheck_verdict.yaml`

## Guiding Principles
- **Conductor, not soloist.** Delegate every implementer task to the
  palette; never inline-write a deliverable yourself.
- **Auditable trail.** Every file in `executions/v{N}/work/` must trace to
  one capability in the dispatch plan.
- **Data over folklore.** Pitch claims reference specific evidence from
  the wiki and Decision Log.
- **Stop on BLOCK / REVISE.** Verdicts mean the loop is not done. Fix
  before declaring success.
