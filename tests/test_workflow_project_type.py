"""Tests for the workflow project type.

Post-Commit-8: the scaffold no longer produces a hardcoded roster of
workflow-named agents (workflow-conductor, comment-reader, etc.). Instead
the capability graph drives execution via the static palette. This file
covers:

- meta-init accepts --project-type workflow
- decision_log.workflow_config is required for workflow projects
- scaffold emits workflow-specific empty output buckets
  (inbox/outbox/state/kb_brief/tests) via scaffold_subdirs_for
- scaffold does NOT emit domain-named agent files under scaffolds/v*/agents/
  or scaffolds/v*/.github/agents/ (capability-driven == palette-only)

The legacy `run_workflow` CLI command + its orchestrator/run_workflow.py
runner are out of scope for the post-dialogue rearchitecture; those tests
were removed along with the old-shape scaffold helpers in Commit 8.
"""
from __future__ import annotations

from pathlib import Path

import json
import pytest
import yaml

from meta_compiler.artifacts import build_paths, ensure_layout, save_manifest
from meta_compiler.project_types import (
    VALID_PROJECT_TYPES,
    project_type_choices,
    scaffold_subdirs_for,
)
from meta_compiler.stages.init_stage import run_meta_init
from meta_compiler.stages.capability_compile_stage import run_capability_compile
from meta_compiler.stages.contract_extract_stage import run_contract_extract
from meta_compiler.stages.skill_synthesis_stage import run_skill_synthesis
from meta_compiler.stages.workspace_bootstrap_stage import (
    PALETTE_AGENTS,
    run_workspace_bootstrap,
)
from meta_compiler.validation import validate_decision_log


def test_workflow_in_valid_project_types():
    assert "workflow" in VALID_PROJECT_TYPES
    assert "workflow" in project_type_choices()


