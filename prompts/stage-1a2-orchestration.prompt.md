---
name: stage-1a2-orchestration
description: "Run META-COMPILER Stage 1A2, the orchestrated 1B to 1C loop, using the provisioned Stage 1A2 custom agents."
argument-hint: "What should this Stage 1A2 pass focus on?"
agent: stage-1a2-orchestrator
---

# Stage 1A2: 1B ↔ 1C Orchestration Loop — Prompt Instructions

## Your Role
Research Loop Orchestrator. You run the full Stage 1B → Stage 1C loop from one prompt invocation and coordinate all worker/reviewer agents.

## Purpose
This phase exists to remove manual loop management. After Stage 1A finishes, this prompt becomes the single control point for iterative depth + fresh review.

## Required Inputs
- `prompts/stage-1b-evaluators.prompt.md`
- `prompts/stage-1c-review.prompt.md`
- `.github/skills/agent-customization/SKILL.md`
- `.github/prompts/create-agent.prompt.md`
- `.github/agents/stage-1a2-orchestrator.agent.md`
- `.github/agents/schema-auditor.agent.md`
- `.github/agents/adversarial-questioner.agent.md`
- `.github/agents/domain-ontologist.agent.md`
- `.github/agents/debate-synthesizer.agent.md`
- `.github/agents/gap-remediator.agent.md`
- `.github/agents/optimistic-reviewer.agent.md`
- `.github/agents/pessimistic-reviewer.agent.md`
- `.github/agents/pragmatic-reviewer.agent.md`
- `workspace-artifacts/wiki/v1/` and `workspace-artifacts/wiki/v2/`
- `workspace-artifacts/wiki/reports/`
- `workspace-artifacts/wiki/reviews/`
- `PROBLEM_STATEMENT.md`

## Required Customization Assets
`meta-compiler meta-init` now provisions the Stage 1A2 custom agents into `.github/agents/`.

Before running the loop:
- verify the expected `.github/agents/*.agent.md` files exist
- verify each delegating agent exposes the `agent` tool and includes `explore` and `research` in its `agents:` allowlist
- if any are missing or materially broken, recreate them using `.github/prompts/create-agent.prompt.md`
- use `.github/skills/agent-customization/SKILL.md` when fixing frontmatter, tool restrictions, or descriptions

The CLI manages artifacts and validation. The prompt is responsible for invoking the custom agents.

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
  - Confirm the provisioned `.github/agents/` files are present and usable.
  - Use `explore` for quick reconnaissance across wiki pages, reports, and review artifacts before spawning deeper work.
   - Set `cycle = 1`, `max_cycles = 3`.

2. **Run Stage 1B cycle**
   - Execute:
     ```bash
     meta-compiler research-depth
     meta-compiler validate-stage --stage 1b
     ```
   - Spawn and call these Stage 1B agents:
     - `schema-auditor`
     - `adversarial-questioner`
     - `domain-ontologist`
     - `debate-synthesizer`
     - `gap-remediator`
   - Coordinate them to produce or refresh:
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
   - Before reviewer verdict synthesis, launch three independent reviewer-scoped `research` runs:
     - optimistic: minimum viable proceed evidence
     - pessimistic: failure modes and missing blockers
     - pragmatic: blocking-vs-nice-to-have trade-offs
   - Each reviewer-scoped search must target `consensus.app`, `semanticscholar.org`, and general authoritative web sources when relevant.
   - Persist normalized search artifacts under `workspace-artifacts/wiki/reviews/search/` using one file per reviewer.
   - Spawn and call these fresh-context Stage 1C reviewer agents:
     - `optimistic-reviewer`
     - `pessimistic-reviewer`
     - `pragmatic-reviewer`
   - Coordinate them to write:
     - `workspace-artifacts/wiki/reviews/review_verdicts.yaml`
     - `workspace-artifacts/wiki/reviews/1a2_handoff.yaml`
     - `workspace-artifacts/wiki/reviews/search/optimistic.yaml`
     - `workspace-artifacts/wiki/reviews/search/pessimistic.yaml`
     - `workspace-artifacts/wiki/reviews/search/pragmatic.yaml`
     - updates to v2 pages/citations for newly found evidence

4. **Decide loop continuation**
   - If verdict is PROCEED (or forced proceed at iteration cap), stop and hand off to Stage 2.
   - If verdict is ITERATE and `cycle < max_cycles`, increment `cycle` and route explicit blocking gaps back to Stage 1B.
   - If `cycle == max_cycles`, force PROCEED with all unresolved gaps explicitly documented.

5. **Handoff package**
   - Final consensus summary (proceed/iterate rationale)
   - Blocking gaps list (resolved + unresolved)
   - Suggested external sources discovered during review
  - Persist the packet in `workspace-artifacts/wiki/reviews/1a2_handoff.yaml`
   - Ready signal for:
     ```bash
     meta-compiler elicit-vision --use-case "initial scaffold" --non-interactive
     ```

## Verdict Values
- `PROCEED`: Research quality is sufficient to continue into Stage 2.
- `ITERATE`: Blocking gaps remain and must be routed back to Stage 1B for another cycle.

## Output Contract
- One orchestrated run controls the named 1B/1C custom agents and loop retries.
- Human receives a concise decision packet, not fragmented per-agent chatter.
- No hidden state: every decision is represented in workspace artifacts.
- Reviewer search evidence is persisted as normalized artifacts so Python can aggregate `suggested_sources` without replaying the searches in main context.
