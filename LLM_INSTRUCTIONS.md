# META-COMPILER: Workspace Compiler for LLM-Driven Projects

**Intent:** Build an LLM-accessible knowledge base to make an LLM a domain and
problem-space expert before any task is posited.

This is a research-first project scaffolding system. It compiles raw information
(seed documents + human intent) into structured workspaces that LLMs can execute
against for programming, writing, and technical tasks.

**You (the LLM assistant) are the intelligence layer.** The `meta-compiler` CLI
handles bookkeeping (validation, manifests, file management). You handle reasoning
(research, evaluation, dialog, extraction). The human provides vision and judgment
between stages.

This system is designed for GitHub Copilot Chat with ChatGPT as the intelligence
layer. Agents, prompts, and skills are structured for the `.github/agents/`,
`.github/prompts/`, and `.github/skills/` pipeline that Copilot Chat reads.

## Guiding Principles

1. **Document everything such that it's auditable by humans and LLMs alike.**
   Every decision, every claim, every gap has a file and a trail. Nothing hides
   in chat history.

2. **Data over folklore.** A reference citation is not enough — there must be
   quoted text, page numbers, section numbers, or line numbers. "Paper X discusses
   topic Y" is folklore. "Paper X establishes [specific claim] (Eq. 12, p.15)" is
   data.

3. **Accessible to everyone.** The user may be an artist, an accountant, a
   secretary, or an engineer. Do not assume technical expertise. Explain
   trade-offs in plain language. This tool should be useful for anyone.

4. **Domain agnostic and project agnostic.** This system works for any field,
   any problem, any project type. Do not assume the user's domain.

5. **Knowledge should be shared and democratized.** Technology should be
   accessible to enable good ideas. Structure content so it can be reused,
   extended, and challenged.

## Quick Reference

```bash
# Install
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .

# Run through the Stage 2 handoff
meta-compiler run-all --project-name "X" --problem-domain "Y" --project-type hybrid --problem-statement-file ./problem_statement.md

# Individual stage commands
meta-compiler meta-init --project-name "X" --problem-domain "Y" --project-type hybrid --problem-statement-file ./problem_statement.md
meta-compiler ingest --scope all
meta-compiler ingest-validate
meta-compiler research-breadth
meta-compiler research-depth
meta-compiler review
meta-compiler elicit-vision --use-case "initial scaffold"
meta-compiler audit-requirements
meta-compiler scaffold
meta-compiler phase4-finalize
meta-compiler wiki-update
meta-compiler wiki-browse
meta-compiler stage2-reentry --reason "scope changed" --sections "architecture,requirements"
meta-compiler finalize-reentry
meta-compiler validate-stage --stage all

# Utility commands
meta-compiler track-seeds                        # Auto-detect and ingest new seeds
meta-compiler clean-workspace --target-stage 0   # Reset to any stage

# Document processing
python scripts/pdf_to_text.py <file.pdf>       # Extract text from PDFs for ingest
python scripts/read_document.py <file.pdf>       # Extract text from documents
python scripts/write_document.py <output.docx>   # Write text to documents
```

## Core Principle

Each stage operates in **fresh context**. Artifacts carry knowledge forward, not
conversation history. This forces crystallization and prevents context pollution.

The human kicks off each stage, adding context and guidance. This is not tedious —
it saves hours of downstream iteration by injecting judgment at the right moments.

## Stage Workflow

### Stage 0: Initialize

**Human action:** Start from `prompts/stage-0-init.prompt.md`, collect the project metadata and a real problem statement, then call the CLI.

```bash
meta-compiler meta-init --project-name "My Project" --problem-domain "domain description" --project-type hybrid --problem-statement-file ./problem_statement.md
meta-compiler validate-stage --stage 0
```

The problem statement must materially populate these sections:
- `## Domain and Problem Space`
- `## Goals and Success Criteria`
- `## Constraints`
- `## Project Type`
- `## Additional Context`

