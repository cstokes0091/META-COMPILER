# Stage 2 Hardening Spec

**Status:** Draft for review
**Last updated:** 2026-04-17
**Scope:** Replace the current Stage 2 elicitation with a prompt-as-conductor design. First expression of the broader `prompts as code/prompt hybrids` pattern that will later apply to ingest-orchestrator, Stage 1A2, and the ralph-loop implementers.

---

## 1. Context and Motivation

The current Stage 2 has two failure modes that this spec eliminates:

- **The interactive path is a form, not a dialog.** `meta_compiler/stages/elicit_stage.py:_add_conventions` through `:_add_agents` is a fixed ladder of `input()` prompts. No wiki context is injected. No question narrows the space. It collects fields; it does not elicit.
- **The non-interactive path is templated padding.** `_auto_fill` fabricates 5 lenses × N in-scope items of EARS-shaped placeholder requirements from keyword searches of the problem statement. The `stage2-orchestrator` agent is documented as the thing that refines the template via `requirement-deriver` + `requirements-auditor`, but that orchestrator is invoked manually in chat; nothing in the Python CLI actually drives a real LLM dialog.

The intended contract — *LLM asks, human answers, decisions land with citations, Decision Log emerges* — is currently split awkwardly across prompt prose, templated YAML, and an agent ralph loop that has to be kicked off by hand.

This spec replaces all of it with a single **prompt-as-conductor** flow.

---

## 2. The Prompt-as-Conductor Pattern

This section introduces the pattern that Stage 2 is the first expression of. It will be extracted into its own doc (`.github/docs/prompt-as-conductor.md`) once validated against the other roadmap items.

**Principle.** A stage prompt (`.github/prompts/*.prompt.md`) is an executable script that sequences three kinds of operation:

1. **Mechanical CLI calls** that check preconditions, render artifacts, and compile structured output deterministically. These are the integrity layer.
2. **Semantic agent invocations** (custom agents under `.github/agents/`) that do bounded LLM judgment at stage boundaries — context-readiness checks upstream, fidelity audits downstream.
3. **Asymmetric dialog** with the human, driven by the LLM reading the prompt, grounded in artifacts written by the CLI.

**Why hybridize.** Pure code can't converse. Pure prompts can't guarantee determinism. The conductor prompt separates what each tool does best:

| Tool | What it owns |
|------|--------------|
| CLI | Filesystem, manifest updates, schema compilation, mechanical validation, preflight prerequisite checks |
| Agents | Semantic judgment at bounded tasks (is the context rich enough? is the ingest faithful?) |
| Prompt | Sequencing the above, conducting the dialog, writing the transcript |

**Data flow is one-directional within a stage.** CLI writes artifacts → LLM reads artifacts → LLM converses with human → LLM writes transcript + decision blocks → CLI compiles transcript → agent audits compile fidelity → next stage. Nothing flows back up. No state hides in chat history.

**Fresh context is preserved by the artifact handoff, not by process isolation.** The prompt does not have to live inside a single LLM session; the artifacts are the state. A user can pause after Step 2 and resume a week later; the prompt picks up from whatever the runtime directory says.

---

## 3. The New Stage 2 Flow

```
.github/prompts/stage-2-dialog.prompt.md (conductor)
│
├─ Step 1: run `meta-compiler elicit-vision --start`
│    CLI: mechanical prereq check → writes brief.md,
│         transcript.md skeleton, precheck_request.yaml
│    Abort on nonzero exit.
│
├─ Step 2: invoke @stage2-orchestrator mode=preflight
│    Agent: semantic readiness audit → writes precheck_verdict.yaml
│    Abort on BLOCK.
│
├─ Step 3: Converse
│    LLM: read problem statement, wiki, brief, skeleton;
│         open with "what are you building?";
│         surface gaps between stated intent and research coverage;
│         append prose + decision blocks to transcript.md.
│
├─ Step 4: run `meta-compiler elicit-vision --finalize`
│    CLI: parse decision blocks → compile decision_log_v{N}.yaml
│         → mechanical fidelity check → write postcheck_request.yaml
│    Abort on nonzero exit.
│
└─ Step 5: invoke @stage2-orchestrator mode=postflight
     Agent: semantic fidelity audit → writes postcheck_verdict.yaml
     On REVISE: return to Step 3 with discrepancies.
     On PROCEED: run `meta-compiler audit-requirements`.
```

All artifacts live under `workspace-artifacts/runtime/stage2/` during the dialog. The compiled Decision Log lands in its canonical location (`workspace-artifacts/decision-logs/decision_log_v{N}.yaml`) at Step 4.

---

## 4. The Prompt — `.github/prompts/stage-2-dialog.prompt.md`

The current prompt is replaced wholesale. The lens-matrix and EARS guidance survive inside Step 3 because they are good; the surrounding ladder disappears.

Full replacement content:

````markdown
---
description: Vision elicitation via prompt-as-conductor. Walk the five steps exactly. The CLI is the integrity layer; the stage2-orchestrator agent audits both boundaries; you conduct the dialog and write the transcript. You never edit Decision Log YAML directly.
---

# Stage 2: Vision Elicitation

You are the Stage 2 conductor. Stage 2 turns the research compiled in Stages 1A/1B/1C into a Decision Log — a rigid, traceable capture of the human's vision, informed by the wiki.

Your job is to walk this prompt top to bottom. Do not skip steps. Do not improvise sequencing.

