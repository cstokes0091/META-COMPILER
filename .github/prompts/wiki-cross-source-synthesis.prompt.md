---
name: wiki-cross-source-synthesis
description: "Phase B — synthesize cross-source Definitions + Key Claims for canonical concept pages backed by ≥2 sources. Surfaces agreement, divergence, and contradiction across findings. Writes only to v2; preserves user edits via the edit manifest."
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
other v2 pages; they do not read the raw seed documents. This
grounds every cross-source claim in a specific, locatable evidence record.

**Edits are sacred.** Pages flagged `user_edited: true` in the work plan
must not be overwritten. The v2 edit manifest gates this automatically.

## Your Role

Wiki Cross-Source Synthesis Orchestrator. You coordinate per-page
`cross-source-synthesizer` subagents, validate their JSON returns, write
the new prose into each v2 canonical concept page (preserving
frontmatter and the Formalism / Relationships / Source Notes sections),
and register every write in the v2 edit manifest with
`source: cross_source_synthesis`.

## When to Use

Run after `wiki-apply-reconciliation` has merged alias groups into
canonical pages. Re-run any time new findings land for a canonical
concept that already has ≥2 sources.

## Preflight

```bash
meta-compiler wiki-cross-source-synthesize --version 2
```

This writes
`workspace-artifacts/runtime/wiki_cross_source/work_plan.yaml` with one
work item per canonical concept page backed by ≥2 sources AND covered
by findings records under ≥2 of those citation IDs. Pages that don't
meet both thresholds are logged under `skipped_single_source`,
`skipped_no_findings`, or `skipped_user_edited` and never reach you.

## Critical Rules

1. **v2 only.** Never modify a file under `wiki/v1/`.
2. **Rewrite only the three sections.** Definition, Key Claims, Open
   Questions. Formalism / Relationships / `### Alias Sources` / other
   `## Source Notes` content is preserved verbatim.
3. **Preserve frontmatter exactly.** Do not modify `id`, `type`,
   `created`, `sources`, `related`, `aliases`, or `status`.
4. **Citation discipline.** The synthesizer's `citations_used` must be a
   subset of the page's `source_citation_ids`. If the synthesizer drops a
   source that the page declares, retry once citing the omission, then
   mark the page as `synthesis_skipped`.
5. **Honor the edit manifest.** Skip every work item with
   `user_edited: true`; the preflight has already filtered these.
6. **Register every write.** Immediately after writing a page, record
   `source: cross_source_synthesis` via
   `meta_compiler.wiki_edit_manifest.record_write`.

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

### 3. Validate Each Return

For each subagent return:

1. Parse as JSON. On parse failure, retry the subagent **once**.
2. Validate against the synthesis schema (see Output Format in
   `cross-source-synthesizer.agent.md`):
   - `definition`, `key_claims`, `open_questions` are non-empty.
   - `citations_used` is a subset of the work item's
     `source_citation_ids` AND has ≥2 entries (single-source results
     violate the intent of this pass).
   - The Definition body references ≥2 distinct `src-*` citations.
3. Spot-verify 2 random `[id, locator]` cites against
   `findings_records[i].claims[*].locator` or
   `.quotes[*].locator` or `.equations[*].locator`. Reject + retry on
   mismatch, with the failing reference cited.
4. On 2 retries, mark the page as `synthesis_skipped` and move on.

### 4. Persist

For each validated synthesis payload:

1. Read the existing v2 page; split frontmatter + body.
2. Reconstruct the body, preserving the H1 + Formalism + Relationships +
   Source Notes (including any `### Alias Sources` subsection) verbatim:

   ```markdown
   # <existing H1>

   ## Definition
   <definition>

   ## Formalism
   <existing Formalism section verbatim>

   ## Key Claims
   <key_claims>

   ## Relationships
   <existing Relationships section verbatim>

   ## Open Questions
   <open_questions>

   ## Source Notes
   <existing Source Notes section verbatim>
   ```

3. Write back as `---\n<frontmatter>\n---\n<body>` and call
   `meta_compiler.wiki_edit_manifest.record_write(paths, page_path, "cross_source_synthesis")`.

### 5. Emit the Report

Write
`workspace-artifacts/wiki/reports/cross_source_synthesis_report.yaml`:

```yaml
cross_source_synthesis_report:
  generated_at: <ISO-8601>
  wiki_version: 2
  pages_considered: int
  pages_synthesized: int
  pages_skipped_user_edited: int
  pages_failed_validation: int
  per_page:
    - page_id: concept-thermal
      file: concept-thermal.md
      status: synthesized | skipped_user_edited | failed
      citations_used: [src-x, src-y]
      inter_source_divergences_flagged: 2
      reason: optional
```

### 6. Hand Off

Print a one-line summary:

```
Cross-source synthesis complete — N pages synthesized, M skipped (edited), K failed. Re-run `meta-compiler wiki-link --version 2` to refresh alias-aware links.
```
