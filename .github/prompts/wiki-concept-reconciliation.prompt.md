---
name: wiki-concept-reconciliation
description: "Phase A — fan out concept-reconciler subagents per bucket, persist their JSON returns, and hand off to `wiki-apply-reconciliation`. The CLI assembles, validates, and applies the proposal."
argument-hint: "Optional --version (default 2)"
---

# Wiki Concept Reconciliation — Prompt Instructions

## Intent

**Cluster synonymous concept names across sources.** The lexical wiki linker
(`wiki_linking.py`) only catches mentions whose text matches a canonical
concept page's display name. That leaves every alias orphaned: a page about
"thermal noise" will never link to a source that wrote "Johnson noise" or
"Nyquist noise" unless something upstream has *told* the wiki those three
are the same concept.

This pass is that upstream signal. It reads the structured findings JSON
(which carries each source's per-concept definition and first-mention
locator) and produces a proposal that maps alias variants to a canonical
name, with evidence. The `wiki-apply-reconciliation` CLI then promotes one
page per group to canonical, merges the source lists, appends the member
definitions under a new `### Alias Sources` block, and rewrites losing
pages as short `type: alias` redirect stubs.

**Evidence-grounded.** Every alias member MUST carry a
`source_citation_id`, `evidence_locator`, and a `definition_excerpt` taken
verbatim from the candidate record. Members that can't cite are rejected.

**No backwards compatibility.** This pass replaces `wiki-update` entirely.
New-seed arrival is handled by re-running `ingest` → `research-breadth`
(both are findings-idempotent), followed by this reconciliation pass.

## Your Role

Wiki Concept Reconciliation Orchestrator. You coordinate per-bucket
`concept-reconciler` subagents and persist each subagent's JSON return to
`workspace-artifacts/runtime/wiki_reconcile/subagent_returns/{bucket_key}.json`.
You do **not** compile the proposal yourself. You do **not** write to v2
pages. The `wiki-apply-reconciliation` CLI reads every persisted return,
validates each against the per-bucket schema, assembles the
`concept_reconciliation_v{N}.yaml` proposal, and applies it to the wiki
deterministically. Removing the synthesis step from this prompt eliminates
a class of silent corruption (orchestrator drops a member field, retries
fail, malformed proposal slips through) that the previous design suffered
from.

## When to Use

Run after `research-breadth` (Stage 1A baseline) completes and any time a
new batch of findings has arrived. Safe to re-run; subsequent passes only
produce new groups when findings have changed.

## Preflight

```bash
meta-compiler wiki-reconcile-concepts --version 2
```

This writes:

- `workspace-artifacts/runtime/wiki_reconcile/work_plan.yaml` — one
  `work_items[]` entry per bucket with candidate concept records.
- `workspace-artifacts/runtime/wiki_reconcile/reconcile_request.yaml` — the
  handoff artifact. The `gate_reconcile_request` hook blocks
  `wiki-apply-reconciliation` until that file exists, until at least one
  proposal exists, and until the proposal validates.

The preflight already dropped singleton buckets and same-source-only
buckets (no cross-source signal); only buckets with ≥2 candidates from ≥2
distinct citation_ids reach you.

## Critical Rules

1. **Structured findings only.** Do not read page prose to make alias
   decisions. The candidate records carry every field a reconciler needs.
2. **Preserve citation IDs.** Every `source_citation_id` that appears in a
   candidate record must survive into the subagent return if that
   candidate is placed in an alias group. The CLI merges `sources:` from
   these IDs.
3. **Bucket scope only.** Each `concept-reconciler` subagent decides alias
   groupings WITHIN its bucket. Cross-bucket mergers are out of scope for
   this pass (handled by the lexical linker once aliases land on canonical
   pages).
4. **Every member carries evidence.** The CLI validator rejects any
   member missing `source_citation_id`, `evidence_locator`, or
   `definition_excerpt`. Retry on rejection.
