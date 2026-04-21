# Stage 4: Execute + Pitch — Prompt-as-Conductor

## Intent

**Build an LLM-accessible knowledge base to make an LLM a domain and problem-space
expert before any task is posited.** Stage 4 proves the knowledge base worked: the
scaffold runs, the deliverables are real, and the pitch tells the story of how
research became a product.

Stage 4 follows the same prompt-as-conductor pattern as Stage 2 ingest:

1. CLI mechanical prep (`phase4-finalize --start`)
2. Preflight verdict (`@execution-orchestrator mode=preflight`)
3. LLM ralph loop fan-out (per-agent implementer subagents)
4. CLI mechanical compile (`phase4-finalize --finalize`)
5. Postflight verdict (`@execution-orchestrator mode=postflight`)

The conductor is *this prompt*. The CLI never holds the loop — the LLM does.

## Your Role
Conduct the Stage 4 ralph loop. You will:
- Trigger the dispatch-plan write
- Invoke the orchestrator agent for preflight readiness
- Fan out implementer subagents (one per dispatch_plan assignment)
- Trigger the final manifest + pitch compile
- Invoke the orchestrator for postflight fidelity audit

You do NOT inline-execute the implementer work yourself. You delegate.

## Step 1 — Mechanical prep (CLI)

Run:

```bash
meta-compiler phase4-finalize --start
```

This writes:
- `workspace-artifacts/executions/v{N}/dispatch_plan.yaml` — the per-agent
  assignment list, derived from the scaffold's `AGENT_REGISTRY.yaml`.
- `workspace-artifacts/runtime/phase4/execution_request.yaml` — the
  preflight request the orchestrator agent will read.
- `workspace-artifacts/executions/v{N}/work/` — the directory each
  implementer agent will populate.

If the scaffold is missing or the decision-log version cannot be resolved,
this step fails. Fix the root cause (re-run `meta-compiler scaffold`) before
proceeding.

## Step 2 — Preflight (orchestrator agent)

Invoke the scaffold-generated `execution-orchestrator` agent in preflight mode:

```
@execution-orchestrator mode=preflight
```

It reads `runtime/phase4/execution_request.yaml`, judges whether the dispatch
plan is sane (no missing implementer agents, no impossible assignments,
scaffold contract intact), and writes its verdict to
`runtime/phase4/preflight_verdict.yaml` with `verdict: PROCEED | BLOCK`.

If `verdict: BLOCK`: read the verdict's `remediation` field, fix the issue,
re-run Step 1, then re-invoke the orchestrator. Do not proceed to fan-out
with a BLOCK verdict.

## Step 3.0 — Registry coverage check

Before any fan-out, read `scaffolds/v{N}/AGENT_REGISTRY.yaml` and assert the
registry owns the actual build work, not just refinement. Fail fast here;
fixing this after fan-out wastes a run.

For `project_type ∈ {algorithm, hybrid}`: at least one registry entry MUST
have `output_kind: code` AND a `responsibility` containing "implement",
"translate", or "write". `algorithm-implementer` is the canonical entry.
Agents whose responsibility only says "normalize", "reduce", or "apply" do
NOT satisfy this check — they are refiners.

For `project_type ∈ {report, hybrid}`: at least one registry entry MUST
have `output_kind: document` AND a `responsibility` declaring ownership of
`report/DRAFT.md` / `report/OUTLINE.md`. `report-writer` is the canonical
entry.

If either check fails, halt with:

```
ERROR: AGENT_REGISTRY.yaml lacks an implementer for project_type=<type>.
Refiner-only registries cannot produce executable artifacts. Re-scaffold
after updating meta_compiler/stages/scaffold_stage.py::_canonical_agents.
```

Do NOT continue to Step 3 with a refiner-only registry.

## Step 3 — Ralph loop fan-out (tiered)

Read `executions/v{N}/dispatch_plan.yaml`. Fan-out runs in three tiers;
later tiers consume earlier outputs, so do NOT flatten them.

**Tier 1 — Implementation (sequential, must complete first):**
- `@algorithm-implementer` for `algorithm|hybrid` — writes real Python to
  `executions/v{N}/work/algorithm-implementer/code/` and tests to
  `.../tests/`. Replaces the scaffold stub, does not just re-export it.
- `@report-writer` for `report|hybrid` — writes `report/OUTLINE.md` and
  `report/DRAFT.md` with real sections and citations, not frontmatter
  only.

Both can run in parallel with each other when `project_type == hybrid`, but
neither can be skipped.

**Tier 2 — Refinement (up to 4 in parallel, Tier 1 outputs are inputs):**
- `@math-conventions-agent`, `@scope-reduction-agent` — refine Tier 1 code.
- `@citation-manager-agent`, `@style-conventions-agent`,
  `@narrative-structure-agent` — refine Tier 1 report.

