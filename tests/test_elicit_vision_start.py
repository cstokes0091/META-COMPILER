"""Integration tests for `meta-compiler elicit-vision --start`.

These set up a minimal but realistic workspace (manifest, problem
statement, wiki v2 page, citation, gap report, Stage 1C handoff) and
assert that --start writes the expected artifacts.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meta_compiler.artifacts import build_paths, ensure_layout, save_manifest
from meta_compiler.stages.elicit_stage import run_elicit_vision_start


PROBLEM_STATEMENT = """\
# PROBLEM_STATEMENT

## Domain and Problem Space
A concrete project for testing the Stage 2 preflight.

## Goals and Success Criteria
Exercise the preflight checks end-to-end.

## Constraints
No external services; must run in a temp directory.

## Project Type
hybrid

## Additional Context
This problem statement is used only by the Stage 2 hardening test suite.
"""


def _seed_workspace(tmp_path: Path, *, handoff_decision: str = "PROCEED") -> Path:
    """Build a workspace that passes all mechanical preflight checks."""
    workspace_root = tmp_path / "ws"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    (workspace_root / "PROBLEM_STATEMENT.md").write_text(
        PROBLEM_STATEMENT, encoding="utf-8"
    )

    manifest = {
        "workspace_manifest": {
            "name": "Test Project",
            "created": "2026-04-17T00:00:00+00:00",
            "last_modified": "2026-04-17T00:00:00+00:00",
            "problem_domain": "testing",
            "project_type": "hybrid",
            "seeds": {
                "version": "deadbeef",
                "last_updated": "2026-04-17T00:00:00+00:00",
                "document_count": 0,
            },
            "wiki": {
                "version": "cafebabe",
                "last_updated": "2026-04-17T00:00:00+00:00",
                "page_count": 1,
                "name": "Test Atlas",
            },
            "decision_logs": [],
            "executions": [],
            "pitches": [],
            "status": "researched",
            "research": {
                "iteration_count": 0,
                "last_completed_stage": "1C",
            },
        }
    }
    save_manifest(paths, manifest)

    # Minimal wiki v2 page
    wiki_page = paths.wiki_v2_pages_dir / "concept-core.md"
    wiki_page.write_text(
        "---\n"
        "id: concept-core\n"
        "type: concept\n"
        "created: 2026-04-17T00:00:00+00:00\n"
        "sources: [src-test]\n"
        "related: []\n"
        "status: reviewed\n"
        "---\n"
        "# Core concept\n\n"
        "## Definition\nA test concept.\n\n"
        "## Key Claims\n- Core behavior is deterministic.\n\n"
        "## Relationships\n- prerequisite_for: []\n"
        "- depends_on: []\n- contradicts: []\n- extends: []\n\n"
        "## Open Questions\n- None at test time.\n\n"
        "## Source Notes\nTest-only.\n",
        encoding="utf-8",
    )

    # Citation index
    paths.citations_index_path.parent.mkdir(parents=True, exist_ok=True)
    with paths.citations_index_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {
                "citations": {
                    "src-test": {
                        "human": "Test source, §1",
                        "source": {"type": "seed", "path": "/seeds/test.md"},
                        "metadata": {"title": "Test", "file_hash": "x" * 64},
                        "status": "raw",
                    }
                }
            },
            handle,
            sort_keys=False,
        )

    # Gap report
    gap_report_path = paths.reports_dir / "merged_gap_report.yaml"
    with gap_report_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {
                "gap_report": {
                    "generated_at": "2026-04-17T00:00:00+00:00",
                    "gaps": [
                        {
                            "id": "GAP-001",
                            "description": "Sample structural gap",
                            "severity": "minor",
                            "type": "structural",
                            "affected_concepts": ["concept-core"],
                            "attribution": ["schema_auditor"],
                            "status": "unresolved",
                        }
                    ],
                    "unresolved_count": 1,
                    "health": {"orphan_pages": [], "sparse_citation_pages": []},
                }
            },
            handle,
            sort_keys=False,
        )

    # Stage 1C handoff
    handoff_path = paths.reviews_dir / "1a2_handoff.yaml"
    with handoff_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {
                "stage_1a2_handoff": {
                    "generated_at": "2026-04-17T00:00:00+00:00",
                    "decision": handoff_decision,
                    "reason": "unanimous_proceed" if handoff_decision == "PROCEED" else "insufficient_coverage",
                    "iteration_count": 0,
                    "unresolved_gap_count": 1,
                    "ready_for_stage_2": handoff_decision == "PROCEED",
                    "blocking_gaps": [],
                    "non_blocking_gaps": [],
                    "suggested_sources": [],
                    "next_action": "Proceed.",
                    "ready_signal": "",
                }
            },
            handle,
            sort_keys=False,
        )

    return workspace_root


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_start_happy_path_writes_all_artifacts(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"

    result = run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    paths = build_paths(artifacts_root)

    assert result["status"] == "ready_for_orchestrator"
    assert result["decision_log_version"] == 1
    assert paths.stage2_brief_path.exists()
    assert paths.stage2_transcript_path.exists()
    assert paths.stage2_precheck_request_path.exists()

    brief_text = paths.stage2_brief_path.read_text(encoding="utf-8")
    assert "Stage 2 Brief" in brief_text
    assert "src-test" in brief_text  # citation inventory
    assert "Decision block format" in brief_text

    transcript_text = paths.stage2_transcript_path.read_text(encoding="utf-8")
    # All six decision areas are stubbed (scope has two sub-areas).
    assert "## Decision Area: Conventions" in transcript_text
    assert "## Decision Area: Architecture" in transcript_text
    assert "## Decision Area: Scope (in)" in transcript_text
    assert "## Decision Area: Scope (out)" in transcript_text
    assert "## Decision Area: Requirements" in transcript_text
    assert "## Decision Area: Open Items" in transcript_text
    assert "## Decision Area: Agents Needed" in transcript_text

    with paths.stage2_precheck_request_path.open("r", encoding="utf-8") as handle:
        precheck = yaml.safe_load(handle)
    root = precheck["stage2_precheck_request"]
    assert root["decision_log_version"] == 1
    check_names = {c["name"] for c in root["mechanical_checks"]}
    assert check_names == {
        "manifest_present",
        "problem_statement_complete",
        "wiki_v2_populated",
        "citation_index_nonempty",
        "gap_report_present",
        "stage_1c_proceed",
    }
    # All PASS in the happy path.
    for check in root["mechanical_checks"]:
        assert check["result"] == "PASS", check


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_start_fails_when_problem_statement_is_template(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    # Overwrite problem statement with template markers.
    (workspace_root / "PROBLEM_STATEMENT.md").write_text(
        "# PROBLEM_STATEMENT\n\n"
        "## Domain and Problem Space\nstuff\n\n"
        "## Goals and Success Criteria\n"
        "Define the measurable outcomes that indicate project success.\n\n"
        "## Constraints\n"
        "List technical constraints, timeline constraints, and resource constraints.\n\n"
        "## Project Type\nhybrid\n\n"
        "## Additional Context\n"
        "Capture assumptions, prior work references, and any known risks.\n",
        encoding="utf-8",
    )
    artifacts_root = workspace_root / "workspace-artifacts"

    with pytest.raises(RuntimeError) as excinfo:
        run_elicit_vision_start(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )
    assert "problem statement incomplete" in str(excinfo.value)

    # precheck_request should still be written for the orchestrator to read.
    paths = build_paths(artifacts_root)
    assert paths.stage2_precheck_request_path.exists()


def test_start_fails_when_handoff_iterates_without_override(tmp_path):
    workspace_root = _seed_workspace(tmp_path, handoff_decision="ITERATE")
    artifacts_root = workspace_root / "workspace-artifacts"

    with pytest.raises(RuntimeError) as excinfo:
        run_elicit_vision_start(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )
    assert "ITERATE" in str(excinfo.value)


def test_start_with_override_iterate_records_reason_and_proceeds(tmp_path):
    workspace_root = _seed_workspace(tmp_path, handoff_decision="ITERATE")
    artifacts_root = workspace_root / "workspace-artifacts"

    result = run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        override_iterate_reason="Human override: proceeding to Stage 2 despite ITERATE.",
    )
    assert result["status"] == "ready_for_orchestrator"

    paths = build_paths(artifacts_root)
    with paths.stage2_precheck_request_path.open("r", encoding="utf-8") as handle:
        precheck = yaml.safe_load(handle)
    root = precheck["stage2_precheck_request"]
    handoff_check = next(c for c in root["mechanical_checks"] if c["name"] == "stage_1c_proceed")
    assert handoff_check["result"] == "WARN"
    assert root["override"]["iterate_override"].startswith("Human override")


# ---------------------------------------------------------------------------
# Re-run safety — transcript is not clobbered
# ---------------------------------------------------------------------------


def test_rerunning_start_preserves_transcript_content(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )

    # Simulate the LLM having appended a decision block mid-dialog.
    existing = paths.stage2_transcript_path.read_text(encoding="utf-8")
    amended = (
        existing
        + "\n"
        + "### Decision: Code style\n"
        "- Section: conventions\n"
        "- Domain: code\n"
        "- Choice: black-formatted, 4-space\n"
        "- Rationale: consistency\n"
        "- Citations: (none)\n"
    )
    paths.stage2_transcript_path.write_text(amended, encoding="utf-8")

    # Re-run --start. Brief.md may be rewritten but transcript.md stays.
    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert (
        "### Decision: Code style"
        in paths.stage2_transcript_path.read_text(encoding="utf-8")
    )
