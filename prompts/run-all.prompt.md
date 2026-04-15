# Run All: Full Pipeline Execution — Prompt Instructions

## Purpose

Execute the entire META-COMPILER pipeline from start to finish with a single
prompt. This is the fastest way to go from a problem statement and seed
documents to a fully scaffolded, executed, and pitched project.

**Intent:** Build an LLM-accessible knowledge base to make an LLM a domain and
problem-space expert, then scaffold and execute the project — all in one pass.

## Who This Is For

Anyone. You do not need to be a programmer or a domain expert. You need:
1. A clear description of what you want to research or build
2. Seed documents (papers, specs, notes) in any common format

The system handles the rest.

## Before You Start

1. **Prepare your problem statement.** Write or paste a description of your
   project with these sections:
   - Domain and Problem Space
   - Goals and Success Criteria
   - Constraints
   - Project Type (algorithm, report, or hybrid)
   - Additional Context

2. **Gather seed documents.** Place papers, specifications, or reference
   materials in `workspace-artifacts/seeds/`. Supported formats:
   `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.txt`, `.md`

3. **Need papers?** Ask the Academic Researcher agent:
   ```
   @academic-researcher Find papers on "<your topic>" published after <year>
   ```

## Quick Start

### Option A: Single CLI Command

```bash
meta-compiler run-all \
  --project-name "My Project" \
  --problem-domain "Description of your domain" \
  --project-type hybrid \
  --problem-statement-file ./PROBLEM_STATEMENT.md
```

### Option B: With a Clean Start

```bash
meta-compiler run-all \
  --project-name "My Project" \
  --problem-domain "Description of your domain" \
  --project-type hybrid \
  --problem-statement-file ./PROBLEM_STATEMENT.md \
  --clean-first
```

### Option C: Step by Step (More Control)

If you want to review between stages, use the individual stage prompts:
1. `prompts/stage-0-init.prompt.md`
2. `prompts/stage-1a-breadth.prompt.md`
3. `prompts/stage-1a2-orchestration.prompt.md`
4. `prompts/stage-2-dialog.prompt.md`
5. `prompts/stage-3-scaffold.prompt.md`
6. `prompts/stage-4-finalize.prompt.md`

## What Happens

The pipeline runs these stages automatically:

| Stage | What Happens | What It Produces |
|-------|-------------|-----------------|
| **0: Init** | Creates workspace structure from your problem statement | Manifest, problem statement, prompts |
| **1A: Breadth** | Reads every seed document, extracts knowledge | Wiki v1 with concepts, claims, citations |
| **1B: Depth** | Evaluates wiki from multiple perspectives | Wiki v2 with filled gaps, debate transcript |
| **1C: Review** | Fresh-eyes review of wiki completeness | Review verdicts (proceed/iterate) |
| **Seed Check** | Detects any new seeds added during research | Auto wiki-update if new seeds found |
| **2: Vision** | Generates decision log from wiki + problem statement | Decision Log with architecture, requirements |
| **3: Scaffold** | Generates project structure from decisions | Full project scaffold with agents, tests, docs |
| **4: Execute** | Runs the scaffold execution contract | Final deliverables + pitch deck (markdown + pptx) |

## After the Pipeline

### Browse Your Wiki
```bash
meta-compiler wiki-browse
```

### Add More Research
Place new documents in `workspace-artifacts/seeds/` and run:
```bash
meta-compiler track-seeds
```

### Reset and Re-run
```bash
# Reset to after Stage 2 (keep research, redo scaffold)
meta-compiler clean-workspace --target-stage 2

# Reset completely
meta-compiler clean-workspace --target-stage 0
```

### Change Scope
```bash
meta-compiler stage2-reentry --reason "expanded scope" --sections "architecture,requirements"
meta-compiler finalize-reentry
meta-compiler scaffold
```

## Constraints

- The pipeline validates after each stage. If validation fails, it stops and
  reports the issue. Fix the issue and re-run.
- Seed documents are immutable once ingested. Do not modify files in
  `workspace-artifacts/seeds/` after they have been processed.
- Every claim in the wiki must trace to a citation with specific page numbers,
  section numbers, or direct quotes — not just "Paper X discusses Y."
- All artifacts are auditable: every decision, every gap, every claim has a
  file and a trail.

## Document Format Support

Need to work with non-plaintext files? Use the document scripts:

```bash
# Read a PDF
python scripts/read_document.py path/to/paper.pdf

# Read a Word document
python scripts/read_document.py path/to/spec.docx --output extracted.txt

# Create a report document
python scripts/write_document.py report.docx --input content.md --title "My Report"
```
