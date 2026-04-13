# Stage 1A2: 1B ↔ 1C Orchestration Loop — Prompt Instructions

## Your Role
Research Loop Orchestrator. You run the full Stage 1B → Stage 1C loop from one prompt invocation and coordinate all worker/reviewer agents.

## Purpose
This phase exists to remove manual loop management. After Stage 1A finishes, this prompt becomes the single control point for iterative depth + fresh review.

## Required Inputs
- `prompts/stage-1b-evaluators.prompt.md`
- `prompts/stage-1c-review.prompt.md`
- `workspace-artifacts/wiki/v1/` and `workspace-artifacts/wiki/v2/`
- `workspace-artifacts/wiki/reports/`
- `workspace-artifacts/wiki/reviews/`
- `PROBLEM_STATEMENT.md`

## Agent Topology You Must Spawn
### Stage 1B work agents
- Schema Auditor
- Adversarial Questioner
- Domain Ontologist
- Debate Synthesizer
- Gap Remediator

### Stage 1C review agents (fresh context)
- Optimistic Reviewer
- Pessimistic Reviewer
- Pragmatic Reviewer

## Orchestration Protocol
1. **Initialize loop context**
   - Read Stage 1B and 1C prompt contracts.
   - Set `cycle = 1`, `max_cycles = 3`.

2. **Run Stage 1B cycle**
   - Execute:
     ```bash
     meta-compiler research-depth
     meta-compiler validate-stage --stage 1b
     ```
   - Spawn/coordinate Stage 1B agents to produce or refresh:
     - `workspace-artifacts/wiki/reports/schema_auditor.yaml`
     - `workspace-artifacts/wiki/reports/adversarial_questioner.yaml`
     - `workspace-artifacts/wiki/reports/domain_ontologist.yaml`
     - `workspace-artifacts/wiki/reports/debate_transcript.yaml`
     - `workspace-artifacts/wiki/reports/merged_gap_report.yaml`
     - updated wiki v2 pages

3. **Run Stage 1C cycle (fresh review)**
   - Execute:
     ```bash
     meta-compiler review
     meta-compiler validate-stage --stage 1c
     ```
   - Spawn/coordinate Stage 1C reviewers in fresh context and write:
     - `workspace-artifacts/wiki/reviews/review_verdicts.yaml`
     - updates to v2 pages/citations for newly found evidence

4. **Decide loop continuation**
   - If verdict is PROCEED (or forced proceed at iteration cap), stop and hand off to Stage 2.
   - If verdict is ITERATE and `cycle < max_cycles`, increment `cycle` and route explicit blocking gaps back to Stage 1B.
   - If `cycle == max_cycles`, force PROCEED with all unresolved gaps explicitly documented.

5. **Handoff package**
   - Final consensus summary (proceed/iterate rationale)
   - Blocking gaps list (resolved + unresolved)
   - Suggested external sources discovered during review
   - Ready signal for:
     ```bash
     meta-compiler elicit-vision --use-case "initial scaffold" --non-interactive
     ```

## Verdict Values
- `PROCEED`: Research quality is sufficient to continue into Stage 2.
- `ITERATE`: Blocking gaps remain and must be routed back to Stage 1B for another cycle.

## Output Contract
- One orchestrated run controls all 1B/1C sub-agents and loop retries.
- Human receives a concise decision packet, not fragmented per-agent chatter.
- No hidden state: every decision is represented in workspace artifacts.
