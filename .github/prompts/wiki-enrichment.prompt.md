---
name: wiki-enrichment
description: "Phase C1 — synthesize v2 wiki pages from findings via wiki-synthesizer subagents. Replaces templated bullets with cross-source prose. Writes only to v2; preserves user edits via the edit manifest."
argument-hint: "Optional --version (default 2)"
---

# Wiki Enrichment — Prompt Instructions

## Intent

**Lift v2 wiki pages from templated bullets into cross-source prose.** Stage 1A
produces a deterministic, per-source baseline (v1, mirrored to v2). Stage 1B
copies those baselines into v2. This pass rewrites each v2 concept page's prose
body so that it (a) reconciles definitions across sources, (b) names tensions
and agreements the per-source extractions could not see, (c) inline-links
sibling concept pages, and (d) cites every claim with `[citation_id, locator]`.

**Data over folklore.** Synthesis is not paraphrase. Every claim still needs a
source + locator. The synthesizer's only license is to combine and structure
the evidence — never to invent it.

**Edits are sacred.** Pages flagged `user_edited: true` in the work plan must
not be overwritten without explicit operator approval. The v2 edit manifest
exists to make this distinction safe and automatic.

## Your Role

Wiki Enrichment Orchestrator. You coordinate per-page `wiki-synthesizer`
subagents, validate their JSON returns against the synthesis schema, write the
new prose into each v2 page (preserving frontmatter), and register every write
in the v2 edit manifest. You do **not** synthesize prose yourself. You do
**not** touch v1 pages.

## When to Use

Auto-fired immediately after `meta-compiler research-depth` succeeds. Can also
be re-run manually after editing v2 pages to re-synthesize remaining
templated pages.

The deterministic prep step is already available:

```bash
meta-compiler enrich-wiki --version 2
```

Run that command first. It writes
`workspace-artifacts/runtime/wiki_enrichment/work_plan.yaml`.

## Critical Rules

1. **v2 only.** Never modify a file under `wiki/v1/`. v1 is the regenerable
   baseline; if v1 is touched, Stage 1A becomes destructive.
2. **Preserve frontmatter exactly.** The synthesizer returns prose for body
   sections only; you reconstruct the file as `---\n<original frontmatter>\n---\n<H1>\n\n<synthesized sections>\n<original Relationships and Source Notes>`.
   Do not modify `id`, `type`, `created`, `sources`, `related`, or `status`.
3. **Preserve every citation ID** that appears in the page's `sources:`
   frontmatter. The synthesizer's `citations_used` must be a subset; if it
   drops a source the page declared, retry with the omission cited.
4. **Honor the edit manifest.** Skip every work item with `user_edited: true`
   unless the operator has explicitly authorized re-enrichment for that page.
5. **Register every write.** Immediately after writing a page, the orchestrator
   must call (via the CLI or directly) the equivalent of:

   ```python
   from meta_compiler.wiki_edit_manifest import record_write
   record_write(paths, page_path, "enrichment")
   ```

   Failure to record means Stage 1B's next sync may overwrite the enriched
   prose. The hook layer enforces this.

## Orchestration Protocol

### 1. Plan the Work

1. Run `meta-compiler enrich-wiki --version 2`.
2. Read `workspace-artifacts/runtime/wiki_enrichment/work_plan.yaml`.
3. Use `work_items[]` as the source of truth for `page_id`, `page_path`,
   `findings_paths`, and the page's `source_citation_ids`.
4. Use `related_pages[]` to populate cross-link hints for the synthesizer.

### 2. Fan Out Synthesizer Subagents

For each work item where `user_edited` is `false` and `findings_paths` is
non-empty, spawn one `wiki-synthesizer` subagent. Run up to **4 in parallel**.
Each subagent receives:

- `page_id` and `page_path`.
- The list of `findings_paths` to read in full.
- `expected_citation_ids` (the page's existing `sources` list).
- `related_pages[]` (every other v2 page's `id`, `file`, `display_name`).
- A copy of the synthesis schema (see Output Format in
  `wiki-synthesizer.agent.md`).

### 3. Validate Each Return

For each subagent return:

1. Parse as JSON. On parse failure, retry the subagent **once** with the raw
   return cited as evidence.
2. Validate against the synthesis schema:
   - `definition`, `formalism`, `key_claims`, `open_questions` are non-empty
     strings.
   - `citations_used` is a subset of the page's `source_citation_ids`.
   - `related_pages_linked` is a subset of `related_pages[].file`.
3. Spot-verify 2 random `[id, locator]` references against the corresponding
   findings JSON. If a locator does not match any quote/claim/equation locator
   in that finding, reject and retry with the failing reference cited.
4. On 2 retries, mark the page as `synthesis_skipped: true` in the report and
   continue.

### 4. Persist

For each validated synthesis payload:

1. Read the existing v2 page; split into frontmatter + body.
2. Reconstruct the body as:

   ```markdown
   # <H1 from existing body>

   ## Definition
   <definition>

   ## Formalism
   <formalism>

   ## Key Claims
   <key_claims>

   ## Relationships
   <existing Relationships section verbatim>

   ## Open Questions
   <open_questions>

   ## Source Notes
   <existing Source Notes section verbatim>
   ```

3. Write back as `---\n<frontmatter>\n---\n<reconstructed body>` and call
   `meta_compiler.wiki_edit_manifest.record_write(paths, page_path, "enrichment")`.

### 5. Emit the Enrichment Report

Write `workspace-artifacts/wiki/reports/enrichment_report.yaml`:

```yaml
enrichment_report:
  timestamp: ISO-8601
  wiki_version: 2
  pages_considered: int
  pages_synthesized: int
  pages_skipped_user_edited: int
  pages_skipped_no_findings: int
  pages_failed_validation: int
  per_page:
    - page_id: concept-foo
      file: concept-foo.md
      status: synthesized | skipped_user_edited | skipped_no_findings | failed
      citations_used: [src-foo, src-bar]
      related_pages_linked: [concept-bar.md]
      reason: optional explanation when skipped/failed
```

### 6. Hand Off

Print a one-line summary:

```
Enrichment complete — N pages synthesized, M skipped (edited), K skipped (no findings), J failed. Ready for wiki-link.
```

Recommend the next step: `meta-compiler wiki-link --version 2` (Phase C1b
deterministic linker).

## Synthesis Schema

The `wiki-synthesizer` subagent returns one JSON object per page:

```json
{
  "definition": "string (non-empty, with inline [src-id, locator] cites)",
  "formalism": "string (non-empty, with inline cites)",
  "key_claims": "string (non-empty, 3–6 cross-source claims, each cited)",
  "open_questions": "string (non-empty, 2–6 questions)",
  "citations_used": ["src-foo", "src-bar"],
  "related_pages_linked": ["concept-x.md"]
}
```

Every cited `src-*` MUST appear in the page's `sources:` frontmatter. Every
`related_pages_linked` entry MUST appear in the work plan's `related_pages`.
