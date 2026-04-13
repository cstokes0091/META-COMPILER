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


def test_sources_yaml_covers_citations() -> None:
    sources_path = SCAFFOLD_ROOT / 'references' / 'SOURCES.yaml'
    if not sources_path.exists():
        return  # Only applicable for report/hybrid
    text = sources_path.read_text(encoding='utf-8')
    for cid in ['src-decision-seed', 'src-sample-seed']:
        assert cid in text, f'SOURCES.yaml missing citation {{cid}}'

