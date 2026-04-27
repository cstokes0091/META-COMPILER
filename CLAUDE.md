# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

META-COMPILER is a research-first project scaffolding system. The Python CLI (`meta-compiler`) handles deterministic bookkeeping (validation, manifests, artifact paths, filesystem updates). LLM-driven prompts and `.github/agents/*.agent.md` files handle the reasoning work (research, extraction, evaluation, dialog). The two halves are deliberately separated.

The primary deliverable is a **workshop** — a reusable workspace that contains compiled knowledge and a generated execution framework — not the final algorithm or report itself.

**Intended runtime:** GitHub Copilot Chat in VSCode, reading `.github/agents/`, `.github/prompts/`, and `.github/skills/`. Stage prompts in `prompts/*.prompt.md` are the operator entry points.

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
pytest workspace-artifacts/scaffolds/v1/verification/ -v  # Scaffold verification stubs (only exist after Stage 3)
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
  2. The `ingest-orchestrator` prompt fans out `seed-reader` subagents that write findings JSON under `workspace-artifacts/wiki/findings/`.
  3. `ingest-validate` checks schema conformance.
  4. `research-breadth` (`breadth_stage.py`) creates the baseline wiki structure and enriches safe wiki pages from the findings.
- **Stage 1A2** — the `stage-1a2-orchestrator` agent loops 1B↔1C from a single prompt. Reviewer search artifacts persist under `workspace-artifacts/wiki/reviews/search/`.
- **Stage 1B** (`depth_stage.py`) — Schema Auditor / Adversarial Questioner / Domain Ontologist debate, producing a merged gap report.
- **Stage 1C** (`review_stage.py`) — three fresh-context reviewers (Optimistic/Pessimistic/Pragmatic) emit PROCEED or ITERATE verdicts.
- **Stage 2** (`elicit_stage.py` + `audit_stage.py`) — prompt-as-conductor. `elicit-vision --start` writes a brief + transcript skeleton + `precheck_request.yaml`; the `stage-2-dialog` prompt invokes `@stage2-orchestrator mode=preflight`, conducts the dialog (human + LLM), then calls `elicit-vision --finalize` which parses decision blocks from the transcript and compiles `decision_log_v{N}.yaml`. The decision log distinguishes **requirements** (REQ-NNN — behaviours to verify) from **constraints** (CON-NNN — bounds on how the system is built; tooling pins, regulatory limits, performance targets). Constraints carry `kind` and `verification_required` flags; `architecture[].constraints_applied` and `agents_needed[].key_constraints` may reference CON-NNN ids. Postflight is a second `@stage2-orchestrator` invocation (fidelity audit), then `audit-requirements`. `run-all` stops after preflight — the dialog cannot happen in a CLI subprocess. Full spec at `.github/docs/stage-2-hardening.md`. **Stage 2 re-entry** follows the 6-step conductor prompt at `.github/prompts/stage2-reentry.prompt.md`; Step 0 (problem-space re-ingestion) produces `reentry_request.yaml` and is enforced by the non-overridable `gate_reentry_request` hook before any CLI fires.
- **Stage 2.5** (`plan_implementation_stage.py`) — implementation planning. `plan-implementation --start` renders `runtime/plan/brief.md` from the decision log + findings + citations, including planner evidence context, trigger vocabulary, and cited finding summaries. The `implementation-planner` agent reads the brief, asks clarifying questions, and writes `decision-logs/implementation_plan_v{N}.md` with six required sections (Overview / Phases / Capabilities / Dependencies / Risks / Open Questions) plus a fenced `capability_plan` YAML block. The markdown is the human-readable, Claude-Code-style step plan; the YAML is the machine-readable extract. `capability_plan.version: 2` capabilities can carry `phase`, `objective`, `implementation_steps`, `acceptance_criteria`, `explicit_triggers`, `evidence_refs`, `parallelizable`, and `rationale`. `plan-implementation --finalize` validates the markdown structure and extracts the YAML to `decision-logs/plan_extract_v{N}.yaml`. The `gate_implementation_plan` hook blocks `--finalize` until the markdown exists; the `gate_capability_compile` hook blocks `compile-capabilities` when the markdown is present but no extract has been generated (operator can pass `--allow-no-plan` for the legacy 1-to-1 fallback). Capabilities in the plan extract may map N-to-M with REQ/CON ids; `verification_required: false` capabilities skip pytest stub generation in Stage 3.
- **Stage 3** is a four-layer capability-driven compile. `scaffold_stage.py::run_scaffold` is a thin composer; the real work is in four stages:
  1. `capability_compile_stage.py` parses the Decision Log + `wiki/findings/*.json` into `workspace-artifacts/scaffolds/v{N}/capabilities.yaml`. When `decision-logs/plan_extract_v{N}.yaml` exists (the Stage 2.5 output), capabilities follow the planner's N-to-M REQ/CON mapping, carry a `constraint_ids` field, propagate `verification_required`, and preserve v2 execution-planning metadata such as explicit triggers, implementation steps, acceptance criteria, phase, objective, and evidence refs. When the plan extract is absent (legacy / bootstrap), the legacy 1-to-1 row mapping runs unchanged. Each capability carries `when_to_use` triggers drawn from planner `explicit_triggers` first, then cited concept vocabulary, plus `required_finding_ids`, `requirement_ids`, `constraint_ids`, and `verification_required` so Stage 2 requirements all map back.
  2. `contract_extract_stage.py` derives I/O contracts from `agents_needed` / `architecture` / `code_architecture.data_model` rows, dedupes shapes, writes `contracts/{id}.yaml` + `_manifest.yaml`, and rewrites capabilities with real `io_contract_ref` values. Contracts may back multiple skills.
  3. `skill_synthesis_stage.py` renders one `skills/{capability_name}/SKILL.md` per capability plus `skills/INDEX.md`. Every `## ` section is populated from cited findings and, when present, the planner's concrete `implementation_steps` and `acceptance_criteria` — no templated slots.
  4. `workspace_bootstrap_stage.py` asserts the repo-level static agent palette (`planner`, `implementer`, `reviewer`, `researcher` under `.github/agents/`), emits `EXECUTION_MANIFEST.yaml`, `DISPATCH_HINTS.yaml`, `SCAFFOLD_MANIFEST.yaml`, `verification/REQ_TRACE.yaml`, one `verification/{hook_id}.py` pytest stub per `verification_required=True` capability (constraint-only / policy capabilities skip the stub but still appear in REQ_TRACE with `hook_ids: []`), and empty output buckets per `project_types.scaffold_subdirs_for(project_type)`. REQ_TRACE entries are keyed by both REQ-NNN and CON-NNN. No domain-named agents are generated anywhere.
