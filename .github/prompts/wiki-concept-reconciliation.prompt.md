---
name: wiki-concept-reconciliation
description: "Phase A — cluster concept aliases across findings via concept-reconciler subagents, write the reconciliation proposal, and hand off to `wiki-apply-reconciliation`. Replaces the legacy wiki-update workflow."
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

**Evidence-grounded.** Every alias member in the proposal MUST carry a
`source_citation_id`, `evidence_locator`, and a `definition_excerpt` taken
verbatim from the candidate record. Members that can't cite are rejected.

**No backwards compatibility.** This pass replaces `wiki-update` entirely.
New-seed arrival is handled by re-running `ingest` → `research-breadth`
(both are findings-idempotent), followed by this reconciliation pass.

## Your Role

Wiki Concept Reconciliation Orchestrator. You coordinate per-bucket
`concept-reconciler` subagents, validate their JSON returns against the
alias-group schema, and compile a single
`workspace-artifacts/wiki/reports/concept_reconciliation_v{N}.yaml`
proposal that the CLI consumes. You do **not** make alias judgments
yourself. You do **not** write to v2 pages.

## When to Use

Run after `research-breadth` (Stage 1A baseline) completes and any time a
new batch of findings has arrived. Safe to re-run; subsequent passes will
only produce new groups when findings have changed.

## Preflight

```bash
meta-compiler wiki-reconcile-concepts --version 2
```

This writes:

- `workspace-artifacts/runtime/wiki_reconcile/work_plan.yaml` — one
  `work_items[]` entry per bucket with candidate concept records.
- `workspace-artifacts/runtime/wiki_reconcile/reconcile_request.yaml` — the
  handoff artifact. The `gate_reconcile_request` hook blocks this prompt's
  fan-out until that file exists.

The preflight already dropped singleton buckets and same-source-only
buckets (no cross-source signal); only buckets with ≥2 candidates from ≥2
distinct citation_ids reach you.

## Critical Rules

1. **Structured findings only.** Do not read page prose to make alias
   decisions. The candidate records carry every field a reconciler needs.
2. **Preserve citation IDs.** Every `source_citation_id` that appears in a
   candidate record must survive into the proposal if that candidate is
   placed in an alias group. The CLI merges `sources:` from these IDs.
3. **Bucket scope only.** Each `concept-reconciler` subagent decides alias
   groupings WITHIN its bucket. Cross-bucket mergers are out of scope for
   this pass (handled by the lexical linker once aliases land on canonical
   pages).
4. **Every member carries evidence.** The schema validator rejects any
   member missing `source_citation_id`, `evidence_locator`, or
   `definition_excerpt`. Retry on rejection.
5. **Do not invent.** Candidates without a locator stay out of the group.

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

### 3. Validate Each Return

For each subagent return:

1. Parse as JSON. On parse failure, retry the subagent **once** with the
   raw return cited as evidence.
2. Validate against the proposal schema:
   - `alias_groups[].canonical_name` is non-empty.
   - Each `alias_groups[].members[]` has `name`,
     `source_citation_id`, `evidence_locator` (a dict), and
     `definition_excerpt` (non-empty string).
   - Every `source_citation_id` appears in the bucket's
     `source_citation_ids`.
   - An `alias_group` has ≥2 members from ≥2 distinct
     `source_citation_id`s (otherwise demote to `distinct_concepts`).
3. On 2 failed retries, log the bucket as
   `reconciliation_skipped: true` and move on.

### 4. Compile the Proposal

Merge all validated subagent returns into one file:

```yaml
# workspace-artifacts/wiki/reports/concept_reconciliation_v2.yaml
concept_reconciliation_proposal:
  generated_at: <ISO-8601>
  version: 2
  bucket_count: <int>
  reconciled_bucket_count: <int>
  skipped_bucket_count: <int>
  alias_groups:
    - canonical_name: "Thermal Noise"
      members:
        - name: "Johnson noise"
          source_citation_id: "src-johnson-1928"
          evidence_locator: {page: 3, section: "2.1"}
          definition_excerpt: "random electron thermal fluctuation..."
        - name: "thermal noise"
          source_citation_id: "src-detector-chapter-4"
          evidence_locator: {page: 87, section: "4.2"}
          definition_excerpt: "noise proportional to kT in the readout chain..."
      justification: "Both definitions invoke Boltzmann thermal energy kT..."
  distinct_concepts:
    - name: "Read noise"
      source_citation_id: "src-detector-chapter-4"
      reason: "Different mechanism (detector electronics), not thermal kT."
```

### 5. Hand Off to `wiki-apply-reconciliation`

Print a one-line summary:

```
Reconciliation proposal written — <N> alias groups, <M> distinct concepts, <K> buckets skipped. Next: `meta-compiler wiki-apply-reconciliation --version 2`.
```

The CLI will then:

- Promote one page per alias group to canonical (creates it if absent).
- Merge the member `sources:` lists into canonical's frontmatter.
- Add `aliases:` to canonical frontmatter (member display names).
- Append the member definitions under `### Alias Sources` within the
  canonical page's `## Source Notes` section.
- Rewrite every member page as a `type: alias` redirect stub pointing at
  canonical.
- Stamp every write via the edit manifest with
  `source: concept_reconciliation`.

After the CLI completes, re-run `meta-compiler wiki-link --version 2` so
the lexical linker can pick up the new `aliases:` and link alias
mentions across the wiki to their canonical pages.

## Proposal Schema

```json
{
  "concept_reconciliation_proposal": {
    "generated_at": "ISO-8601",
    "version": 2,
    "alias_groups": [
      {
        "canonical_name": "string (non-empty)",
        "members": [
          {
            "name": "string (non-empty)",
            "source_citation_id": "src-*",
            "evidence_locator": {"page": 3, "section": "2.1"},
            "definition_excerpt": "string (verbatim from candidate.definition)"
          }
        ],
        "justification": "string (one sentence)"
      }
    ],
    "distinct_concepts": [
      {
        "name": "string",
        "source_citation_id": "src-*",
        "reason": "string"
      }
    ]
  }
}
```