5. **Do not invent.** Candidates without a locator stay out of the group.
6. **Do not write the proposal YAML yourself.** The CLI synthesizes the
   `concept_reconciliation_v{N}.yaml` from the per-bucket returns. Writing
   it from the prompt re-introduces the silent-corruption failure mode.

## Orchestration Protocol

### 1. Plan the Work

1. Read `workspace-artifacts/runtime/wiki_reconcile/work_plan.yaml`.
2. Treat `work_items[]` as the source of truth. Each item has
   `bucket_key`, `candidate_count`, `source_citation_ids`, and
   `candidates[]`.

### 2. Fan Out Reconciler Subagents

For each work item, spawn one `concept-reconciler` subagent. Run up to
**4 in parallel**. Each subagent receives:

- `bucket_key` (a stem like `"noise"` — diagnostic only).
- `candidates[]` — full records including `name`, `definition`,
  `importance`, `first_mention`, `source_citation_id`, `source_path`,
  `findings_path`.

### 3. Persist Each Return

For each subagent return:

1. Parse as JSON. On parse failure, retry the subagent **once** with the
   raw return cited as evidence.
2. Inject the `bucket_key` field if the subagent omitted it (use the
   work item's `bucket_key`). The CLI uses this to look up
   `expected_citation_ids` per bucket.
3. Write the JSON verbatim to
   `workspace-artifacts/runtime/wiki_reconcile/subagent_returns/{bucket_key}.json`.
   Do NOT modify the payload — the CLI validator runs against the raw
   subagent output.
4. On 2 failed retries, log the bucket as skipped (write nothing for it)
   and move on.

### 4. Hand Off to `wiki-apply-reconciliation`

Print a one-line summary:

```
{N} bucket returns persisted at runtime/wiki_reconcile/subagent_returns/. Next: `meta-compiler wiki-apply-reconciliation --version 2`.
```

The CLI will then:

- Read every `subagent_returns/*.json`.
- Validate each return (`validate_concept_reconciliation_return`).
- Assemble the `concept_reconciliation_v{N}.yaml` proposal at
  `workspace-artifacts/wiki/reports/`.
- Promote one page per alias group to canonical (creates it if absent).
- Merge member `sources:` lists into canonical's frontmatter.
- Add `aliases:` to canonical frontmatter (member display names).
- Append member definitions under `### Alias Sources` within the
  canonical page's `## Source Notes` section.
- Rewrite every member page as a `type: alias` redirect stub pointing at
  canonical.
- Stamp every write via the edit manifest with
  `source: concept_reconciliation`.

After the CLI completes, re-run `meta-compiler wiki-link --version 2` so
the lexical linker can pick up the new `aliases:` and link alias mentions
across the wiki to their canonical pages.

## Subagent Return Schema (the JSON each `subagent_returns/*.json` holds)

```json
{
  "bucket_key": "noise",
  "alias_groups": [
    {
      "canonical_name": "Thermal Noise",
      "members": [
        {
          "name": "Johnson noise",
          "source_citation_id": "src-johnson-1928",
          "evidence_locator": {"page": 3, "section": "2.1"},
          "definition_excerpt": "random electron thermal fluctuation in a resistor..."
        },
        {
          "name": "thermal noise",
          "source_citation_id": "src-detector-chapter-4",
          "evidence_locator": {"page": 87, "section": "4.2"},
          "definition_excerpt": "noise proportional to kT in the readout chain..."
        }
      ],
      "justification": "Both definitions invoke Boltzmann thermal energy kT..."
    }
  ],
  "distinct_concepts": [
    {
      "name": "Read noise",
      "source_citation_id": "src-detector-chapter-4",
      "reason": "Different mechanism (detector electronics), not thermal kT."
    }
  ]
}
```

The CLI rejects any return missing required member fields, with a
`source_citation_id` outside the bucket's allowlist, or with an alias
group spanning fewer than 2 distinct sources.
