# Wiki Update — Prompt Instructions

## Your Role
Wiki Update agent. You incrementally expand the wiki when new seed documents
are added, without re-processing existing content.

## When to Use
After scaffolding is complete and new seed documents arrive. The human adds
new files to `workspace-artifacts/seeds/` and invokes this command.

## Procedure

### 1. Run the CLI
```bash
meta-compiler wiki-update
```
This detects new seeds, creates baseline wiki stubs, and produces an impact report.

### 2. Enrich New Pages
Same as Stage 1A — read the new seed documents and fill in wiki pages with
real extracted content. Documents, not summaries.

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
