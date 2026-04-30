---
name: final-synthesis
description: "Stage 4 final-synthesis sub-stage: fan out per-modality synthesizer subagents, persist JSON returns. The CLI validates and assembles executions/v{N}/final/<bucket>/ deterministically."
argument-hint: "Optional --decision-log-version (default: latest)"
---

# Final Synthesis — Prompt Instructions

## Intent

**Turn per-capability fragments into ONE deliverable.** After the
`@execution-orchestrator` ralph loop finishes, `executions/v{N}/work/<cap>/`
holds dozens of fragments — Python files, markdown stubs, YAML data, test
files. None of those fragments alone is a Python library, a research
document, or a runnable workflow application. This pass composes them into
a coherent deliverable per the project's `meta.project_type`.

**Project-type branching is automatic.** The preflight reads `project_type`
from the EXECUTION_MANIFEST and emits `modality_keys[]`:

| project_type | modalities |
|---|---|
| `algorithm` (code) | `library` |
| `report` (document) | `document` |
| `hybrid` | `library`, `document` |
| `workflow` | `application` |

**Structured fragments, not free-form code generation.** Synthesizers read
only the fragments the preflight bundled for them, plus the decision log
meta + relevant blocks (code_architecture for library, workflow_config for
application, citations index for document). They do not read seeds, do not
read the wiki, do not invent code paths.

**Edits are sacred.** When a previous final/ tree exists and a file under
it has been edited after the last `final_synthesis_report.yaml`, the CLI
refuses to overwrite (pass `--force` to override).

## Your Role

Final-Synthesis Orchestrator. You coordinate per-modality synthesizer
subagents (`@library-synthesizer`, `@document-synthesizer`,
`@workflow-synthesizer`) and persist each subagent's JSON return to
`workspace-artifacts/runtime/final_synthesis/subagent_returns/<modality>.json`.
You do **not** validate the returns yourself, **not** assemble the
deliverable, **not** copy fragment bodies, **not** manage the workspace
manifest. The `final-synthesize-finalize` CLI reads every persisted return,
validates each against schema, runs the REQ-trace continuity check, and
assembles `executions/v{N}/final/<bucket>/` deterministically.

## When to Use

Run after the `@execution-orchestrator` postflight verdict says PROCEED
and the per-capability `work/<cap>/` directories are populated. Run
before `meta-compiler phase4-finalize --finalize` (the pitch sub-loop will
then cite the assembled artifacts in the deck).

## Preflight

```bash
meta-compiler final-synthesize-start
```

This walks `executions/v{N}/work/`, classifies every file by modality
(code / document / data, excluding `_plan.yaml` / `_verdict.yaml` /
`_manifest.yaml` capability bookkeeping), and writes:

- `workspace-artifacts/runtime/final_synthesis/work_plan.yaml` — one slice
  per modality. Each slice carries the fragments to synthesize plus the
  output directory under `executions/v{N}/final/<bucket>/`.
- `workspace-artifacts/runtime/final_synthesis/synthesis_request.yaml` —
  the orchestrator entry point. The `gate_final_synthesize_request` hook
  blocks `final-synthesize-finalize` until this file plus at least one
  subagent return is on disk.

## Critical Rules

1. **Read the work plan.** `modality_keys[]` is your authoritative fan-out
   list. Spawn exactly one subagent per modality.
2. **REQ preservation is non-negotiable.** Every subagent's prompt
   explicitly demands that `# REQ-NNN` annotations in source fragments
   survive into the assembled tree. The CLI postflight runs a continuity
   check over the assembled `final/.tmp/`; dropped REQs cause a hard
   rejection unless explicitly allowed via `--allow-req-drop REQ-NNN`.
3. **Citation discipline (document modality).** A subagent return whose
   inline cites include any ID outside `expected_citation_ids[]` will be
   rejected. Retry the synthesizer once; on a second failure, drop the
   modality (write nothing).
4. **No silent fragment loss.** Every entry in
   `modalities.<modality>.expected_fragment_tokens[]` (formatted
   `<capability>:<relative_path>`) MUST appear in the synthesizer's
   layout (`module_layout[].sources[]`, `section_order[].source`, or
   `directory_layout.<bucket>[].source`) OR be explicitly listed in
   `deduplications_applied[].dropped[]`. Silent loss is a hard rejection.
5. **Persist verbatim.** Do not edit the subagent return JSON before
   writing it to disk. The CLI validator runs against the raw payload.

## Orchestration Protocol

### 1. Plan the Work

1. Read `workspace-artifacts/runtime/final_synthesis/work_plan.yaml`.
2. Note `project_type`, `modality_keys[]`, `expected_citation_ids[]`, and
   `expected_req_ids[]`.
3. For each modality in `modality_keys[]`, pull
   `modalities.<modality>` — that's the slice your subagent will read.