## Prompt-as-Conductor Contract

This prompt is executable. It sequences:

1. **Mechanical CLI calls** that check preconditions, render artifacts, and compile the final Decision Log deterministically. Never skip these — they are the integrity layer.
2. **Semantic agent invocations** (`stage2-orchestrator`) at both boundaries to validate context readiness and ingest fidelity.
3. **Asymmetric dialog** with the human, driven by you, grounded in the wiki and problem statement.

Artifacts flow one direction: CLI writes → you read → you converse → you write decision blocks → CLI compiles → agent audits. You never edit Decision Log YAML directly.

---

## Step 1 — Preflight (CLI)

Run:

```bash
meta-compiler elicit-vision --start
```

This writes, under `workspace-artifacts/runtime/stage2/`:

- `brief.md` — pointers to wiki, citations, gap report, problem statement, plus the decision-block format spec
- `transcript.md` — skeleton with one `## Decision Area:` heading per Decision Log section, each annotated with relevant gaps and citation suggestions
- `precheck_request.yaml` — the artifact the orchestrator preflight reads

The CLI exits nonzero if mechanical prerequisites fail (problem statement missing or templated, wiki v2 empty, gap report missing, citation index empty, Stage 1C handoff not PROCEED). On nonzero exit: **STOP**. Surface the failing checks to the human and ask how to remediate (e.g., iterate Stage 1B).

## Step 2 — Orchestrator Preflight (Semantic readiness)

Invoke:

```
@stage2-orchestrator mode=preflight
```

Input: `workspace-artifacts/runtime/stage2/precheck_request.yaml`

Output: `workspace-artifacts/runtime/stage2/precheck_verdict.yaml`, a `stage2_orchestrator_verdict` object with `verdict: PROCEED | BLOCK` and per-check results.

On `BLOCK`: present the blocking check reasons to the human with remediation guidance. Offer two paths: iterate Stage 1B to close the gaps, or override and proceed (which records an explicit `open_item` in the Decision Log). Do not enter Step 3 without a `PROCEED` verdict or a human override.

## Step 3 — Converse

Read, in order:

- `PROBLEM_STATEMENT.md` (intent)
- `workspace-artifacts/runtime/stage2/brief.md` (pointers + schema + decision-block format)
- `workspace-artifacts/runtime/stage2/transcript.md` (skeleton with annotated decision areas)
- Wiki pages under `workspace-artifacts/wiki/v2/pages/` on demand, using the `explore` subagent for fast reconnaissance and `research` when a question demands external context the wiki cannot answer

Open the conversation with: **"What are you building?"**

Your dialog is asymmetric:

- Cross-reference the human's stated intent against the wiki. If they want to build X and the wiki has nothing on X, that is an explicit gap — do not invent coverage.
- Ask one focused, narrowing question at a time. Present researched options:
  > "The wiki shows approaches A (citing `src-smith2024-psf §3.2`) and B (citing `src-jones2023-orbital eq.14`). A optimizes for X; B optimizes for Y. Which fits your constraint that Z?"
- Avoid yes/no ladders. Avoid forms. Avoid schema-shaped questions. "What are your conventions?" is a form; "the wiki has no committed notation for this concept — does your existing code use <A> or <B>?" is a conversation.

### Writing to the transcript

Append turn-by-turn prose to `transcript.md` under the appropriate `## Decision Area:` heading. Prose captures thinking; decision blocks capture commitments.

When a decision actually lands, write a **decision block** in this exact format:

```markdown
### Decision: <short name>
- Section: <conventions | architecture | scope | requirements | open_items | agents_needed>
- <section-specific fields, see below>
- Rationale: <why, natural language, referencing wiki content or user-stated intent>
- Citations: src-..., src-...
```

Per-section required fields are listed in the brief and in §7 of the spec. You must include all required fields for the chosen Section, or the compile step will reject the block.

Do **not** assign `REQ-NNN` IDs yourself. The `--finalize` step assigns them sequentially.

### Lens matrix for requirements

For each in-scope item captured, walk the lens matrix before finalizing:

| Lens | Question it answers |
|------|---------------------|
| functional | What must the system do for this item? |
| performance | What speed, throughput, or latency bounds apply? |
| reliability | What failure rate, recovery time, or durability is required? |
| usability | What user-facing behavior or accessibility is required? |
| security | What auth, authorization, or data protection is required? |
| maintainability | What code or doc structure makes ongoing work feasible? |
| portability | What environments must this support? |
| constraint | What regulatory, legal, or resource limits apply? |
| data | What inputs, outputs, or schemas are required? |
| interface | What APIs or protocols with other components are required? |
| business-rule | What domain invariants must hold? |

If a lens does not apply to an item, note why in prose and move on — do not skip silently.

Phrase every requirement decision block using one of the EARS forms: "When / While / If-then / Where / The <system> shall …".

### Continuing

Continue the conversation until either:

- The human signals the dialog is complete.
- You judge every `## Decision Area:` heading has at least one decision block (or an explicit "defer to open_items" block).

## Step 4 — Finalize (CLI)

Run:

```bash
meta-compiler elicit-vision --finalize
```

This:

- Parses decision blocks from `transcript.md`.
- Assigns `REQ-NNN` IDs to `Section: requirements` blocks sequentially starting at `REQ-001`.
- Compiles `workspace-artifacts/decision-logs/decision_log_v{N}.yaml`.
- Runs mechanical fidelity checks: every transcript block → one YAML entry; no YAML entry without a source block; citation IDs resolve to the index; Section values valid; schema validates.
- Writes `workspace-artifacts/runtime/stage2/postcheck_request.yaml`.

