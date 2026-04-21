# Stage 2 Probe Library

**Purpose.** Stage 2 dialog tends to produce shallow decision blocks when the
LLM asks generic questions and accepts the first answer. This library is the
fix: a fixed set of mandatory probes per decision section that the dialog must
walk before writing a block in that section.

**Use.** The Stage 2 dialog prompt requires the conductor to address at least
**4 of the section's probes** before writing a decision block in that section.
The `stage2-orchestrator` postflight check (`probe_coverage`) verifies the
transcript contains evidence that the probes were addressed; shallow blocks
are flagged as `REVISE`.

The probes are not a script — the dialog is still a conversation. They are the
floor: every probe should at least be considered, even if the answer is "not
applicable here, because <reason>".

---

## Section: `conventions`

A convention is a load-bearing choice about notation, naming, citation style,
or terminology that downstream stages rely on. Probes:

1. **Domain.** Which domain does this convention belong to: math, code,
   citation, or terminology? Why this domain and not an adjacent one?
2. **Default.** What does the wiki currently use for this kind of choice?
   Are we extending an existing convention or breaking from it?
3. **Alternatives.** What are 2–3 other options? Why are we not picking each?
4. **Failure mode.** What breaks if we pick this and the codebase later
   conflicts with it? Is there an escape hatch?
5. **Scope.** Does this convention apply repo-wide, to one module, to one
   stage, or to one document?
6. **Enforcement.** How will we notice if a future change violates this
   convention? Is there a lint rule, schema check, or CI gate possible?
7. **Reversibility.** If we change this in 6 months, what has to change with
   it (file renames, doc rewrites, citation re-stamps)?
8. **Citation.** Which wiki page or external authority establishes the basis
   for this convention?

---

## Section: `architecture`

Architecture decisions describe a component's approach plus the constraints
that ruled out the alternatives. Probes:

1. **Component definition.** What exactly is this component responsible for?
   Where do its responsibilities end?
2. **Approach summary.** In one sentence: what does the chosen approach do
   that the alternatives don't?
3. **Alternatives rejected.** What 2–3 alternatives were considered? For each,
   why was it rejected — name the constraint it violated, not just "we
   preferred X".
4. **Invariants.** What MUST hold true for this approach to be correct?
   What property would, if violated, break the system?
5. **Failure modes.** What can fail in this approach? Which failures are
   acceptable, which are not?
6. **Measurable success.** How will we know it's working in practice — what
   single metric, output, or invariant tells us "yes"?
7. **Constraints applied.** Which constraints from the problem statement,
   wiki, or upstream decisions narrowed this choice?
8. **Cost.** What does this approach cost in complexity, runtime, build time,
   maintenance burden, or operator effort?
9. **Boundaries.** What other components does this one talk to? What's the
   contract at each boundary?
10. **Future revision.** What change in requirements would force us to revisit
    this decision? How much rework would that be?

---

## Section: `code-architecture`

**Applicability.** Required for `algorithm` and `hybrid` projects. Forbidden
for `report` projects.

Code-architecture decisions describe HOW the logical architecture is
realized in code: language, libraries, module layout, build/test tooling,
runtime. They are not the same as `architecture`, which captures the
component decomposition. A single `architecture` block may be backed by
several `code-architecture` aspects (e.g. one `language`, one `libraries`,
one `module_layout`, one `build_tooling`).

At minimum, the dialog must produce one block with `Aspect: language` and
one with `Aspect: libraries`. The other aspects (`module_layout`,
`build_tooling`, `runtime`) are encouraged when load-bearing. Probes:

1. **Aspect.** Which aspect does this block address — `language`,
   `libraries`, `module_layout`, `build_tooling`, or `runtime`? Pick exactly
   one. Walk separate blocks for each load-bearing aspect.
2. **Choice.** What is the concrete commitment? "Python 3.11", "numpy >=1.26
   + pyarrow >=15", "src/foo/{ingest,transform,emit}.py", "uv + pytest",
   "containerized on GHA runners". Avoid placeholders like "TBD" or
   "standard tooling".
3. **Library candidates and version pins.** When `Aspect=libraries`, list
   each library with name, version pin, and load-bearing purpose under a
   `Libraries:` sublist (e.g. `  - numpy: PSF math (>=1.26)`). Vague entries
   like "as needed" do not count.
4. **Alternatives rejected.** Which 1–3 alternatives were considered for
   this aspect? For language, why not Rust / Julia / TypeScript / etc.? For
   libraries, what are the rejected options and the constraint that ruled
   them out (license, performance, team familiarity, maintenance status)?
5. **Constraints applied.** Which problem-statement, wiki, or upstream
   architecture constraints narrowed this aspect? "must run on M-series
   Macs", "cannot add a Java toolchain", "license must be permissive" all
   count. Tie back to a citation when possible.
6. **Module/package layout.** When `Aspect=module_layout`, describe the
   file/package layout as a `Module layout:` line (e.g.
   `src/foo/{ingest,transform,emit}.py`). Note where the entry point lives
   and how tests mirror the source tree.
7. **Build & test tooling.** When `Aspect=build_tooling`, what runs the
   tests, lints, and packages the code? Pin the tooling versions when the
   choice is load-bearing.
8. **Runtime / deployment target.** When `Aspect=runtime`, what executes
   the code in production — a CLI binary, a notebook, a container, a
   serverless function? Note OS/arch constraints and runtime version pins.
9. **Reversibility.** If we swap this choice in 6 months, what has to move
   with it (other libraries, the module layout, CI config, agent specs)?
