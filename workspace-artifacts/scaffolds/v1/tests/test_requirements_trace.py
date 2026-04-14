"""Semantic self-tests for scaffold integrity.

These tests verify that the scaffold's generated artifacts maintain
traceability and consistency with the Decision Log.
"""
import re
from pathlib import Path

SCAFFOLD_ROOT = Path(__file__).resolve().parents[1]


def test_requirement_ids_present_in_code() -> None:
    code_path = SCAFFOLD_ROOT / 'code' / 'main.py'
    text = code_path.read_text(encoding='utf-8')
    assert 'REQUIREMENT_IDS' in text
    for req_id in ['REQ-001', 'REQ-002']:
        assert req_id in text, f'Missing requirement {{req_id}} in main.py'


def test_citation_ids_present_in_code() -> None:
    code_path = SCAFFOLD_ROOT / 'code' / 'main.py'
    text = code_path.read_text(encoding='utf-8')
    assert 'CITATION_IDS' in text


def test_agent_specs_embed_decisions() -> None:
    agents_dir = SCAFFOLD_ROOT / 'agents'
    agent_specs = list(agents_dir.glob('*.md'))
    assert len(agent_specs) >= 1, 'No agent specs found'
    for spec in agent_specs:
        text = spec.read_text(encoding='utf-8')
        assert '## Decisions Embedded' in text, f'{{spec.name}} missing decisions'
        assert '## Requirement Trace' in text, f'{{spec.name}} missing req trace'


def test_custom_agent_files_have_frontmatter() -> None:
    agents_dir = SCAFFOLD_ROOT / '.github' / 'agents'
    agent_specs = list(agents_dir.glob('*.agent.md'))
    assert len(agent_specs) >= 1, 'No custom agent files found'
    for spec in agent_specs:
        text = spec.read_text(encoding='utf-8')
        assert text.startswith('---\n'), f'{{spec.name}} missing frontmatter'
        assert 'description:' in text, f'{{spec.name}} missing description frontmatter'
        assert 'agent' in text, f'{{spec.name}} missing agent tool support'
        assert 'explore' in text, f'{{spec.name}} missing explore allowlist entry'
        assert 'research' in text, f'{{spec.name}} missing research allowlist entry'
        assert '## Decision Trace' in text, f'{{spec.name}} missing decision trace'


def test_custom_skills_exist() -> None:
    skill_files = list((SCAFFOLD_ROOT / '.github' / 'skills').glob('*/SKILL.md'))
    assert len(skill_files) >= 1, 'No custom skill files found'
    for skill in skill_files:
        text = skill.read_text(encoding='utf-8')
        assert text.startswith('---\n'), f'{{skill.parent.name}} missing frontmatter'
        assert 'description:' in text, f'{{skill.parent.name}} missing description frontmatter'


def test_custom_instructions_exist() -> None:
    instruction_files = list((SCAFFOLD_ROOT / '.github' / 'instructions').glob('*.instructions.md'))
    assert len(instruction_files) >= 2, 'Too few custom instruction files found'
    for instruction in instruction_files:
        text = instruction.read_text(encoding='utf-8')
        assert text.startswith('---\n'), f'{{instruction.name}} missing frontmatter'
        assert 'description:' in text, f'{{instruction.name}} missing description frontmatter'


def test_requirements_traced_covers_all_ids() -> None:
    req_path = SCAFFOLD_ROOT / 'REQUIREMENTS_TRACED.md'
    text = req_path.read_text(encoding='utf-8')
    for req_id in ['REQ-001', 'REQ-002']:
        assert req_id in text, f'REQUIREMENTS_TRACED.md missing {{req_id}}'


def test_conventions_doc_exists_and_nonempty() -> None:
    conv_path = SCAFFOLD_ROOT / 'CONVENTIONS.md'
    text = conv_path.read_text(encoding='utf-8')
    assert len(text.strip()) > 20, 'CONVENTIONS.md is essentially empty'


def test_trace_matrix_covers_requirements() -> None:
    matrix_path = SCAFFOLD_ROOT / 'requirements' / 'REQ_TRACE_MATRIX.md'
    text = matrix_path.read_text(encoding='utf-8')
    assert '| Requirement |' in text, 'Missing table header'
    for req_id in ['REQ-001', 'REQ-002']:
        assert req_id in text, f'Trace matrix missing {{req_id}}'


def test_execution_contract_exists() -> None:
    assert (SCAFFOLD_ROOT / 'EXECUTION_MANIFEST.yaml').exists()
    assert (SCAFFOLD_ROOT / 'orchestrator' / 'run_stage4.py').exists()


def test_sources_yaml_covers_citations() -> None:
    sources_path = SCAFFOLD_ROOT / 'references' / 'SOURCES.yaml'
    if not sources_path.exists():
        return  # Only applicable for report/hybrid
    text = sources_path.read_text(encoding='utf-8')
    for cid in ['src-decision-seed', 'src-sample-seed']:
        assert cid in text, f'SOURCES.yaml missing citation {{cid}}'

