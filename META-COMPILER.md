# META-COMPILER: Research-First Project Scaffolding System

## Intent

**Build an LLM-accessible knowledge base to make an LLM a domain and problem-space
expert before any task is posited.**

## Core Insight

An LLM is faster and smarter than a human at research and synthesis, but is stateless. A human has context, vision, and judgment, but limited bandwidth. This system bridges the gap by front-loading research and crystallizing knowledge into reusable artifacts before a single line of code is written.

The LLM's job is not to be smart at execution time — it's to make itself smart during setup by compiling research into structured knowledge, then generating its own execution framework from that compiled knowledge.

The human provides vision and judgment at the narrowest bottleneck (Stage 2 dialog). Everything else is LLM labor with human checkpoints.

**Key Framing:** This system builds a *workshop*, not a piece of furniture. The output is a reusable workspace for algorithm development or technical report generation — not the algorithm or report itself.

## Guiding Principles

1. **Document everything such that it's auditable by humans and LLMs alike.**
   Every decision, every claim, every gap has a file and a trail.
2. **Data over folklore.** A reference citation is not enough — there must be
   quoted text, page numbers, section numbers, or line numbers.
3. **Accessible to everyone.** The user may be an artist, an accountant, a
   secretary, or an engineer. This tool should be useful for anyone.
4. **Domain agnostic and project agnostic.** This system works for any field.
5. **Knowledge should be shared and democratized.** Technology should be
   accessible to enable good ideas.

---

## Architecture Overview

```
Stage 0: Prompt-Led Initialization (human + agent)
     ↓ seed docs + problem statement
Stage 1A: Breadth Research (agent)
     ↓ Wiki v1
Stage 1A2: Orchestration Loop Controller (agent)
  ↓ 1B ↔ 1C Loop Managed From One Prompt + Reviewer Search Artifacts
Stage 1B: Depth Pass (3 agents → debate → synthesis)
     ↓ Wiki v2 + Gap Report + Debate Transcript
Stage 1C: Fresh Review Panel (3 independent agents, fresh context)
     ↓ Verdict: PROCEED or ITERATE
     ↓ [human decides]
Stage 2: Vision Elicitation (agent + human dialog, fresh context)
     ↓ Decision Log (rigid schema)
Stage 3: Project Scaffolding (agent, fresh context)
  ↓ Folder structure, execution contract, What I Built, .github custom agents/skills/instructions
Stage 4: Execute + Pitch (agent, fresh context)
  ↓ Final deliverables + refreshed What I Built + PPTX pitch deck

─────────────────────────────────────────────────────────────
Post-Scaffold Commands (human-triggered, fresh context each):
─────────────────────────────────────────────────────────────
     wiki-update: Incremental wiki expansion from new seeds
     stage2-reentry: Revise Decision Log for changed scope
```

**Critical constraint:** Each stage operates in fresh context. Artifacts pass between stages, not conversation history. This forces crystallization and prevents context pollution.

**Human-in-the-loop:** A human kicks off each stage, adding context, knowledge, scope, and guidance. This is not tedious — it saves hours or days of downstream iteration by injecting judgment at the right moments.

---

## Workspace Manifest

Every META-COMPILER workspace has a top-level manifest that makes it self-describing for future re-entry:

```yaml
workspace_manifest:
  name: string
  created: ISO-8601
  last_modified: ISO-8601
  problem_domain: string  # e.g., "Orbital rendezvous imaging simulation"
  project_type: algorithm | report | hybrid
  
  seeds:
    version: sha256
    last_updated: ISO-8601
    document_count: int
    
  wiki:
    name: string
    version: sha256
    last_updated: ISO-8601
    page_count: int
    
  decision_logs:
    - version: 1
      created: ISO-8601
      use_case: string
      scaffold_path: /scaffolds/v1/
    - version: 2
      created: ISO-8601
      parent_version: 1
      reason_for_revision: string
      scaffold_path: /scaffolds/v2/

  executions:
    - version: 1
      created: ISO-8601
      output_dir: /executions/v1/

  pitches:
    - version: 1
      created: ISO-8601
      pptx_path: /pitches/pitch_v1.pptx
      
  status: initialized | researched | scaffolded | active
```