Exits nonzero on any mechanical failure. On nonzero exit: **STOP**. Surface the failure (usually a malformed decision block or an unresolvable citation) and return to Step 3 to fix the transcript.

## Step 5 — Orchestrator Postflight (Fidelity audit)

Invoke:

```
@stage2-orchestrator mode=postflight
```

Input: the transcript, the compiled Decision Log, and `postcheck_request.yaml`.

The orchestrator's job is **fidelity audit**: does each YAML entry faithfully represent the source transcript block? Flags to watch:

- YAML `choice` or `description` paraphrases that change meaning
- Missing rationale or alternatives that were present in the block
- Requirement verifications that don't match the human's language
- Internal contradictions across decisions (cascade)

Output: `workspace-artifacts/runtime/stage2/postcheck_verdict.yaml` with `verdict: PROCEED | REVISE` and a discrepancy list.

On `REVISE`: return to Step 3 with the flagged discrepancies. Either amend the transcript (if the block was ambiguous) or flag a compile bug (if the block was clear but the YAML diverged).

On `PROCEED`, run:

```bash
meta-compiler audit-requirements
```

This kicks off the existing Decision Log audit — not your job to orchestrate. Record the path to the audit output in your final handoff message to the human.

---

## Out of scope

- You do not run `meta-compiler scaffold`. That's Stage 3, a separate prompt.
- You do not edit `decision_log_v{N}.yaml` directly. The transcript is the source; the CLI is the compiler; the orchestrator is the auditor.
- You do not ask the human for things the wiki already answers. If the wiki has the answer, state it and ask for the decision, not the information.

## On refusal

If the human asks you to skip a CLI call or an orchestrator invocation, refuse. The integrity layer exists for a reason. Stage 2 re-entry, Stage 1B iteration, and manual Decision Log editing are all supported paths — but not skipping steps of this prompt.
````

---

## 5. CLI Contract — `meta-compiler elicit-vision`

The `elicit-vision` subcommand is rewritten. Two mutually exclusive modes: `--start` or `--finalize`.

### 5.1 `--start`

**Signature.**

```
meta-compiler elicit-vision --start [--workspace-root PATH] [--artifacts-root PATH]
```

No `--use-case`, no `--resume`, no `--non-interactive`, no `--context-note`. These flags are deleted.

**Mechanical prerequisite checks (nonzero exit on any failure).**

1. Manifest present at `workspace-artifacts/manifests/workspace_manifest.yaml`.
2. `PROBLEM_STATEMENT.md` present and passes `validate_problem_statement` (no unedited template markers, all required sections present).
3. Wiki v2 directory has ≥1 page.
4. `workspace-artifacts/wiki/citations/index.yaml` has ≥1 citation.
5. `workspace-artifacts/wiki/reports/merged_gap_report.yaml` exists.
6. `workspace-artifacts/wiki/reviews/1a2_handoff.yaml` exists with `decision: PROCEED`. If `decision: ITERATE`, exit nonzero unless the human passes `--override-iterate "<reason>"` (reason is recorded in `precheck_request.yaml` for the orchestrator to weigh).

**Artifacts written on success.**

`workspace-artifacts/runtime/stage2/brief.md`:

```markdown
# Stage 2 Brief

Generated: <iso>
Decision Log version: v<N>
Wiki version: <sha256>

## Where to look

- PROBLEM_STATEMENT.md
- workspace-artifacts/wiki/v2/index.md
- workspace-artifacts/wiki/citations/index.yaml
- workspace-artifacts/wiki/reports/merged_gap_report.yaml
- workspace-artifacts/wiki/reviews/1a2_handoff.yaml

## Open gaps from Stage 1C

<top 20 gaps from merged_gap_report.yaml and suggested_sources from 1a2_handoff.yaml>

## Citation inventory

<one-line summary per citation ID: src-slug — title — type>

## Decision block format

<inline copy of §7 — per-section required fields>

## Decision Log schema

See `META-COMPILER.md` § "Decision Log Schema" for the full YAML shape. You do not author YAML. The `--finalize` step compiles your transcript into it.

## Transcript path

workspace-artifacts/runtime/stage2/transcript.md
```

`workspace-artifacts/runtime/stage2/transcript.md`:

```markdown
# Stage 2 Transcript — v<N>

Generated: <iso>

## Decision Area: Conventions

<gap annotations if any; citation suggestions if any>

## Decision Area: Architecture

<gap annotations if any; citation suggestions if any>

## Decision Area: Scope

<gap annotations if any; citation suggestions if any>

## Decision Area: Requirements

<lens matrix reminder>

## Decision Area: Open Items

## Decision Area: Agents Needed
```

The six `## Decision Area:` headings are fixed. The annotations under each are derived from the gap report (which gaps fall under which section) and the citation index (which citations are topically relevant).

`workspace-artifacts/runtime/stage2/precheck_request.yaml`:

