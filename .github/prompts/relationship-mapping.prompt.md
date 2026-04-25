---
name: relationship-mapping
description: "Phase C1b — discover cross-document concept relationships and merge accepted proposals into v2 wiki pages."
argument-hint: "(none)"
---

# Relationship Mapping — Prompt Instructions

## Intent

**Make the wiki a connected knowledge graph, not a flat collection of pages.**
Per-source seed-readers can only declare relationships visible inside one
document. They cannot say "concept A in src-1 extends concept B in src-2" —
each only saw its own source. This pass closes that gap.

**Cross-document evidence is the gate.** A relationship is only worth recording
at this layer if it shows up across at least 2 distinct sources. Single-source
relationships were already captured at ingest.

## Your Role

Conductor for the relationship-mapping pass. You run two CLI commands and one
agent invocation, in order.

## Orchestration Protocol

### 1. Prep

```bash
meta-compiler propose-relationships
```

Writes `workspace-artifacts/runtime/wiki_relationships/request.yaml` listing
every v2 concept page and the path to the citation index.

### 2. Invoke the Mapper

Invoke `@relationship-mapper`. The agent reads the request, scans every v2
concept page + every relevant findings JSON, and writes
`workspace-artifacts/wiki/reports/relationship_proposals.yaml`.

### 3. Apply

```bash
meta-compiler apply-relationships --version 2
```

The CLI validates each proposal:

- Subject and target are real v2 page IDs.
- Relationship type is one of `prerequisite_for`, `depends_on`, `contradicts`,
  `extends`.
- Evidence cites at least 2 distinct citation IDs.

Accepted proposals are merged into the corresponding page's `## Relationships`
section and added to its `related:` frontmatter. Every write is registered in
the v2 edit manifest with source `relationship_mapper`. Provenance is recorded
in `wiki/reports/relationship_provenance.yaml`.

### 4. Hand Off

Print: `Relationships applied — N additions across M pages, K rejected. Wiki graph updated.`

## Critical Rules

1. **v2 only.** Never modify v1 pages.
2. **No invented page IDs.** Subject and target must come from the request's
   `concept_pages` list.
3. **Cross-document only.** Single-source proposals are silently rejected (the
   per-source seed-reader already captured them).
4. **Provenance is permanent.** Every applied relationship is logged with its
   evidence in `relationship_provenance.yaml` for later review or rollback.

## Reference

- Mapper agent: `.github/agents/relationship-mapper.agent.md`
- Linker (run before this): `meta-compiler wiki-link --version 2`