---

## Citation System

Citations must be both human-readable and LLM-resolvable. Every source ingested in Stage 1A receives a canonical citation record.

### Citation Schema

```yaml
citation:
  id: string           # Unique key: src-{author}{year}-{topic}
  human: string        # Human-readable: "Smith et al. (2024), §3.2"
  
  source:
    type: seed | web | derived
    path: string       # /seeds/smith2024_psf_modeling.pdf
    page: int          # Optional: specific page
    section: string    # Optional: section identifier
    url: string        # For web sources
    accessed: ISO-8601 # For web sources
    
  metadata:
    authors: [strings]
    title: string
    year: int
    venue: string      # Journal, conference, technical report series
    doi: string        # Optional
    
  status: raw | verified | disputed
  notes: string        # Optional extraction notes
```

### Citation Index

Stage 1A maintains `/wiki/citations/index.yaml`:

```yaml
citations:
  src-smith2024-psf:
    human: "Smith et al. (2024), §3.2"
    path: /seeds/smith2024_psf_modeling.pdf#page=7
    type: seed
    
  src-jones2023-orbital:
    human: "Jones & Chen (2023), Eq. 14"
    path: /seeds/jones2023_orbital_mechanics.pdf#page=22
    type: seed
    
  src-nasa-rp1121:
    human: "NASA RP-1121, Table 3"
    path: /seeds/nasa_rp1121_thermal_properties.pdf#page=45
    type: seed
```

### Citation Rules

1. **Deduplication:** Same source, different pages → single citation `id`, multiple page references in wiki pages
2. **Seed documents are immutable:** Once ingested, citation paths never change
3. **Web sources require archive:** If citing web content, capture snapshot in `/wiki/web-archive/`
4. **Wiki pages reference by `id`:** Rendering layer swaps in `human` form for output

---

## Stage Specifications

### Stage 0: Project Initialization

**Actor:** Human

**Actions:**
- Start from the Stage 0 prompt so the agent collects the metadata and calls `meta-init`
- Create project directory with seed documents (papers, specs, prior work)
- Write or normalize a problem statement with these required sections:
  - Domain and problem space
  - Goals and success criteria
  - Constraints (technical, timeline, resources)
  - Project type (algorithm development / technical report / hybrid)

**CLI contract:**

```bash
meta-compiler meta-init --project-name "My Project" --problem-domain "domain description" --project-type hybrid --problem-statement-file ./problem_statement.md
meta-compiler validate-stage --stage 0
```

**Output:** `/project/seeds/` directory + `PROBLEM_STATEMENT.md` + `workspace_manifest.yaml`

**Purpose:** Provides "tension" that scopes downstream research. Without this, breadth search is unbounded.

**Seed Document Assumption:** Seed documents are curated by a subject matter expert and are the source of truth. The system trusts their accuracy and completeness for the stated problem domain.

---

### Stage 1A: Breadth Research

**Actor:** Research Crawler agent

**Input:**
- Seed documents
- Problem statement

**Behavior:**
- Ingest each seed document → extract to wiki pages
- Build citation index as sources are processed
- Web search scoped by problem statement keywords
- For each relevant source found:
  - Create wiki page following schema
  - Register citation in index
  - Cross-link to existing concepts
  - Flag open questions
- Build `index.md` and `log.md` per Karpathy pattern

**Output:** Wiki v1 — broad coverage, may be shallow in places

#### Wiki Page Schema

```yaml
---
id: unique-slug
type: concept | relationship | equation | source | open-question
created: ISO-8601
sources: [list of citation IDs]  # References citation index
related: [list of concept IDs]
status: raw | reviewed | validated
---
```

**Page body structure:**

```markdown
# Title

## Definition
[Precise definition, 2-3 sentences]

## Formalism
[Mathematical formulation if applicable, LaTeX]

## Key Claims
- Claim 1 [citation-id]
- Claim 2 [citation-id]

## Relationships
- prerequisite_for: [concepts]
- depends_on: [concepts]
- contradicts: [concepts, if any]
- extends: [concepts]

## Open Questions
- [Anything unresolved about this concept]

## Source Notes
[Verbatim extractions with page numbers — these are NOT summaries]
```

