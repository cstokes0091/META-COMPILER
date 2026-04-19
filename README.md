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
# Run through the Stage 2 handoff (single command — recommended starting point)
meta-compiler run-all --project-name "My Project" --problem-domain "Example domain" --project-type hybrid --problem-statement-file ./problem_statement.md

# Core pipeline (individual stages)
meta-compiler meta-init --project-name "My Project" --problem-domain "Example domain" --project-type algorithm --problem-statement-file ./problem_statement.md
meta-compiler ingest --scope all
meta-compiler ingest-validate
meta-compiler research-breadth
meta-compiler research-depth
meta-compiler review
meta-compiler elicit-vision --start        # write Stage 2 brief + transcript skeleton
# (LLM conducts the dialog per .github/prompts/stage-2-dialog.prompt.md)
meta-compiler elicit-vision --finalize     # compile transcript → decision_log_v{N}.yaml
meta-compiler audit-requirements
meta-compiler scaffold
meta-compiler phase4-finalize

# Post-scaffold
meta-compiler wiki-reconcile-concepts        # Phase A preflight: cluster alias candidates
# (LLM runs wiki-concept-reconciliation.prompt.md → writes the proposal)
meta-compiler wiki-apply-reconciliation      # Phase A postflight: merge aliases into canonical pages
meta-compiler wiki-cross-source-synthesize   # Phase B preflight: cross-source definition work plan
# (LLM runs wiki-cross-source-synthesis.prompt.md → rewrites Definition/Key Claims)
meta-compiler wiki-browse
meta-compiler stage2-reentry --reason "scope changed" --sections "architecture,requirements"
meta-compiler finalize-reentry

# Code ingestion (pin a git repo as a seed and fan out over its files)
meta-compiler add-code-seed --repo https://github.com/org/widget-lib --ref v1.2.0 --name widget-lib
meta-compiler bind-code-seed --path seeds/code/already-cloned

# Utility commands
meta-compiler track-seeds                        # Auto-detect and ingest new seeds
meta-compiler clean-workspace --target-stage 0   # Reset to any stage

# Validation
meta-compiler validate-stage --stage all

# Document processing
python scripts/pdf_to_text.py <file.pdf>        # Extract text from PDFs for ingest
python scripts/read_document.py <file.pdf>       # Extract text from PDF/DOCX/XLSX/PPTX
python scripts/write_document.py <output.docx>   # Write text to documents
```

## Code Ingestion

META-COMPILER ingests code through the same fan-out architecture as documents,
with two key differences:

1. **Seeds are git repos** pinned to a specific commit SHA, placed under
   `workspace-artifacts/seeds/code/<name>/`. Immutability is enforced at the
   commit boundary (not per-file). `meta-compiler add-code-seed --repo <url> --ref <sha|tag> --name <slug>`
   clones the repo and records a `code_bindings` entry in
   `workspace-artifacts/manifests/source_bindings.yaml`. If you already have the
   repo checked out under `seeds/code/`, `bind-code-seed --path <rel>` records
   the current HEAD.
2. **Ingest is two-pass.** When `meta-compiler ingest` sees a registered code
   repo it emits `repo_map_items[]` (one per repo) into the work plan alongside
   per-file `work_items` with `seed_kind: code`. The `ingest-orchestrator` first
   spawns `repo-mapper` subagents that walk each repo and emit a RepoMap YAML
   to `runtime/ingest/repo_map/<name>.yaml`. It then fans out `code-reader`
   subagents (≤4 in parallel) that full-read each priority file and emit code
   findings JSON with line-anchored locators.

The wiki stays a single tree. Code findings render per-file `type: code` pages
(one per source file), plus a `type: code-repo` overview page per seed. Concept
aggregation naturally merges document concepts with code concepts — so when a
concept appears in both a paper and a source file, the concept page carries
both citations automatically.

Full schemas (RepoMap + Code Findings) live in
`.github/prompts/ingest-orchestrator.prompt.md`. Agent specs are at
`.github/agents/repo-mapper.agent.md` and `.github/agents/code-reader.agent.md`.

## Hooks and Determinism

Meta-compiler uses VSCode Copilot hooks (`.github/hooks/main.json` + per-agent `hooks:` frontmatter) to enforce pipeline ordering and artifact integrity. Hooks gate out-of-order CLI calls, auto-fire deterministic steps at transition boundaries, and capture command output so the LLM cannot paraphrase it.

**Key points:**

- **Auto-fired steps:** Invoking a stage prompt (e.g., `/stage-1a-breadth`) auto-fires the pure-CLI calls for that stage. The prompt body describes only the semantic work (what the LLM is supposed to reason about).
- **Gated calls:** `meta-compiler` invocations are denied unless the manifest's `last_completed_stage` matches the command's precondition. Override with `META_COMPILER_SKIP_HOOK=1` only where integrity permits.
- **Stage 2 re-entry:** Non-overridable gate requires `reentry_request.yaml` (produced by Step 0 of `stage2-reentry.prompt.md`) before the CLI fires.

See `.github/docs/hooks.md` for the full check inventory, override mechanisms, and audit log format.

## End-to-End Flow

### Quick Start (Single Command)

```bash
# Place seed documents in workspace-artifacts/seeds/
# Then run through the Stage 2 handoff:
meta-compiler run-all \
  --project-name "My Project" \
  --problem-domain "Example domain" \
  --project-type hybrid \
  --problem-statement-file ./problem_statement.md

