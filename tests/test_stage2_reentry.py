"""Tests for stage2-reentry under the prompt-as-conductor flow.

Re-entry now produces a seeded transcript (not a partial YAML template).
Carried-forward decisions appear as decision blocks; revised sections are
left empty with prior decisions shown as prose.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meta_compiler.artifacts import build_paths
from meta_compiler.stages.elicit_stage import (
    parse_decision_blocks,
    run_elicit_vision_finalize,
    run_elicit_vision_start,
)
from meta_compiler.stages.stage2_reentry import (
    run_finalize_reentry,
    run_stage2_reentry,
)
from tests.test_elicit_vision_finalize import FULL_TRANSCRIPT_ALL_SECTIONS
from tests.test_elicit_vision_start import _seed_workspace


def _produce_v1_decision_log(tmp_path: Path) -> tuple[Path, Path]:
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    paths.stage2_transcript_path.write_text(
        FULL_TRANSCRIPT_ALL_SECTIONS, encoding="utf-8"
    )
    run_elicit_vision_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    return workspace_root, artifacts_root


# ---------------------------------------------------------------------------
# Seeded-transcript behavior
# ---------------------------------------------------------------------------


def test_reentry_seeds_transcript_and_preserves_unchanged_sections(tmp_path):
    workspace_root, artifacts_root = _produce_v1_decision_log(tmp_path)
    paths = build_paths(artifacts_root)

    result = run_stage2_reentry(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        reason="scope expanded to include polarimetric modeling",
        sections=["scope", "requirements"],
    )

    assert result["status"] == "transcript_seeded"
    assert result["new_version"] == 2
    assert result["parent_version"] == 1
    assert paths.stage2_transcript_path.exists()

    text = paths.stage2_transcript_path.read_text(encoding="utf-8")
    assert "re-entry from v1" in text
    assert "scope expanded to include polarimetric modeling" in text

    # Carried-forward sections (not in revised set) appear as decision blocks.
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    by_section = {b.section: 0 for b in blocks}
    for b in blocks:
        by_section[b.section] = by_section.get(b.section, 0) + 1
    assert by_section.get("conventions", 0) == 1
    assert by_section.get("architecture", 0) == 1
    assert by_section.get("open_items", 0) == 1
    assert by_section.get("agents_needed", 0) == 1

    # Revised sections (scope, requirements) should have no blocks — they are
    # waiting for the dialog to re-author them.
    assert "scope-in" not in by_section
    assert "scope-out" not in by_section
    assert "requirements" not in by_section

    # Prior decisions show up as prose for the revised sections.
    assert "Prior in-scope item" in text
    assert "Prior requirement" in text


def test_reentry_cascade_report_is_written(tmp_path):
    workspace_root, artifacts_root = _produce_v1_decision_log(tmp_path)
    paths = build_paths(artifacts_root)

    run_stage2_reentry(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        reason="convention update",
        sections=["conventions"],
    )

    cascade_path = paths.stage2_runtime_dir / "cascade_report_v2.yaml"
    assert cascade_path.exists()
    with cascade_path.open("r", encoding="utf-8") as handle:
        cascade = yaml.safe_load(handle)
    root = cascade["cascade_report"]
    assert root["parent_version"] == 1
    assert root["new_version"] == 2
    assert "architecture" in root["affected_downstream"]


def test_reentry_rejects_invalid_section(tmp_path):
    workspace_root, artifacts_root = _produce_v1_decision_log(tmp_path)

    with pytest.raises(RuntimeError) as excinfo:
        run_stage2_reentry(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            reason="whatever",
            sections=["not_a_real_section"],
        )
    assert "Invalid sections" in str(excinfo.value)


def test_reentry_rejects_empty_sections(tmp_path):
    workspace_root, artifacts_root = _produce_v1_decision_log(tmp_path)

    with pytest.raises(RuntimeError) as excinfo:
        run_stage2_reentry(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            reason="whatever",
            sections=[],
        )
    assert "at least one section" in str(excinfo.value)


def test_reentry_fails_when_no_prior_decision_log(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"

    with pytest.raises(RuntimeError) as excinfo:
        run_stage2_reentry(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            reason="test",
            sections=["conventions"],
        )
    assert "No existing Decision Log" in str(excinfo.value)


# ---------------------------------------------------------------------------
# End-to-end reentry → dialog → finalize
# ---------------------------------------------------------------------------


def test_reentry_to_finalize_produces_v2_with_parent_version(tmp_path):
    workspace_root, artifacts_root = _produce_v1_decision_log(tmp_path)
    paths = build_paths(artifacts_root)

    run_stage2_reentry(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        reason="revise scope",
        sections=["scope"],
    )

    # Simulate the LLM writing replacement scope blocks after the dialog.
    existing = paths.stage2_transcript_path.read_text(encoding="utf-8")
    amended = (
        existing
        + "\n"
        "### Decision: Revised in-scope item\n"
        "- Section: scope-in\n"
        "- Item: Updated scope coverage\n"
        "- Rationale: reflects scope expansion from revision\n"
        "- Citations: src-test\n"
        "\n"
        "### Decision: Revised out-of-scope item\n"
        "- Section: scope-out\n"
        "- Item: Legacy UI\n"
        "- Rationale: no longer in scope after revision\n"
        "- Revisit if: legacy users escalate\n"
        "- Citations: src-test\n"
    )
    paths.stage2_transcript_path.write_text(amended, encoding="utf-8")

    result = run_finalize_reentry(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert result["status"] == "compiled"
    assert result["decision_log_version"] == 2

    with (paths.decision_logs_dir / "decision_log_v2.yaml").open(
        "r", encoding="utf-8"
    ) as handle:
        v2 = yaml.safe_load(handle)
    meta = v2["decision_log"]["meta"]
    assert meta["version"] == 2
    assert meta["parent_version"] == 1
    assert meta["reason_for_revision"] is not None
    # Revised scope replaces the v1 scope; carried-forward sections survive.
    assert len(v2["decision_log"]["scope"]["in_scope"]) == 1
    assert v2["decision_log"]["scope"]["in_scope"][0]["item"] == "Updated scope coverage"
    assert len(v2["decision_log"]["conventions"]) == 1  # carried
    assert len(v2["decision_log"]["architecture"]) == 1  # carried


def test_finalize_fails_when_revised_section_has_no_fresh_block(tmp_path):
    """After stage2-reentry seeds the transcript, running --finalize without
    authoring new blocks under revised sections raises RuntimeError."""
    workspace_root, artifacts_root = _produce_v1_decision_log(tmp_path)

    run_stage2_reentry(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        reason="revise scope without doing the dialog work",
        sections=["scope"],
    )

    # Do NOT amend the transcript. The seeded transcript for a revised
    # section is empty of blocks, so finalize must refuse to compile v2.
    with pytest.raises(RuntimeError) as exc:
        run_finalize_reentry(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )
    assert "scope" in str(exc.value)
    assert "fresh" in str(exc.value).lower() or "no fresh" in str(exc.value).lower()


def test_finalize_reentry_clears_reentry_state_on_success(tmp_path):
    """After a successful re-entry finalize, manifest.research goes back to
    last_completed_stage='2' and reentry_version is cleared."""
    workspace_root, artifacts_root = _produce_v1_decision_log(tmp_path)
    paths = build_paths(artifacts_root)

    run_stage2_reentry(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        reason="revise scope",
        sections=["scope"],
    )
    existing = paths.stage2_transcript_path.read_text(encoding="utf-8")
    amended = (
        existing
        + "\n"
        "### Decision: Revised in-scope item\n"
        "- Section: scope-in\n"
        "- Item: Brand-new item\n"
        "- Rationale: fresh\n"
        "- Citations: src-test\n"
    )
    paths.stage2_transcript_path.write_text(amended, encoding="utf-8")

    run_finalize_reentry(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )

    manifest = yaml.safe_load(paths.manifest_path.read_text(encoding="utf-8"))
    research = manifest["workspace_manifest"]["research"]
    assert research.get("last_completed_stage") == "2"
    assert "reentry_version" not in research or research["reentry_version"] is None


def test_finalize_reentry_rejects_version_mismatch(tmp_path):
    workspace_root, artifacts_root = _produce_v1_decision_log(tmp_path)
    paths = build_paths(artifacts_root)

    run_stage2_reentry(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        reason="test",
        sections=["conventions"],
    )
    # Write at least one new decision block so --finalize doesn't error out
    # on "no blocks" before it reaches the version check.
    existing = paths.stage2_transcript_path.read_text(encoding="utf-8")
    paths.stage2_transcript_path.write_text(
        existing
        + "\n"
        "### Decision: New convention\n"
        "- Section: conventions\n"
        "- Domain: code\n"
        "- Choice: updated\n"
        "- Rationale: test\n"
        "- Citations: (none)\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError) as excinfo:
        run_finalize_reentry(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            version=99,  # deliberately wrong
        )
    assert "version mismatch" in str(excinfo.value)
