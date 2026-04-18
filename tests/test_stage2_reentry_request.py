"""Tests for --from-request flag + brief/precheck_request emission."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from meta_compiler.stages.stage2_reentry import run_stage2_reentry


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_v1_workspace(tmp_path):
    """Create minimal v1 fixture: manifest + decision_log_v1.yaml + PROBLEM_STATEMENT.md."""
    ws = tmp_path
    artifacts = ws / "workspace-artifacts"
    (artifacts / "manifests").mkdir(parents=True)
    (artifacts / "decision-logs").mkdir(parents=True)
    (artifacts / "runtime" / "stage2").mkdir(parents=True)
    ps = ws / "PROBLEM_STATEMENT.md"
    ps_content = "Problem v1\n"
    ps.write_text(ps_content)
    (artifacts / "manifests" / "workspace_manifest.yaml").write_text(
        "workspace_manifest:\n"
        "  project_name: test\n"
        "  problem_domain: x\n"
        "  project_type: research\n"
        "  research:\n"
        "    last_completed_stage: '2'\n"
        "  decision_logs: []\n"
        "  seeds: {}\n"
        "  wiki: {}\n"
    )
    (artifacts / "decision-logs" / "decision_log_v1.yaml").write_text(
        "decision_log:\n"
        "  meta:\n"
        "    version: 1\n"
        "  architecture:\n"
        "    - component: old-comp\n"
        "      approach: x\n"
        "  conventions: []\n"
        "  scope: {in_scope: [], out_of_scope: []}\n"
        "  requirements: []\n"
        "  open_items: []\n"
        "  agents_needed: []\n"
    )
    return ws, artifacts, _sha(ps_content)


def _write_request(ws, prev_sha, cur_sha, updated=False, reason="scope changed",
                   revised_sections=None):
    revised = revised_sections or ["architecture"]
    path = ws / "workspace-artifacts" / "runtime" / "stage2" / "reentry_request.yaml"
    path.write_text(
        "stage2_reentry_request:\n"
        "  parent_version: 1\n"
        f"  problem_change_summary: 'summary'\n"
        "  problem_statement:\n"
        f"    previously_ingested_sha256: {prev_sha}\n"
        f"    current_sha256: {cur_sha}\n"
        f"    updated: {'true' if updated else 'false'}\n"
        f"    update_rationale: rationale\n"
        f"  reason: '{reason}'\n"
        "  revised_sections:\n"
        + "".join(f"    - {s}\n" for s in revised)
        + "  carried_consistency_risks: []\n"
    )
    return path


def test_from_request_derives_reason_and_sections(tmp_path):
    ws, artifacts, sha = _make_v1_workspace(tmp_path)
    req = _write_request(ws, sha, sha, reason="derived-reason", revised_sections=["architecture"])
    result = run_stage2_reentry(
        artifacts_root=artifacts,
        workspace_root=ws,
        reason=None,
        sections=None,
        from_request=req,
    )
    assert result["sections_to_revise"] == ["architecture"]
    assert "derived-reason" in result.get("reason", "derived-reason")


def test_conflict_between_request_and_flags_errors(tmp_path):
    ws, artifacts, sha = _make_v1_workspace(tmp_path)
    req = _write_request(ws, sha, sha)
    with pytest.raises(RuntimeError):
        run_stage2_reentry(
            artifacts_root=artifacts,
            workspace_root=ws,
            reason="conflict",
            sections=["conventions"],  # conflicts with request's [architecture]
            from_request=req,
        )


def test_brief_and_precheck_request_written(tmp_path):
    ws, artifacts, sha = _make_v1_workspace(tmp_path)
    req = _write_request(ws, sha, sha)
    run_stage2_reentry(
        artifacts_root=artifacts,
        workspace_root=ws,
        reason=None,
        sections=None,
        from_request=req,
    )
    brief = artifacts / "runtime" / "stage2" / "brief.md"
    precheck = artifacts / "runtime" / "stage2" / "precheck_request.yaml"
    assert brief.exists()
    assert "Re-entry context" in brief.read_text()
    assert precheck.exists()
    precheck_data = yaml.safe_load(precheck.read_text())
    assert "reentry" in precheck_data.get("stage2_precheck_request", {})


def test_old_flag_signature_still_works(tmp_path):
    ws, artifacts, sha = _make_v1_workspace(tmp_path)
    result = run_stage2_reentry(
        artifacts_root=artifacts,
        workspace_root=ws,
        reason="legacy",
        sections=["architecture"],
    )
    assert result["sections_to_revise"] == ["architecture"]