Add seed documents (papers, specs, prior work) to `workspace-artifacts/seeds/`.
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
meta-compiler ingest --scope all      # Writes work_plan.yaml and pre-extracts binaries
# Then run prompts/ingest-orchestrator.prompt.md to produce findings JSON
meta-compiler ingest-validate         # Validate findings JSON before enrichment
meta-compiler research-breadth        # Creates baseline wiki structure and enriches from findings
meta-compiler validate-stage --stage 1a
```

The CLI now separates Stage 1A into deterministic ingest prep plus enrichment.
`meta-compiler ingest` writes `workspace-artifacts/runtime/ingest/work_plan.yaml`
and pre-extracts binary seeds. The ingest-orchestrator prompt fans out
`seed-reader` subagents that write findings JSON under
`workspace-artifacts/wiki/findings/`. Then `research-breadth` creates the
baseline structure and enriches safe wiki pages from those findings.

After the CLI creates the baseline structure, **you must enrich or review the
wiki pages**. The CLI only creates stubs plus findings-backed upgrades. Fill in:
- Precise definitions (not summaries)
- Mathematical formalisms (LaTeX)
- Key claims with citation IDs
- Relationships between concepts
- Open questions
- Verbatim source notes with page numbers

**Critical:** "Paper X discusses sensor noise" is a summary. "Paper X establishes
that read noise follows Poisson-Gaussian mixture (Eq. 12), parameterized by gain k
and offset sigma_read" is a document. Create documents, not summaries.

**Full paper text enforcement:** Every wiki page must include direct quotes or
specific references (page number, section number, equation number) from source
material. Pages with only paraphrased summaries are insufficient.

**Non-plaintext seeds:** PDFs use the dedicated wrapper and other binaries use
the general document reader. `meta-compiler ingest` performs this pre-extract
step automatically, but you can run the scripts directly when needed:
```bash
python scripts/pdf_to_text.py workspace-artifacts/seeds/paper.pdf --output /tmp/paper_text.md
python scripts/read_document.py workspace-artifacts/seeds/spec.docx --output /tmp/spec_text.md
```

### Stage 1A2: 1B ↔ 1C Orchestration Loop

**Your job:** After Stage 1A completes, run the iterative Stage 1B/1C loop from a
single orchestration prompt backed by the provisioned `.github/agents/*.agent.md`
files. The prompt invokes the named worker and review agents; the CLI still owns
artifact generation and validation.

Read `prompts/stage-1a2-orchestration.prompt.md` for detailed instructions.

This phase should:
- verify or repair the provisioned Stage 1A2 custom agents
- verify delegating agents expose the `agent` tool and include `explore` and `research` in `agents:`
- launch Stage 1B evaluator, debate, and remediation agents by name
- launch Stage 1C fresh review agents by name
- run `research-depth` and `review` cycles
- launch three independent reviewer-scoped `research` passes that persist normalized search artifacts under `workspace-artifacts/wiki/reviews/search/`
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

Use `explore` for fast artifact reconnaissance and `research` when a gap requires
deeper multi-source investigation that the current workspace cannot answer.

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

Each reviewer must search independently. Use `explore` to inspect the current
wiki, citations, and gap report, then use `research` for the external search.
Target `consensus.app`, `semanticscholar.org`, and other authoritative sources
when relevant. Persist one normalized artifact per reviewer in
`workspace-artifacts/wiki/reviews/search/` so the Python review stage can
aggregate `suggested_sources` into the Stage 1A2 handoff.

The human decides: PROCEED or ITERATE back to Stage 1B.

### Stage 2: Vision Elicitation

**Your job:** Conduct an asymmetric dialog with the human. YOU ask questions
based on wiki content, the human provides intent and decisions.

Stage 2 also generates and stores the wiki name. Preserve that name when
referring to the index or page headers, and keep Stage 3/4 execution needs in
view while you narrow the decision space.

Read `prompts/stage-2-dialog.prompt.md` for detailed instructions.

```bash
meta-compiler elicit-vision --use-case "initial scaffold" --non-interactive
meta-compiler validate-stage --stage 2
meta-compiler audit-requirements
```

`run-all` intentionally stops here. The human must review the Decision Log and
`workspace-artifacts/decision-logs/requirements_audit.yaml` before Stage 3.

The `--non-interactive` flag creates a baseline Decision Log. Then you refine it
through dialog:

1. Query the wiki for each decision area (conventions, architecture, scope, etc.)
2. Present researched options: "The literature shows approaches A and B. A has
   property X, B has property Y. Which fits your requirements?"
3. Capture each decision with: choice, alternatives rejected, rationale, citations
4. The output is a rigid Decision Log schema — not prose

When capturing `agents_needed`, record execution-time delegation expectations.
If an agent is expected to delegate, note that it should expose the `agent`
tool and include `explore` and `research` in its allowlist unless a narrower
policy is explicitly justified.

**Key principle:** Structure the conversation to narrow the solution space. This
is systematic disambiguation using researched options, not open-ended brainstorming.

### Stage 3: Scaffold

**Your job:** The CLI handles this mechanically. Review the output.

Read `prompts/stage-3-scaffold.prompt.md` for detailed instructions.

```bash
meta-compiler scaffold
meta-compiler validate-stage --stage 3
```

Stage 3 consumes the Decision Log ONLY — not the wiki, not raw sources, and not
the findings JSON directly. It produces:
- Agent specifications with embedded decisions
- Real `.github/agents/*.agent.md` files for downstream execution
- Real `.github/skills/<name>/SKILL.md` files for domain-specific tasks
- Real `.github/instructions/*.instructions.md` files
- Human-readable summary docs alongside those customization artifacts
- Code/report stubs with requirement anchors
- Semantic self-tests that verify scaffold integrity
- `EXECUTION_MANIFEST.yaml` and `orchestrator/run_stage4.py` for Stage 4 execution
- An initial `workspace-artifacts/wiki/provenance/what_i_built.md`

Generated delegating agents should share the same `explore`/`research` subagent
palette unless the Decision Log explicitly narrows it.

Run the self-tests: `pytest workspace-artifacts/scaffolds/v1/tests/`

### Stage 4: Execute + Pitch

**Your job:** Run the scaffold-generated execution contract, verify the final
deliverables, and ensure the product is packaged into a real PowerPoint deck.

Read `prompts/stage-4-finalize.prompt.md` for detailed instructions.

```bash
meta-compiler phase4-finalize
meta-compiler validate-stage --stage 4
```

Stage 4 should:
- execute the generated `orchestrator/run_stage4.py`
- write final outputs to `workspace-artifacts/executions/v{N}/`
- refresh `workspace-artifacts/wiki/provenance/what_i_built.md`
- emit `workspace-artifacts/pitches/pitch_v{N}.md`
- emit a real `workspace-artifacts/pitches/pitch_v{N}.pptx`

### Post-Scaffold: Wiki Update

When new seed documents arrive after scaffolding:

```bash
meta-compiler wiki-update
```

Detects new seeds, ingests them, produces impact report. If new seeds substantially
change the problem space, recommend Stage 2 re-entry.

### Post-Scaffold: Automatic Seed Tracking

New seeds are automatically detected and ingested:

```bash
meta-compiler track-seeds
```

This checks for seed files not yet in the citation index, runs wiki-update if any
are found, and saves a tracking report and inventory snapshot.

### Post-Scaffold: Reset Workspace

To reset the workspace to a specific stage:

```bash
meta-compiler clean-workspace --target-stage 2   # Reset to after Stage 2
meta-compiler clean-workspace --target-stage 0   # Full reset (keep seeds)
```

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
| Seed Tracking Report | `workspace-artifacts/wiki/reports/seed_tracking_report.yaml` | Tracks new seed detection and ingestion |
| Seed Inventory | `workspace-artifacts/manifests/seed_inventory.yaml` | Snapshot of all seeds and their bindings |
| Wiki v1 | `workspace-artifacts/wiki/v1/` | Stage 1A breadth output |
| Wiki v2 | `workspace-artifacts/wiki/v2/` | Stage 1B depth output |
| Citation Index | `workspace-artifacts/wiki/citations/index.yaml` | Source traceability |
| Gap Report | `workspace-artifacts/wiki/reports/merged_gap_report.yaml` | Knowledge gaps |
| Review Verdicts | `workspace-artifacts/wiki/reviews/review_verdicts.yaml` | Proceed/iterate |
| Review Search Artifacts | `workspace-artifacts/wiki/reviews/search/*.yaml` | Reviewer-scoped external discovery |
| Decision Log | `workspace-artifacts/decision-logs/decision_log_v*.yaml` | Human decisions |
| Scaffold | `workspace-artifacts/scaffolds/v*/` | Generated project workspace |
| Execution Outputs | `workspace-artifacts/executions/v*/` | Stage 4 final deliverables |
| Pitch Decks | `workspace-artifacts/pitches/` | Markdown and PPTX sales artifacts |
| Manifest | `workspace-artifacts/manifests/workspace_manifest.yaml` | Workspace state |

## Citation Format

Every claim must trace to a citation ID. Citations are dual-format:
- **ID:** `src-smith2024-psf` (for LLM tool resolution)
- **Human:** `Smith et al. (2024), section 3.2` (for report rendering)

## Prompt Files

Stage-specific instructions are in `prompts/*.prompt.md`. Read the relevant prompt before
executing each stage. The prompt set includes:
- `run-all.prompt.md` — full pipeline execution with a single prompt
- `stage-0-init.prompt.md` — prompt-led Stage 0 initialization
- `stage-1a-breadth.prompt.md` — breadth research
- `stage-1a2-orchestration.prompt.md` — 1B ↔ 1C orchestration loop
- `stage-1b-evaluators.prompt.md` — depth pass evaluators
- `stage-1c-review.prompt.md` — fresh review panel
- `stage-2-dialog.prompt.md` — vision elicitation dialog
- `stage-3-scaffold.prompt.md` — scaffold review
- `stage-4-finalize.prompt.md` — execute + pitch

Workspace custom agents (including `academic-researcher.agent.md`), reusable
prompts, and skills are provisioned in `.github/`.

## Academic Researcher Agent

The `@academic-researcher` agent retrieves full-text papers from Semantic Scholar,
CORE, arXiv, PubMed Central, and gray literature. Any agent can call it:

```
@academic-researcher Find 5 papers on "topic" published after 2020
```

Papers are deposited in `workspace-artifacts/seeds/` and tracked automatically.

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
