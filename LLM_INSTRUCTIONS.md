# META-COMPILER: Workspace Compiler for LLM-Driven Projects

This is a research-first project scaffolding system. It compiles raw information
(seed documents + human intent) into structured workspaces that LLMs can execute
against for programming, writing, and technical tasks.

**You (the LLM assistant) are the intelligence layer.** The `meta-compiler` CLI
handles bookkeeping (validation, manifests, file management). You handle reasoning
(research, evaluation, dialog, extraction). The human provides vision and judgment
between stages.

This system is LLM-agnostic. It works with any reasoning model capable of reading
files, following structured instructions, and producing schema-compliant artifacts
inside a VSCode-style environment (Copilot, Claude Code, Cursor, Windsurf, etc.).

## Quick Reference

```bash
# Install
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .

# Stage commands
meta-compiler meta-init --project-name "X" --problem-domain "Y" --project-type hybrid
meta-compiler research-breadth
meta-compiler research-depth
meta-compiler review
meta-compiler elicit-vision --use-case "initial scaffold"
meta-compiler scaffold
meta-compiler wiki-update
meta-compiler wiki-browse
meta-compiler stage2-reentry --reason "scope changed" --sections "architecture,requirements"
meta-compiler finalize-reentry
meta-compiler validate-stage --stage all
```

## Core Principle

Each stage operates in **fresh context**. Artifacts carry knowledge forward, not
conversation history. This forces crystallization and prevents context pollution.

The human kicks off each stage, adding context and guidance. This is not tedious —
it saves hours of downstream iteration by injecting judgment at the right moments.

## Stage Workflow

### Stage 0: Initialize

**Human action:** Create project, add seeds, write problem statement.

```bash
meta-compiler meta-init --project-name "My Project" --problem-domain "domain description" --project-type hybrid
```

Then edit `PROBLEM_STATEMENT.md` with real project context. Add seed documents
(papers, specs, prior work) to `workspace-artifacts/seeds/`.
`meta-init` also provisions stage prompts in `prompts/*.prompt.md` and workspace
customization assets in `.github/`.

The problem statement provides "tension" that scopes all downstream research.
Without it, breadth search is unbounded.

### Stage 1A: Breadth Research

**Your job:** Read every seed document. Extract concepts, relationships, equations,
and claims into wiki pages. Build citations. This is the most critical stage for
information fidelity.

Read `prompts/stage-1a-breadth.prompt.md` for detailed instructions.

```bash
meta-compiler research-breadth        # Creates baseline wiki structure
meta-compiler validate-stage --stage 1a
```

After the CLI creates the baseline structure, **you must enrich the wiki pages**.
The CLI only creates stubs. Read each seed document and fill in:
- Precise definitions (not summaries)
- Mathematical formalisms (LaTeX)
- Key claims with citation IDs
- Relationships between concepts
- Open questions
- Verbatim source notes with page numbers

**Critical:** "Paper X discusses sensor noise" is a summary. "Paper X establishes
that read noise follows Poisson-Gaussian mixture (Eq. 12), parameterized by gain k
and offset sigma_read" is a document. Create documents, not summaries.

### Stage 1A2: 1B ↔ 1C Orchestration Loop

**Your job:** After Stage 1A completes, run the iterative Stage 1B/1C loop from a
single orchestration prompt backed by the provisioned `.github/agents/*.agent.md`
files. The prompt invokes the named worker and review agents; the CLI still owns
artifact generation and validation.

Read `prompts/stage-1a2-orchestration.prompt.md` for detailed instructions.

This phase should:
- verify or repair the provisioned Stage 1A2 custom agents
- launch Stage 1B evaluator, debate, and remediation agents by name
- launch Stage 1C fresh review agents by name
- run `research-depth` and `review` cycles
- route actionable ITERATE gaps back to Stage 1B
- persist a `workspace-artifacts/wiki/reviews/1a2_handoff.yaml` packet
- stop on PROCEED or iteration cap

### Stage 1B: Depth Pass

**Your job:** Evaluate Wiki v1 from three perspectives, conduct a real debate,
fill gaps. This is epistemic lint, not structural lint.

Read `prompts/stage-1b-evaluators.prompt.md` for detailed instructions.

```bash
meta-compiler research-depth          # Creates evaluation framework
meta-compiler validate-stage --stage 1b
```

After the CLI runs structural checks, **you must do the epistemic evaluation**:

1. **Schema Auditor perspective:** Is every concept fully specified? Definitions,
   formalisms, citations, relationships?
2. **Adversarial Questioner perspective:** What assumptions are implicit? What
   would a skeptical reviewer challenge? What alternatives exist?
3. **Domain Ontologist perspective:** Read the problem statement, generate an
   expected topic list, check coverage. What should be here that isn't?

Then conduct a real debate:
- Round 1: Produce three independent gap assessments
- Round 2: Each perspective responds to the other two — agreements, disagreements,
  and NEW gaps surfaced through interaction
- Round 3: Synthesize into a merged gap report

Update wiki pages to fill gaps where possible. Document what cannot be resolved.

