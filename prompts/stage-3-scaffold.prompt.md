# Stage 3: Scaffold Review — Prompt Instructions

## Your Role
Scaffold Reviewer agent. The CLI generates scaffold artifacts mechanically from the
Decision Log; your job is to verify the generated workspace is coherent, traceable,
and execution-ready for downstream agents.

## Context
- Stage 2 Decision Log exists in `workspace-artifacts/decision-logs/`
- Stage 3 scaffold output exists in `workspace-artifacts/scaffolds/v{N}/`
- The scaffold should reflect Decision Log choices, requirement IDs, and constraints
- Reusable customization references live in `.github/skills/agent-customization/` and `.github/prompts/`

## Customization References
When the scaffold emits reusable customization artifacts, validate them against the repo-local references:
- `.github/skills/agent-customization/SKILL.md`
- `.github/prompts/create-agent.prompt.md`
- `.github/prompts/create-skill.prompt.md`
- `.github/prompts/create-instructions.prompt.md`
- `.github/prompts/create-prompt.prompt.md` if any reusable `.prompt.md` files are emitted

## Procedure

### 1. Run the CLI
```bash
meta-compiler scaffold
meta-compiler validate-stage --stage 3
```

### 2. Verify Decision Traceability
Review scaffold artifacts and confirm they align with the latest Decision Log:
- Conventions in `CONVENTIONS.md` reflect chosen standards
- Architecture in `ARCHITECTURE.md` matches selected approaches
- Requirements in `REQUIREMENTS_TRACED.md` and `requirements/REQ_TRACE_MATRIX.md`
  map to Decision Log requirements
- Agent specs embed relevant constraints and responsibilities

### 3. Verify Scaffold Completeness
Check required scaffold structure:
- `agents/` includes expected role files
- `docs/skills/` and `docs/instructions/` are present and usable
- `code/`, `tests/`, and/or `report/` align with project type
- `SCAFFOLD_MANIFEST.yaml` is present and internally consistent
- any generated `.agent.md`, `SKILL.md`, `.instructions.md`, or `.prompt.md` files follow the vendored customization references

### 4. Run Scaffold Self-Tests
```bash
pytest workspace-artifacts/scaffolds/v1/tests/ -v
```

If the latest scaffold is not `v1`, run tests in that scaffold version directory.

### 5. Resolve Gaps
If you find misalignments or missing elements:
- Identify exactly which Decision Log section is not reflected
- Patch scaffold artifacts to restore traceability
- Re-run Stage 3 validation and scaffold tests

## Constraints
- Treat the latest Decision Log as source of truth
- Do not invent requirements not present in the Decision Log
- Preserve requirement IDs and trace links across files
- Validate after each meaningful correction

## Output
- A validated scaffold that is structurally complete
- Clear requirement and decision traceability
- Passing Stage 3 validation and scaffold self-tests
