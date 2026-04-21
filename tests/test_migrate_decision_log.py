"""Tests for `meta-compiler migrate-decision-log` --plan and --apply.

These exercise the schema-migration path: a v1 Decision Log with legacy
untyped reads/writes (plus, for algorithm/hybrid, missing code_architecture)
becomes a v2 log with typed inputs/outputs and a code_architecture section.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meta_compiler.artifacts import build_paths, ensure_layout, save_manifest
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.migrate_decision_log_stage import (
    run_migrate_decision_log_apply,
    run_migrate_decision_log_plan,
)
from meta_compiler.validation import validate_decision_log


def _seed_workspace(
    tmp_path: Path,
    *,
    project_type: str,
    include_code_architecture: bool = False,
) -> tuple[Path, Path]:
    workspace_root = tmp_path / "ws"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    (workspace_root / "PROBLEM_STATEMENT.md").write_text(
        "# PROBLEM_STATEMENT\n\n## Domain and Problem Space\nMigration test.\n",
        encoding="utf-8",
    )

    manifest = {
        "workspace_manifest": {
            "name": "Migration Test",
            "created": "2026-04-21T00:00:00+00:00",
            "last_modified": "2026-04-21T00:00:00+00:00",
            "problem_domain": "testing",
            "project_type": project_type,
            "seeds": {"version": "v0", "last_updated": "2026-04-21T00:00:00+00:00", "document_count": 0},
            "wiki": {
                "version": "w0",
                "last_updated": "2026-04-21T00:00:00+00:00",
                "page_count": 0,
                "name": "Test Atlas",
            },
            "decision_logs": [{"version": 1, "created": "2026-04-21T00:00:00+00:00",
                                "parent_version": None, "reason_for_revision": None,
                                "use_case": "test", "scaffold_path": None}],
            "executions": [],
            "pitches": [],
            "status": "researched",
            "research": {"iteration_count": 0, "last_completed_stage": "2"},
        }
    }
    save_manifest(paths, manifest)

    legacy_log = {
        "decision_log": {
            "meta": {
                "project_name": "Migration Test",
                "project_type": project_type,
                "created": "2026-04-21T00:00:00+00:00",
                "version": 1,
                "parent_version": None,
                "reason_for_revision": None,
                "problem_statement_hash": "abc",
                "wiki_version": "w0",
            },
            "conventions": [],
            "architecture": [
                {
                    "component": "core",
                    "approach": "monolith",
                    "alternatives_rejected": [],
                    "constraints_applied": ["simplicity"],
                    "citations": [],
                }
            ],
            "scope": {"in_scope": [], "out_of_scope": []},
            "requirements": [],
            "open_items": [],
            "agents_needed": [
                {
                    "role": "scaffold-generator",
                    "responsibility": "Generate scaffold from decision log",
                    # legacy untyped lists (the migration target)
                    "reads": ["decision_log"],
                    "writes": ["scaffold", "agents", "docs"],
                    "key_constraints": ["input is decision_log only"],
                    "rationale": "deterministic transform",
                    "citations": [],
                }
            ],
        }
    }
    if include_code_architecture:
        legacy_log["decision_log"]["code_architecture"] = [
            {
                "aspect": "language",
                "choice": "Python 3.11",
                "alternatives_rejected": [],
                "constraints_applied": [],
                "citations": [],
                "rationale": "test",
            },
            {
                "aspect": "libraries",
                "choice": "pyyaml",
                "libraries": [{"name": "pyyaml", "description": "YAML I/O (>=6.0)"}],
                "alternatives_rejected": [],
                "constraints_applied": [],
                "citations": [],
                "rationale": "test",
            },
        ]
    log_path = paths.decision_logs_dir / "decision_log_v1.yaml"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    dump_yaml(log_path, legacy_log)

    return workspace_root, artifacts_root


def test_plan_writes_proposal_with_typed_io_and_unresolved_artifacts(tmp_path):
    workspace_root, artifacts_root = _seed_workspace(
        tmp_path, project_type="hybrid"
    )

    result = run_migrate_decision_log_plan(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )

    assert result["status"] == "proposal_written"
    assert result["parent_version"] == 1
    assert result["new_version"] == 2

    proposal_path = artifacts_root / "runtime" / "migration" / "proposal.yaml"
    assert proposal_path.exists()
    proposal = load_yaml(proposal_path)
    body = proposal["decision_log_migration_proposal"]
    agents = body["agents_needed"]
    assert agents[0]["role"] == "scaffold-generator"
    assert agents[0]["inputs"] == [{"name": "decision_log", "modality": "document"}]
    # `scaffold` is in the default-code bucket; `agents`/`docs` default to document.
    out_names = {entry["name"]: entry["modality"] for entry in agents[0]["outputs"]}
    assert out_names == {"scaffold": "code", "agents": "document", "docs": "document"}

    # Code-architecture is required for hybrid; the parent has none, so a
    # blocks skeleton was seeded.
    assert body["needs_code_architecture"] is True
    blocks_path = artifacts_root / "runtime" / "migration" / "code_architecture_blocks.md"
    assert blocks_path.exists()


def test_apply_round_trips_to_v2_schema_for_hybrid(tmp_path):
    workspace_root, artifacts_root = _seed_workspace(
        tmp_path, project_type="hybrid"
    )
    run_migrate_decision_log_plan(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )

    # Author code-architecture blocks before --apply.
    blocks_path = artifacts_root / "runtime" / "migration" / "code_architecture_blocks.md"
    blocks_path.write_text(
        (
            "## Decision Area: Code Architecture\n\n"
            "### Decision: language-choice\n"
            "- Section: code-architecture\n"
            "- Aspect: language\n"
            "- Choice: Python 3.11\n"
            "- Rationale: matches workspace toolchain\n"
            "- Citations: (none)\n\n"
            "### Decision: libraries-choice\n"
            "- Section: code-architecture\n"
            "- Aspect: libraries\n"
            "- Choice: pyyaml\n"
            "- Libraries:\n"
            "  - pyyaml: YAML I/O (>=6.0)\n"
            "- Rationale: stable\n"
            "- Citations: (none)\n"
        ),
        encoding="utf-8",
    )

    result = run_migrate_decision_log_apply(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert result["status"] == "migrated"
    assert result["new_version"] == 2

    new_log_path = artifacts_root / "decision-logs" / "decision_log_v2.yaml"
    assert new_log_path.exists()
    payload = load_yaml(new_log_path)
    assert validate_decision_log(payload) == []
    root = payload["decision_log"]
    assert root["meta"]["parent_version"] == 1
    assert "schema migration" in root["meta"]["reason_for_revision"]
    assert len(root["code_architecture"]) == 2
    agent = root["agents_needed"][0]
    assert agent["inputs"] == [{"name": "decision_log", "modality": "document"}]
    out_modalities = {e["name"]: e["modality"] for e in agent["outputs"]}
    assert out_modalities == {"scaffold": "code", "agents": "document", "docs": "document"}


def test_apply_carries_forward_existing_code_architecture(tmp_path):
    workspace_root, artifacts_root = _seed_workspace(
        tmp_path, project_type="algorithm", include_code_architecture=True
    )
    run_migrate_decision_log_plan(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    # No need to author code-architecture blocks — the parent already has them.
    result = run_migrate_decision_log_apply(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert result["status"] == "migrated"
    payload = load_yaml(artifacts_root / "decision-logs" / "decision_log_v2.yaml")
    assert validate_decision_log(payload) == []
    assert len(payload["decision_log"]["code_architecture"]) == 2


def test_apply_for_report_forces_document_outputs(tmp_path):
    workspace_root, artifacts_root = _seed_workspace(
        tmp_path, project_type="report"
    )
    run_migrate_decision_log_plan(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    result = run_migrate_decision_log_apply(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert result["status"] == "migrated"
    payload = load_yaml(artifacts_root / "decision-logs" / "decision_log_v2.yaml")
    assert validate_decision_log(payload) == []
    root = payload["decision_log"]
    assert "code_architecture" not in root
    for agent in root["agents_needed"]:
        for out in agent["outputs"]:
            assert out["modality"] == "document"


def test_apply_blocks_when_proposal_missing(tmp_path):
    _, artifacts_root = _seed_workspace(tmp_path, project_type="hybrid")
    workspace_root = artifacts_root.parent
    with pytest.raises(RuntimeError) as excinfo:
        run_migrate_decision_log_apply(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )
    assert "Migration proposal not found" in str(excinfo.value)


def test_apply_for_hybrid_blocks_when_code_architecture_blocks_missing(tmp_path):
    workspace_root, artifacts_root = _seed_workspace(
        tmp_path, project_type="hybrid"
    )
    run_migrate_decision_log_plan(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    # Delete the seeded blocks file so --apply has nothing to parse.
    blocks_path = artifacts_root / "runtime" / "migration" / "code_architecture_blocks.md"
    blocks_path.unlink()

    with pytest.raises(RuntimeError) as excinfo:
        run_migrate_decision_log_apply(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )
    msg = str(excinfo.value)
    assert "code_architecture_blocks.md" in msg
