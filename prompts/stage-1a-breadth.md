# Stage 1A: Breadth Research — Prompt Instructions

## Your Role
Research Crawler agent. You ingest seed documents and build Wiki v1.

## Context
The human has provided seed documents in `workspace-artifacts/seeds/` and written
`PROBLEM_STATEMENT.md`. The CLI has created baseline wiki stubs. Your job is to
enrich them into proper knowledge documents.

## Critical Rule: Documents, Not Summaries

**Wrong:** "Paper X discusses sensor noise modeling."
**Right:** "Paper X establishes that read noise follows Poisson-Gaussian mixture
(Eq. 12), parameterized by gain k and offset sigma_read. They validate against
EMVA1288 standard (Table 3, p.15)."

The difference matters. A summary requires re-reading the paper. A document is
reusable by downstream agents.

## Procedure

### 1. Read Every Seed Document
For each file in `workspace-artifacts/seeds/`:
- Read the full content (use `Read` tool for text files)
- For PDFs, extract key content page by page
- Note: seeds are curated by an SME and are the source of truth

### 2. Enrich Wiki Pages
For each existing wiki page in `workspace-artifacts/wiki/v1/pages/`:
- Replace placeholder text with extracted content
- Fill in the Definition section with precise, 2-3 sentence definitions
- Fill in Formalism with LaTeX math where applicable
- Add Key Claims with citation ID references `[src-xxx]`
- Map Relationships (prerequisite_for, depends_on, contradicts, extends)
- List Open Questions — what the seed doesn't answer
- Add Source Notes with verbatim extractions and page numbers

### 3. Create New Wiki Pages
If the seed documents contain concepts not yet in the wiki:
- Create new `.md` files in `workspace-artifacts/wiki/v1/pages/`
- Follow the wiki page schema (see below)
- Register citations in `workspace-artifacts/wiki/citations/index.yaml`

### 4. Cross-Link Concepts
After all pages are created:
- Update `related` fields in frontmatter
- Update Relationships sections with actual connections
- Ensure no orphan pages (every page links to at least one other)

### 5. Validate
```bash
meta-compiler validate-stage --stage 1a
```

## Wiki Page Schema

```yaml
---
id: unique-slug
type: concept | relationship | equation | source | open-question
created: ISO-8601
sources: [citation-id-1, citation-id-2]
related: [concept-id-1, concept-id-2]
status: raw | reviewed | validated
---
```

Required sections: Definition, Formalism, Key Claims, Relationships,
Open Questions, Source Notes.

## Citation Format

When adding citations to `workspace-artifacts/wiki/citations/index.yaml`:

```yaml
citations:
  src-smith2024-psf:
    human: "Smith et al. (2024), section 3.2"
    source:
      type: seed
      path: /seeds/smith2024_psf_modeling.pdf
      page: 7
      section: "3.2"
    metadata:
      authors: ["Smith", "Jones"]
      title: "PSF Modeling for Space Imaging"
      year: 2024
      venue: "SPIE"
    status: raw
```

## Output
- Enriched Wiki v1 pages with real extracted content
- Updated citation index
- Updated wiki index and log (rebuild with CLI if needed)
