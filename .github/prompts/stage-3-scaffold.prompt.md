# Stage 3: Scaffold Review — Prompt Instructions

## Intent

**Compile a capability-driven workspace that a small fixed agent palette can
execute against.** Stage 3 turns Decision Log rows + cited findings into
`capabilities.yaml`, `contracts/`, `skills/{name}/SKILL.md`, and a
`verification/` harness. The primary deliverable is not a roster of
domain-named agents but a pull-indexed skill library that the static
planner/implementer/reviewer/researcher palette binds against at Stage 4.

**Document everything such that it's auditable by humans and LLMs alike.**
Every capability traces to a REQ-NNN + citation set; every contract
deduplicates IO shapes across capabilities; every skill body is drawn
verbatim from cited findings — no templated slots.

## Your Role
Scaffold Reviewer agent. The CLI runs the four post-dialogue stages
(`compile-capabilities`, `extract-contracts`, `synthesize-skills`,
`workspace-bootstrap`) mechanically from the Decision Log + findings; your
job is to verify the generated workspace is coherent, traceable, and
ready for the Stage 4 palette to execute.

## Context
- Stage 2 Decision Log exists in `workspace-artifacts/decision-logs/`.
- Stage 3 scaffold output exists in `workspace-artifacts/scaffolds/v{N}/`.
- Findings consumed by the compiler live at `workspace-artifacts/wiki/findings/*.json`.
- The static palette is checked into repo `.github/agents/` and copied
  into downstream workspaces by `meta-init`. Its four members are:
  `planner`, `implementer`, `reviewer`, `researcher`.
- Reusable customization references live in
  `.github/skills/agent-customization/` and `.github/prompts/`.

## Procedure

### 1. Run the CLI
The scaffold chain auto-fires on `/stage-3-scaffold` via the
`user_prompt_submit_dispatch` hook:
```
meta-compiler scaffold
meta-compiler validate-stage --stage 3
```
Inspect the generated artefacts under `workspace-artifacts/scaffolds/v{N}/`.

### 2. Verify Capability Coverage
- Every `REQ-NNN` in the latest Decision Log appears in the union of
  `capabilities[*].requirement_ids`. The CLI's `validate_capability_coverage`
  hook enforces this — if `validate-stage --stage 3` passed, coverage is
  already green.
- Every `capabilities[*].required_finding_ids` resolves either in
  `wiki/findings/` (normal path) or in `wiki/citations/index.yaml` (v1
  bootstrap exception).
- Every `capabilities[*].when_to_use` trigger contains at least one
  domain-vocabulary token. Generic triggers ("use when implementing") are
  rejected by `validate_trigger_specificity`.

### 3. Verify Contract Reuse
- Every contract in `contracts/_manifest.yaml` is referenced by at least
  one capability's `io_contract_ref` or a composed chain.
- Contracts dedupe correctly: two capabilities with identical
  `(inputs_name_modality_set, outputs_name_modality_set)` point at the
  same `contract_id`.

### 4. Verify Skill Bodies
Open 2–3 `skills/{name}/SKILL.md` files and confirm:
- Frontmatter `name` matches the parent directory name.
- Frontmatter `triggers` match the capability's `when_to_use`.
- Every `## ` section has non-empty content (no stub sections).
- `## Evidence` cites specific findings with locators + quotes.

### 5. Verify Manifests
- `SCAFFOLD_MANIFEST.yaml` lists `capability_count`, `contract_count`,
  `skill_count`, `verification_hook_count`, and the palette.
- `EXECUTION_MANIFEST.yaml` is capability-keyed and points at the new
  artefact paths (no `orchestrator_path`).
- `DISPATCH_HINTS.yaml` has `dispatch_policy: capability-keyed` and one
  assignment per capability.
- `verification/REQ_TRACE.yaml` maps every `REQ-NNN` → capability_id →
  hook_id.

### 6. Resolve Gaps
If you find misalignments:
- Identify exactly which Decision Log row is not reflected and which
  stage (compile / extract / synthesize / bootstrap) produced the bad
  output.
- Never hand-edit `capabilities.yaml` or `contracts/*.yaml` — the
  `gate_artifact_writes` hook blocks those. Fix the upstream Decision
  Log (via `stage2-reentry`) or the wiki finding, then re-run the
  relevant sub-stage.

## Constraints
- Treat the latest Decision Log + cited findings as source of truth.
- Do not invent capabilities, contracts, or skills not traceable to
  Decision Log rows + findings.
- Preserve REQ-NNN and citation IDs across every artefact.
- Do not generate domain-named agents. The static palette is the only
  agent roster.

## Output
- A validated scaffold passing `meta-compiler validate-stage --stage 3`.
- Clear requirement and citation traceability through
  `verification/REQ_TRACE.yaml`.
- Passing Stage 3 hook checks (`validate_capability_schema`,
  `validate_skill_finding_citations`, `validate_trigger_specificity`,
  `validate_capability_coverage`).

## Guiding Principles
- **Document everything** — every scaffold artefact traces to a Decision
  Log row + a cited finding.
- **Data over folklore** — skill bodies quote findings verbatim with
  locators; contract invariants are drawn from normative claim
  statements.
- **Findings are first-class citations** — every capability / contract /
  skill names finding IDs; unresolvable IDs fail the scaffold validator.
- **Project type only shapes directory layout** — `code/`, `report/`,
  `inbox/`, etc. are empty buckets for Stage 4 to fill. Capabilities
  themselves are project-type-neutral.
- **Atomic skills, explicit composition** — implementer logic lives in
  one skill, numerical verification in another, regression testing in
  a third. Composition is declared in `capabilities[*].composes` and
  mirrored in `SkillIndexEntry.composes`.
