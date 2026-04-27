# META-COMPILER Workspace Instructions

This file guides GitHub Copilot Chat (in VSCode) when working in this repository.

## What This Project Is

META-COMPILER is a research-first project scaffolding system. The Python CLI (`meta-compiler`) handles deterministic bookkeeping (validation, manifests, artifact paths, filesystem updates). LLM-driven prompts and `.github/agents/*.agent.md` files handle the reasoning work (research, extraction, evaluation, dialog). The two halves are deliberately separated.

The primary deliverable is a **workshop** — a reusable workspace that contains compiled knowledge and a generated execution framework — not the final algorithm or report itself.

**Runtime:** GitHub Copilot Chat in VSCode, reading `.github/agents/`, `.github/prompts/`, and `.github/skills/`. Stage prompts in `prompts/*.prompt.md` are the operator entry points. Agent-scoped hooks live in each `.github/agents/*.agent.md` frontmatter and require `chat.useCustomAgentHooks: true` in `.vscode/settings.json`.

## Operating Rules

- Treat `workspace-artifacts/` as the source of truth. Persist reasoning in artifacts, not chat history.
- Seed files under `workspace-artifacts/seeds/` are immutable once tracked. Code seeds under `workspace-artifacts/seeds/code/<name>/` are immutable at the commit-SHA boundary (do not `git checkout`, `git pull`, or `git commit` inside them).
- Read the stage prompt before acting on a stage. `.github/prompts/` is the canonical prompt source; root `prompts/` is a generated mirror for initialized target workspaces.
- Validate after each stage with `meta-compiler validate-stage --stage ...`.

## Setup and Commands

