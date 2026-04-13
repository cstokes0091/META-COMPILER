# Agent Spec: narrative-structure-agent

Decision Log Version: v1
Project Type: hybrid
Responsibility: Map architecture decisions and requirements into a coherent report narrative.

## Reads
- decision_log
- architecture
- requirements

## Writes
- report
- docs

## Key Constraints
- cover all requirement IDs in narrative plan
- fresh context
- artifact-only handoff
- strict validation

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
