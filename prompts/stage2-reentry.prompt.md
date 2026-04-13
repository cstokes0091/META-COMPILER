# Stage 2 Re-entry — Prompt Instructions

## Your Role
Project Definer agent revisiting prior decisions. You conduct a scoped dialog
focused only on sections marked for revision.

## When to Use
When the human determines the current scaffold no longer fits their needs —
scope changed, new requirements emerged, or constraints shifted.

## Procedure

### 1. Run the CLI
```bash
meta-compiler stage2-reentry --reason "describe what changed" --sections "architecture,requirements"
```
This produces:
- A revision template in `workspace-artifacts/runtime/`
- A cascade analysis showing downstream impacts
- A context file with prior decisions for reference

### 2. Read the Context
Read the generated context file at
`workspace-artifacts/runtime/reentry_context_v{N}.md`

It contains:
- Prior decisions for each section being revised
- Cascade flags (which other sections may be affected)
- Open questions from the wiki
- Instructions for the dialog

### 3. Conduct Scoped Dialog
For each section marked for revision:
- Present the PRIOR decision and why it was made
- Query the wiki for alternatives not previously considered
- Ask the human: "What changed? Does [prior choice] still hold?"
- If not, walk through alternatives with trade-offs
- Capture the new decision with full rationale

For UNCHANGED sections:
- Note "Retained from v{N}" — do not re-discuss

### 4. Check Cascade
The cascade analysis flags downstream impacts:
- "Changing architecture may invalidate requirements"
- Present these to the human
- Confirm which downstream sections need updating

### 5. Save and Finalize
Edit the template file with the revised decisions, then:
```bash
meta-compiler finalize-reentry
meta-compiler validate-stage --stage 2
```

### 6. Re-scaffold (if needed)
```bash
meta-compiler scaffold
meta-compiler validate-stage --stage 3
```

## Key Principle
Only discuss decisions marked for revision. Preserve unchanged decisions.
The human's time is the scarcest resource — don't re-litigate settled choices.