```yaml
stage2_precheck_request:
  generated_at: <iso>
  decision_log_version: <N>
  wiki_version: <sha256>
  inputs:
    problem_statement: PROBLEM_STATEMENT.md
    wiki_v2: workspace-artifacts/wiki/v2/
    citation_index: workspace-artifacts/wiki/citations/index.yaml
    gap_report: workspace-artifacts/wiki/reports/merged_gap_report.yaml
    review_handoff: workspace-artifacts/wiki/reviews/1a2_handoff.yaml
  mechanical_checks:
    - name: problem_statement_complete
      result: PASS
      evidence: "5/5 required sections present; 0 template markers"
    - name: wiki_v2_populated
      result: PASS
      evidence: "<page_count> pages"
    - name: gap_report_present
      result: PASS
    - name: citation_index_nonempty
      result: PASS
      evidence: "<citation_count> citations"
    - name: stage_1c_proceed
      result: PASS
      evidence: "decision: PROCEED; 3/3 proceed_votes"
  override:
    iterate_override: null   # or "<reason>" if --override-iterate was used
  verdict_output_path: workspace-artifacts/runtime/stage2/precheck_verdict.yaml
```

**JSON result printed to stdout:**

```json
{
  "status": "ready_for_orchestrator",
  "brief_path": "...",
  "transcript_path": "...",
  "precheck_request_path": "...",
  "decision_log_version": <N>,
  "instruction": "Invoke @stage2-orchestrator mode=preflight next."
}
```

### 5.2 `--finalize`

**Signature.**

```
meta-compiler elicit-vision --finalize [--workspace-root PATH] [--artifacts-root PATH]
```

**Preconditions (nonzero exit on failure).**

- `workspace-artifacts/runtime/stage2/transcript.md` exists and has ≥1 decision block.
- `workspace-artifacts/runtime/stage2/precheck_verdict.yaml` exists with `verdict: PROCEED` (or documented override).

**Behavior.**

1. Parse decision blocks from the transcript per §7.
2. Assign `REQ-NNN` IDs sequentially to `Section: requirements` blocks, starting at `REQ-001`.
3. Build `decision_log_v{N}.yaml` by mapping blocks to Decision Log sections. `N` is `latest_decision_log_version + 1`, with `parent_version` set to the prior version (null for v1). `use_case` is derived from the transcript (the first prose paragraph after the opening question) or set to a default and flagged in `open_items`.
4. Run `validate_decision_log` on the compiled YAML. Any schema failure → nonzero exit.
5. Run mechanical fidelity checks:
   - Count of decision blocks in transcript equals count of entries in the YAML (sum across conventions/architecture/scope.in_scope+out_of_scope/requirements/open_items/agents_needed).
   - Every citation ID in the YAML resolves to `citations/index.yaml`.
   - `REQ-NNN` IDs are sequential, unique, and zero-padded to 3 digits.
   - No block with an unknown `Section:` value (caught during parse).
6. Write the compiled YAML to `workspace-artifacts/decision-logs/decision_log_v{N}.yaml`.
7. Update manifest: append to `decision_logs`, set `research.last_completed_stage: "2"`.
8. Write `workspace-artifacts/runtime/stage2/postcheck_request.yaml` (see §8.2 for schema).
9. Delete the transcript draft path `runtime/decision_log_draft.yaml` if it exists (legacy cleanup).

**JSON result printed to stdout:**

```json
{
  "status": "compiled",
  "decision_log_path": "...",
  "decision_log_version": <N>,
  "block_count": <int>,
  "requirement_count": <int>,
  "postcheck_request_path": "...",
  "instruction": "Invoke @stage2-orchestrator mode=postflight next."
}
```

### 5.3 Flags removed

The following flags are deleted from the CLI surface and from `cli.py`:

- `--use-case` (use-case is captured in the transcript or deferred to open_items)
- `--resume` (resume is implicit: `--start` is idempotent; `--finalize` picks up whatever transcript.md contains)
- `--non-interactive` (there is no interactive vs non-interactive distinction anymore)
- `--context-note` (the brief provides context; no inline context note is needed)

---

## 6. File Layout — `workspace-artifacts/runtime/stage2/`

```
workspace-artifacts/runtime/stage2/
├── brief.md                  # Step 1 output; LLM reads at start of Step 3
├── transcript.md             # Step 1 skeleton; LLM appends throughout Step 3; CLI parses in Step 4
├── precheck_request.yaml     # Step 1 output; orchestrator reads in Step 2
├── precheck_verdict.yaml     # Step 2 output; LLM reads before proceeding to Step 3
├── postcheck_request.yaml    # Step 4 output; orchestrator reads in Step 5
└── postcheck_verdict.yaml    # Step 5 output; LLM reads before running audit-requirements
```

On `meta-compiler clean-workspace --target-stage 1c` or lower, this directory is wiped. On `--target-stage 2`, it is preserved (used for re-entry) but the compiled Decision Log is preserved too.

---

## 7. Decision Block Format

### 7.1 General structure

A decision block begins with a line matching `^### Decision: (.+)$` and continues until the next `### Decision:` line or `## ` heading. Every block has:

```markdown
### Decision: <short name>
- Section: <one of six>
- <section-specific required fields>
- Rationale: <natural language>
- Citations: <comma-separated src-ids | (none)>
```

The `Section:` value must be one of: `conventions`, `architecture`, `scope-in`, `scope-out`, `requirements`, `open_items`, `agents_needed`. `scope.in_scope` and `scope.out_of_scope` are split into two Section values to keep the block format flat.

### 7.2 Per-section required fields

**`Section: conventions`**

```markdown
### Decision: <short name>
- Section: conventions
- Domain: <math | code | citation | terminology>
- Choice: <the convention in natural language>
- Rationale: <why>
- Citations: <comma-separated src-ids | (none)>
```

