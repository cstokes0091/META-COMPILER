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

## Step 3 — Ralph loop fan-out (implementer subagents)

Read `executions/v{N}/dispatch_plan.yaml`. For each `assignment`:

- Invoke the named implementer agent (e.g., `@math-conventions-agent`,
  `@narrative-structure-agent`) with the responsibility and outputs from the
  assignment.
- Each agent writes its deliverables under
  `executions/v{N}/work/<agent-slug>/`.
- Up to 4 implementer agents in parallel; subsequent agents that consume
  earlier outputs run sequentially.
- If an agent returns errors, retry once with the failure cited; on second
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

It reads the `FINAL_OUTPUT_MANIFEST.yaml` and the dispatch plan, spot-verifies
deliverable fidelity (does each declared output exist? Do the files have
non-trivial content? Does the pitch deck match what was actually built?),
and writes `runtime/phase4/postcheck_verdict.yaml` with
`verdict: PROCEED | REVISE`.

`REVISE` means at least one assignment's output is missing, empty, or
inconsistent with the dispatch plan. Re-run Step 3 for the failing
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