- **Stage 4** (`phase4_stage.py`) — the LLM ralph loop populates `workspace-artifacts/executions/v{N}/work/{capability_id}/` via `@execution-orchestrator` plus the planner/implementer/reviewer/researcher palette, then `phase4-finalize --finalize` compiles the final manifest and pitch deck. Stage 4 now reads `DISPATCH_HINTS.yaml` (capability-keyed) instead of the legacy `AGENT_REGISTRY.yaml`, and the subprocess-based `orchestrator/run_stage4.py` fallback is gone.

Post-scaffold commands (`stage2_reentry.py`, `seed_tracker.py`, `clean_stage.py`) preserve version history under `workspace-artifacts/`. Semantic wiki enrichment replaces the legacy `wiki-update` command — see `concept_reconciliation_stage.py` and the §Semantic Wiki Enrichment section below.

### Semantic Wiki Enrichment

After the Stage 1A baseline produces per-source concept pages, two dedicated passes reconcile concepts across sources. Both follow the same shape — preflight CLI → orchestrator prompt fan-out → CLI postflight — and the page-rewrite logic lives in CLI code, not the orchestrator prompts. Subagent JSON returns are validated against schema in `validation.py` before the CLI mutates any page; the orchestrator prompts are thin dispatchers that persist returns to disk.

1. **Concept Reconciliation** (`wiki-reconcile-concepts` → `wiki-concept-reconciliation` prompt → `wiki-apply-reconciliation`). The preflight CLI flattens every `concepts[].name` across findings, buckets by normalized stem, and writes `runtime/wiki_reconcile/work_plan.yaml` plus `reconcile_request.yaml`. The orchestrator fans out `concept-reconciler` subagents (≤4 parallel) and persists each subagent's JSON to `runtime/wiki_reconcile/subagent_returns/{bucket_key}.json`. The postflight CLI reads every return, validates each via `validate_concept_reconciliation_return`, synthesizes the `concept_reconciliation_v{N}.yaml` proposal (or loads it directly if the orchestrator wrote it), and applies it: promotes one canonical page per group, merges `sources:` lists, appends member definitions under `### Alias Sources` in `## Source Notes`, adds `aliases:` frontmatter, and rewrites losing pages as `type: alias` redirect stubs. Every write is stamped with `source: concept_reconciliation` in the edit manifest. The `gate_reconcile_request` hook validates the proposal payload (when present) before the apply CLI runs.
2. **Cross-Source Synthesis** (`wiki-cross-source-synthesize` → `wiki-cross-source-synthesis` prompt → `wiki-apply-cross-source-synthesis`). For every canonical page backed by ≥2 sources AND covered by findings under ≥2 of those citations, the preflight CLI bundles findings records into a per-page work item and writes `runtime/wiki_cross_source/cross_source_request.yaml`. The orchestrator fans out `cross-source-synthesizer` subagents and persists each return to `runtime/wiki_cross_source/subagent_returns/{page_id}.json`. The postflight CLI validates each return via `validate_cross_source_synthesis_return`, deterministically rewrites Definition / Key Claims / Open Questions on each page (preserving frontmatter, H1, Formalism, Relationships, Source Notes including `### Alias Sources`), and emits `wiki/reports/cross_source_synthesis_applied_v{N}.yaml`. Writes are stamped with `source: cross_source_synthesis`. The `gate_cross_source_synthesis_returns` hook gates the apply step until the work plan and ≥1 subagent return are present.