Install:
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .
```
Requires Python ≥ 3.11. The CLI entrypoint is `meta-compiler` (defined in `pyproject.toml` → `meta_compiler.cli:main`).

Tests:
```bash
pytest tests/ -v                                      # Unit tests for CLI stages
pytest tests/test_ingest_stage.py -v                  # Single test file
pytest tests/test_review_stage.py::test_name -v       # Single test case
pytest workspace-artifacts/scaffolds/v1/tests/ -v     # Scaffold self-tests (only exist after Stage 3)
pytest .github/hooks/bin/tests/ -v                    # Hook unit tests (stdlib only, no VSCode)
```

No lint/format/typecheck commands are configured in this repo. Do not introduce them unprompted.

Pipeline shortcut (runs Stages 0 → 1A → 1B → 1C → 2, then stops at the human review boundary):
```bash
meta-compiler run-all --project-name "X" --problem-domain "Y" --project-type hybrid --problem-statement-file ./problem_statement.md
```

Individual stage commands, post-scaffold commands, validation, and utilities are documented exhaustively in `README.md` and `LLM_INSTRUCTIONS.md`. Read those before running stages — do not improvise.

## Architecture

### CLI ↔ Prompt Split

The key design constraint: **`meta_compiler/` never does reasoning**; it only manipulates artifacts. Reasoning lives in `prompts/*.prompt.md` and `.github/agents/*.agent.md`, executed by the LLM. When adding a feature, decide which side it belongs on and keep them aligned — the stage prompt, the CLI stage function, and any provisioned `.github/` customization asset must all agree.

### Stage Pipeline

Each stage operates in **fresh context**. Artifacts pass knowledge forward, not conversation history. This forces crystallization and is why the system works:

- **Stage 0** (`init_stage.py`) — creates the workspace manifest and provisions prompts/agents into `.github/`.
- **Stage 1A** is two-phase and findings-first:
  1. `ingest` (`ingest_stage.py`) writes `workspace-artifacts/runtime/ingest/work_plan.yaml` and pre-extracts binary seeds.
  2. The `ingest-orchestrator` prompt fans out `seed-reader` subagents (and `code-reader` subagents for code seeds) that write findings JSON under `workspace-artifacts/wiki/findings/`.
  3. `ingest-validate` checks schema conformance.
  4. `research-breadth` (`breadth_stage.py`) creates the baseline wiki structure and enriches safe wiki pages from the findings.
- **Stage 1A2** — the `stage-1a2-orchestrator` agent loops 1B↔1C from a single prompt. Reviewer search artifacts persist under `workspace-artifacts/wiki/reviews/search/`.
- **Stage 1B** (`depth_stage.py`) — Schema Auditor / Adversarial Questioner / Domain Ontologist debate, producing a merged gap report.
- **Stage 1C** (`review_stage.py`) — three fresh-context reviewers (Optimistic/Pessimistic/Pragmatic) emit PROCEED or ITERATE verdicts.
- **Stage 2** (`elicit_stage.py` + `audit_stage.py`) — prompt-as-conductor. `elicit-vision --start` writes a brief + transcript skeleton + `precheck_request.yaml`; the `stage-2-dialog` prompt invokes `@stage2-orchestrator mode=preflight`, conducts the dialog (human + LLM) using the `grill-me` questioning discipline, then calls `elicit-vision --finalize` which parses decision blocks from the transcript and compiles `decision_log_v{N}.yaml`. Postflight is a second `@stage2-orchestrator` invocation (fidelity audit, including dialog-depth review), then `audit-requirements`. `run-all` stops after preflight — the dialog cannot happen in a CLI subprocess. Full spec at `.github/docs/stage-2-hardening.md`. **Stage 2 re-entry** follows the 6-step conductor prompt at `.github/prompts/stage2-reentry.prompt.md`; Step 0 (problem-space re-ingestion) produces `reentry_request.yaml` and is enforced by the non-overridable `gate_reentry_request` hook before any CLI fires.
- **Stage 2.5** (`plan_implementation_stage.py`) — implementation planning. `plan-implementation --start` renders `runtime/plan/brief.md` from the decision log + findings + citations, including planner evidence context, trigger vocabulary, and cited finding summaries. The `implementation-planner` agent asks clarifying questions and writes `decision-logs/implementation_plan_v{N}.md`; the markdown is the human-readable step-by-step implementation plan, while the fenced `capability_plan` YAML is the machine-readable extract. `capability_plan.version: 2` entries can carry `phase`, `objective`, `implementation_steps`, `acceptance_criteria`, `explicit_triggers`, `evidence_refs`, `parallelizable`, and `rationale`. `plan-implementation --finalize` validates the plan and writes `decision-logs/plan_extract_v{N}.yaml`.
- **Stage 3** is a four-layer capability-driven compile: capability compile → contract extract → skill synthesis → workspace bootstrap. It consumes the Decision Log plus cited findings; when a Stage 2.5 plan extract exists, capabilities preserve N-to-M REQ/CON mappings, planner `explicit_triggers`, concrete `implementation_steps`, acceptance criteria, and evidence refs. Skills render those steps into `SKILL.md` instead of falling back to generic procedures.
- **Stage 4** (`phase4_stage.py`) — the LLM ralph loop uses `@execution-orchestrator` plus the static planner/implementer/reviewer/researcher palette to populate `workspace-artifacts/executions/v{N}/work/{capability_id}/`, then `phase4-finalize --finalize` compiles the final manifest and pitch deck. Stage 4 reads `DISPATCH_HINTS.yaml`; the legacy `orchestrator/run_stage4.py` fallback is gone.

Post-scaffold commands (`concept_reconciliation_stage.py`, `stage2_reentry.py`, `seed_tracker.py`, `clean_stage.py`) preserve version history under `workspace-artifacts/`. The legacy `wiki-update` command was replaced by a two-phase semantic wiki enrichment pipeline (see CLAUDE.md §Semantic Wiki Enrichment).

### Hook-enforced determinism

CLI calls in stage prompts are enforced by VSCode Copilot hooks (`.github/hooks/main.json` + per-agent `hooks:` frontmatter). Auto-fire chains eliminate the "LLM skips the CLI" failure for pure-CLI steps; `gate_cli` blocks out-of-order invocations; `gate_reentry_request` closes the Stage 2 re-entry dialog gap. See `.github/docs/hooks.md` for the full check inventory.

Override mechanisms:
- Per-call env flag: `META_COMPILER_SKIP_HOOK=1` (not honored by `gate_reentry_request`, `gate_orchestrator_mode_*`, `require_verdict_*`, `validate_findings_schema`, or `validate_repo_map_schema`).
- Config file: `.github/hooks/overrides.json` (gitignored, time-bounded).

### Artifacts Layout

The artifact tree under `workspace-artifacts/` is the source of truth. All paths are owned by `meta_compiler/artifacts.py::build_paths`:

```
workspace-artifacts/
  seeds/                    # Immutable once tracked
    code/<repo-name>/       # Git-pinned code seed (immutable at commit SHA)
  wiki/
    v1/pages/               # Stage 1A output (type: source|concept|code|code-repo)
    v2/pages/               # Stage 1B output
    citations/index.yaml    # Every claim traces to an ID here
    findings/               # Findings JSON from ingest-orchestrator (doc + code)
    reports/                # Gap reports, impact reports, seed tracking
    reviews/search/         # Reviewer-scoped external search artifacts
    provenance/what_i_built.md  # Refreshed at Stage 3 and Stage 4
  decision-logs/            # decision_log_v{N}.yaml + requirements_audit.yaml
  scaffolds/v{N}/           # Generated project workspaces + tests/
  executions/v{N}/          # Stage 4 final outputs
  pitches/                  # Markdown + PPTX decks
  manifests/workspace_manifest.yaml
  manifests/source_bindings.yaml  # bindings (per-file) + code_bindings (per-repo commit SHA)
  runtime/                  # Ephemeral work plans (e.g., ingest/work_plan.yaml)
    ingest/repo_map/<name>.yaml   # Per-repo RepoMap written by the repo-mapper subagent
```

When modifying any stage, verify `validation.py` still enforces the invariants for the affected artifacts (`validate-stage --stage <N>`).

### Code Ingestion

Code seeds are registered by `meta-compiler add-code-seed --repo <url> --ref <sha|tag> --name <slug>` (clones into `seeds/code/<slug>/` and pins HEAD) or `meta-compiler bind-code-seed --path <rel>` (records an existing clone's HEAD). Both write a `code_bindings` entry to `source_bindings.yaml`; `validate_seed_immutability` enforces commit-SHA drift for those prefixes instead of per-file SHA.

`meta-compiler ingest` classifies every seed as `seed_kind: doc` or `seed_kind: code` (based on whether its path lives under a registered code_bindings prefix plus an extension check), mints `src-<repo>-<path-slug>` citation IDs for code, and emits a `repo_map_items[]` block in the work plan. The `ingest-orchestrator` runs a two-pass protocol:

- **Pass 1 (repo-mapping).** For each `repo_map_items[]` entry, spawn a `repo-mapper` subagent (≤2 parallel). It walks the pinned tree via `git ls-files`, detects languages / entry points / modules / manifests, and writes `runtime/ingest/repo_map/<name>.yaml`. The `validate_repo_map_schema` hook gates that write.
- **Pass 2 (per-file fan-out).** Partition `work_items` by `seed_kind`; spawn `seed-reader` for doc items and `code-reader` for code items (≤4 parallel across both). Each `code-reader` receives the RepoMap YAML for its repo as additional context and emits a code findings JSON with `source_type: "code"` and `{file, line_start, line_end}` locators.

Both finding kinds share `wiki/findings/`. `validate_findings_file` dispatches on `source_type`/`file_metadata`; hook `validate_findings_schema` is correspondingly polymorphic.

In the wiki, code findings render `type: code` pages (per file), and each registered repo gets a `type: code-repo` overview page rendered from its RepoMap. Concept aggregation (`breadth_stage.py:_aggregate_concepts_from_findings`) is unchanged — concepts from doc and code findings merge into the same concept page, giving doc↔code cross-references for free.

### Provisioned `.github/` Assets

`meta-init` writes workspace customization assets into `.github/agents/`, `.github/prompts/`, `.github/skills/`, and `.github/instructions/`. Stage 1A2 delegating agents must expose the `agent` tool and include `explore` and `research` in their `agents:` allowlist (unless the Decision Log explicitly narrows it). When you change a stage's behavior, update the canonical `.github/` asset; `meta-init` mirrors `.github/prompts/*.prompt.md` into target root `prompts/`.

### Document Scripts

Binary seed pre-extraction and final deliverable writing use dedicated scripts:
- `scripts/pdf_to_text.py` — PyMuPDF-based PDF extraction.
- `scripts/read_document.py` — PDF/DOCX/XLSX/PPTX/TXT/MD/RST/TEX/CSV extraction.
- `scripts/write_document.py` — writes generated DOCX/XLSX/PPTX outputs (Stage 4 uses this for the pitch deck).

## Stage Boundaries (Quick Reference)

- `meta-compiler run-all` stops after Stage 2 preflight. It prepares ingest, runs Stage 1A/1B/1C, and runs `elicit-vision --start` which writes `workspace-artifacts/runtime/stage2/brief.md`, `transcript.md`, and `precheck_request.yaml`. The Stage 2 dialog itself happens in Copilot Chat reading `.github/prompts/stage-2-dialog.prompt.md`; the conductor uses the `grill-me` questioning discipline before writing decision blocks, then the operator runs `meta-compiler elicit-vision --finalize` (compiles transcript → `decision_log_v{N}.yaml`) and `meta-compiler audit-requirements`. Humans review the Decision Log and the requirements audit before `meta-compiler scaffold`. Full spec: `.github/docs/stage-2-hardening.md`.
- Stage 1A is findings-first:
  1. `meta-compiler ingest --scope all|new`
  2. Use `prompts/ingest-orchestrator.prompt.md` or `@ingest-orchestrator` to write findings JSON under `workspace-artifacts/wiki/findings/`
  3. `meta-compiler ingest-validate`
  4. `meta-compiler research-breadth`
- Stage 3 consumes the Decision Log only. Do not pull raw wiki or seed content directly into scaffold output unless the Decision Log explicitly requires it.
- When new seeds arrive after Stage 3, run `meta-compiler track-seeds` to report the handoff, then `ingest --scope new` → `research-breadth` → `wiki-reconcile-concepts` + `wiki-apply-reconciliation` + `wiki-cross-source-synthesize`. Recommend Stage 2 re-entry when new evidence changes scope, architecture, or requirements.

## Evidence Quality Rules

These rules are load-bearing for the system to work — do not soften them:

- **Data over folklore.** Every claim in a wiki page needs a direct quote, page number, section number, equation number, or line number. Paraphrased summaries are insufficient and cause the validation layer to flag pages.
- **Seeds are immutable** once tracked. Never rewrite or delete files under `workspace-artifacts/seeds/`. Code seeds are immutable at the commit-SHA boundary — `git checkout`, `git pull`, `git commit` inside a `seeds/code/<name>/` tree are policy violations.
- **Citation IDs and requirement IDs must survive transformations.** Stage 3 embeds them into generated agents; do not drop or renumber them.
- **Findings JSON is reusable infrastructure.** Keep it schema-valid and mark gaps explicitly rather than inventing content to fill them.
- **Stage 3 consumes the Decision Log only.** Do not pull raw wiki or seed content into scaffold output unless the Decision Log explicitly requires it.
- **Full paper text enforcement.** Every wiki page must include direct quotes or specific references (page, section, equation, or line number) from source material. Pages with only paraphrased summaries are insufficient.

## Subagent Discipline

- Do **not** use the Explore subagent for reading seeds or code — it samples, which produces hallucinated quotes. Use `seed-reader` for doc items, `code-reader` for code items, `repo-mapper` for repo walks.
- Agent outputs are validated against schemas (findings JSON, RepoMap YAML) at write time by the `validate_findings_schema` and `validate_repo_map_schema` hooks. Fix the payload rather than bypassing the hook.
- When chunking a long document or file, record `extraction_stats.chunks_used` in the returned JSON. Never silently truncate.