Maps to `decision_log.conventions[]` with fields `name` (from block title), `domain`, `choice`, `rationale`, `citations`.

**`Section: architecture`**

```markdown
### Decision: <short name>
- Section: architecture
- Component: <component name>
- Approach: <the chosen approach in natural language>
- Alternatives rejected:
  - <alt-name-1>: <reason>
  - <alt-name-2>: <reason>
- Constraints applied: <comma-separated constraint strings>
- Rationale: <why>
- Citations: <comma-separated src-ids | (none)>
```

Maps to `decision_log.architecture[]`. `Alternatives rejected` sublist is optional but strongly preferred.

**`Section: scope-in`**

```markdown
### Decision: <short name>
- Section: scope-in
- Item: <short item name>
- Rationale: <why it's in scope>
- Citations: <comma-separated src-ids | (none)>
```

Maps to `decision_log.scope.in_scope[]` with fields `item`, `rationale`.

**`Section: scope-out`**

```markdown
### Decision: <short name>
- Section: scope-out
- Item: <short item name>
- Rationale: <why it's out of scope>
- Revisit if: <condition for reconsideration>
- Citations: <comma-separated src-ids | (none)>
```

Maps to `decision_log.scope.out_of_scope[]` with fields `item`, `rationale`, `revisit_if`.

**`Section: requirements`**

```markdown
### Decision: <short requirement name>
- Section: requirements
- Source: <user | derived>
- Description: <EARS-phrased requirement>
- Verification: <how we'd know it's met>
- Lens: <one of the lens-matrix lenses>
- Rationale: <why this requirement exists>
- Citations: <comma-separated src-ids | (none)>
```

Maps to `decision_log.requirements[]`. The compile step assigns `id: REQ-NNN` sequentially. `Lens` is preserved as an extra field on the YAML entry (already supported — `_auto_fill` currently emits it).

**`Section: open_items`**

```markdown
### Decision: <short name>
- Section: open_items
- Description: <what's unresolved>
- Deferred to: <implementation | future_work>
- Owner: <human | agent role name>
- Rationale: <why it's deferred>
- Citations: <comma-separated src-ids | (none)>
```

**`Section: agents_needed`**

```markdown
### Decision: <short name>
- Section: agents_needed
- Role: <agent role name>
- Responsibility: <what this agent does>
- Reads: <comma-separated artifact types>
- Writes: <comma-separated artifact types>
- Key constraints: <comma-separated constraint strings>
- Rationale: <why this agent is needed>
- Citations: <comma-separated src-ids | (none)>
```

### 7.3 Parsing rules

- Block parser: markdown-level. Regex-anchored on `^### Decision:` line start; fields on `^- <Field>:` lines; sublists (alternatives rejected) on `^  - <alt>: <reason>` indented lines.
- All field keys match case-insensitively ("Section:" and "section:" both parse).
- Comma-separated lists split on `,` with whitespace trimmed. `(none)` is an explicit empty list.
- Every required field for the declared Section must be present. Missing field → parse error → CLI nonzero exit with the block name + missing field reported.
- Unknown Section value → parse error.
- Unknown field on a block → warning logged to stderr; field ignored (future-forward tolerance).
- Prose between decision blocks is ignored by the parser; preserved verbatim in the transcript for audit.

### 7.4 `REQ-NNN` assignment

Sequential. Starting `REQ-001`. Assigned in the order `### Decision:` lines of `Section: requirements` appear in the transcript. Zero-padded to 3 digits. Collisions are impossible because the LLM never writes IDs.

---

## 8. Orchestrator Agent — `.github/agents/stage2-orchestrator.agent.md`

The existing `stage2-orchestrator.agent.md` is rewritten. It no longer drives the Stage 2 ralph loop (that role was redundant with the CLI and is deleted). Its new responsibility is **boundary integrity**: one agent, two modes, invoked twice per Stage 2 run.

### 8.1 Preflight mode

**Invocation.** `@stage2-orchestrator mode=preflight`

**Input.** `workspace-artifacts/runtime/stage2/precheck_request.yaml`.

**Job.** Determine whether the context is rich enough to support a productive dialog. The CLI has already done mechanical checks (existence, completeness, handoff verdict); the agent adds semantic judgment the CLI cannot make.

**Checks the agent performs.**

- Read the problem statement. Is the problem space clear enough that narrowing questions are answerable? If not, what's missing?
- Read the top 20 gaps from `merged_gap_report.yaml`. Are any severe enough that Stage 2 should wait for a Stage 1B iteration? (e.g., a critical gap in a topic central to the problem domain.)
- Scan wiki v2 for coverage of the problem statement's core topics. Are there topics in the problem statement that have no wiki page at all?
- Check `suggested_sources` in `1a2_handoff.yaml` — are there blocking sources the reviewers wanted that were never ingested?

**Output.** `workspace-artifacts/runtime/stage2/precheck_verdict.yaml`. Schema in §8.3.

**Verdict logic.** If any check flags a `severity: BLOCK`, return `verdict: BLOCK`. Otherwise `verdict: PROCEED`. `WARN`-severity findings return PROCEED with the warnings listed.

### 8.2 Postflight mode

**Invocation.** `@stage2-orchestrator mode=postflight`

**Input.** `workspace-artifacts/runtime/stage2/postcheck_request.yaml`, which the CLI writes at Step 4 containing:

