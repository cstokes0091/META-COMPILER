# REQ_TRACE_MATRIX

| Requirement | Description | Verification | Citations |
| --- | --- | --- | --- |
| REQ-001 | Decision log must be schema-valid and citation-traceable. | Run validate-stage --stage 2 with zero issues. | src-decision-seed, src-sample-seed |
| REQ-002 | Scaffold generator must consume Decision Log only. | Run scaffold command and verify generated files include decision traces. | src-decision-seed, src-sample-seed |