---

### Stage 1A2: 1B ↔ 1C Orchestration

**Actor:** Loop Orchestrator agent

**Input:** Stage 1B and Stage 1C prompt contracts + wiki/review artifacts

**Behavior:**
- Use the provisioned `.github/agents/stage-1a2-orchestrator.agent.md` as the control agent
- Spawn and coordinate the provisioned Stage 1B evaluator, debate, and remediation agents
- Spawn and coordinate the provisioned Stage 1C fresh-review agents
- Ensure all delegating agents expose the shared `explore` and `research` subagent palette
- Run the 1B→1C loop from one control prompt
- Launch three reviewer-scoped search passes and persist normalized artifacts under `workspace-artifacts/wiki/reviews/search/`
- Route actionable ITERATE findings from 1C back to 1B
- Persist a handoff packet at `workspace-artifacts/wiki/reviews/1a2_handoff.yaml`
- Stop on PROCEED or iteration cap

**Output:** Final proceed/iterate packet with blocking-gap status and Stage 2 readiness

---

### Stage 1B: Depth Pass

**Actors:** Three parallel agents → Debate Synthesizer

**Input:** Wiki v1

#### Agent Roles

**1. Schema Auditor**
- Evaluates structural completeness
- "Does every concept have: definition, formalism (if applicable), at least one citation, relationships to other concepts?"
- Produces gap report: missing fields, orphan pages, dead links

**2. Adversarial Questioner**
- Evaluates epistemic soundness
- "What assumptions are implicit? What would a skeptical reviewer challenge? What alternative approaches exist that we haven't covered?"
- Produces gap report: unstated assumptions, missing alternatives, weak evidence

**3. Domain Ontologist**
- Evaluates coverage against auto-generated skeleton
- Reads seed docs → extracts expected topic list → checks wiki coverage
- "For a [domain] project, we should cover X, Y, Z. Are they present? At what depth?"
- Produces gap report: missing topics, shallow coverage areas

#### Debate Protocol

1. **Round 1:** Each agent produces independent gap report
2. **Round 2:** Each agent sees other two reports, responds with agreements, disagreements, and new gaps surfaced
3. **Round 3:** Debate Synthesizer produces merged gap report with attribution

#### Behavior After Debate

- For each gap: attempt targeted research to fill
- Update wiki pages
- Register new citations as discovered
- Document what couldn't be resolved

**Output:**
- Wiki v2 (gaps filled where possible)
- Gap Report (structured, attributed)
- Debate Transcript (preserved for Stage 2 context)

---

### Stage 1C: Fresh Review Panel

**Actors:** Three independent reviewers (fresh context — no access to Stage 1B work history)

**Input:** Wiki v2 + Gap Report (artifacts only)

**Search protocol:** Each reviewer searches independently. Use `explore` for fast
workspace reconnaissance and `research` for external discovery. Reviewer search
artifacts should be normalized into `workspace-artifacts/wiki/reviews/search/`
and should target `consensus.app`, `semanticscholar.org`, and other authoritative
sources when relevant.

#### Reviewer Roles

1. **Optimistic:** "What's the minimum viable coverage to proceed?"
2. **Pessimistic:** "What could go wrong? What gaps would cause downstream failure?"
3. **Pragmatic:** "Given time constraints, is this good enough? What's blocking vs. nice-to-have?"

#### Reviewer Output Schema

```yaml
verdict: PROCEED | ITERATE
confidence: 0.0-1.0
blocking_gaps:
  - description: "..."
    why_blocking: "..."
non_blocking_gaps:
  - description: "..."
    impact_if_ignored: "..."
proceed_if: "Condition under which ITERATE becomes PROCEED"
```

#### Consensus Rules

- 3/3 PROCEED → proceed
- 2/3 PROCEED → human judgment call
- Unanimous ITERATE → back to Stage 1B with specific gaps

**Iteration cap:** Maximum 3 cycles through 1B→1C before forced proceed with gaps documented.

---

