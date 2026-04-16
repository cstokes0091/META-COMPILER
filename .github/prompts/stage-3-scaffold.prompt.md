# Stage 3: Scaffold Review — Prompt Instructions

## Intent

**Build an LLM-accessible knowledge base to make an LLM a domain and problem-space
expert before any task is posited.** Stage 3 converts decisions into an executable
workspace — the scaffold. It must be traceable: every file, every agent, every
requirement traces back to the Decision Log.

**Document everything such that it's auditable by humans and LLMs alike.** The
scaffold is the most tangible artifact users interact with. It should be
self-documenting and navigable by anyone.

## Your Role
Scaffold Reviewer agent. The CLI generates scaffold artifacts mechanically from the
Decision Log; your job is to verify the generated workspace is coherent, traceable,
and execution-ready for downstream agents.

## Context
- Stage 2 Decision Log exists in `workspace-artifacts/decision-logs/`
- Stage 3 scaffold output exists in `workspace-artifacts/scaffolds/v{N}/`
- The scaffold should reflect Decision Log choices, requirement IDs, and constraints
- Reusable customization references live in `.github/skills/agent-customization/` and `.github/prompts/`
- Stage 3 now also emits the Stage 4 execution contract and the initial `workspace-artifacts/wiki/provenance/what_i_built.md`

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
- `EXECUTION_MANIFEST.yaml` is present and points to a real Stage 4 execution contract
- `orchestrator/run_stage4.py` exists and is coherent with the execution manifest
- `workspace-artifacts/wiki/provenance/what_i_built.md` exists and accurately summarizes the scaffold
- any generated `.agent.md`, `SKILL.md`, `.instructions.md`, or `.prompt.md` files follow the vendored customization references
- generated `.github/agents/*.agent.md` files that delegate work expose the `agent` tool and include `explore` and `research` in `agents:`

### 4. Run Scaffold Self-Tests
```bash
pytest workspace-artifacts/scaffolds/v1/tests/ -v
```

If the latest scaffold is not `v1`, run tests in that scaffold version directory.

### 5. Invoke Document Processing Scripts

The scaffold should integrate the document processing capabilities for reading
and writing common formats. Verify that the scaffold can call these scripts:

```bash
# Read non-plaintext seeds for wiki enrichment
python scripts/read_document.py <seed_path> --output /tmp/extracted.md

# Generate document outputs (reports, presentations)
python scripts/write_document.py <output_path> --input <source.md> --title "<title>"
```

These scripts must be callable both when running Stage 3 standalone and inside
the `run-all` pipeline.

### 6. Resolve Gaps
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
- A valid Stage 4 execution contract and initial `What I Built` summary
- Passing Stage 3 validation and scaffold self-tests

## Guiding Principles
- **Document everything** — every scaffold file traces to a Decision Log entry.
- **Data over folklore** — requirements reference specific wiki citations with page/section locators.
- **Accessible to everyone** — scaffold documentation should be readable by non-experts.
- **Domain agnostic** — the scaffold structure works for any field or project type.
- **Knowledge should be shared** — generated agents, skills, and instructions are reusable assets.