Tier 2 agents must read the Tier 1 `work/<tier1-slug>/` outputs as inputs.
If a Tier 2 agent finds its Tier 1 input missing or stub-shaped, it fails
its assignment instead of producing a placeholder.

**Tier 3 — Review (sequential per implementer):**
- `@<slug>-reviewer` for every implementer that ran, in fresh context.
  Returns `PASS | REVISE`. On `REVISE` with cycle < 3, feed the verdict's
  `blocking_gaps` and `proposed_fixes` back to the implementer and retry.
  On cycle == 3, force-advance and record an `open_item` in
  `executions/v{N}/ralph_loop_log.yaml`.

If an agent returns errors, retry once with the failure cited; on second
failure, mark the assignment `status: failed` in the dispatch_plan and
continue.

Do NOT bypass the dispatch plan. Every deliverable must trace to one named
agent.

## Step 4 — Mechanical compile (CLI)

Once all assignments are complete (or marked failed), run:

```bash
meta-compiler phase4-finalize --finalize
```

This:
- Walks `executions/v{N}/work/` and compiles
  `executions/v{N}/FINAL_OUTPUT_MANIFEST.yaml` from the LLM-written files.
- Refreshes `workspace-artifacts/wiki/provenance/what_i_built.md`.
- Generates `workspace-artifacts/pitches/pitch_v{N}.md` and
  `pitch_v{N}.pptx`.
- Writes `runtime/phase4/postcheck_request.yaml` for the next step.

If `executions/v{N}/work/` is empty when this CLI runs, the
`gate_phase4_finalize` hook blocks the call. Conduct Step 3 properly first.

## Step 5 — Postflight (orchestrator agent)

Invoke the orchestrator a second time:

```
@execution-orchestrator mode=postflight
```

It reads the `FINAL_OUTPUT_MANIFEST.yaml` and the dispatch plan and issues
`verdict: PROCEED | REVISE` against concrete criteria. Vague "deliverable
fidelity" is not enough; the postflight MUST enforce the following and name
specific files on failure.

**For `algorithm|hybrid` project types:**
- `executions/v{N}/work/algorithm-implementer/code/main.py` (or equivalent)
  contains `def`/`class` definitions and calls beyond the scaffold stub
  `def run_workflow() -> None: return None`. A file whose entire function
  body is `return None` or `pass` fails.
- At least one file under
  `executions/v{N}/work/algorithm-implementer/tests/` imports from the
  implementer's `code/` and invokes something — not just `assert True`.
- Every `REQ-NNN` listed in `requirements/REQ_TRACE_MATRIX.md` appears in
  at least one code file or test.

**For `report|hybrid` project types:**
- `executions/v{N}/work/report-writer/report/DRAFT.md` contains section
  headers (`##` or `###`) AND citation markers (`[src-*]` or `[REQ-*]`).
  Frontmatter-only files fail.
- `executions/v{N}/work/report-writer/report/OUTLINE.md` covers every
  requirement ID from `REQ_TRACE_MATRIX.md`.

**For every project type:**
- Every `assignment` in `dispatch_plan.yaml` with `status: completed` has
  at least one non-empty file in its `work/<slug>/` directory.
- `pitches/pitch_v{N}.pptx` was written and its slide content references
  files that actually exist in `work/`.

`REVISE` means at least one criterion failed. The verdict MUST list the
specific file(s) and criterion. Re-run Step 3 (Tier 1 first) for the failing
assignments, then re-run Step 4 + Step 5.

## Validation

After PROCEED, run:

```bash
meta-compiler validate-stage --stage 4
```

This is the final gate. If it returns issues, address them and re-run.

## Output
- `executions/v{N}/dispatch_plan.yaml` — preflight assignment record
- `executions/v{N}/work/<agent>/` — per-agent deliverables (LLM-written)
- `executions/v{N}/FINAL_OUTPUT_MANIFEST.yaml` — compiled manifest
- `wiki/provenance/what_i_built.md` — refreshed product summary
- `pitches/pitch_v{N}.md` + `pitch_v{N}.pptx` + `pitch_v{N}.yaml`
- `runtime/phase4/preflight_verdict.yaml` + `postcheck_verdict.yaml`

## Guiding Principles
- **Conductor, not soloist.** Delegate every implementer task; never inline-write a deliverable yourself.
- **Auditable trail.** Every file in `executions/v{N}/work/` must trace to one agent in the dispatch plan.
- **Data over folklore.** Pitch claims reference specific evidence from the wiki and Decision Log.
- **Stop on BLOCK / REVISE.** Verdicts mean the loop is not done. Fix before declaring success.