### Stage 2: Vision Elicitation

**Actor:** Project Definer agent + Human (dialog)

**Input:**
- Wiki v2 available via tool access (NOT loaded into context)
- Gap Report
- User-provided project goals and constraints (entered at stage kickoff)
- Stable wiki name from the manifest, generated at Stage 2 start

#### Wiki Tool Interface

```python
get_concept(name) -> full page content + metadata
search_wiki(query) -> ranked list of relevant pages
get_relationships(concept) -> inbound/outbound links with types
get_equations(concept) -> extracted formalisms
get_citations(concept) -> source references with full citation records
get_open_questions() -> all flagged gaps across wiki
get_debate_transcript(topic) -> why reviewers flagged/approved
```

#### Behavior

- Agent queries wiki on-demand as dialog requires
- Asks targeted questions: "The literature shows approaches A and B. A has property X, B has property Y. Which fits your requirements?"
- Captures each decision with:
  - The choice made
  - Alternatives rejected
  - Rationale
  - Citations back to wiki (using citation IDs)

**Output:** Decision Log (rigid schema — see below)

**Key principle:** The agent structures the conversation to narrow the solution space. It's not open-ended brainstorming — it's systematic disambiguation using researched options.

---

### Stage 3: Project Scaffolding

**Actor:** Scaffold Generator agent

**Input:** Decision Log only (NOT wiki, NOT raw sources)

**Output:**
- Folder structure appropriate to project type
- Agent specifications (for multi-agent execution)
- Skill files / instruction documents
- Requirements document with traced citations
- Conventions document (math notation, code style, etc.)
- `ARCHITECTURE.md` describing the generated structure
- `EXECUTION_MANIFEST.yaml` and `orchestrator/run_stage4.py`
- Initial `workspace-artifacts/wiki/provenance/what_i_built.md`
- Generated custom agents whose delegation policy defaults to `explore` and `research`

#### Behavior

- Parse Decision Log
- Generate agents whose prompts embed the decisions
- Example: If Decision Log says "PSF modeling: separable kernel, spatial domain" → the PSF agent's prompt says "Implement PSF convolution using separable kernels in spatial domain per [citation-id]. Do NOT use FFT-based approaches."

#### Project Type Variations

| Type | Scaffold Contents |
|------|-------------------|
| Algorithm | Code scaffold, test stubs, math conventions agent, scope reduction agent |
| Technical Report | Document outline, citation manager, style conventions, narrative structure agent |
| Hybrid | Both |

---

### Stage 4: Execute + Pitch

**Actor:** Execution and packaging agent

**Input:** Latest scaffold execution contract + Decision Log version

**Behavior:**
- Execute the scaffold-generated `orchestrator/run_stage4.py`
- Persist final deliverables under `workspace-artifacts/executions/v{N}/`
- Refresh `workspace-artifacts/wiki/provenance/what_i_built.md` with actual outputs
- Generate both a markdown pitch and a real `.pptx` pitch deck under `workspace-artifacts/pitches/`

**CLI contract:**

```bash
meta-compiler phase4-finalize
meta-compiler validate-stage --stage 4
```

**Output:** Final execution outputs + refreshed product summary + PPTX sales deck

---

## Post-Scaffold Commands

These commands allow workspace evolution after initial scaffolding. Each operates in fresh context with explicit artifact inputs.

### wiki-update Command

**Purpose:** Incrementally expand the wiki when new seed documents are added, without full re-processing.

**Actor:** Wiki Update agent

**Trigger:** Human adds new documents to `/seeds/` and invokes command

**Input:**
- Existing Wiki v2
- Existing citation index
- New seed documents (delta)
- Problem statement (unchanged)

#### Behavior

1. **Diff Detection:**
   - Identify new files in `/seeds/` not present in citation index
   - Log: "Found N new seed documents for integration"

2. **Incremental Ingestion:**
   - For each new document:
     - Extract content → create/update wiki pages
     - Register citations in index
     - Cross-link to existing concepts
     - Flag new open questions
   - Do NOT re-process existing seeds

