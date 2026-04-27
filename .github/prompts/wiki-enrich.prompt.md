---
name: wiki-enrich
description: "Single-prompt semantic wiki enrichment: refresh new findings when needed, reconcile aliases, synthesize cross-source concept pages, and relink v2 wiki aliases."
argument-hint: "Optional --scope new|all, --refresh true|false, --dry-run"
agent: wiki-enrichment-orchestrator
---

# Wiki Enrich — Prompt-as-Conductor

## Intent

Run semantic wiki enrichment from one chat entry point. This prompt keeps the
CLI/LLM split intact: the CLI prepares and validates artifact mutations, while
the LLM conductor runs the subagent fan-outs that VS Code cannot launch from a
plain CLI command.

Use this when the operator wants the wiki **enriched**, not merely refreshed:

- refresh new seed findings if needed;
- merge synonymous concept pages into canonical pages with aliases;
- synthesize multi-source Definition / Key Claims / Open Questions sections;
- refresh alias-aware wiki links;
- report skipped/no-op cases without treating them as failures.

## Arguments

Parse the user's invocation text conservatively:

- `--scope new|all` controls seed refresh scope. Default: `new`.
- `--refresh true|false` controls whether to run the seed refresh branch before
  semantic enrichment. Default: `true`.
- `--dry-run` runs preflight/planning only and must not invoke fan-out subagents
  or apply page rewrites. It may write runtime work-plan artifacts produced by
  CLI preflights.

Semantic enrichment is v2-only. Do not expose a version flag unless the CLI adds
support beyond `--version 2`.

## Your Role

You are the Wiki Enrichment Orchestrator. You coordinate the entire maintenance
workflow, but you do not hand-edit v2 wiki pages. Let the CLI apply validated
page mutations through `wiki-apply-reconciliation` and
`wiki-apply-cross-source-synthesis`.

## Critical Rules

1. **Structured findings only.** Reconciler and synthesizer work is driven by
   `workspace-artifacts/wiki/findings/*.json` and CLI work plans, not by raw seed
   prose or rendered wiki prose.
2. **Preserve the CLI/LLM boundary.** The CLI writes work plans, validates
   returns, applies wiki edits, and refreshes links. This prompt runs the LLM
   fan-out between preflight and postflight commands.
3. **No direct v2 page edits.** Do not write `.md` files under
   `workspace-artifacts/wiki/v2/pages/` from this prompt or its subagents.
4. **No invented citations.** Subagent returns must use only citation IDs and
   locators present in the work item they received.
5. **No-op is success.** Zero new seeds, zero alias buckets, or zero
   cross-source pages are valid terminal states. Report them clearly.
6. **Stage 2 re-entry remains a human boundary.** Recommend re-entry when
   enrichment exposes scope-changing aliases, contradictions, or divergences;
   never trigger it automatically.

## Workflow

### 1. Optional Seed Refresh

If `--refresh false` was provided, skip to Step 2.

Run:

```bash
meta-compiler wiki-update --scope {scope}
```

If the result status is `updated`, continue to Step 2.

If the result status is `ingest_pending_orchestrator`, run the guarded ingest
sequence before continuing:

```bash
meta-compiler ingest-precheck --scope {scope}
```

Invoke:

```
@ingest-orchestrator mode=preflight
```

If the preflight verdict is `BLOCK`, stop with the blocker list and remediation.
Otherwise invoke:

```
@ingest-orchestrator mode=fanout scope={scope}
```

Then run:

```bash
meta-compiler ingest-postcheck --scope {scope}
```

Invoke:

```
@ingest-orchestrator mode=postflight
```

If the postflight verdict is blocking or revisionary, stop with the findings
fidelity issues. Otherwise run:

```bash
meta-compiler ingest-validate
meta-compiler research-breadth
```

### 2. Concept Reconciliation Preflight

Run:

```bash
meta-compiler wiki-reconcile-concepts --version 2
```

Read `workspace-artifacts/runtime/wiki_reconcile/work_plan.yaml`.

If `work_items[]` is empty, record `alias_bucket_count: 0` and skip directly to
Step 4.

If `--dry-run` was provided, report the number of work items and stop before
fan-out or apply commands.

### 3. Concept Reconciler Fan-Out

For each work item, spawn one `concept-reconciler` subagent, up to 4 in
parallel. Pass the full work item: `bucket_key`, `candidate_count`,
`source_citation_ids`, and `candidates[]`.

For each subagent return:

1. Parse JSON. Retry once on parse failure.
2. Inject `bucket_key` if omitted, using the work item's `bucket_key`.
3. Write the JSON verbatim to
   `workspace-artifacts/runtime/wiki_reconcile/subagent_returns/{bucket_key}.json`.
4. After two failed attempts, skip that bucket and record it in the final
   summary.

Then run:

```bash
meta-compiler wiki-apply-reconciliation --version 2
```

### 4. Cross-Source Synthesis Preflight

Run:

```bash
meta-compiler wiki-cross-source-synthesize --version 2
```

Read `workspace-artifacts/runtime/wiki_cross_source/work_plan.yaml`.

If `work_items[]` is empty, record `cross_source_page_count: 0`, run Step 6,
and finish with a successful no-op or partial-enrichment summary.

If `--dry-run` was provided, report the number of work items and stop before
fan-out or apply commands.

### 5. Cross-Source Synthesizer Fan-Out

For each work item, spawn one `cross-source-synthesizer` subagent, up to 4 in
parallel. Pass the full work item: `page_id`, `page_file`, `aliases[]`,
`source_citation_ids[]`, `covered_citation_ids[]`, and `findings_records[]`.

For each subagent return:

1. Parse JSON. Retry once on parse failure.
2. Inject `page_id` if omitted, using the work item's `page_id`.
3. Write the JSON verbatim to
   `workspace-artifacts/runtime/wiki_cross_source/subagent_returns/{page_id}.json`.
4. After two failed attempts, skip that page and record it in the final summary.

Then run:

```bash
meta-compiler wiki-apply-cross-source-synthesis --version 2
```

### 6. Alias-Aware Link Refresh

Run:

```bash
meta-compiler wiki-link --version 2
```

## Final Report

Finish with a concise summary that includes:

- seed refresh status (`updated`, `no_new_seeds`, `skipped`, or `blocked`);
- alias bucket returns persisted and buckets skipped;
- reconciliation apply report path, if produced;
- cross-source page returns persisted and pages skipped;
- cross-source apply report path, if produced;
- `wiki-link` result;
- whether Stage 2 re-entry is recommended because enrichment surfaced
  scope-changing aliases, contradictions, or material divergences.

Do not end by asking the human whether to run Stage 2 re-entry. Name the
recommendation and the artifact evidence that supports it.