The `wiki_linking.py` linker indexes every canonical page's `aliases:` list as secondary display names, so a mention of "Johnson noise" anywhere in the wiki now links to `concept-thermal.md`. Run `meta-compiler wiki-link --version 2` after reconciliation to pick up the new aliases.

New-seed arrival is handled by re-running `ingest --scope new` + `research-breadth`. The `meta-compiler wiki-update` command is a convenience wrapper that chains both: it runs the ingest preflight, halts with a remediation message if new seeds need orchestrator extraction (so `research-breadth` doesn't rebuild the index from stale findings), and otherwise refreshes the v1 wiki. Pass `--force` to refresh the index from existing findings without waiting for extraction. `track-seeds` reports the handoff but does not mutate the wiki itself.

### Hook-enforced determinism

As of 2026-04, CLI calls in stage prompts are enforced by VSCode Copilot hooks (`.github/hooks/main.json` + per-agent `hooks:` frontmatter). Auto-fire chains eliminate the "LLM skips the CLI" failure for pure-CLI steps; `gate_cli` blocks out-of-order invocations; `gate_reentry_request` closes the Stage 2 re-entry dialog gap. See `.github/docs/hooks.md` for the full check inventory.

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
                            # + implementation_plan_v{N}.md (Stage 2.5)
                            # + plan_extract_v{N}.yaml (machine-readable plan)
  scaffolds/v{N}/           # Generated project workspace: capabilities.yaml,
                            # contracts/, skills/{name}/SKILL.md + INDEX.md,
                            # verification/{hook_id}.py + REQ_TRACE.yaml,
                            # SCAFFOLD_MANIFEST.yaml + EXECUTION_MANIFEST.yaml
                            # + DISPATCH_HINTS.yaml, and empty output buckets
                            # (code/, tests/, report/, …) per project_type.
  executions/v{N}/          # Stage 4 final outputs
  pitches/                  # Markdown + PPTX decks
  manifests/workspace_manifest.yaml
  manifests/source_bindings.yaml  # bindings (per-file) + code_bindings (per-repo commit SHA)
  runtime/                  # Ephemeral work plans (e.g., ingest/work_plan.yaml)
    ingest/repo_map/<name>.yaml   # Per-repo RepoMap written by the repo-mapper subagent
    plan/brief.md                 # Stage 2.5 implementation-planner brief
    wiki_reconcile/subagent_returns/{bucket}.json    # concept-reconciler outputs
    wiki_cross_source/subagent_returns/{page}.json   # cross-source-synthesizer outputs
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

## Evidence Quality Rules

These rules are load-bearing for the system to work — do not soften them:

- **Data over folklore.** Every claim in a wiki page needs a direct quote, page number, section number, equation number, or line number. Paraphrased summaries are insufficient and cause the validation layer to flag pages.
- **Seeds are immutable** once tracked. Never rewrite or delete files under `workspace-artifacts/seeds/`.
- **Citation IDs and requirement IDs must survive transformations.** Stage 3 embeds them into generated agents; do not drop or renumber them.
- **Findings JSON is reusable infrastructure.** Keep it schema-valid. Mark gaps explicitly rather than inventing content to fill them.
- **Stage 3 consumes the Decision Log plus the findings it cites.** The capability compiler, contract extractor, and skill synthesizer read `wiki/findings/*.json` and `wiki/citations/index.yaml` to populate capability triggers, contract invariants, and skill bodies from cited concept vocabulary / claim statements / quotes. Do not pull raw seed content or uncited wiki prose into scaffold output.
