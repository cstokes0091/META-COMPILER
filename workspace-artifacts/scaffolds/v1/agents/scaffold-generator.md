# Agent Spec: scaffold-generator

Decision Log Version: v1
Project Type: hybrid
Responsibility: Generate project structure and agent specs from Decision Log.

## Reads
- decision_log

## Writes
- scaffold
- agents
- docs
- requirements

## Key Constraints
- input is Decision Log only
- do not read wiki or raw sources
- trace outputs to requirement and citation IDs
- fresh context
- artifact-only handoff
- strict validation
- no raw source access
- trace every instruction to decision log

## Decisions Embedded
- Architecture: workflow-orchestrator -> Artifact-driven stage transitions with strict schema checks (citations: src-decision-seed, src-sample-seed)
- Convention (code): Prefer clear modular Python with explicit validation (citations: src-decision-seed, src-sample-seed)

## Requirement Trace
- REQ-001: Decision log must be schema-valid and citation-traceable.
  Verification: Run validate-stage --stage 2 with zero issues.
- REQ-002: Scaffold generator must consume Decision Log only.
  Verification: Run scaffold command and verify generated files include decision traces.

## Citation Anchors
- src-decision-seed
- src-sample-seed

## Stage 3 Guardrails
- Input is Decision Log only; do not consume wiki or raw sources.
- Preserve scope boundaries unless Stage 2 issues a revised decision log.
- Generated from Decision Log entries; update via Stage 2 re-entry if needed.
