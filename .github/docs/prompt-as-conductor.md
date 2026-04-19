# Prompt-as-Conductor Pattern

## Why this pattern exists

Stages that fan out to multiple LLM subagents need a conductor — something
that decides "are we ready to start? did each subagent do its job? are we
done?" The CLI cannot be the conductor: it has no judgment. A single LLM
chat cannot be the conductor either: it loses fidelity over a long session
and tends to skip CLI bookends.

The compromise is a **prompt-as-conductor**: a stage prompt that explicitly
walks five steps — three CLI calls and two LLM agent invocations — with
hooks enforcing the order. The CLI is the deterministic substrate, the
agent is the judge, the prompt is the score.

## The five steps

| # | Step                       | Actor      | Output                                  |
|---|----------------------------|------------|-----------------------------------------|
| 1 | Mechanical prep            | CLI        | `<stage>_request.yaml` for the agent    |
| 2 | Preflight verdict          | Agent      | `preflight_verdict.yaml` (PROCEED/BLOCK)|
| 3 | LLM fan-out                | Subagents  | Per-task deliverables                   |
| 4 | Mechanical compile         | CLI        | Stage-final manifest + artifacts        |
| 5 | Postflight verdict         | Agent      | `postcheck_verdict.yaml` (PROCEED/REVISE)|

Each step has a single clear gate. The next step cannot start until the
previous step's artifact is on disk.

### Step 1 — Mechanical prep

A CLI command writes a request file enumerating exactly what the agent must
check. Schema is owned by the CLI; the agent never invents the request.
Examples: `meta-compiler ingest-precheck --scope new`,
`meta-compiler elicit-vision --start`, `meta-compiler phase4-finalize --start`.

The request must include `verdict_output_path` so the agent knows where to
write its decision. The request itself is also written even on a failed
preflight — operators need the evidence to diagnose blockers.

### Step 2 — Preflight verdict

The orchestrator agent reads the request, performs *semantic* checks the CLI
cannot (does the seed coverage match the problem statement? Are the
implementer agents that the dispatch plan names actually defined? Is the
Stage 1C handoff PROCEED?), and writes a verdict file with one of two
results:

- `verdict: PROCEED` — clear to fan out
- `verdict: BLOCK` — surface remediation; do not fan out

`SubagentStop` hooks prevent the agent from stopping without writing the
verdict. `PreToolUse` hooks block the next CLI call if no verdict exists
or the verdict is BLOCK.

### Step 3 — LLM fan-out

The conductor prompt invokes per-task subagents. Maximum 4 in parallel.
Each subagent's deliverable lands in a known directory.

This step has no CLI call. The CLI cannot do reasoning; the LLM cannot do
mechanics. They split the work cleanly.

### Step 4 — Mechanical compile

A second CLI command consumes the fan-out outputs and writes the
stage-final manifest (e.g., `decision_log_v{N}.yaml`,
`FINAL_OUTPUT_MANIFEST.yaml`, `ingest_report.yaml`). This is again schema-
owned and deterministic. The CLI also writes the **postcheck request** the
postflight agent will read.

If the fan-out left a known directory empty, the CLI raises here. The hook
chain typically blocks the call before that point.

### Step 5 — Postflight verdict

The orchestrator agent runs a second time, this time auditing the compiled
output for fidelity (do quotes match the source text? Do declared outputs
exist on disk? Do citations resolve?). It writes its verdict:

- `verdict: PROCEED` — stage complete
- `verdict: REVISE` — re-run Step 3 for the failing items, then Step 4 + 5

REVISE differs from BLOCK: it acknowledges the work happened but is not
yet acceptable. The conductor prompt loops back to Step 3 only for the
items the postflight flagged.

## Canonical implementations

Three stages use this pattern as of 2026-04. Use them as the reference when
adding the pattern to a new stage:

| Stage                | Conductor prompt                                | CLI commands                                      | Orchestrator agent             |
|----------------------|-------------------------------------------------|---------------------------------------------------|--------------------------------|
| Stage 2 vision       | `.github/prompts/stage-2-dialog.prompt.md`      | `elicit-vision --start` / `--finalize`            | `stage2-orchestrator`          |
| Ingest (Stage 1A)    | `.github/prompts/ingest-orchestrator.prompt.md` | `ingest-precheck` / `ingest-postcheck`            | `ingest-orchestrator`          |
| Stage 4 execution    | `.github/prompts/stage-4-finalize.prompt.md`    | `phase4-finalize --start` / `--finalize`          | `execution-orchestrator`       |

Each follows the same shape. Stage 1A2 revisit (when added) should follow
the same structure.

## Hook contract

Every conductor stage relies on three hook gates:

- **`gate_<stage>_<bookend>`** (PreToolUse on Bash) — blocks the CLI if the
  previous step's artifact is missing.
- **`require_<stage>_<verdict>`** (SubagentStop) — blocks the agent from
  stopping without writing its verdict.
- **`gate_<stage>` on tool/file invocations** — for stages with downstream
  artifacts (e.g., scaffold), blocks the next stage's CLI if the postflight
  verdict is missing or REVISE.

Hook handlers live in `.github/hooks/bin/meta_hook.py`. Per-agent hooks are
declared in agent frontmatter (see `.github/agents/ingest-orchestrator.agent.md`
for the canonical example).

## When to use this pattern

Apply prompt-as-conductor when *all three* are true:

1. The stage involves multiple LLM subagent invocations (fan-out).
2. The judgment about readiness or fidelity is semantic (a deterministic
   check is insufficient).
3. The stage produces a hand-off artifact downstream stages depend on.

Stages that are pure CLI (e.g., `meta-init`) or pure judgment with no
fan-out (e.g., a single reviewer agent) do not need this pattern.

## When NOT to use it

- One-shot deterministic compute (use a plain CLI command).
- Pure dialog without compute (use a single agent + transcript).
- Steps where humans should pick the verdict (use a confirmation prompt
  instead).

The pattern is heavyweight; the five-step ceremony is justified only when
the stage genuinely benefits from a separation between mechanical and
semantic work.
