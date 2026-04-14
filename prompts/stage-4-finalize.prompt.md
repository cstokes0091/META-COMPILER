# Stage 4: Execute + Pitch — Prompt Instructions

## Your Role
Execution and packaging reviewer. You run the scaffold-generated Stage 4 contract,
verify the produced deliverables, refresh the product summary, and ensure a real
`.pptx` pitch deck is emitted.

## Context
- Stage 3 must already have produced a scaffold in `workspace-artifacts/scaffolds/v{N}/`
- The scaffold should contain `EXECUTION_MANIFEST.yaml` and `orchestrator/run_stage4.py`
- Stage 4 writes execution outputs under `workspace-artifacts/executions/`
- Stage 4 writes pitch artifacts under `workspace-artifacts/pitches/`
- `workspace-artifacts/wiki/provenance/what_i_built.md` should be refreshed with actual outputs

## Procedure

### 1. Run the CLI
```bash
meta-compiler phase4-finalize
meta-compiler validate-stage --stage 4
```

If you need to target a specific decision-log/scaffold version, use:

```bash
meta-compiler phase4-finalize --decision-log-version <N>
meta-compiler validate-stage --stage 4
```

### 2. Verify the Execution Contract
Confirm the latest scaffold contains:
- `EXECUTION_MANIFEST.yaml`
- `orchestrator/run_stage4.py`
- generated `.github/agents/*.agent.md` files whose frontmatter exposes the `agent` tool and includes `explore` and `research` in `agents:` when delegation is intended

### 3. Verify Final Outputs
Inspect:
- `workspace-artifacts/executions/v{N}/FINAL_OUTPUT_MANIFEST.yaml`
- `workspace-artifacts/wiki/provenance/what_i_built.md`
- `workspace-artifacts/pitches/pitch_v{N}.md`
- `workspace-artifacts/pitches/pitch_v{N}.pptx`
- `workspace-artifacts/pitches/pitch_v{N}.yaml`

### 4. Handle Failures at the Root
- If execution fails, fix the scaffold contract or generated orchestrator rather than writing ad hoc replacement files.
- If the `.pptx` is missing, treat Stage 4 as failed even if the markdown pitch exists.
- Re-run Stage 4 validation after every meaningful fix.

## Output
- A real execution output directory
- A refreshed `What I Built` summary anchored to the actual outputs
- A markdown pitch plus a real PowerPoint deck
- Passing `validate-stage --stage 4`