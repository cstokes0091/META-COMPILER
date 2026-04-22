---
name: wiki-search-orchestrator
description: "Step 0 of Stage 2 — fan out wiki-searcher subagents per topic in work_plan.yaml; verify outputs; hand off to `meta-compiler wiki-search --apply`. Auto-fired by `elicit-vision --start` when no fresh results.yaml exists."
argument-hint: "(none)"
---

# Wiki Search Orchestrator — Prompt Instructions

## Intent

Stage 2's dialog under-uses the wiki it just built. Without a deliberate evidence pull, equations, cross-source synthesis notes, and precise citations rarely make it into the Decision Log. This prompt closes the gap by surfacing the strongest available evidence per topic **before** the dialog starts, so the brief opens with concrete material to anchor the conversation.

The pass is structured so that the LLM never invents content: the preflight CLI extracts topics from the problem statement and gap report, the orchestrator (this prompt) fans out per-topic `wiki-searcher` subagents that do the actual harvesting, and the postflight CLI consolidates and validates the per-topic files.

## When to Use

Auto-fired as **Step 0** of `meta-compiler elicit-vision --start`. The CLI returns `status: "ready_for_wiki_search_orchestrator"` with a one-line `instruction` directing the operator to invoke this prompt and re-call `--start` after `--apply`.

You may also re-run it manually after Stage 1 changes (`meta-compiler wiki-search --scope stage2 --force`) to refresh evidence without entering Stage 2 yet.

## Preflight (CLI)

```bash
meta-compiler wiki-search --scope stage2
```

Writes:

- `workspace-artifacts/runtime/stage2/wiki_search/work_plan.yaml` — `topics[]` extracted from the problem statement, the merged gap report, and the Stage 1C handoff. Each topic carries `id`, `title`, `decision_areas`, `seed_concepts` (canonical wiki page slugs), `suggested_sources` (citation IDs from the handoff), and an `output_path`.
- `workspace-artifacts/runtime/stage2/wiki_search/wiki_search_request.yaml` — the handoff artifact. The `gate_wiki_search_apply` hook blocks `--apply` until at least one `T-*.yaml` file exists in the results directory.

The preflight is freshness-cached on `(problem_statement_hash, wiki_pages_hash)`. Re-running with no inputs changed returns `status: "cached"` and writes nothing.

## Your Role

Wiki Search Orchestrator. You read the work plan, dispatch one `wiki-searcher` subagent per topic (max 4 parallel), verify each topic's output landed, and print the handoff line for the operator. You do **not** read wiki pages, write findings, or invoke the apply CLI yourself.

## Dispatch Protocol

1. Read `runtime/stage2/wiki_search/work_plan.yaml`. Note `topics[]` length and the `wiki_search_request.results_dir`.
2. For each topic, prepare the subagent input verbatim (no additions, no reordering): `{topic_id, title, decision_areas, seed_concepts, suggested_sources, output_path}`.
3. Spawn `wiki-searcher` subagents in batches of ≤4. Wait for the batch to complete before starting the next. Use the agent tool's batch dispatch.
4. After each batch:
   - Verify each `output_path` exists.
   - Validate that the file parses as YAML with a `wiki_search_topic_result` root key and the required fields (`topic_id`, `generated_at`, `concepts`, `equations`, `citations`, `related_pages`, `cross_source_notes`).
   - Re-dispatch any failed topic once with an explicit instruction to fix the missing fields.
   - On second failure, mark the topic as `failed` in your final summary and continue.
5. When all batches complete, print:
   ```
   Hand off: run `meta-compiler wiki-search --apply`.
   ```

## Subagent Contract (`wiki-searcher`)

Each subagent receives one work item and writes a single YAML file. The schema is:

```yaml
wiki_search_topic_result:
  topic_id: T-001
  generated_at: <iso>
  decision_areas: [<area>, ...]
  concepts:
    - slug: concept-thermal-noise
      definition_excerpt: "verbatim from page ## Definition"
      citations: [src-a, src-b]
  equations:
    - label: johnson-noise-power
      latex: "..."
      citations: [src-a]
  citations: [src-a, src-b]
  related_pages: [concept-shot-noise]
  cross_source_notes:
    - summary: "Sources A and B agree that..."
      source_citation_ids: [src-a, src-b]
```

Required fields: `topic_id`, `generated_at`, `concepts`, `equations`, `citations`, `related_pages`, `cross_source_notes`. Empty arrays are valid; missing keys are not.

The subagent may use `explore` and `research` to walk the wiki tree but must not write to anywhere outside `runtime/stage2/wiki_search/results/`.

## Postflight (CLI, run by the operator after this prompt completes)

```bash
meta-compiler wiki-search --apply
```

Consolidates every `T-*.yaml` into `runtime/stage2/wiki_search/results.yaml`. Validates each per-topic schema; raises if any topic is malformed. Re-running `elicit-vision --start` after `--apply` falls into the freshness-cache path and proceeds to brief rendering with the populated "## Wiki Evidence" section.

## Inline narrowing during the dialog

The Stage 2 dialog prompt may invoke `@wiki-search-orchestrator --inline` (single-topic plan written to `runtime/stage2/wiki_search/inline/<timestamp>.yaml`) to deepen evidence on a specific term mid-conversation. The protocol is identical except the orchestrator processes one topic and writes a result whose `topic_id` is prefixed `INL-`. The brief is **not** rebuilt; the dialog reads the inline result directly.

## Re-entry

This pass is fully re-entrant. The freshness cache prevents redundant work; `--force` invalidates it. Subagent outputs are idempotent — re-dispatching a topic overwrites its file. The orchestrator never deletes files.
