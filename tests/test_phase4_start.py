"""Phase B tests: phase4-finalize --start dispatch plan + execution request."""
from __future__ import annotations

from pathlib import Path

import pytest

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.phase4_stage import run_phase4_start


def _bootstrap_scaffold(
    tmp_path: Path,
    *,
    decision_log_version: int = 1,
    capability_assignments: list[dict] | None = None,
) -> tuple[Path, Path]:
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    # Decision log so latest_decision_log_path resolves.
    decision_log_path = (
        paths.decision_logs_dir / f"decision_log_v{decision_log_version}.yaml"
    )
    dump_yaml(
        decision_log_path,
        {"decision_log": {"version": decision_log_version, "decisions": {}}},
    )

    scaffold_root = paths.scaffolds_dir / f"v{decision_log_version}"
    scaffold_root.mkdir(parents=True, exist_ok=True)
    dump_yaml(
        scaffold_root / "EXECUTION_MANIFEST.yaml",
        {
            "execution": {
                "decision_log_version": decision_log_version,
                "project_type": "hybrid",
                "capabilities_path": "capabilities.yaml",
            }
        },
    )

    if capability_assignments is None:
        capability_assignments = [
            {
                "capability": "req-001-alpha",
                "skill_path": "skills/req-001-alpha/SKILL.md",
                "contract_ref": "contract-alpha",
                "verification_hook_ids": ["ver-req-001-alpha-001"],
                "expected_work_dir_relative": "work/req-001-alpha/",
            },
            {
                "capability": "req-002-beta",
                "skill_path": "skills/req-002-beta/SKILL.md",
                "contract_ref": "contract-beta",
                "verification_hook_ids": ["ver-req-002-beta-001"],
                "expected_work_dir_relative": "work/req-002-beta/",
            },
        ]
    dump_yaml(
        scaffold_root / "DISPATCH_HINTS.yaml",
        {
            "dispatch_hints": {
                "decision_log_version": decision_log_version,
                "project_type": "hybrid",
                "agent_palette": ["planner", "implementer", "reviewer", "researcher"],
                "skill_index_path": "skills/INDEX.md",
                "capabilities_path": "capabilities.yaml",
                "contracts_manifest_path": "contracts/_manifest.yaml",
                "verification_dir": "verification",
                "dispatch_policy": "capability-keyed",
                "assignments": capability_assignments,
            }
        },
    )

    return workspace_root, artifacts_root


def test_run_phase4_start_writes_dispatch_plan_and_request(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap_scaffold(tmp_path)

    result = run_phase4_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ready_for_orchestrator"
    assert result["decision_log_version"] == 1
    assert result["capability_count"] == 2

    paths = build_paths(artifacts_root)
    plan_path = paths.executions_dir / "v1" / "dispatch_plan.yaml"
    assert plan_path.exists()
    plan = load_yaml(plan_path)["dispatch_plan"]
    assert plan["decision_log_version"] == 1
    assert plan["project_type"] == "hybrid"
    assignments = plan["assignments"]
    assert {a["capability"] for a in assignments} == {"req-001-alpha", "req-002-beta"}
    for a in assignments:
        assert a["assigned_agent"] == "planner"
        assert a["status"] == "pending"
        assert "expected_work_dir" in a

    req = load_yaml(paths.phase4_execution_request_path)["phase4_execution_request"]
    assert req["decision_log_version"] == 1
    assert req["dispatch_plan_path"].endswith("dispatch_plan.yaml")
    assert req["work_dir"].endswith("work")
    assert req["verdict_output_path"].endswith("preflight_verdict.yaml")


def test_run_phase4_start_creates_work_dir(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap_scaffold(tmp_path)

    run_phase4_start(artifacts_root=artifacts_root, workspace_root=workspace_root)

    work_dir = artifacts_root / "executions" / "v1" / "work"
    assert work_dir.is_dir()


def test_run_phase4_start_handles_empty_dispatch_hints(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap_scaffold(
        tmp_path, capability_assignments=[]
    )

    result = run_phase4_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["capability_count"] == 0
    paths = build_paths(artifacts_root)
    plan = load_yaml(paths.executions_dir / "v1" / "dispatch_plan.yaml")["dispatch_plan"]
    assert plan["assignments"] == []


def test_run_phase4_start_raises_when_no_decision_log(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    with pytest.raises(RuntimeError, match="No decision log found"):
        run_phase4_start(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )


def test_run_phase4_start_raises_when_no_scaffold(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    decision_log_path = paths.decision_logs_dir / "decision_log_v1.yaml"
    dump_yaml(
        decision_log_path,
        {"decision_log": {"version": 1, "decisions": {}}},
    )

    with pytest.raises(RuntimeError, match="Scaffold root not found"):
        run_phase4_start(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            decision_log_version=1,
        )


def test_run_phase4_start_raises_on_missing_execution_manifest(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap_scaffold(tmp_path)
    # Remove the execution manifest after bootstrap.
    paths = build_paths(artifacts_root)
    (paths.scaffolds_dir / "v1" / "EXECUTION_MANIFEST.yaml").unlink()

    with pytest.raises(RuntimeError, match="Execution manifest missing"):
        run_phase4_start(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )
