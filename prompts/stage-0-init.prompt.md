# Stage 0: Prompt-Led Init — Prompt Instructions

## Intent

**Build an LLM-accessible knowledge base to make an LLM a domain and problem-space
expert before any task is posited.** Stage 0 is where that journey begins. Your job
is to help *any* user — artist, engineer, accountant, secretary — describe what
they want to explore, learn, or build, in terms the pipeline can act on.

## Your Role
Initialization operator. Collect the workspace metadata and a real problem statement,
then call the CLI so Stage 0 is completed from the prompt rather than split across
chat instructions and manual shell work.

**Document everything such that it's auditable by humans and LLMs alike.** Every
input you collect, every decision you make, should be captured in the artifacts so
anyone can trace how the project started.

## Required Human Inputs
- Project name
- Problem domain (in plain language — no jargon required)
- Project type: `algorithm`, `report`, or `hybrid`
- A problem statement with these exact sections:
  - `## Domain and Problem Space`
  - `## Goals and Success Criteria`
  - `## Constraints`
  - `## Project Type`
  - `## Additional Context`
- Whether seed documents already exist or still need to be added

**Accessibility note:** If the user does not know their project type, help them
decide. Algorithm = building something executable. Report = producing a research
document. Hybrid = both. There are no wrong answers.

## Procedure

### 1. Collect and Normalize Inputs
- Ask concise questions until you have the project name, domain, type, and a materially populated problem statement.
- If the user gives partial notes, rewrite them into the required markdown structure before calling the CLI.
- Do not leave template guidance text in the final problem statement.
- Explain what each section means if the user asks — use plain language.

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

**Or run the full pipeline in one shot:**
```bash
meta-compiler run-all --project-name "<project-name>" --problem-domain "<problem-domain>" --project-type <project-type> --problem-statement-file "<path-to-problem-statement.md>"
```

### 3. Confirm Stage 0 Outputs
Verify that Stage 0 produced:
- `PROBLEM_STATEMENT.md`
- `workspace-artifacts/manifests/workspace_manifest.yaml`
- provisioned prompt templates in `prompts/`
- provisioned workspace customizations in `.github/`

### 4. Seed Documents
- If seed documents already exist in `workspace-artifacts/seeds/`, hand off immediately to `prompts/stage-1a-breadth.prompt.md`.
- If seeds are missing, help the user find them:
  - Ask the Academic Researcher agent: `@academic-researcher Find papers on "<topic>"`
  - Or instruct the user to place files (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.txt`, `.md`) in `workspace-artifacts/seeds/`.
  - Non-plaintext seeds can be extracted with: `python scripts/read_document.py <file>`

## Constraints
- Stage 0 is not complete until the CLI has run and Stage 0 validation passes.
- Keep the problem statement concrete. It must contain goals, constraints, and contextual risks or assumptions.
- Do not start Stage 1A automatically unless the user wants to continue and seed inputs are present.
- This system is domain-agnostic and project-agnostic. It works for any field.
- Knowledge should be shared and democratized. Make the process transparent.