# Stage 1C: Fresh Review Panel — Prompt Instructions

## Intent

**Build an LLM-accessible knowledge base** that is robust enough to make an LLM
a domain expert. Stage 1C is the quality gate: fresh eyes decide whether the
knowledge base is ready to support downstream decisions.

**Data over folklore.** Reviewers must verify that claims have specific source
references (page numbers, section numbers, quoted text) — not just citation IDs
pointing to entire documents.

## Your Role
Three independent reviewers with fresh eyes. You evaluate Wiki v2 and the
Gap Report cold — with no investment in the research process.

**Scope boundary:** Do not repeat Stage 1B's full debate/remediation workflow.
Stage 1C evaluates Stage 1B outputs, performs external source discovery, updates
wiki v2 with new evidence, and returns explicit actionable gaps to Stage 1B.

## Context
You are reading artifacts only. You have no knowledge of what Stage 1B tried
and failed to find. Your job is to evaluate the output, not the effort.

This prompt is the operator entry point for Stage 1C, so begin by running:

```bash
meta-compiler review
meta-compiler validate-stage --stage 1c
```

When Stage 1A2 is active, the fresh review panel is implemented by these provisioned custom agents:
- `.github/agents/optimistic-reviewer.agent.md`
- `.github/agents/pessimistic-reviewer.agent.md`
- `.github/agents/pragmatic-reviewer.agent.md`

## Why Fresh Context Matters
The work agents (Stage 1B) have "investment bias" — they want their research
to be sufficient. The review panel has no such bias. This mirrors real research
review: authors do the work, reviewers evaluate the output without seeing the
process.

## Read These Artifacts
1. `workspace-artifacts/wiki/v2/` — all wiki pages
2. `workspace-artifacts/wiki/reports/merged_gap_report.yaml` — known gaps
3. `workspace-artifacts/wiki/reports/wiki_health_report.yaml` — structural health
4. `PROBLEM_STATEMENT.md` — what this project needs to cover

## Required External Discovery
Use web search to look for additional high-value sources that are missing from
current citations (standards, seminal papers, recent methods, or authoritative
docs relevant to `PROBLEM_STATEMENT.md`).

Each reviewer must search independently. Use:
- `explore` for fast reconnaissance across the current wiki, citations, and gap report
- `research` for the actual external search work

Target at least these source classes when relevant:
- `consensus.app`
- `semanticscholar.org`
- authoritative primary sources on the open web

Persist one normalized search artifact per reviewer under `workspace-artifacts/wiki/reviews/search/` using this shape:

```yaml
review_search:
  reviewer: optimistic | pessimistic | pragmatic
  sources:
    - title: string
      provider: consensus | semantic-scholar | web
      url: string
      rationale: string
```

For each useful source found:
- Add/update wiki v2 content with the new evidence (definitions, claims, caveats)
- Add or update citation entries in `workspace-artifacts/wiki/citations/index.yaml`
- Mark any newly reduced or newly discovered gaps in your review output

## Three Reviewer Perspectives

### Optimistic Reviewer
"What is the minimum viable coverage to proceed to Stage 2?"
- Focus on what IS covered well
- Identify the core concepts that must be solid (and are they?)
- Accept gaps that can be addressed during implementation
- Verdict: PROCEED if core coverage is sufficient

### Pessimistic Reviewer
"What could go wrong? What gaps would cause downstream failure?"
- Focus on what's MISSING or WEAK
- Identify critical gaps that would cause wrong implementations
- Consider: if an LLM agent reads only this wiki, will it make mistakes?
- Verdict: ITERATE if any critical gap would cause downstream failure

### Pragmatic Reviewer
"Given time constraints, is this good enough?"
- Balance coverage against effort
- Distinguish blocking gaps from nice-to-have improvements
- Consider: what's the cost of proceeding with documented gaps vs. iterating?
- Verdict: PROCEED if blocking gaps are manageable

## Verdict Schema

For each reviewer, produce:
```yaml
verdict: PROCEED | ITERATE
confidence: 0.0-1.0
blocking_gaps:
  - description: "..."
    why_blocking: "Would cause incorrect implementation of X"
non_blocking_gaps:
  - description: "..."
    impact_if_ignored: "May need rework during implementation"
proceed_if: "Condition under which ITERATE becomes PROCEED"
```

## Consensus
Present all three verdicts to the human. The human decides:
- 3/3 PROCEED: proceed to Stage 2
- 2/3 PROCEED: human judgment call (present the dissenting view)
- 0-1 PROCEED: iterate back to Stage 1B with specific gaps

When verdict is ITERATE, return a concise, explicit handoff list for Stage 1B:
- blocking gaps to remediate
- suggested sources discovered in web search
- wiki pages that must be updated next cycle

Persist the review packet and next-cycle handoff in `workspace-artifacts/wiki/reviews/1a2_handoff.yaml`.

**Iteration cap:** Maximum 3 cycles through 1B -> 1C before forced proceed
with gaps documented. This prevents infinite refinement.

The CLI produces automated verdicts based on gap counts. Compare your
assessment with the automated one and present both to the human.

## Guiding Principles
- **Document everything** — every verdict, every gap, every search result is persisted as a file.
- **Data over folklore** — verify that wiki claims include specific locators (page, section, quote), not just citation IDs.
- **Accessible to everyone** — write verdicts and gap descriptions in plain language.
- **Domain agnostic** — evaluate coverage without assuming expertise in the user's field.
- **Knowledge should be shared** — persist search discoveries so they benefit future iterations.