3. **Impact Analysis:**
   - Identify existing wiki pages that reference concepts now expanded
   - Flag pages that may need relationship updates
   - Produce impact report

4. **Light Validation:**
   - Schema Auditor runs on new/modified pages only
   - Produces targeted gap report

**Output:**
- Wiki v2.1 (incremented version)
- Updated citation index
- Impact report: pages affected, new cross-links, new gaps
- Updated `workspace_manifest.yaml`

#### Wiki Update Output Schema

```yaml
wiki_update_report:
  timestamp: ISO-8601
  previous_version: sha256
  new_version: sha256
  
  documents_added:
    - path: /seeds/new_document.pdf
      citation_id: src-newauthor2024-topic
      pages_created: [concept-ids]
      pages_modified: [concept-ids]
      
  impact_analysis:
    cross_links_added: int
    relationships_updated: [concept-ids]
    gaps_surfaced:
      - description: string
        affected_concepts: [concept-ids]
        
  validation:
    schema_errors: [...]
    recommendations: [...]
    
  status: complete | needs_review
```

#### Constraints

- Never modifies original Wiki v2 in place — creates versioned copy
- If new seeds contradict existing wiki content → flag for human review, do not auto-resolve
- If new seeds substantially change problem scope → recommend Stage 2 re-entry

---

### stage2-reentry Command

**Purpose:** Revise Decision Log when project scope, use case, or requirements change.

**Actor:** Project Definer agent + Human (dialog)

**Trigger:** Human determines current scaffold no longer fits needs

**Input:**
- Current Decision Log (with version history)
- Wiki (current version)
- Human-provided revision context:
  - What changed?
  - Which decisions need revisiting?
  - New constraints or goals

#### Behavior

1. **Decision Review:**
   - Load prior Decision Log
   - Human specifies which sections to revisit
   - Agent queries wiki for alternatives not previously considered

2. **Scoped Dialog:**
   - Only discuss decisions marked for revision
   - Preserve unchanged decisions with note: "Retained from v{N}"
   - Capture new decisions with full rationale

3. **Cascade Analysis:**
   - Identify downstream decisions affected by changes
   - Flag: "Changing X may invalidate decisions Y, Z"
   - Human confirms cascade scope

4. **Version Increment:**
   - Produce Decision Log v{N+1}
   - Link to parent version
   - Document revision rationale

**Output:**
- Decision Log v{N+1} with versioning metadata
- Cascade report (what else changed)
- Updated `workspace_manifest.yaml`

#### Decision Log Versioning

```yaml
decision_log:
  meta:
    project_name: string
    project_type: algorithm | report | hybrid
    created: ISO-8601
    version: 2
    parent_version: 1
    reason_for_revision: "Scope expanded to include polarimetric modeling"
    wiki_version: sha256
    
  # ... rest of Decision Log schema
```

#### Re-scaffolding

After Stage 2 re-entry, human may invoke Stage 3 again. Scaffold Generator:
- Diffs Decision Log v{N+1} against v{N}
- Updates only affected agents/files where possible
- Flags breaking changes that require full regeneration
- Preserves scaffold version history in `/scaffolds/v{N}/`

---

## Decision Log Schema

This is the critical handoff artifact. Stage 2 produces it, Stage 3 consumes it. Rigid structure prevents hallucination downstream.

```yaml
decision_log:
  meta:
    project_name: string
    project_type: algorithm | report | hybrid
    created: ISO-8601
    version: int              # Starts at 1
    parent_version: int|null  # null if first version
    reason_for_revision: string|null
    problem_statement_hash: sha256  # Links back to Stage 0 input
    wiki_version: sha256            # Links back to wiki used
    
  conventions:
    - name: string
      domain: math | code | citation | terminology
      choice: string
      rationale: string
      citations: [citation IDs]
      
  architecture:
    - component: string
      approach: string
      alternatives_rejected:
        - name: string
          reason: string
      constraints_applied: [strings]
      citations: [citation IDs]
      
  scope:
    in_scope:
      - item: string
        rationale: string
    out_of_scope:
      - item: string
        rationale: string
        revisit_if: string  # Optional condition for reconsideration
        
  requirements:
    - id: REQ-NNN
      description: string
      source: user | derived
      citations: [citation IDs]
      verification: string  # How to test this is met
      
  open_items:
    - description: string
      deferred_to: implementation | future_work
      owner: string  # Which agent/human handles this
      
  agents_needed:
    - role: string
      responsibility: string
      reads: [artifact types]
      writes: [artifact types]
      key_constraints: [from decisions above]
```

