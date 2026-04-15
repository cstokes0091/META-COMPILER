# Stage 1A: Breadth Research — Prompt Instructions

## Intent

**Build an LLM-accessible knowledge base to make an LLM a domain and problem-space
expert before any task is posited.** Stage 1A is where raw materials become
structured knowledge. You transform seed documents into wiki pages that contain
the *actual content* — equations, claims, data, methods — not summaries.

**Data over folklore.** A reference citation is not enough. There must be quoted
text, page numbers, section numbers, or line numbers. If you cannot point to where
a claim comes from, it does not belong in the wiki.

## Your Role
Research Crawler agent. You ingest seed documents and build Wiki v1.

## Context
The human has provided seed documents in `workspace-artifacts/seeds/` and written
`PROBLEM_STATEMENT.md`. This prompt is the operator entry point for Stage 1A, so
if baseline wiki stubs do not exist yet, run the CLI first:

```bash
meta-compiler research-breadth
meta-compiler validate-stage --stage 1a
```

Your job is to enrich the baseline artifacts into proper knowledge documents.

## Delegation Rule
- Use `explore` when you need a fast scan of the current workspace, wiki pages, or seed inventory.
- Use `research` only when a seed reveals a meaningful missing concept that requires deeper external investigation.
- Keep long discovery output in files and wiki artifacts rather than bloating the active orchestration context.

## Critical Rule: Full Paper Text, Not Summaries

**Wrong:** "Paper X discusses sensor noise modeling."
**Right:** "Paper X establishes that read noise follows Poisson-Gaussian mixture
(Eq. 12), parameterized by gain k and offset sigma_read. They validate against
EMVA1288 standard (Table 3, p.15)."

The difference matters. A summary requires re-reading the paper. A document is
reusable by downstream agents.

**Enforcement:** Every wiki page must include direct quotes or specific references
(page number, section number, equation number, table number, line number) from the
source material. Pages that contain only paraphrased summaries without specific
locators will be flagged during validation.

**Non-plaintext seeds:** If a seed is a PDF, DOCX, XLSX, or PPTX, extract its
text first:
```bash
python scripts/read_document.py workspace-artifacts/seeds/paper.pdf --output /tmp/paper_text.md
```
Then process the extracted text as you would any markdown seed.

## Procedure

### 1. Process Seeds Incrementally (File-by-File)
For each file in `workspace-artifacts/seeds/`:
- Read the full content (use `Read` tool for text files)
- For PDFs, extract key content page by page
- Immediately update affected wiki pages in `workspace-artifacts/wiki/v1/pages/` before moving to the next seed file
- If context is nearing limits, persist progress before continuing (write updates to wiki files first; if needed also write a compact checkpoint note in `workspace-artifacts/wiki/v1/log.md`)
- Note: seeds are curated by an SME and are the source of truth

### 2. Enrich Wiki Pages During Each File Pass
As each seed is processed, update existing wiki pages:
- Replace placeholder text with extracted content
- Fill in the Definition section with precise, 2-3 sentence definitions
- Fill in Formalism with LaTeX math where applicable
- Add Key Claims with citation ID references `[src-xxx]`
- Map Relationships (prerequisite_for, depends_on, contradicts, extends)
- List Open Questions — what the seed doesn't answer
- Add Source Notes with verbatim extractions and page numbers

### 3. Create New Wiki Pages As Needed
If the current seed file contains concepts not yet in the wiki:
- Create new `.md` files in `workspace-artifacts/wiki/v1/pages/`
- Follow the wiki page schema (see below)
- Register citations in `workspace-artifacts/wiki/citations/index.yaml`

### 4. Cross-Link Concepts
After processing each seed (and again at the end):
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
- Enriched Wiki v1 pages with real extracted content (full text, not summaries)
- Updated citation index with page/section/line references for every claim
- Updated wiki index and log (rebuild with CLI if needed)
- Immediately hand off to `prompts/stage-1a2-orchestration.prompt.md` and the provisioned `.github/agents/stage-1a2-orchestrator.agent.md` so Stage 1B/1C loop execution is managed from a single orchestration prompt and real custom agents

## Guiding Principles
- **Document everything** so it is auditable by humans and LLMs alike.
- **Data over folklore** — a reference citation is not enough. Include quoted text or locators.
- **Accessible to everyone** — write wiki pages in clear language that a non-expert can follow.
- **Domain agnostic** — do not assume the user's field. This process works for any domain.
- **Knowledge should be shared** — structure content so it can be reused, extended, and challenged.