```yaml
stage2_postcheck_request:
  generated_at: <iso>
  decision_log_version: <N>
  inputs:
    transcript: workspace-artifacts/runtime/stage2/transcript.md
    decision_log: workspace-artifacts/decision-logs/decision_log_v<N>.yaml
  mechanical_checks:
    - name: block_count_matches_entry_count
      result: PASS
      evidence: "<K> blocks, <K> entries"
    - name: citation_ids_resolve
      result: PASS
    - name: req_ids_sequential
      result: PASS
    - name: schema_validates
      result: PASS
  verdict_output_path: workspace-artifacts/runtime/stage2/postcheck_verdict.yaml
```

**Job.** Fidelity audit. The CLI has confirmed the structural parts (every block has an entry, citations resolve). The agent checks semantic faithfulness.

**Checks the agent performs.**

- For each decision block → YAML entry pair: does the YAML paraphrase preserve meaning? Flag any `choice` / `description` that drops, adds, or reverses information.
- Are any rationales truncated or lost in compile?
- For `Section: requirements`: does the `verification` field match the block's `Verification` text without semantic drift?
- Does the Decision Log have internal contradictions? (e.g., a convention that conflicts with an architecture decision; an in_scope item with no corresponding requirement; an out_of_scope item referenced by an in_scope requirement.)
- For re-entry runs (`parent_version != null`): are the carried-forward decisions from the prior version still consistent with the new ones?

**Output.** `workspace-artifacts/runtime/stage2/postcheck_verdict.yaml`.

**Verdict logic.** Any semantic drift that changes the meaning of a decision → `verdict: REVISE`. Any contradiction → `REVISE`. Otherwise `PROCEED`.

### 8.3 Shared verdict schema

Both preflight and postflight write this shape:

```yaml
stage2_orchestrator_verdict:
  stage: preflight | postflight
  verdict: PROCEED | BLOCK | REVISE
  generated_at: <iso>
  decision_log_version: <N>
  checks:
    - name: <short check identifier>
      result: PASS | FAIL | WARN
      severity: <INFO | WARN | BLOCK | REVISE>   # only meaningful for non-PASS
      evidence: <what was checked, where>
      remediation: <if not PASS, what to do>
      transcript_anchor: <optional: decision block name for postflight fidelity checks>
      yaml_anchor: <optional: decision_log.<section>[<idx>] for postflight>
  summary: <one-paragraph human-readable summary>
  next_action: <string; the LLM surfaces this to the human>
```

For preflight: `verdict` ∈ `{PROCEED, BLOCK}`. For postflight: `verdict` ∈ `{PROCEED, REVISE}`.

### 8.4 Agent file contents

New `.github/agents/stage2-orchestrator.agent.md` frontmatter:

```yaml
name: stage2-orchestrator
description: "Stage 2 boundary integrity. Preflight: verify context readiness before dialog. Postflight: verify the compiled Decision Log faithfully represents the transcript. Invoked twice per Stage 2 run."
tools: [read, search, agent]
agents: [explore, research]
user-invocable: false
argument-hint: "mode=preflight | mode=postflight"
```

Body sections: `## Purpose`, `## Modes` (preflight + postflight contracts), `## Verdict Schema` (links to §8.3), `## Decision Trace` (which Decision Log version was audited).

---

## 9. Code Changes

### 9.1 Files to delete or gut

- `meta_compiler/stages/elicit_stage.py`: **gut and rewrite**. Keep the module; replace its contents. Delete: `_prompt`, `_yes_no`, `_csv`, `_collect_citations`, `_save_checkpoint`, `_add_conventions`, `_add_architecture`, `_add_scope`, `_add_requirements`, `_add_open_items`, `_add_agents`, `LENS_TEMPLATES`, `_extract_problem_section` (migrate if useful), `_derive_scope_items`, `_citations_for_item`, `_auto_fill`. Rewrite `run_elicit_vision` to dispatch on `mode` (`start` | `finalize`).
- `meta_compiler/cli.py`: delete `--use-case`, `--resume`, `--non-interactive`, `--context-note` from the `elicit-vision` subparser. Add mutually exclusive `--start` / `--finalize` group. Add `--override-iterate "<reason>"` as an optional escape hatch for preflight.
- `.github/agents/stage2-orchestrator.agent.md`: rewrite per §8.4.
- `.github/prompts/stage-2-dialog.prompt.md`: replace wholesale per §4.
- `prompts/stage-2-dialog.prompt.md`: same replacement (root mirror; see §13).
- `.github/agents/requirement-deriver.agent.md`: **delete**. The deriver's role (fan-out requirements per in-scope item) is replaced by the LLM conducting the lens matrix directly inside Step 3 of the prompt. If we want fan-out parallelism later, it re-enters as a runtime fan-out concern (separate roadmap item).

### 9.2 Files to add

- `meta_compiler/stages/elicit_stage.py`: the new implementation. New private helpers:
  - `_render_brief(paths, manifest, gap_report, citation_index) -> str`
  - `_render_transcript_skeleton(paths, gap_report) -> str`
  - `_write_precheck_request(paths, mechanical_checks) -> Path`
  - `parse_decision_blocks(transcript_text: str) -> list[DecisionBlock]` — pure function, testable in isolation
  - `compile_decision_log(blocks, prior_version, project_meta) -> dict` — pure function
  - `_mechanical_fidelity_checks(transcript_blocks, compiled_log) -> list[Check]`
  - `_write_postcheck_request(paths, decision_log, mechanical_checks) -> Path`
