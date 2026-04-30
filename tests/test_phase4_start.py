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
        # Change E: assigned_agent: "planner" was dropped; routing is now
        # deterministic (orchestrator runs implementer → reviewer always,
        # researcher only on knowledge_gap).
        assert "assigned_agent" not in a
        assert a["status"] == "pending"
        assert "expected_work_dir" in a
        # Per-cap _dispatch.yaml exists at the listed path.
        assert "dispatch_path" in a
        cap_dispatch = artifacts_root / a["dispatch_path"]
        assert cap_dispatch.exists(), f"missing per-cap dispatch: {cap_dispatch}"

    req = load_yaml(paths.phase4_execution_request_path)["phase4_execution_request"]
    assert req["decision_log_version"] == 1
    assert req["dispatch_plan_path"].endswith("dispatch_plan.yaml")
    assert req["work_dir"].endswith("work")
    assert req["verdict_output_path"].endswith("preflight_verdict.yaml")
    # Stage 4 planner is gone; the next_action message says so.
    assert "Stage 4 planner agent is gone" in req["next_action"]


def test_run_phase4_start_writes_per_cap_dispatch_yaml_with_v2_1_fields(tmp_path: Path):
    """Change E: each capability gets a `_dispatch.yaml` carrying its full
    v2.1 plan-extract field set (user_story, the_problem, the_fix,
    anti_patterns, out_of_scope, dispatch_kind, ...) so the implementer
    + reviewer have everything they need without a Stage 4 planner hop."""
    workspace_root, artifacts_root = _bootstrap_scaffold(tmp_path)

    # Write a capabilities.yaml with v2.1 fields populated for one cap.
    paths = build_paths(artifacts_root)
    capabilities_path = paths.scaffolds_dir / "v1" / "capabilities.yaml"
    dump_yaml(
        capabilities_path,
        {
            "capability_graph": {
                "generated_at": "2026-04-30T00:00:00Z",
                "decision_log_version": 1,
                "project_type": "hybrid",
                "capabilities": [
                    {
                        "name": "req-001-alpha",
                        "description": "alpha capability",
                        "io_contract_ref": "contract-alpha",
                        "verification_type": "behavioral",
                        "verification_hook_ids": ["ver-req-001-alpha-001"],
                        "requirement_ids": ["REQ-001"],
                        "constraint_ids": [],
                        "citation_ids": ["src-test"],
                        "when_to_use": ["alpha trigger"],
                        "required_finding_ids": ["src-test#abc"],
                        "verification_required": True,
                        "user_story": (
                            "As a user, I want alpha output, so that "
                            "downstream pipelines work."
                        ),
                        "the_problem": "Without alpha, downstream breaks.",
                        "the_fix": "Compile alpha rows.",
                        "anti_patterns": ["Do NOT silently skip rows"],
                        "out_of_scope": ["streaming"],
                        "deletion_test": (
                            "Without this, three callers reimplement alpha."
                        ),
                        "dispatch_kind": "afk",
                        "parallelizable": True,
                        "implementation_steps": ["step 1"],
                        "acceptance_criteria": ["alpha output non-empty"],
                        "explicit_triggers": ["alpha trigger"],
                        "evidence_refs": ["src-test"],
                    },
                    {
                        "name": "req-002-beta",
                        "description": "beta capability",
                        "io_contract_ref": "contract-beta",
                        "verification_type": "behavioral",
                        "verification_hook_ids": ["ver-req-002-beta-001"],
                        "requirement_ids": ["REQ-002"],
                        "constraint_ids": [],
                        "citation_ids": ["src-test"],
                        "when_to_use": ["beta trigger"],
                        "required_finding_ids": ["src-test#abc"],
                        "verification_required": True,
                    },
                ],
            }
        },
    )

    run_phase4_start(artifacts_root=artifacts_root, workspace_root=workspace_root)

    alpha_dispatch = (
        artifacts_root / "executions" / "v1" / "work" / "req-001-alpha" / "_dispatch.yaml"
    )
    assert alpha_dispatch.exists()
    payload = load_yaml(alpha_dispatch)["dispatch"]
    assert payload["capability_id"] == "req-001-alpha"
    assert payload["user_story"].startswith("As a user")
    assert payload["the_problem"].startswith("Without alpha")
    assert payload["the_fix"] == "Compile alpha rows."
    assert payload["anti_patterns"] == ["Do NOT silently skip rows"]
    assert payload["out_of_scope"] == ["streaming"]
    assert payload["dispatch_kind"] == "afk"
    assert payload["parallelizable"] is True
    assert payload["deletion_test"].startswith("Without this")
    assert payload["verification_spec_paths"] == [
        "verification/ver-req-001-alpha-001_spec.yaml"
    ]
    assert payload["implementation_steps"] == ["step 1"]
    assert payload["acceptance_criteria"] == ["alpha output non-empty"]

    # Cap that did not have v2.1 fields populated still gets a dispatch
    # file (the orchestrator handles missing fields by escalating).
    beta_dispatch = (
        artifacts_root / "executions" / "v1" / "work" / "req-002-beta" / "_dispatch.yaml"
    )
    assert beta_dispatch.exists()
    beta_payload = load_yaml(beta_dispatch)["dispatch"]
    assert beta_payload["capability_id"] == "req-002-beta"
    assert beta_payload["user_story"] is None  # not set in capabilities.yaml
    assert beta_payload["anti_patterns"] == []
    assert beta_payload["dispatch_kind"] == "afk"
    assert beta_payload["parallelizable"] is False


def test_run_phase4_start_rejects_invalid_dispatch_metadata(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap_scaffold(
        tmp_path,
        capability_assignments=[
            {
                "capability": "req-001-alpha",
                "skill_path": "skills/req-001-alpha/SKILL.md",
                "contract_ref": "contract-alpha",
                "dispatch_kind": "robot",
                "parallelizable": False,
            }
        ],
    )

    with pytest.raises(RuntimeError, match="Invalid dispatch_kind"):
        run_phase4_start(artifacts_root=artifacts_root, workspace_root=workspace_root)


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