---

## Agent Roster

| Agent | Stage | Role |
|-------|-------|------|
| Research Crawler | 1A | Breadth search, wiki page creation, citation indexing |
| Schema Auditor | 1B | Structural completeness evaluation |
| Adversarial Questioner | 1B | Assumption and alternative analysis |
| Domain Ontologist | 1B | Coverage evaluation against skeleton |
| Debate Synthesizer | 1B | Merge gap reports, manage debate |
| Reviewer (Optimistic) | 1C | Minimum viable coverage assessment |
| Reviewer (Pessimistic) | 1C | Failure mode identification |
| Reviewer (Pragmatic) | 1C | Time-constrained sufficiency |
| Project Definer | 2 | Human dialog, decision capture |
| Scaffold Generator | 3 | Project structure generation |
| Wiki Update | Command | Incremental wiki expansion |

Each agent gets a dedicated prompt template. Prompts should:
- State role and goal explicitly
- Define tool access (what this agent can read/write)
- Embed relevant constraints from prior stages
- Specify output format precisely

---

## Implementation Priorities

1. **Decision Log schema** — done above, validate with test cases
2. **Citation system** — index format, deduplication logic
3. **Wiki tool interface** — Python functions, storage format secondary
4. **Stage 2 prompt (Project Definer)** — most human-facing, exercises wiki tools
5. **Stage 1A prompt (Research Crawler)** — entry point, establishes wiki format
6. **Stage 1B prompts** — the three evaluators + synthesizer
7. **Stage 1C prompts** — simpler, just evaluation
8. **Stage 3 prompt** — mechanical given good Decision Log
9. **wiki-update command** — incremental processing logic
10. **stage2-reentry command** — versioning and cascade analysis

---

## Design Rationale (Key Decisions)

| Decision | Rationale |
|----------|-----------|
| Fresh context per stage | Forces crystallization into artifacts; prevents context pollution; breaks "investment" bias |
| Human between every stage | Adds context, judgment, and steering; not tedious because it prevents downstream rework |
| Wiki as LLM-optimized IR | Storage format doesn't matter; tool interface matches LLM query patterns |
| Debate + fresh review panel | Single evaluator has systematic blind spots; fresh panel isn't anchored to sunk cost |
| Rigid Decision Log schema | Prevents Stage 3 hallucination; traceable citations; mechanical transformation to agents |
| Domain ontology auto-generated from seeds | Avoids requiring user to specify complete ontology; depth pass checks coverage against it |
| 3-cycle iteration cap | "Is this complete?" is unbounded; forced proceed with documented gaps |
| Dual-format citations | Human readability for reports; LLM resolvability for tool access |
| Seeds as trusted source of truth | SME curation assumed; system doesn't second-guess seed quality |
| Workshop not furniture | Output is reusable workspace; execution is separate concern |
| Explicit re-entry points | Workspaces evolve; scope changes shouldn't require full rebuild |

---

## Foundational References

This architecture builds on:

- **Karpathy's LLM Wiki pattern:** Persistent, compounding wiki maintained by LLM instead of stateless RAG. Raw sources (immutable) → Wiki (LLM-maintained) → Schema (governance). Key insight: the wiki is a "compiled intermediate layer" — knowledge distilled once and kept current, not re-derived per query.

- **Multi-agent debate patterns:** Independent evaluation followed by structured debate surfaces gaps that single-agent evaluation misses. Fresh review panel without work history eliminates anchoring bias.

---

## What This Document Enables

A future conversation can:
1. Pick any stage and implement the prompt
2. Build the wiki tool interface
3. Implement the citation system
4. Build orchestration scripts
5. Implement post-scaffold commands
6. Test end-to-end on a real project

The architecture is complete. Remaining work is implementation.