10. **Citation.** Which wiki page, code seed, or external authority backs
    this choice? An empty Citations is acceptable for team-local conventions
    but should be rare.

---

## Section: `requirements`

Requirements are the testable specification of what the system must do. The
EARS phrasing is mandatory; the probes ensure the requirement is not just
well-phrased but well-grounded. Probes:

1. **Trigger or state.** What starts this requirement firing — an event, a
   continuous state, an unconditional rule, or a feature flag? Pick the EARS
   form that matches.
2. **Source.** Did the human state this directly, or did we derive it from
   another decision? If derived, from which?
3. **Verification method.** Concretely: how will we test that this
   requirement holds? Unit test, integration test, manual inspection, runtime
   metric, formal proof?
4. **Lens.** Which of the 11 lenses does this primarily address? Walk all 11
   lenses for the parent in-scope item before declaring the lens matrix
   complete.
5. **Failure visibility.** If this requirement is violated, who notices and
   how? Loud crash, silent degradation, future bug report?
6. **Conflict.** Does this requirement conflict with any other captured
   requirement, in spirit or letter? If so, which one wins, and why?
7. **Boundary cases.** Name 1–2 boundary cases (empty input, max input,
   permission-denied, network failure) and state whether the requirement
   addresses them.
8. **Citations.** Which wiki page, source document, or earlier decision
   block grounds this requirement?

---

## Section: `scope-in` / `scope-out`

Scope decisions narrow what the system will and won't do. They prevent
feature-creep and frame what the requirements have to cover. Probes:

1. **Item definition.** State the item in one sentence. Avoid passive voice.
2. **In-or-out rationale.** Why is this in-scope (or out-of-scope) for THIS
   version? Tie to the problem statement.
3. **Adjacency.** What's an adjacent item that could plausibly be in scope —
   what makes that one different from this one?
4. **Revisit if (out-of-scope only).** Under what concrete future condition
   would this become in-scope? Be specific: "when X metric exceeds Y" beats
   "if the user asks".
5. **Cost of inclusion.** If this in-scope item is included, what
   requirements does it force into the spec? What complexity does it add?
6. **Cost of exclusion (in-scope items).** If we cut this from scope, what
   value is lost? What would the user notice?
7. **Coverage.** Are there sub-items implicitly excluded by including this
   one? Say so explicitly so they aren't accidentally added later.

---

## Section: `open_items`

Open items are decisions deferred deliberately rather than answered. They are
not the same as gaps — gaps are missing knowledge; open items are known
choices we're leaving until later. Probes:

1. **Description.** State the open item in one sentence. What is the unresolved
   choice?
2. **Why deferred.** Why not decide now? Lack of information, lack of
   constraint, time-boxing, or because it depends on a downstream decision?
3. **Deferral target.** Implementation phase or future work? If implementation,
   who owns the resolution; if future work, what triggers the revisit?
4. **Owner.** Who is responsible for closing this item? "We" is not an answer.
5. **Blocking risk.** Does any in-scope item depend on resolving this? If so,
   the item belongs in scope or requirements, not open_items.
6. **Resolution criteria.** What does "resolved" look like — a Decision Log
   amendment, a code commit, a test result?

---

## Section: `agents_needed`

Agents needed describe Stage 3 scaffold output: roles, responsibilities, and
the interfaces between generated agents. Probes:

1. **Role.** Name the agent in 1–3 words. What is its single responsibility?
2. **Inputs (typed).** Which artifacts does this agent consume? Use the
   `Inputs:` sublist and tag every entry with modality (`document` or
   `code`):
       - Inputs:
         - decision_log: document
         - code: code
   "the wiki" is too vague; "wiki/v2/pages/concept-foo.md" is right. Modality
   makes Stage 3 generate the right tooling — code-modality inputs imply a
   reader that handles source files; document-modality inputs imply markdown
   ingestion.
3. **Outputs (typed).** Which artifacts does this agent produce, and at what
   modality? Same `Outputs:` sublist with modality tags. For
   `project_type=report`, every output modality must be `document`. For
   `algorithm`/`hybrid`, outputs may be `code`, `document`, or a mix —
   declare each entry explicitly. Don't default to "document" when the
   artifact is actually source code.
4. **Boundary.** What does this agent NOT do? What belongs to a sibling agent?
5. **Tools required.** Read-only, search, edit, execute, agent (delegation),
   todo? Don't grant tools the agent doesn't need. Modality should drive
   tool selection — code-modality outputs imply `execute` for tests; pure
   document outputs usually do not.
6. **Failure mode.** What kind of failure is most likely (parse error, missing
   input, hallucination, timeout)? How does it surface?
7. **Verification.** How does the next stage verify this agent's output? CLI
   schema check, hash-tracked manifest, downstream agent review? Code outputs
   imply test/typecheck verification; document outputs imply citation/lint
   verification.
8. **Concurrency.** Does this agent fan out to subagents? What's the cap?

---

## Probe Coverage Floor

For each decision block written, the dialog must show evidence (in transcript
prose, not just the block itself) that **at least 4 probes** from the
corresponding section were addressed. The `probe_coverage` postflight check
counts probe addressing per block; blocks below the floor are flagged as
shallow and the orchestrator returns `REVISE`.

What counts as "addressing a probe":

- The conductor asked the human a question that maps to the probe.
- The conductor stated an answer drawn from the wiki and the human confirmed
  or amended it.
- The conductor explicitly noted "not applicable" with a one-sentence reason.

Implicit assumptions ("we'll obviously want X") do not count. Walking the
probe library is the discipline that prevents shallow blocks.
