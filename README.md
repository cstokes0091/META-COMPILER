# META-COMPILER Orchestrator

Research-first project scaffolding system. Compiles seed documents + human intent
into structured workspaces for LLM-driven programming, writing, and technical tasks.

**This tool runs in VSCode with an LLM assistant as the intelligence layer.** It is
model-agnostic — works with any reasoning model in any VSCode-integrated coding
assistant (Copilot, Claude Code, Cursor, Windsurf, etc.). See `LLM_INSTRUCTIONS.md` for full
workflow instructions. Stage-specific prompts are in `prompts/*.prompt.md`.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Commands

```bash
# Core pipeline
meta-compiler meta-init --project-name "My Project" --problem-domain "Example domain" --project-type algorithm
meta-compiler research-breadth
meta-compiler research-depth
meta-compiler review
meta-compiler elicit-vision --use-case "baseline design"
meta-compiler scaffold

# Post-scaffold
meta-compiler wiki-update
meta-compiler wiki-browse
meta-compiler stage2-reentry --reason "scope changed" --sections "architecture,requirements"
meta-compiler finalize-reentry

# Validation
meta-compiler validate-stage --stage all
```

## End-to-End Flow

```bash
# Stage 0: Initialize
meta-compiler meta-init --project-name "My Project" --problem-domain "Example domain" --project-type hybrid
# Edit PROBLEM_STATEMENT.md, add seeds to workspace-artifacts/seeds/
# `meta-init` also provisions stage prompts into prompts/*.prompt.md
# and workspace custom agents, prompts, and skills into .github/

# Stage 1A: Breadth research
meta-compiler research-breadth
# LLM enriches wiki pages (see prompts/stage-1a-breadth.prompt.md)
meta-compiler validate-stage --stage 1a

# Stage 1A2: Orchestrate the 1B <-> 1C loop from one prompt
# Use prompts/stage-1a2-orchestration.prompt.md
# This prompt uses the provisioned .github/agents/stage-1a2-orchestrator.agent.md
# and named Stage 1B/1C agents while the CLI runs research-depth/review
# iterations until PROCEED or iteration cap

# Stage 2: Vision elicitation
meta-compiler elicit-vision --use-case "initial scaffold" --non-interactive
# LLM refines Decision Log via dialog (see prompts/stage-2-dialog.prompt.md)
meta-compiler validate-stage --stage 2

# Stage 3: Scaffold
meta-compiler scaffold
# LLM performs scaffold review/traceability checks (see prompts/stage-3-scaffold.prompt.md)
# Generated scaffolds now include human-readable summaries plus real .github/
# custom agents, skills, and instructions for downstream Copilot execution
meta-compiler validate-stage --stage 3

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

# When scope or requirements change
meta-compiler stage2-reentry --reason "expanded to include X" --sections "architecture,requirements"
# LLM conducts scoped revision dialog
meta-compiler finalize-reentry
meta-compiler scaffold  # Re-scaffold with new decisions
```

## VSCode Integration

Tasks are configured in `.vscode/tasks.json`. Use the Command Palette
(`Cmd+Shift+P` > `Tasks: Run Task`) to execute any stage or open the wiki browser.

## Artifact Root

Artifacts are persisted under `workspace-artifacts/` by default.

## Wiki Browser

`meta-compiler wiki-browse` starts a lightweight local browser for the wiki and
opens it automatically in your default browser. It prefers wiki v2 when present,
falls back to wiki v1 automatically, and the browser window remains fully
resizable because it uses the system browser instead of a fixed native UI.
