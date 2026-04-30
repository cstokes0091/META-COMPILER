# Stage 4: Execute + Pitch — Prompt-as-Conductor

## Intent

**Build an LLM-accessible knowledge base to make an LLM a domain and problem-space
expert before any task is posited.** Stage 4 proves the knowledge base worked: the
capability graph executes, the deliverables are real, and the pitch tells the story
of how research became a product.

Stage 4 follows the same prompt-as-conductor pattern as Stage 2 ingest:

1. CLI mechanical prep (`phase4-finalize --start`) — also writes a
   per-capability `_dispatch.yaml` denormalizing every plan-extract field
   the implementer + reviewer need (Change E removed the Stage 4 planner
   agent; planning lives upstream in Stage 2.5).
2. Preflight verdict (`@execution-orchestrator mode=preflight`)
3. Per-capability work loop: implementer → reviewer (always) → researcher
   (only when the reviewer's verdict has `gap_kind: knowledge_gap`). The
   orchestrator routes deterministically; there is no Stage 4 planning
   step.
4. CLI mechanical compile (`phase4-finalize --finalize`)
5. Postflight verdict (`@execution-orchestrator mode=postflight`)

The conductor is *this prompt*. The CLI never holds the loop — the LLM does.

## Your Role
Conduct the Stage 4 work loop against the static three-agent palette
(`implementer`, `reviewer`, `researcher`). You will:
- Trigger the dispatch-plan write (this also emits each capability's
  `work/<cap>/_dispatch.yaml`)
- Invoke the orchestrator agent for preflight readiness
- Drive the per-capability loop: implementer → reviewer (every iteration)
  → researcher (only on `gap_kind: knowledge_gap`)
- Trigger the final manifest + pitch compile
- Invoke the orchestrator for postflight fidelity audit

You do NOT inline-execute implementer work yourself. You delegate to the
three-agent palette. There is no Stage 4 `@planner` agent — planning is
upstream in Stage 2.5; the implementer reads `_dispatch.yaml` directly.

## Step 1 — Mechanical prep (CLI)

Run:

```bash
meta-compiler phase4-finalize --start
```

This writes:
- `workspace-artifacts/executions/v{N}/dispatch_plan.yaml` — the
  capability-keyed assignment list, derived from the scaffold's
  `DISPATCH_HINTS.yaml`. Each entry carries `capability`,
  `skill_path`, `contract_ref`, `expected_work_dir`, `dispatch_path`,
  `dispatch_kind` (`hitl|afk`), and `verification_spec_paths`.
  (`assigned_agent` is no longer present — Change E removed the Stage
  4 planner.)
- `workspace-artifacts/executions/v{N}/work/<capability>/_dispatch.yaml`
  per capability — full v2.1 plan-extract denormalization (user_story,
  the_problem, the_fix, anti_patterns, out_of_scope, dispatch_kind,
  acceptance_spec_path, etc.). The implementer reads this directly at
  Step 0; the reviewer reads it during the audit phases.
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

## Step 3 — Per-capability work loop (deterministic routing)

Read `executions/v{N}/dispatch_plan.yaml`. The orchestrator agent
already validated it in Step 2. **There is no Step 3a planner sub-step
any more — Change E removed the Stage 4 planner agent. Routing is
deterministic: for each capability the orchestrator runs implementer →
reviewer (every iteration) → researcher (only when the reviewer's
verdict has `gap_kind: knowledge_gap`).**

Group assignments by `dispatch_kind`. AFK capabilities can run as one
unattended batch; HITL capabilities require operator confirmation
before each starts. Within a batch, capabilities with
`parallelizable: true` and no shared `composes` link can run in
parallel.

For each capability:

```
load _dispatch.yaml + SKILL.md + CONTEXT.md  (fresh read set)
attempt = 1
loop:
    @implementer  ← writes work/<cap>/tests/test_acceptance.py at
                    Step 0 (translating verification/<hook>_spec.yaml
                    into pytest), confirms RED, then runs tracer-bullet
                    TDD on internals (test_unit_*.py).
    @reviewer     ← runs FIDELITY_AUDIT → RED → GREEN → UNIT_TESTS →
                    ANTI_PATTERN_AUDIT → OUT_OF_SCOPE_AUDIT →
                    VOCABULARY_AUDIT → USER_STORY_AUDIT; writes
                    work/<cap>/_verdict.yaml. NEVER edits any test file.
    if verdict.decision == "PROCEED": break
    if attempt >= max_attempts:
        BLOCK + emit stage2-reentry hint
    if verdict.gap_kind == "knowledge_gap":
        @researcher  ← reads the verdict's gap_statement, produces
                        either work/<cap>/_research.md (in-corpus) or
                        work/<cap>/_gap_escalation.yaml (out-of-corpus,
                        operator pause).
    # all other gap_kinds (anti_pattern, out_of_scope, vocab_drift,
    # user_story_gap) loop straight back to @implementer
    attempt += 1
```

If an agent returns errors, retry once; on second failure, mark the
assignment `status: failed` in `dispatch_plan.yaml` and continue.

### Reviewer details

The reviewer:

- Runs in fresh context — it did not write the artifact it reviews.
- Reads `verification/<hook_id>_spec.yaml` (the planner-frozen
  acceptance spec; Change B replaced the legacy `.py` stubs) and runs
  the implementer's own `work/<cap>/tests/test_acceptance.py` plus
  every `test_unit_*.py` in the work dir.
- **Never edits any test file** (`work/<cap>/tests/*.py` is implementer-
  owned; `verification/<hook>_spec.yaml` is planner-owned). Earlier
  versions instructed the reviewer to "upgrade xfail markers into real
  assertions against the implementer's outputs" — that's the conflict-
  of-interest Change C removed. If a test is wrong, ITERATE; the
  implementer fixes it.
- Runs FIDELITY_AUDIT first (each scenario in the spec maps to a real
  pytest function with a real call site + real assertion). Trivially-
  passing tests (`assert True`) are caught here.
- Confirms every contract invariant holds against the outputs.
- Writes `_verdict.yaml` with new fields: `decision: PROCEED | ITERATE
  | BLOCK`, `gap_kind`, `gap_statement`, `fidelity_audit_passed`,
  `acceptance_red_observed`, `acceptance_green_observed`, unit test
  counts, plus per-audit violation lists.

### Iteration cap

Cap at 3 cycles per capability. On cycle 3 force-advance and append
an `open_item` to `executions/v{N}/ralph_loop_log.yaml`.

`BLOCK` verdicts indicate the contract needs to change upstream — halt
and surface the issue rather than forcing a cycle. Same for
`gap_kind: knowledge_gap` paths where the researcher writes
`_gap_escalation.yaml`: pause for operator decision (add seed,
`/wiki-enrich`, or Stage 2.5 re-entry).

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