def test_meta_init_accepts_workflow(tmp_path):
    workspace_root = tmp_path / "ws"
    artifacts_root = workspace_root / "workspace-artifacts"
    run_meta_init(
        workspace_root=workspace_root,
        artifacts_root=artifacts_root,
        project_name="Reviewer Replies",
        problem_domain="academic peer review",
        project_type="workflow",
    )
    paths = build_paths(artifacts_root)
    manifest = yaml.safe_load(paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["workspace_manifest"]["project_type"] == "workflow"
    from meta_compiler.artifacts import derive_wiki_name
    assert derive_wiki_name("Reviewer Replies", "workflow").endswith("Workflow Atlas")


def test_meta_init_rejects_unknown_project_type(tmp_path):
    workspace_root = tmp_path / "ws"
    artifacts_root = workspace_root / "workspace-artifacts"
    with pytest.raises(ValueError) as excinfo:
        run_meta_init(
            workspace_root=workspace_root,
            artifacts_root=artifacts_root,
            project_name="bad",
            problem_domain="bad",
            project_type="bogus",
        )
    assert "workflow" in str(excinfo.value)


# --- Decision-log validation (schema gates Stage 2, not Stage 3) --------------


def _minimal_workflow_decision_log(with_workflow_config: bool = True) -> dict:
    body = {
        "decision_log": {
            "meta": {
                "version": 1,
                "parent_version": 0,
                "reason_for_revision": "initial",
                "created": "2026-04-21T00:00:00+00:00",
                "project_name": "Reviewer Replies",
                "project_type": "workflow",
                "problem_statement_hash": "x" * 64,
                "wiki_version": "abc",
            },
            "conventions": [],
            "architecture": [],
            "scope": {"in_scope": [], "out_of_scope": []},
            "requirements": [],
            "open_items": [],
            "agents_needed": [],
        }
    }
    if with_workflow_config:
        body["decision_log"]["workflow_config"] = {
            "trigger": "inbox_watch",
            "inputs": [{"kind": "tracked_doc", "locator": "inbox/*.docx"}],
            "outputs": [
                {"kind": "tracked_edit", "target": "inbox/*.docx"},
                {"kind": "comment_reply", "target": "inline"},
            ],
            "state_keys": ["processed_comment_ids"],
            "escalation_policy": {"on_missing_kb_evidence": "pause_for_human"},
        }
    return body


def test_decision_log_requires_workflow_config_for_workflow_type():
    issues = validate_decision_log(_minimal_workflow_decision_log(with_workflow_config=False))
    assert any("workflow_config" in issue for issue in issues), issues


def test_decision_log_accepts_full_workflow_config():
    issues = validate_decision_log(_minimal_workflow_decision_log())
    assert all("workflow_config" not in i for i in issues), issues


def test_decision_log_rejects_invalid_trigger():
    body = _minimal_workflow_decision_log()
    body["decision_log"]["workflow_config"]["trigger"] = "fairy_dust"
    issues = validate_decision_log(body)
    assert any("trigger" in issue for issue in issues), issues


def test_decision_log_rejects_workflow_config_for_other_types():
    body = _minimal_workflow_decision_log()
    body["decision_log"]["meta"]["project_type"] = "report"
    issues = validate_decision_log(body)
    assert any("workflow_config" in issue for issue in issues), issues


# --- Scaffold layout (capability-driven shape) ---------------------------------


def test_scaffold_subdirs_for_workflow_is_workflow_layout():
    subdirs = scaffold_subdirs_for("workflow")
    assert subdirs == frozenset({"inbox", "outbox", "state", "kb_brief", "tests"})


def _seed_workflow_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Build a workflow workspace with enough content to drive compile →
    extract → synth → bootstrap through to success."""
    workspace_root = tmp_path
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    save_manifest(
        paths,
        {
            "workspace_manifest": {
                "project_type": "workflow",
                "project_name": "Reviewer Replies",
                "problem_domain": "peer review",
                "use_case": "unit-test",
                "wiki": {"name": "Reviewer Replies Workflow Atlas", "version": "x"},
                "research": {"last_completed_stage": "2"},
            }
        },
    )
    # Citations index
    citations = {
        "citations_index": {
            "citations": {
                "src-workflow-seed": {
                    "human": "Workflow seed",
                    "source": {"type": "document", "path": "seeds/workflow-seed.md"},
                    "metadata": {"title": "seed"},
                    "status": "tracked",
                }
            }
        }
    }
    paths.citations_index_path.parent.mkdir(parents=True, exist_ok=True)
    paths.citations_index_path.write_text(yaml.safe_dump(citations, sort_keys=False), encoding="utf-8")

    body = _minimal_workflow_decision_log()
    body["decision_log"]["requirements"] = [
        {
            "id": "REQ-001",
            "description": "Respond to every reviewer comment with a cited reply.",
            "source": "derived",
            "citations": ["src-workflow-seed"],
            "verification": "Every comment has a reply traced to a citation.",
        }
    ]
    body["decision_log"]["agents_needed"] = [
        {
            "role": "tracked-edit-writer",
            "responsibility": "write tracked edits against the reviewer comment",
            "inputs": [{"name": "comment_reply", "modality": "document"}],
            "outputs": [{"name": "tracked_doc", "modality": "document"}],
            "key_constraints": ["cite every reply"],
        }
    ]
    paths.decision_logs_dir.mkdir(parents=True, exist_ok=True)
    (paths.decision_logs_dir / "decision_log_v1.yaml").write_text(
        yaml.safe_dump(body, sort_keys=False), encoding="utf-8"
    )
    # Palette at workspace root so bootstrap passes.
    agents_dir = workspace_root / ".github" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for name in PALETTE_AGENTS:
        (agents_dir / f"{name}.agent.md").write_text(
            f"---\nname: {name}\ndescription: test\ntools: [read]\nagents: []\nuser-invocable: false\n---\n# {name}\n",
            encoding="utf-8",
        )
    return workspace_root, artifacts_root


def test_scaffold_workflow_emits_workflow_buckets(tmp_path):
    workspace_root, artifacts_root = _seed_workflow_workspace(tmp_path)
    run_capability_compile(artifacts_root)
    run_contract_extract(artifacts_root)
    run_skill_synthesis(artifacts_root)
    run_workspace_bootstrap(artifacts_root, workspace_root=workspace_root)

    scaffold_root = artifacts_root / "scaffolds" / "v1"
    for bucket in ("inbox", "outbox", "state", "kb_brief", "tests"):
        assert (scaffold_root / bucket).is_dir(), f"workflow bucket missing: {bucket}"


def test_scaffold_workflow_omits_domain_agent_files(tmp_path):
    """No domain-named agents are produced — we rely on the 4-agent palette."""
    workspace_root, artifacts_root = _seed_workflow_workspace(tmp_path)
    run_capability_compile(artifacts_root)
    run_contract_extract(artifacts_root)
    run_skill_synthesis(artifacts_root)
    run_workspace_bootstrap(artifacts_root, workspace_root=workspace_root)

    scaffold_root = artifacts_root / "scaffolds" / "v1"
    # Old-shape directories should not be created by the new pipeline.
    assert not (scaffold_root / "agents").exists()
    assert not (scaffold_root / ".github").exists()
    # No orchestrator/run_workflow.py under the new shape.
    legacy_runner = scaffold_root / "orchestrator" / "run_workflow.py"
    assert not legacy_runner.exists()


def test_scaffold_workflow_palette_not_inline(tmp_path):
    """The palette agents live at repo/.github/agents — not embedded in the scaffold."""
    workspace_root, artifacts_root = _seed_workflow_workspace(tmp_path)
    run_capability_compile(artifacts_root)
    run_contract_extract(artifacts_root)
    run_skill_synthesis(artifacts_root)
    run_workspace_bootstrap(artifacts_root, workspace_root=workspace_root)

    # Palette agents are at the workspace root, not per-scaffold.
    for name in PALETTE_AGENTS:
        assert (workspace_root / ".github" / "agents" / f"{name}.agent.md").exists()
