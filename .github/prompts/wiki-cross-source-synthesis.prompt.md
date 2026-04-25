---
name: wiki-cross-source-synthesis
description: "Phase B — fan out cross-source-synthesizer subagents per page, persist their JSON returns. The CLI validates each return and rewrites v2 page sections deterministically."
argument-hint: "Optional --version (default 2)"
---

# Wiki Cross-Source Synthesis — Prompt Instructions

## Intent

**Turn per-source paraphrase into cross-source reconciliation.** After
`wiki-apply-reconciliation`, canonical concept pages can carry `sources:`
lists drawn from multiple citations (one per alias). The page body,
however, is still whatever the Stage 1A baseline wrote from the first
source that mentioned the concept. This pass rewrites the Definition,
Key Claims, and Open Questions to explicitly name what each source
brings — agreements, divergences, contradictions — with every claim
cited by `[citation_id, locator]`.

**Structured findings, not rendered prose.** Synthesizers read only the
findings JSON records the preflight bundled for them. They do not read
other v2 pages; they do not read the raw seed documents. This grounds
every cross-source claim in a specific, locatable evidence record.

**Edits are sacred.** Pages flagged `user_edited: true` in the work plan
must not be overwritten. The v2 edit manifest gates this automatically.

## Your Role

Wiki Cross-Source Synthesis Orchestrator. You coordinate per-page
`cross-source-synthesizer` subagents and persist each subagent's JSON
return to
`workspace-artifacts/runtime/wiki_cross_source/subagent_returns/{page_id}.json`.
You do **not** validate the returns yourself, **not** rewrite v2 pages,
**not** manage the edit manifest. The
`wiki-apply-cross-source-synthesis` CLI reads every persisted return,
validates each against the page's expected citation IDs, reconstructs
the page body deterministically (preserving frontmatter, H1, Formalism,
Relationships, Source Notes including any `### Alias Sources`
subsection), records edit-manifest writes, and emits
`wiki/reports/cross_source_synthesis_applied_v{N}.yaml`. Removing the
page-rewrite step from this prompt eliminates a class of failure
(validation prose drifts from CLI behavior, mid-session crashes leave
pages half-rewritten, no audit trail) that the previous design suffered
from.

## When to Use

Run after `wiki-apply-reconciliation` has merged alias groups into
canonical pages. Re-run any time new findings land for a canonical
concept that already has ≥2 sources.

## Preflight

```bash
meta-compiler wiki-cross-source-synthesize --version 2
```

This writes:

- `workspace-artifacts/runtime/wiki_cross_source/work_plan.yaml` — one
  work item per canonical concept page backed by ≥2 sources AND covered
  by findings records under ≥2 of those citation IDs. Pages that don't
  meet both thresholds are logged under `skipped_single_source`,
  `skipped_no_findings`, or `skipped_user_edited` and never reach you.
- `workspace-artifacts/runtime/wiki_cross_source/cross_source_request.yaml`
  — the handoff artifact. The `gate_cross_source_synthesis_returns` hook
  blocks `wiki-apply-cross-source-synthesis` until this file exists and
  at least one subagent return has been persisted.

## Critical Rules

1. **v2 only — and don't rewrite pages.** The CLI rewrites v2 pages.
   This prompt does not touch any `.md` file under `wiki/v2/`.
2. **Citation discipline.** A subagent return whose `citations_used`
   includes any ID outside the work item's `source_citation_ids` will be
   rejected by the CLI validator. Retry the subagent once with the
   offending citation cited as evidence; on a second failure, drop the
   bucket (write nothing for it).
3. **Cross-source signal is mandatory.** Returns with fewer than 2
   distinct citations in `citations_used`, or whose `definition` text
   doesn't reference ≥2 distinct citation IDs inline, will be rejected.
   Retry once; on a second failure, drop the page.
4. **Persist verbatim.** Do not edit the subagent return JSON before
   writing it to disk. The CLI validator runs against the raw payload.

## Orchestration Protocol

### 1. Plan the Work

1. Read `workspace-artifacts/runtime/wiki_cross_source/work_plan.yaml`.
2. Treat `work_items[]` as source of truth. Each item has `page_id`,
   `page_file`, `aliases[]`, `source_citation_ids[]`,
   `covered_citation_ids[]`, and `findings_records[]`.
3. Use `findings_records[i].matched_concepts` to let the synthesizer
   see exactly which concept entries (under the canonical name or its
   aliases) each source carried.

### 2. Fan Out Synthesizer Subagents

For each work item, spawn one `cross-source-synthesizer` subagent. Run
up to **4 in parallel**. Each subagent receives:

- `page_id`, `page_file`, `aliases`.
- `source_citation_ids[]` — the authoritative cite allowlist.
- `findings_records[]` — per-citation bundles of matched concepts,
  claims, quotes, equations, relationships.

### 3. Persist Each Return

For each subagent return:

1. Parse as JSON. On parse failure, retry the subagent **once**.
2. Inject the `page_id` field if the subagent omitted it (use the work
   item's `page_id`). The CLI uses this to look up
   `expected_citation_ids` per page.
3. Write the JSON verbatim to
   `workspace-artifacts/runtime/wiki_cross_source/subagent_returns/{page_id}.json`.
4. On 2 failed retries, drop the page (write nothing). The CLI will
   surface the omission in the apply report.

### 4. Hand Off to `wiki-apply-cross-source-synthesis`

Print a one-line summary:

```
{N} page returns persisted at runtime/wiki_cross_source/subagent_returns/. Next: `meta-compiler wiki-apply-cross-source-synthesis --version 2` then `meta-compiler wiki-link --version 2`.
```

The CLI will then:

- Read every `subagent_returns/*.json`.
- Validate each return (`validate_cross_source_synthesis_return`).
- For each validated return, load the v2 page, skip if user-edited,
  rewrite Definition / Key Claims / Open Questions while preserving
  frontmatter, H1, Formalism, Relationships, and Source Notes (including
  `### Alias Sources`).
- Register every write via the edit manifest with
  `source: cross_source_synthesis`.
- Emit
  `workspace-artifacts/wiki/reports/cross_source_synthesis_applied_v{N}.yaml`.

## Subagent Return Schema (the JSON each `subagent_returns/*.json` holds)

```json
{
  "page_id": "concept-thermal",
  "definition": "Both sources agree the concept is X. [src-johnson, p.3] frames it as kT fluctuation; [src-detector, p.87] frames it as readout. Must cite >=2 sources inline.",
  "key_claims": "- (agreement) kT scaling [src-johnson, p.3] [src-detector, p.87]\n- (divergence) Spectral shape: [src-johnson, p.4] white; [src-detector, §4.3] colored after filter.",
  "open_questions": "- Why does src-detector avoid Johnson's name?",
  "citations_used": ["src-johnson", "src-detector"],
  "inter_source_divergences": [
    {
      "topic": "spectral shape",
      "sources": ["src-johnson", "src-detector"],
      "summary": "White vs colored after readout filter."
    }
  ]
}
```

`citations_used` MUST equal the set of `src-*` IDs that appear in inline
cites across the prose fields, MUST be a subset of the orchestrator's
`source_citation_ids`, and MUST contain at least 2 entries.
`inter_source_divergences` may be empty when sources unanimously agree —
but state that agreement explicitly in the Definition.
