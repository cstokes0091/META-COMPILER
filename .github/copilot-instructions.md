# META-COMPILER Workspace Instructions

META-COMPILER is a hybrid Python and prompt workspace. The Python CLI owns
deterministic stage transitions, manifests, validation, artifact paths, and
filesystem updates. Prompts and `.github/agents/` own research, extraction,
review, and human-facing dialog.

## Operating Rules

- Treat `workspace-artifacts/` as the source of truth. Persist reasoning in artifacts, not chat history.
- Seed files under `workspace-artifacts/seeds/` are immutable once tracked.
- Read the stage prompt before acting on a stage. Keep root `prompts/` and `.github/` customization assets aligned when the workflow changes.
- Validate after each stage with `meta-compiler validate-stage --stage ...`.

## Stage Boundaries

- `meta-compiler run-all` stops after Stage 2 preflight. It prepares ingest, runs Stage 1A/1B/1C, and runs `elicit-vision --start` which writes `workspace-artifacts/runtime/stage2/brief.md`, `transcript.md`, and `precheck_request.yaml`. The Stage 2 dialog itself happens in a chat runtime reading `.github/prompts/stage-2-dialog.prompt.md`; after the dialog the operator runs `meta-compiler elicit-vision --finalize` (compiles transcript → `decision_log_v{N}.yaml`) and `meta-compiler audit-requirements`. Humans review the Decision Log and the requirements audit before `meta-compiler scaffold`. Full spec: `.github/docs/stage-2-hardening.md`.
- Stage 1A is findings-first:
  1. `meta-compiler ingest --scope all|new`
  2. Use `prompts/ingest-orchestrator.prompt.md` or `@ingest-orchestrator` to write findings JSON under `workspace-artifacts/wiki/findings/`
  3. `meta-compiler ingest-validate`
  4. `meta-compiler research-breadth`
- Stage 3 consumes the Decision Log only. Do not pull raw wiki or seed content directly into scaffold output unless the Decision Log explicitly requires it.
- Use `meta-compiler wiki-update` or `meta-compiler track-seeds` when new seeds arrive after Stage 3. Recommend Stage 2 re-entry when new evidence changes scope, architecture, or requirements.

## Evidence Quality

- Prefer direct quotes, page numbers, section numbers, and equation locators over paraphrase.
- Findings JSON is reusable infrastructure. Keep it schema-valid and mark gaps explicitly instead of inventing content.
- Preserve requirement IDs, citation IDs, and traceability across generated outputs.

## Document Scripts

- Use `python scripts/pdf_to_text.py` for PDFs.
- Use `python scripts/read_document.py` for DOCX, XLSX, PPTX, and other supported text extraction.
- Use `python scripts/write_document.py` for generated document outputs.