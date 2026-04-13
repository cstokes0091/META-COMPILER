# Stage 2 Re-entry Context

## Revision: v2
**Reason:** testing cascade
**Sections to revise:** architecture, requirements

## Cascade Analysis
- Changing 'architecture' may invalidate 'scope' decisions.
- Changing 'architecture' may invalidate 'requirements' decisions.
- Changing 'architecture' may invalidate 'agents_needed' decisions.
- Changing 'requirements' may invalidate 'agents_needed' decisions.
- **Also review:** agents_needed, scope

## Prior Architecture
  1. {'component': 'workflow-orchestrator', 'approach': 'Artifact-driven stage transitions with strict schema checks', 'alternatives_rejected': [{'name': 'chat-history-coupled flow', 'reason': 'Violates fresh-context constraint.'}], 'constraints_applied': ['fresh context', 'artifact-only handoff', 'strict validation'], 'citations': ['src-decision-seed', 'src-sample-seed']}

## Prior Requirements
  1. {'id': 'REQ-001', 'description': 'Decision log must be schema-valid and citation-traceable.', 'source': 'derived', 'citations': ['src-decision-seed', 'src-sample-seed'], 'verification': 'Run validate-stage --stage 2 with zero issues.'}
  2. {'id': 'REQ-002', 'description': 'Scaffold generator must consume Decision Log only.', 'source': 'derived', 'citations': ['src-decision-seed', 'src-sample-seed'], 'verification': 'Run scaffold command and verify generated files include decision traces.'}

## Wiki Resources
Use wiki tool interface to query for alternatives not previously considered.

### Open Questions from Wiki
- [concept-sample] Should this concept remain in scope after Stage 1B depth checks?
- [decision-seed] What additional extraction is required for this seed?
- [gap-remediation-v2] GAP-001: Wiki contains orphan pages with no meaningful inbound or outbound links
- [gap-remediation-v2] GAP-002: Expected topic 'criteria' from problem statement has weak or missing coverage
- [gap-remediation-v2] GAP-003: Expected topic 'define' from problem statement has weak or missing coverage
- [gap-remediation-v2] GAP-004: Expected topic 'measurable' from problem statement has weak or missing coverage
- [gap-remediation-v2] GAP-005: Expected topic 'outcomes' from problem statement has weak or missing coverage
- [gap-remediation-v2] GAP-006: Expected topic 'problem' from problem statement has weak or missing coverage
- [gap-remediation-v2] GAP-007: Expected topic 'projects' from problem statement has weak or missing coverage
- [gap-remediation-v2] GAP-008: Expected topic 'scaffolding' from problem statement has weak or missing coverage

## Instructions for Claude Code

1. Read this context and the prior Decision Log
2. For each section marked for revision, conduct asymmetric dialog:
   - Present the prior decision and why it was made
   - Query wiki for alternatives not previously considered
   - Ask the user targeted questions to narrow the revised choice
   - Capture the new decision with rationale and citations
3. For unchanged sections, note 'Retained from v{N}'
4. Check cascade analysis and confirm affected downstream sections
5. Save the completed Decision Log and run validation:
   meta-compiler validate-stage --stage 2