# run-all stops after Stage 2 preflight (writes brief + transcript skeleton).
# The Stage 2 dialog then happens in an LLM runtime driven by
# .github/prompts/stage-2-dialog.prompt.md, which invokes the stage2-orchestrator
# agent and calls `meta-compiler elicit-vision --finalize` when done.
# Review the Decision Log and requirements audit before running scaffold.

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

# Stage 1A: Findings-backed breadth research
meta-compiler ingest --scope all
# Use prompts/ingest-orchestrator.prompt.md to write findings JSON
meta-compiler ingest-validate
meta-compiler research-breadth
# Breadth enriches wiki pages from findings when present
meta-compiler validate-stage --stage 1a

# Stage 1A2: Orchestrate the 1B <-> 1C loop from one prompt
# Use prompts/stage-1a2-orchestration.prompt.md
# This prompt uses the provisioned .github/agents/stage-1a2-orchestrator.agent.md
# and named Stage 1B/1C agents while the CLI runs research-depth/review.
# All provisioned and generated agents share the explore/research subagent palette.
# Reviewer-specific web-search artifacts are persisted under
# workspace-artifacts/wiki/reviews/search/ for the Python review stage to aggregate.

# Stage 2: Vision elicitation (prompt-as-conductor — see .github/docs/stage-2-hardening.md)
meta-compiler elicit-vision --start          # write brief + transcript skeleton
# LLM conducts the dialog per .github/prompts/stage-2-dialog.prompt.md:
#   1. @stage2-orchestrator mode=preflight  (semantic readiness audit)
#   2. converse with human, append decision blocks to transcript.md
meta-compiler elicit-vision --finalize       # parse blocks → decision_log_v{N}.yaml
# LLM continues:
#   3. @stage2-orchestrator mode=postflight (fidelity audit of compile)
meta-compiler validate-stage --stage 2
meta-compiler audit-requirements
# run-all intentionally stops at the Stage 2 preflight boundary.
# Review decision_log_v*.yaml and requirements_audit.yaml before scaffold.

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
# When new seed documents arrive, re-run ingest → research-breadth, then:
meta-compiler wiki-reconcile-concepts         # cluster any new alias candidates
# (LLM runs wiki-concept-reconciliation.prompt.md)
meta-compiler wiki-apply-reconciliation       # merge into canonical pages
meta-compiler wiki-cross-source-synthesize    # surface inter-source divergence
# (LLM runs wiki-cross-source-synthesis.prompt.md)
meta-compiler wiki-link                       # alias-aware link pass

# Detect new seeds and report the handoff
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
python scripts/pdf_to_text.py paper.pdf --output paper.txt
python scripts/read_document.py spec.docx --output extracted.txt

# Create documents from text
python scripts/write_document.py report.docx --input content.md --title "My Report"
python scripts/write_document.py slides.pptx --input notes.txt --title "Presentation"
```

Supported formats: `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.txt`, `.md`, `.rst`, `.tex`, `.csv`

## VSCode Integration

Tasks are configured in `.vscode/tasks.json`. Use the Command Palette
(`Cmd+Shift+P` > `Tasks: Run Task`) to execute the Stage 2 handoff flow,
prepare ingest work plans, validate findings, audit Stage 2 requirements, or
open the wiki browser. Stage 0 expects a problem-statement file path, and Stage
4 remains available as a separate manual task after review.

## Artifact Root

Artifacts are persisted under `workspace-artifacts/` by default.

## Wiki Browser

`meta-compiler wiki-browse` starts a lightweight local browser for the wiki and
opens it automatically in your default browser. It prefers wiki v2 when present,
falls back to wiki v1 automatically, and the browser window remains fully
resizable because it uses the system browser instead of a fixed native UI.
