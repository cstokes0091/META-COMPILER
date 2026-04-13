# Stage 1B: Depth Pass — Prompt Instructions

## Your Role
Three evaluator perspectives + Debate Synthesizer. You perform epistemic lint
on Wiki v1 — asking "do we know enough to build this?" not just "is the wiki
well-formed?"

## Context
Wiki v1 has been created in Stage 1A. The CLI has run structural checks. Your
job is the deeper epistemic evaluation that requires actual reasoning.

## The Three Evaluators

### 1. Schema Auditor Perspective
Evaluate structural completeness with depth:
- Does every concept have a precise definition (not just "auto-ingested stub")?
- Does every concept with mathematical content have Formalism filled in?
- Does every claim have at least one citation ID?
- Are relationships bidirectional? (If A depends_on B, does B list A?)
- Are there dead links in related fields?
- Are Source Notes verbatim extractions or just paraphrases?

**Output format:**
```yaml
gaps:
  - description: "Concept X has no mathematical formalism despite being quantitative"
    severity: major
    type: structural
    affected_concepts: [concept-x]
```

### 2. Adversarial Questioner Perspective
Evaluate epistemic soundness — this is the critical differentiator:
- **What assumptions are implicit?** What does the wiki take for granted?
- **What would a skeptical reviewer challenge?** Which claims are weakly supported?
- **What alternative approaches exist?** Are there methods/frameworks the seeds
  didn't cover but that a practitioner would expect?
- **What edge cases are missing?** Where does the coverage assume happy paths?
- **What contradictions exist?** Do different sources disagree?

**Output format:**
```yaml
gaps:
  - description: "Wiki assumes Gaussian noise model but seed doc 2 shows
      heavy-tailed behavior in low-light conditions"
    severity: critical
    type: epistemic
    affected_concepts: [noise-model, sensor-characterization]
```

### 3. Domain Ontologist Perspective
Evaluate coverage against what SHOULD exist:
- Read `PROBLEM_STATEMENT.md` — what domain is this?
- Generate an expected topic skeleton: "For a [domain] project addressing
  [problem], we should cover: [list of expected topics]"
- Check each expected topic against wiki coverage
- Rate: fully covered / partially covered / missing / not applicable

**Output format:**
```yaml
expected_topics:
  - topic: "sensor noise characterization"
    coverage: fully_covered
    wiki_pages: [sensor-noise, read-noise-model]
  - topic: "atmospheric turbulence effects"
    coverage: missing
    wiki_pages: []
    gap: "No coverage of atmospheric effects despite being critical for ground-based imaging"
```

## Debate Protocol

### Round 1: Independent Assessment
Produce all three evaluations independently. Write each to the wiki reports
directory if desired, or hold in context.

### Round 2: Cross-Evaluation (This is where the value is)
For each of the other two perspectives:
- "I agree with [specific finding] because [reason]"
- "I disagree with [specific finding] because [reason]"
- "This surfaces a NEW gap I didn't catch: [description]"

**This round must produce new insights.** If Round 2 just says "I agree with
everything" it has failed. The disagreements and newly surfaced gaps are the
whole point.

### Round 3: Synthesis
Merge all gaps (Round 1 + Round 2 discoveries):
- Deduplicate identical gaps
- Raise severity if multiple evaluators flagged the same thing
- Attribute each gap to its source evaluator(s)
- Produce the merged gap report

## After Debate: Fill Gaps
For each gap in the merged report:
- Attempt targeted research to fill it (search wiki, check seeds again)
- Update wiki pages with new content
- If a gap cannot be resolved, document WHY in the gap report
- Create new wiki pages if coverage gaps require them

## Update Wiki to v2
The CLI copies v1 to v2 and adds a gap remediation page. After your enrichment,
update the v2 pages directly with any improvements.

## Validate
```bash
meta-compiler validate-stage --stage 1b
```

## Key Principle
A single evaluator has systematic blind spots. Three perspectives surface
different gaps. The debate forces explicit justification. This is the difference
between code linting and code review.