- `.github/docs/stage-2-hardening.md`: this file.
- `.github/docs/prompt-as-conductor.md`: extracted after this spec is validated against a second roadmap item (ingest-orchestrator likely).

### 9.3 Files to update

- `meta_compiler/validation.py`: add `validate_stage2_precheck_request`, `validate_stage2_verdict`, `validate_stage2_postcheck_request`. Extend `validate_stage` to include these when `stage` is `2` or `all`.
- `meta_compiler/stages/stage2_reentry.py`: update to honor the new flow. `stage2-reentry` now produces a seeded transcript (with prior decisions carried forward as prose + marked blocks) rather than a partial YAML. `finalize-reentry` becomes `elicit-vision --finalize` (same code path).
- `meta_compiler/stages/run_all_stage.py`: update to call `elicit-vision --start` but then **stop**, because the dialog has to happen in chat. `run-all` adjusts its terminal message: "Stage 2 preflight complete. Open `.github/prompts/stage-2-dialog.prompt.md` in your LLM runtime to begin the dialog, then run `meta-compiler elicit-vision --finalize` followed by `meta-compiler audit-requirements`." Remove the current `run_elicit_vision(non_interactive=True)` call.
- `meta_compiler/artifacts.py`: add paths for `runtime/stage2/brief.md`, `transcript.md`, `precheck_request.yaml`, `precheck_verdict.yaml`, `postcheck_request.yaml`, `postcheck_verdict.yaml` to `ArtifactPaths`. Update `ensure_layout` accordingly.
- `README.md`: update the "Stage 2" section and the `run-all` description.
- `LLM_INSTRUCTIONS.md`: update the "Stage 2: Vision Elicitation" section.
- `CLAUDE.md`: update the "Stage 2" description in the stage pipeline.
- `.vscode/tasks.json`: replace the "Stage 2: Vision Elicitation (non-interactive)" task with "Stage 2: Preflight" (runs `--start`) and "Stage 2: Finalize" (runs `--finalize`).

---

## 10. Validation Changes

Add to `meta_compiler/validation.py`:

- `validate_stage2_precheck_request(payload) -> list[str]`: checks `stage2_precheck_request` root, required fields (`generated_at`, `decision_log_version`, `mechanical_checks`, `verdict_output_path`), and `mechanical_checks` list schema.
- `validate_stage2_postcheck_request(payload) -> list[str]`: analogous for `stage2_postcheck_request`.
- `validate_stage2_verdict(payload) -> list[str]`: checks `stage2_orchestrator_verdict` root, `stage` ∈ `{preflight, postflight}`, `verdict` ∈ appropriate set, `checks` list schema.
- `validate_transcript(path) -> list[str]`: parses decision blocks; returns one issue per malformed block.