### Stage 1C: Review Panel

**Your job:** With fresh eyes, evaluate whether the wiki is sufficient to proceed.

Read `prompts/stage-1c-review.prompt.md` for detailed instructions.

```bash
meta-compiler review
meta-compiler validate-stage --stage 1c
```

The CLI produces verdicts based on gap counts. Present these to the human along
with your own assessment:
- **Optimistic:** What is the minimum viable coverage to proceed?
- **Pessimistic:** What gaps would cause downstream failure?
- **Pragmatic:** Given constraints, is this good enough?

The human decides: PROCEED or ITERATE back to Stage 1B.

### Stage 2: Vision Elicitation

**Your job:** Conduct an asymmetric dialog with the human. YOU ask questions
based on wiki content, the human provides intent and decisions.

Read `prompts/stage-2-dialog.prompt.md` for detailed instructions.

```bash
meta-compiler elicit-vision --use-case "initial scaffold" --non-interactive
meta-compiler validate-stage --stage 2
```

The `--non-interactive` flag creates a baseline Decision Log. Then you refine it
through dialog:

1. Query the wiki for each decision area (conventions, architecture, scope, etc.)
2. Present researched options: "The literature shows approaches A and B. A has
   property X, B has property Y. Which fits your requirements?"
3. Capture each decision with: choice, alternatives rejected, rationale, citations
4. The output is a rigid Decision Log schema — not prose

**Key principle:** Structure the conversation to narrow the solution space. This
is systematic disambiguation using researched options, not open-ended brainstorming.

### Stage 3: Scaffold

**Your job:** The CLI handles this mechanically. Review the output.

Read `prompts/stage-3-scaffold.prompt.md` for detailed instructions.

```bash
meta-compiler scaffold
meta-compiler validate-stage --stage 3
```

Stage 3 consumes the Decision Log ONLY — not the wiki, not raw sources. It produces:
- Agent specifications with embedded decisions
- Real `.github/agents/*.agent.md` files for downstream execution
- Real `.github/skills/<name>/SKILL.md` files for domain-specific tasks
- Real `.github/instructions/*.instructions.md` files
- Human-readable summary docs alongside those customization artifacts
- Code/report stubs with requirement anchors
- Semantic self-tests that verify scaffold integrity

Run the self-tests: `pytest workspace-artifacts/scaffolds/v1/tests/`

### Post-Scaffold: Wiki Update

When new seed documents arrive after scaffolding:

```bash
meta-compiler wiki-update
```

Detects new seeds, ingests them, produces impact report. If new seeds substantially
change the problem space, recommend Stage 2 re-entry.

### Post-Scaffold: Stage 2 Re-entry

When scope, use case, or requirements change:

```bash
meta-compiler stage2-reentry --reason "expanded to include X" --sections "architecture,requirements"
```

This creates a revision template with cascade analysis. Conduct the dialog, then:

```bash
meta-compiler finalize-reentry
meta-compiler scaffold  # Re-scaffold with new decisions
```

## Key Artifacts

| Artifact | Location | Purpose |
|----------|----------|---------|
| Problem Statement | `PROBLEM_STATEMENT.md` | Scopes all research |
| Seeds | `workspace-artifacts/seeds/` | Immutable source documents |
| Wiki v1 | `workspace-artifacts/wiki/v1/` | Stage 1A breadth output |
| Wiki v2 | `workspace-artifacts/wiki/v2/` | Stage 1B depth output |
| Citation Index | `workspace-artifacts/wiki/citations/index.yaml` | Source traceability |
| Gap Report | `workspace-artifacts/wiki/reports/merged_gap_report.yaml` | Knowledge gaps |
| Review Verdicts | `workspace-artifacts/wiki/reviews/review_verdicts.yaml` | Proceed/iterate |
| Decision Log | `workspace-artifacts/decision-logs/decision_log_v*.yaml` | Human decisions |
| Scaffold | `workspace-artifacts/scaffolds/v*/` | Generated project workspace |
| Manifest | `workspace-artifacts/manifests/workspace_manifest.yaml` | Workspace state |

## Citation Format

Every claim must trace to a citation ID. Citations are dual-format:
- **ID:** `src-smith2024-psf` (for LLM tool resolution)
- **Human:** `Smith et al. (2024), section 3.2` (for report rendering)

## Prompt Files

Stage-specific instructions are in `prompts/*.prompt.md`. Read the relevant prompt before
executing each stage. Workspace custom agents, reusable prompts, and skills are
provisioned in `.github/`. Together they provide the epistemic criteria, dialog
patterns, and reusable agent contracts that make each stage effective.

## Wiki Browser

Use `meta-compiler wiki-browse` when you want a quick, resizable browser view of
wiki v1 or v2. It opens automatically, prefers wiki v2 when available, and falls
back to wiki v1 when Stage 1B has not populated v2 yet.

## Validation

Always validate after each stage:
```bash
meta-compiler validate-stage --stage all
```

Fix any issues before proceeding to the next stage. Validation prevents
hallucination from propagating downstream.
