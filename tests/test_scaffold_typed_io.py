"""Tests for Stage 3 scaffold rendering of typed agent inputs/outputs and the
new CODE_ARCHITECTURE.md doc.

Stage 3 consumes the Decision Log only. Under the v2 schema:
  - Every agent's inputs/outputs is a list of {name, modality} dicts.
  - For algorithm/hybrid projects the Decision Log carries a top-level
    code_architecture section that scaffold renders into CODE_ARCHITECTURE.md
    and into each agent's Decision Trace block.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from meta_compiler.artifacts import build_paths, ensure_layout, save_manifest
from meta_compiler.io import dump_yaml
from meta_compiler.stages.scaffold_stage import run_scaffold


def _seed_workspace_with_v2_log(tmp_path: Path, *, project_type: str) -> tuple[Path, Path]:
    workspace_root = tmp_path / "ws"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    save_manifest(
        paths,
        {
            "workspace_manifest": {
                "name": "Scaffold Typed IO Test",
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
                "decision_logs": [],
                "executions": [],
                "pitches": [],
                "status": "researched",
                "research": {"iteration_count": 0, "last_completed_stage": "2"},
            }
        },
    )

    log = {
        "decision_log": {
            "meta": {
                "project_name": "Scaffold Typed IO Test",
                "project_type": project_type,
                "created": "2026-04-21T00:00:00+00:00",
                "version": 1,
                "parent_version": None,
                "reason_for_revision": None,
                "problem_statement_hash": "abc",
                "wiki_version": "w0",
                "use_case": "test",
            },
            "conventions": [],
            "architecture": [
                {
                    "component": "core-pipeline",
                    "approach": "modular",
                    "alternatives_rejected": [],
                    "constraints_applied": ["stateless components"],
                    "citations": [],
                    "rationale": "test",
                }
            ],
            "scope": {"in_scope": [], "out_of_scope": []},
            "requirements": [
                {
                    "id": "REQ-001",
                    "description": "test req",
                    "source": "derived",
                    "citations": [],
                    "verification": "smoke test",
                }
            ],
            "open_items": [],
            "agents_needed": [
                {
                    "role": "custom-implementer",
                    "responsibility": "Build the thing",
                    "inputs": [
                        {"name": "decision_log", "modality": "document"},
                        {"name": "code", "modality": "code"},
                    ],
                    "outputs": [
                        {"name": "code", "modality": "code"},
                        {"name": "docs", "modality": "document"},
                    ],
                    "key_constraints": ["test-constraint"],
                    "rationale": "test",
                    "citations": [],
                }
            ],
        }
    }
    if project_type in {"algorithm", "hybrid"}:
        log["decision_log"]["code_architecture"] = [
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
                "choice": "pyyaml + numpy",
                "libraries": [
                    {"name": "pyyaml", "description": "YAML I/O (>=6.0)"},
                    {"name": "numpy", "description": "math (>=1.26)"},
                ],
                "alternatives_rejected": [],
                "constraints_applied": [],
                "citations": [],
                "rationale": "test",
            },
        ]
    log_path = paths.decision_logs_dir / "decision_log_v1.yaml"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    dump_yaml(log_path, log)

    return workspace_root, artifacts_root


def test_scaffold_renders_modality_in_agent_specs_for_hybrid(tmp_path):
    _, artifacts_root = _seed_workspace_with_v2_log(
        tmp_path, project_type="hybrid"
    )
    result = run_scaffold(artifacts_root=artifacts_root)
    assert result["agent_count"] >= 1

    scaffold_root = artifacts_root / "scaffolds" / "v1"
    impl_md = (scaffold_root / "agents" / "custom-implementer.md").read_text(
        encoding="utf-8"
    )
    assert "## Inputs" in impl_md
    assert "- decision_log (modality: document)" in impl_md
    assert "- code (modality: code)" in impl_md
    assert "- docs (modality: document)" in impl_md

    impl_agent = (
        scaffold_root / ".github" / "agents" / "custom-implementer.agent.md"
    ).read_text(encoding="utf-8")
    assert "decision_log (modality: document)" in impl_agent
    assert "code (modality: code)" in impl_agent

    # CODE_ARCHITECTURE.md exists for hybrid projects.
    code_arch_doc = (scaffold_root / "CODE_ARCHITECTURE.md").read_text(
        encoding="utf-8"
    )
    assert "## language" in code_arch_doc
    assert "Python 3.11" in code_arch_doc
    assert "## libraries" in code_arch_doc
    assert "pyyaml: YAML I/O (>=6.0)" in code_arch_doc

    # AGENT_REGISTRY uses the typed shape too.
    registry = yaml.safe_load(
        (scaffold_root / "AGENT_REGISTRY.yaml").read_text(encoding="utf-8")
    )
    impl_entry = next(
        e for e in registry["agent_registry"]["entries"] if e["slug"] == "custom-implementer"
    )
    assert impl_entry["inputs"] == [
        {"name": "decision_log", "modality": "document"},
        {"name": "code", "modality": "code"},
    ]
    assert {(o["name"], o["modality"]) for o in impl_entry["outputs"]} == {
        ("code", "code"),
        ("docs", "document"),
    }
    assert impl_entry["output_kind"] == "code"


def test_scaffold_omits_code_architecture_doc_for_report(tmp_path):
    _, artifacts_root = _seed_workspace_with_v2_log(
        tmp_path, project_type="report"
    )
    # report payload requires document outputs only — fix the fixture's agent.
    log_path = artifacts_root / "decision-logs" / "decision_log_v1.yaml"
    log = yaml.safe_load(log_path.read_text(encoding="utf-8"))
    log["decision_log"]["agents_needed"][0]["outputs"] = [
        {"name": "report", "modality": "document"},
    ]
    log["decision_log"]["agents_needed"][0]["inputs"] = [
        {"name": "decision_log", "modality": "document"},
    ]
    dump_yaml(log_path, log)

    result = run_scaffold(artifacts_root=artifacts_root)
    assert result["agent_count"] >= 1

    scaffold_root = artifacts_root / "scaffolds" / "v1"
    assert not (scaffold_root / "CODE_ARCHITECTURE.md").exists()
