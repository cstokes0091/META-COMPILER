---
description: Ralph loop protocol for scaffold implementers
---
# Ralph Loop Instructions

Every implementer in this scaffold follows the orchestrator -> implement -> review -> loop pattern.

## Pattern

1. **Orchestrator** reads `AGENT_REGISTRY.yaml`, picks the next unblocked implementer, and invokes it with a scoped brief.
2. **Implementer** produces its declared output artifact, writing only to paths it owns in the registry.
3. **Reviewer** (fresh context) validates the artifact against decision log constraints, requirement trace, and citation fidelity. Returns PASS or REVISE.
4. On REVISE, orchestrator feeds `blocking_gaps` and `proposed_fixes` back to the implementer. Max 3 cycles.
5. On PASS, registry entry is marked `completed` and the orchestrator advances.

## Format-agnostic Review

Reviewers pick their validator based on the implementer's `output_kind`:

- `code` — unit tests + type checker + requirement trace.
- `document` — markdown lint + citation resolution + outline compliance + requirement trace.
- `artifact` — format-specific syntactic checks + requirement trace.

## Termination

- Every registry entry reaches `status: completed` or `status: force-advanced`.
- Force-advanced entries are logged in `executions/v<N>/ralph_loop_log.yaml` and in the Decision Log `open_items` list on the next Stage 2 re-entry.
