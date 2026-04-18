"""Tests for _check_reentry_block_freshness in elicit_stage."""
from meta_compiler.stages.elicit_stage import _check_reentry_block_freshness


def _block(title, section):
    # Adjust attribute names to match the real DecisionBlock dataclass
    class _B:
        pass
    b = _B()
    b.title = title
    b.section = section
    return b


def test_fresh_block_in_every_revised_section_passes():
    parent = {
        "decision_log": {
            "architecture": [{"component": "old-comp", "approach": "x"}],
            "requirements": [{"description": "old req"}],
        }
    }
    cascade = {"cascade_report": {"revised_sections": ["architecture", "requirements"]}}
    blocks = [
        _block("new-comp", "architecture"),
        _block("REQ — new", "requirements"),
    ]
    issues = _check_reentry_block_freshness(blocks, cascade, parent)
    assert issues == []


def test_empty_revised_section_fails():
    parent = {
        "decision_log": {
            "architecture": [{"component": "old-comp"}],
        }
    }
    cascade = {"cascade_report": {"revised_sections": ["architecture"]}}
    blocks = [_block("old-comp", "architecture")]  # same title as parent — not fresh
    issues = _check_reentry_block_freshness(blocks, cascade, parent)
    assert len(issues) == 1
    assert "architecture" in issues[0]


def test_scope_revision_satisfied_by_scope_in_or_scope_out():
    parent = {"decision_log": {"scope": {"in_scope": [{"item": "old"}], "out_of_scope": []}}}
    cascade = {"cascade_report": {"revised_sections": ["scope"]}}
    blocks = [_block("new-item", "scope-in")]
    issues = _check_reentry_block_freshness(blocks, cascade, parent)
    assert issues == []


def test_mixed_fresh_and_stale_reports_only_stale():
    parent = {
        "decision_log": {
            "architecture": [{"component": "old-a"}],
            "conventions": [{"name": "old-c"}],
        }
    }
    cascade = {"cascade_report": {"revised_sections": ["architecture", "conventions"]}}
    blocks = [
        _block("new-a", "architecture"),
        _block("old-c", "conventions"),  # stale
    ]
    issues = _check_reentry_block_freshness(blocks, cascade, parent)
    assert len(issues) == 1
    assert "conventions" in issues[0]