### 2. Fan Out Per-Modality Synthesizers

Spawn one subagent per modality, in parallel when there's more than one
(hybrid: 2 in parallel):

| modality | subagent | reasoning |
|---|---|---|
| `library` | `@library-synthesizer` | designs package layout, exports, README, optional pyproject |
| `document` | `@document-synthesizer` | section ordering, intro/transitions/conclusion, deduplicated citations |
| `application` | `@workflow-synthesizer` | inbox/outbox/state wiring, runnable entry point, env vars, requirements |

Each subagent receives:

- The full `modalities.<modality>` slice (fragments[] + expected_fragment_tokens[] + output_dir).
- `expected_citation_ids[]` (full citations index — for the document synthesizer).
- `expected_req_ids[]` (REQ-NNN ids that must survive).
- `workflow_buckets[]` (only meaningful for `application`).
- The decision log's `meta`, `code_architecture` (library), `workflow_config` (application).

### 3. Persist Each Return

For each subagent return:

1. Parse as JSON. On parse failure, retry the subagent **once** with a
   directive to return only a JSON object.
2. Inject the `modality` field if the subagent omitted it (use the
   modality key the subagent was spawned for).
3. Write the JSON verbatim to
   `workspace-artifacts/runtime/final_synthesis/subagent_returns/<modality>.json`.
4. On 2 failed retries, drop that modality (write nothing). The CLI
   postflight will surface the omission.

### 4. Hand Off to `final-synthesize-finalize`

Print a one-line summary:

```
{N} modality returns persisted at runtime/final_synthesis/subagent_returns/. Next: `meta-compiler final-synthesize-finalize`.
```

The CLI will then:

- Read every `subagent_returns/<modality>.json`.
- Validate each return (modality-specific validators in
  `meta_compiler/validation.py`).
- Materialize `executions/v{N}/final/.tmp/<bucket>/` from the valid returns
  (library: package layout, README, optional pyproject; document: a
  single `<slug>.md` + `references.md` + optional `.docx`; application:
  bucketed layout + `run.py` + `requirements.txt` + `README.md`).
- Run the REQ-trace continuity check.
- Atomically swap `final/.tmp/` into place at `executions/v{N}/final/`.
- Emit `executions/v{N}/final_synthesis_report.yaml` with what was
  assembled, REQ-trace coverage, and `allowed_req_drops[]` (if any).
- Update the workspace manifest's `last_completed_stage` to
  `4-synthesized`.

## Subagent Return Schemas

Every subagent return is a single JSON object. The exact schema lives in
each subagent's `.github/agents/<name>.agent.md`. Sketches:

### library
```json
{
  "modality": "library",
  "package_name": "<snake_case>",
  "module_layout": [{"target_path": "<package>/<module>.py", "sources": [{"capability": "...", "relative_path": "..."}], "header_prose": "...", "footer_prose": "..."}],
  "exports": ["..."],
  "public_api": [{"symbol": "...", "summary": "...", "source_capability": "..."}],
  "entry_points": [{"name": "...", "target": "<module>:<callable>"}],
  "readme_sections": [{"heading": "Overview", "body": "..."}],
  "package_metadata": {"name": "...", "description": "...", "python_requires": ">=3.10"},
  "deduplications_applied": [{"kept": "...", "dropped": ["..."], "reason": "..."}]
}
```

### document
```json
{
  "modality": "document",
  "title": "...",
  "abstract": "<=500 chars",
  "section_order": [
    {"heading": "Background", "source": {"synthesizer_prose": "..."}, "transitions_after": null, "citations_inline": ["src-foo"]},
    {"heading": "Approach", "source": {"capability": "...", "file": "..."}, "transitions_after": "...", "citations_inline": []}
  ],
  "intro_prose": "...",
  "conclusion_prose": "...",
  "references_unified": [{"id": "src-foo", "human": "..."}],
  "deduplications_applied": [...]
}
```

### application
```json
{
  "modality": "application",
  "application_name": "kebab-case",
  "directory_layout": {
    "inbox": [{"source": "<cap>:<path>", "target": "inbox/sample.docx"}],
    "outbox": [],
    "state": [...],
    "kb_brief": [...],
    "tests": [...],
    "orchestrator": [...]
  },
  "entry_point": {"filename": "run.py", "body": "<full Python source>", "invocation": "python run.py --inbox ..."},
  "environment_variables": [{"name": "API_KEY", "purpose": "...", "required": true}],
  "dependencies": ["python-docx==1.1.0"],
  "readme_sections": [{"heading": "Overview", "body": "..."}],
  "deduplications_applied": [...]
}
```

`directory_layout` must include every bucket in `workflow_buckets[]` from
the work plan. `entry_point.body` must parse as valid Python. The CLI
rejects silent fragment loss and malformed entry points.