Extend `validate_stage` to run these when `stage` is `2` or `all` and the runtime/stage2 artifacts exist (they won't exist on a fresh workspace or after a `clean-workspace --target-stage 1c`, which is fine).

---

## 11. Tests

New tests under `tests/`:

- `tests/test_stage2_decision_blocks.py`
  - Parse a well-formed transcript with one block per section type; assert all six Sections produce correct entries.
  - Parse a transcript with an unknown Section value; assert parse error.
  - Parse a transcript with missing required fields per section; assert parse error with the specific missing field reported.
  - Parse a transcript with three `Section: requirements` blocks; assert `REQ-001`, `REQ-002`, `REQ-003` assignment.
  - Parse a transcript with only prose (no blocks); assert empty result.
  - Parse a transcript with mixed prose and blocks; assert prose is ignored and blocks are extracted in order.
- `tests/test_stage2_compile.py`
  - End-to-end: decision blocks → compiled YAML matches `validate_decision_log`.
  - Citation IDs that don't resolve → fidelity check fails.
  - Block count mismatch with YAML count → fidelity check fails.
- `tests/test_elicit_vision_start.py`
  - With all prereqs met: brief.md and transcript.md and precheck_request.yaml written; JSON status = `ready_for_orchestrator`.
  - Missing problem statement → nonzero exit with specific check failure.
  - Stage 1C handoff = ITERATE without `--override-iterate` → nonzero exit; with override → proceed and reason recorded.
  - Idempotent: two runs in a row produce identical artifacts (mod timestamps).
- `tests/test_elicit_vision_finalize.py`
  - With a well-formed transcript: compiled Decision Log validates, postcheck_request.yaml written, manifest updated.
  - With a malformed transcript: nonzero exit, no YAML written, manifest untouched.
  - Re-entry (parent_version != null): carried-forward blocks merge correctly.
- `tests/test_stage2_integration.py`
  - Run `--start`, simulate the LLM writing a minimal valid transcript, run `--finalize`. Assert the flow end-to-end without touching real LLMs.

Remove or update:

- `tests/test_run_all_stage.py`: update to reflect `run-all` now stopping at preflight, not at non-interactive elicit.

---

## 12. Implementation Plan

Ordered, each step independently reviewable:

1. **Land the spec.** This doc, reviewed and merged (no code changes yet).
2. **Add pure-function block parsing.** `parse_decision_blocks` and `compile_decision_log` as pure functions under `meta_compiler/stages/elicit_stage.py`, with `tests/test_stage2_decision_blocks.py` passing. No CLI wiring yet.
3. **Add artifact paths.** Update `ArtifactPaths` + `ensure_layout` for `runtime/stage2/*`. Update `validation.py` with the new validators.
4. **Implement `--start`.** Rewrite `run_elicit_vision` dispatch, wire mechanical prerequisite checks, write brief/transcript/precheck_request. Add `tests/test_elicit_vision_start.py`.
5. **Implement `--finalize`.** Wire block parsing, compile, mechanical fidelity checks, write postcheck_request. Add `tests/test_elicit_vision_finalize.py`.
6. **Rewrite the orchestrator agent.** `.github/agents/stage2-orchestrator.agent.md` per §8.4. Delete `requirement-deriver.agent.md`.
7. **Replace the prompt.** `.github/prompts/stage-2-dialog.prompt.md` and the root mirror per §4.
8. **Update `run-all`.** Stop after `--start` writes artifacts; emit guidance.
9. **Update `stage2-reentry`.** Produce seeded transcript instead of partial YAML.
10. **Delete CLI flags + old code paths.** `--use-case`, `--resume`, `--non-interactive`, `--context-note`.
11. **Update all docs.** README, LLM_INSTRUCTIONS, CLAUDE, copilot-instructions, VSCode tasks.
12. **Regenerate a sample workspace end-to-end** with a simple problem statement to smoke-test the full flow. Fix the bugs that will definitely surface.

Each step should be a separate commit. The system should remain runnable end-to-end after steps 1, 2, 3 (no behavior change yet), and then behavior changes land starting at step 4 with the `--start` subcommand.

---

## 13. Open Questions and Migration

**Dual prompt source (root `prompts/` vs `.github/prompts/`).** The current code provisions both during `meta-init` (`_provision_workspace_prompts` copies root `prompts/` to target `prompts/`; `_provision_workspace_customizations` copies the whole `.github/` tree). For a downstream project workspace where meta-compiler is the orchestrator, both end up in the target. For meta-compiler itself (source = target), the copies are no-ops. Question: is the dual source intentional (one for Copilot Chat's `.github/prompts/`, one for humans/non-Copilot runtimes that read `prompts/`), or legacy? This spec updates both. We should decide whether to consolidate on `.github/prompts/` only in a follow-up.

**How does the prompt runtime actually invoke `@stage2-orchestrator`?** Different LLM runtimes handle custom-agent invocation differently. Copilot Chat supports `@agent-name`; Claude Code uses the `Agent` tool; other runtimes may need explicit prompt engineering. The spec assumes the invocation mechanism exists; actual behavior across runtimes is validated at implementation step 12.

**Override semantics for `BLOCK` verdicts.** `--override-iterate` is proposed for `--start` to bypass a Stage-1C `ITERATE` handoff. But semantic `BLOCK` from the orchestrator preflight (e.g., "wiki has no coverage of X") has no CLI override — the only path is to iterate Stage 1B. Is that correct, or should the prompt also support `--override-preflight-block "<reason>"`? Proposed: keep the override narrow for now; escape hatches proliferate when you let them.

**Migration for existing Decision Logs (v1, v2, etc. already on disk).** No migration needed. The Decision Log YAML schema is unchanged — only the authoring path changes. Existing Decision Logs continue to work with Stage 3 scaffold generation.

**Re-entry.** `stage2-reentry` currently produces a partial YAML template with `_prior_*` sections. Under the new flow, re-entry produces a transcript seeded with carried-forward decision blocks (marked as `from_version: v<N>`), and the human+LLM converse only about the sections flagged for revision. The same `--finalize` code path handles v2+. The implementation lives in `meta_compiler/stages/stage2_reentry.py` and is exercised by `tests/test_stage2_reentry*.py`; the conductor prompt is `.github/prompts/stage2-reentry.prompt.md`.

---

## 14. Relation to the other roadmap items

This spec was the template; the pattern is now extracted into
`.github/docs/prompt-as-conductor.md`. Status of the four roadmap items:

- **Ingest-orchestrator prompt-as-conductor — implemented.** `meta-compiler ingest-precheck` (Step 2) and `meta-compiler ingest-postcheck` (Step 5) are wired; `ingest-orchestrator` runs in `mode=preflight | fanout | postflight`. Hooks: `gate_ingest_precheck`, `gate_ingest_postcheck`, `require_ingest_precheck_verdict`, `require_ingest_postcheck_verdict`.
- **Ralph-loop implementers (Stage 4 runtime) — implemented.** `phase4-finalize` is split into `--start` (writes `dispatch_plan.yaml` + `execution_request.yaml`) and `--finalize` (compiles `FINAL_OUTPUT_MANIFEST.yaml` from LLM-populated `executions/v{N}/work/`, writes pitch deck + postcheck request). Hook: `gate_phase4_finalize`. Conductor prompt: `.github/prompts/stage-4-finalize.prompt.md`.
- **Stage 1A2 orchestration — remaining.** Still uses an older shape (`stage-1a2-orchestration.prompt.md` + `stage-1a2-orchestrator.agent.md`). Apply the conductor pattern in a follow-up; nothing about Stage 1A2 makes it special, it just hasn't been migrated yet.
- **Runtime fan-out — implemented at the prompt layer.** All three conductor prompts (stage-2-dialog, ingest-orchestrator, stage-4-finalize) instruct the LLM to fan out subagents in parallel at Step 3.

Future stages should consult `.github/docs/prompt-as-conductor.md` rather than re-derive the pattern.
