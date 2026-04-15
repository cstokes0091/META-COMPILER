# META-COMPILER Orchestrator

Research-first project scaffolding system. Compiles seed documents + human intent
into structured workspaces for LLM-driven programming, writing, and technical tasks.

**Intent:** Build an LLM-accessible knowledge base to make an LLM a domain and
problem-space expert before any task is posited.

**This tool runs in VSCode with GitHub Copilot Chat (ChatGPT) as the intelligence
layer.** Agents, prompts, and skills are structured for the `.github/agents/`,
`.github/prompts/`, and `.github/skills/` pipeline. See `LLM_INSTRUCTIONS.md` for
full workflow instructions. Stage-specific prompts are in `prompts/*.prompt.md`.

**Who is this for?** Anyone. Artists, engineers, accountants, secretaries,
researchers, students. You do not need to be a programmer. You need a problem to
explore and documents to feed the system. The system handles the rest.

The prompts are the primary operator entry points. Each stage prompt tells the
assistant which `meta-compiler` command to run, what artifacts to inspect, and
when to delegate to the shared `explore` and `research` platform agents.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Commands

```bash
# Full pipeline (single command — recommended for first-time users)
meta-compiler run-all --project-name "My Project" --problem-domain "Example domain" --project-type hybrid --problem-statement-file ./problem_statement.md

# Core pipeline (individual stages)
meta-compiler meta-init --project-name "My Project" --problem-domain "Example domain" --project-type algorithm --problem-statement-file ./problem_statement.md
meta-compiler research-breadth
meta-compiler research-depth
meta-compiler review
meta-compiler elicit-vision --use-case "baseline design"
meta-compiler scaffold
meta-compiler phase4-finalize

# Post-scaffold
meta-compiler wiki-update
meta-compiler wiki-browse
meta-compiler stage2-reentry --reason "scope changed" --sections "architecture,requirements"
meta-compiler finalize-reentry

# Utility commands
meta-compiler track-seeds                        # Auto-detect and ingest new seeds
meta-compiler clean-workspace --target-stage 0   # Reset to any stage

# Validation
meta-compiler validate-stage --stage all

# Document processing
python scripts/read_document.py <file.pdf>       # Extract text from PDF/DOCX/XLSX/PPTX
python scripts/write_document.py <output.docx>   # Write text to documents
```

## End-to-End Flow

### Quick Start (Single Command)

```bash
# Place seed documents in workspace-artifacts/seeds/
# Then run the full pipeline:
meta-compiler run-all \
  --project-name "My Project" \
  --problem-domain "Example domain" \
  --project-type hybrid \
  --problem-statement-file ./problem_statement.md

# With a clean start:
meta-compiler run-all \
  --project-name "My Project" \
  --problem-domain "Example domain" \
  --project-type hybrid \
  --problem-statement-file ./problem_statement.md \
  --clean-first
```

### Step-by-Step (More Control)

```bash
# Stage 0: Prompt-led init
# Use prompts/stage-0-init.prompt.md to collect the problem statement and run:
meta-compiler meta-init --project-name "My Project" --problem-domain "Example domain" --project-type hybrid --problem-statement-file ./problem_statement.md
meta-compiler validate-stage --stage 0
# Add seeds to workspace-artifacts/seeds/
# `meta-init` also provisions stage prompts into prompts/*.prompt.md
# and workspace custom agents, prompts, and skills into .github/

# Stage 1A: Breadth research
meta-compiler research-breadth
# LLM enriches wiki pages (see prompts/stage-1a-breadth.prompt.md)
meta-compiler validate-stage --stage 1a

# Stage 1A2: Orchestrate the 1B <-> 1C loop from one prompt
# Use prompts/stage-1a2-orchestration.prompt.md
# This prompt uses the provisioned .github/agents/stage-1a2-orchestrator.agent.md
# and named Stage 1B/1C agents while the CLI runs research-depth/review.
# All provisioned and generated agents share the explore/research subagent palette.
# Reviewer-specific web-search artifacts are persisted under
# workspace-artifacts/wiki/reviews/search/ for the Python review stage to aggregate.

# Stage 2: Vision elicitation
meta-compiler elicit-vision --use-case "initial scaffold" --non-interactive
# LLM refines Decision Log via dialog (see prompts/stage-2-dialog.prompt.md)
# Stage 2 also generates and stores the wiki name used in page headers and browser home.
meta-compiler validate-stage --stage 2

# Stage 3: Scaffold
meta-compiler scaffold
# LLM performs scaffold review/traceability checks (see prompts/stage-3-scaffold.prompt.md)
# Generated scaffolds now include human-readable summaries, real .github/
# custom agents, skills, and instructions, plus an execution contract
# (EXECUTION_MANIFEST.yaml + orchestrator/run_stage4.py) and an initial
# workspace-artifacts/wiki/provenance/what_i_built.md summary.
meta-compiler validate-stage --stage 3

# Stage 4: Execute + pitch
# Use prompts/stage-4-finalize.prompt.md
meta-compiler phase4-finalize
meta-compiler validate-stage --stage 4
# This writes final execution outputs plus a markdown pitch and a real .pptx deck.

# Browse the wiki in a local browser window
meta-compiler wiki-browse

# Run scaffold self-tests
pytest workspace-artifacts/scaffolds/v1/tests/ -v

# Validate everything
meta-compiler validate-stage --stage all
```

## Post-Scaffold Operations

```bash
# When new seed documents arrive
meta-compiler wiki-update

# Auto-detect and ingest new seeds (checks + updates in one step)
meta-compiler track-seeds

# When scope or requirements change
meta-compiler stage2-reentry --reason "expanded to include X" --sections "architecture,requirements"
# LLM conducts scoped revision dialog
meta-compiler finalize-reentry
meta-compiler scaffold  # Re-scaffold with new decisions

# Reset workspace to a specific stage
meta-compiler clean-workspace --target-stage 2   # Redo from Stage 3 onward
meta-compiler clean-workspace --target-stage 0   # Full reset (keeps seeds)
```

## Academic Research

The Academic Researcher agent (`@academic-researcher`) retrieves full-text papers
from open-access sources and deposits them in `workspace-artifacts/seeds/`:

```
@academic-researcher Find 5 papers on "reinforcement learning" published after 2020
```

Sources: Semantic Scholar, CORE, arXiv, PubMed Central, and gray literature.

## Document Processing

Read and write common document formats:

```bash
# Extract text from any supported format
python scripts/read_document.py paper.pdf
python scripts/read_document.py spec.docx --output extracted.txt

# Create documents from text
python scripts/write_document.py report.docx --input content.md --title "My Report"
python scripts/write_document.py slides.pptx --input notes.txt --title "Presentation"
```

Supported formats: `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.txt`, `.md`, `.rst`, `.tex`, `.csv`

## VSCode Integration

Tasks are configured in `.vscode/tasks.json`. Use the Command Palette
(`Cmd+Shift+P` > `Tasks: Run Task`) to execute any stage or open the wiki browser.
Stage 0 expects a problem-statement file path, and Stage 4 is available as a
first-class task alongside the earlier stages.

## Artifact Root

Artifacts are persisted under `workspace-artifacts/` by default.

## Wiki Browser

`meta-compiler wiki-browse` starts a lightweight local browser for the wiki and
opens it automatically in your default browser. It prefers wiki v2 when present,
falls back to wiki v1 automatically, and the browser window remains fully
resizable because it uses the system browser instead of a fixed native UI.
