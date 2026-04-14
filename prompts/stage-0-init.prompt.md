# Stage 0: Prompt-Led Init — Prompt Instructions

## Your Role
Initialization operator. Collect the workspace metadata and a real problem statement,
then call the CLI so Stage 0 is completed from the prompt rather than split across
chat instructions and manual shell work.

## Required Human Inputs
- Project name
- Problem domain
- Project type: `algorithm`, `report`, or `hybrid`
- A problem statement with these exact sections:
  - `## Domain and Problem Space`
  - `## Goals and Success Criteria`
  - `## Constraints`
  - `## Project Type`
  - `## Additional Context`
- Whether seed documents already exist or still need to be added

## Procedure

### 1. Collect and Normalize Inputs
- Ask concise questions until you have the project name, domain, type, and a materially populated problem statement.
- If the user gives partial notes, rewrite them into the required markdown structure before calling the CLI.
- Do not leave template guidance text in the final problem statement.

### 2. Run Stage 0 Through the CLI
If the problem statement is short enough to pass inline, use:

```bash
meta-compiler meta-init --project-name "<project-name>" --problem-domain "<problem-domain>" --project-type <project-type> --problem-statement "<full-problem-statement>"
meta-compiler validate-stage --stage 0
```

If quoting or multiline content would be awkward, write the problem statement to a markdown file first and use:

```bash
meta-compiler meta-init --project-name "<project-name>" --problem-domain "<problem-domain>" --project-type <project-type> --problem-statement-file "<path-to-problem-statement.md>"
meta-compiler validate-stage --stage 0
```

### 3. Confirm Stage 0 Outputs
Verify that Stage 0 produced:
- `PROBLEM_STATEMENT.md`
- `workspace-artifacts/manifests/workspace_manifest.yaml`
- provisioned prompt templates in `prompts/`
- provisioned workspace customizations in `.github/`

### 4. Decide the Next Handoff
- If seed documents already exist in `workspace-artifacts/seeds/`, hand off immediately to `prompts/stage-1a-breadth.prompt.md`.
- If seeds are missing, stop with a clear instruction to add them before Stage 1A.

## Constraints
- Stage 0 is not complete until the CLI has run and Stage 0 validation passes.
- Keep the problem statement concrete. It must contain goals, constraints, and contextual risks or assumptions.
- Do not start Stage 1A automatically unless the user wants to continue and seed inputs are present.