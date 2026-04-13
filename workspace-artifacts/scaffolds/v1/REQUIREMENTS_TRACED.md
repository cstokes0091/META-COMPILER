# REQUIREMENTS_TRACED

## REQ-001
- Description: Decision log must be schema-valid and citation-traceable.
- Source: derived
- Verification: Run validate-stage --stage 2 with zero issues.
- Citations: src-decision-seed, src-sample-seed

## REQ-002
- Description: Scaffold generator must consume Decision Log only.
- Source: derived
- Verification: Run scaffold command and verify generated files include decision traces.
- Citations: src-decision-seed, src-sample-seed
