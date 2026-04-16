# Wiki Update — Prompt Instructions

## Intent

**Build an LLM-accessible knowledge base** that grows as new evidence arrives.
When new seed documents are added, the wiki must expand to include their actual
content — not just stubs.

**Data over folklore.** New wiki pages must include direct quotes, page numbers,
section numbers, or equation references from the source material. A stub that
says "auto-ingested source page" is a starting point, not an endpoint.

## Your Role
Wiki Update agent. You incrementally expand the wiki when new seed documents
are added, without re-processing existing content.

## When to Use
After scaffolding is complete and new seed documents arrive. The human adds
new files to `workspace-artifacts/seeds/` and invokes this command.

## Procedure

### 1. Run the Ingest Pass (new seeds only)
```bash
meta-compiler ingest --scope new
```
Then invoke the `ingest-orchestrator` agent
(`.github/agents/ingest-orchestrator.agent.md`). It reads the work plan, fans
out `seed-reader` subagents for the new seeds only, and writes one findings
JSON per new seed under `workspace-artifacts/wiki/findings/`. The findings
index is updated so future runs skip these seeds.

Validate:
```bash
meta-compiler ingest-validate
```

**Critical:** Do NOT use the `explore` subagent for reading seeds. It
hallucinates on long documents. Use `ingest-orchestrator`, which dispatches
`seed-reader` subagents designed for faithful extraction.

### 2. Run the wiki-update CLI
```bash
meta-compiler wiki-update
```
This registers citations, creates baseline wiki stubs for the new seeds, and
produces an impact report. It reuses the citation IDs already minted during
the ingest pass.

### 3. Enrich New Pages from Findings
Read `workspace-artifacts/wiki/findings/<citation_id>.json` for each newly
added seed and populate the matching wiki pages with verbatim quotes,
equations, and locators. Do NOT re-read the original seeds — the seed-reader
subagents already extracted them faithfully during the ingest pass.

### 3. Review Impact Report
Check `workspace-artifacts/wiki/reports/wiki_update_report.yaml`:
- Which existing pages are affected by the new content?
- Do any existing relationships need updating?
- Are there contradictions between new and existing content?

### 4. Update Cross-Links
- Link new pages to existing concepts
- Update existing pages' `related` fields if relevant
- Flag contradictions for human review (do NOT auto-resolve)

### 5. Assess Scope Impact
If new seeds substantially change the problem space:
- Recommend Stage 2 re-entry to the human
- Specify which Decision Log sections need revision

## Constraints
- Do NOT modify existing seed documents (immutable)
- Do NOT re-process seeds that are already in the citation index
- If new content contradicts existing wiki content, flag it — don't auto-resolve
- Always validate after: `meta-compiler validate-stage --stage 1a`
- Non-plaintext seeds (PDF, DOCX, XLSX, PPTX) should be extracted first:
  ```bash
  python scripts/read_document.py workspace-artifacts/seeds/new_paper.pdf --output /tmp/extracted.md
  ```

## Automatic Seed Tracking

New seeds are automatically detected when you run:
```bash
meta-compiler track-seeds
```

This checks for untracked seeds and runs wiki-update automatically. It also
saves a seed inventory snapshot and tracking report.

## Guiding Principles
- **Document everything** — every ingested seed, every created page, every impact analysis is logged.
- **Data over folklore** — new wiki pages must include specific locators (page, section, quote), not just paraphrases.
- **Accessible to everyone** — write wiki content in clear language.
- **Domain agnostic** — the update process works for any domain.
- **Knowledge should be shared** — new evidence benefits the entire knowledge base.
