"""Tests for meta_compiler.stages.workspace_bootstrap_stage.

Exercises:
- missing palette agent -> RuntimeError
- EXECUTION_MANIFEST.yaml new shape (no orchestrator_path)
- DISPATCH_HINTS.yaml replaces AGENT_REGISTRY.yaml
- SCAFFOLD_MANIFEST.yaml new shape
- scaffold_subdirs_for controls empty output buckets
- one verification/*.py stub per verification_hook_id
- REQ_TRACE.yaml maps REQ-NNN -> capability -> hooks
- workspace manifest advanced to stage 3
- provenance refreshed with capability counts
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from meta_compiler.project_types import scaffold_subdirs_for
from meta_compiler.stages.capability_compile_stage import run_capability_compile
from meta_compiler.stages.contract_extract_stage import run_contract_extract
from meta_compiler.stages.skill_synthesis_stage import run_skill_synthesis
from meta_compiler.stages.workspace_bootstrap_stage import (
    PALETTE_AGENTS,
    run_workspace_bootstrap,
)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def _write_palette(ws_root: Path) -> None:
    agents_dir = ws_root / ".github" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for name in PALETTE_AGENTS:
        (agents_dir / f"{name}.agent.md").write_text(
            f"---\nname: {name}\ndescription: test\ntools: [read]\nagents: []\nuser-invocable: false\n---\n# {name}\n",
            encoding="utf-8",
        )


def _setup_fixture(tmp_path: Path) -> Path:
    """Build a workspace with findings + stages 3.1-3.3 pre-run."""
    ws_root = tmp_path
    artifacts = ws_root / "workspace-artifacts"
    _write(
        artifacts / "manifests" / "workspace_manifest.yaml",
        {
            "workspace_manifest": {
                "project_name": "Test",
                "project_type": "hybrid",
                "wiki_name": "Test Project Atlas",
                "problem_domain": "testing",
                "use_case": "unit-test",
                "research": {
                    "last_completed_stage": "2",
                    "problem_statement_hash": "abc",
                    "wiki_version": "xyz",
                },
            }
        },
    )
    _write(
        artifacts / "wiki" / "citations" / "index.yaml",
        {
            "citations_index": {
                "citations": {
                    "src-decision-seed": {
                        "human": "Decision seed",
                        "source": {"type": "document", "path": "seeds/decision-seed.md"},
                        "metadata": {"title": "seed"},
                        "status": "tracked",
                    }
                }
            }
        },
    )
    _write(
        artifacts / "decision-logs" / "decision_log_v1.yaml",
        {
            "decision_log": {
                "meta": {
                    "project_name": "Test",
                    "project_type": "hybrid",
                    "created": "2026-04-22T00:00:00+00:00",
                    "version": 1,
                    "parent_version": None,
                    "reason_for_revision": None,
                    "problem_statement_hash": "abc",
                    "wiki_version": "xyz",
                    "use_case": "unit-test",
                },
                "conventions": [],
                "architecture": [
                    {
                        "component": "workflow-orchestrator",
                        "approach": "Artifact-driven stage transitions",
                        "alternatives_rejected": [{"name": "chat", "reason": "coupled"}],
                        "constraints_applied": ["fresh context"],
                        "citations": ["src-decision-seed"],
                    }
                ],
                "scope": {
                    "in_scope": [{"item": "decision capture", "rationale": "needed"}],
                    "out_of_scope": [],
                },
                "requirements": [
                    {
                        "id": "REQ-001",
                        "description": "Decision log must be schema-valid and citation-traceable.",
                        "source": "derived",
                        "citations": ["src-decision-seed"],
                        "verification": "Run validate-stage --stage 2.",
                    }
                ],
                "open_items": [],
                "agents_needed": [
                    {
                        "role": "scaffold-generator",
                        "responsibility": "generate scaffold",
                        "inputs": [{"name": "decision_log", "modality": "document"}],
                        "outputs": [{"name": "scaffold", "modality": "document"}],
                        "key_constraints": ["trace instructions"],
                    }
                ],
                "code_architecture": [
                    {"aspect": "language", "choice": "Python 3.11", "rationale": "runtime", "citations": ["src-decision-seed"]},
                    {
                        "aspect": "libraries",
                        "choice": "stdlib",
                        "rationale": "deterministic",
                        "citations": ["src-decision-seed"],
                        "libraries": [{"name": "PyYAML", "version": ">=6.0", "citation": "src-decision-seed", "description": "YAML parsing"}],
                    },
                ],
            }
        },
    )
    # Minimal finding
    payload = {
        "citation_id": "src-decision-seed",
        "seed_path": "seeds/decision-seed.md",
        "file_hash": "seedhashAseedhashA",
        "extracted_at": "2026-04-22T00:00:00+00:00",
        "extractor": "test",
        "document_metadata": {"title": "seed"},
        "concepts": [{"name": "Decision Log Schema"}, {"name": "Workflow Orchestrator"}],
        "quotes": [],
        "equations": [],
        "claims": [{"statement": "Every requirement has a citation.", "locator": {"page": 1}}],
        "tables_figures": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {},
    }
    finding_path = artifacts / "wiki" / "findings" / "src-decision-seed.json"
    finding_path.parent.mkdir(parents=True, exist_ok=True)
    finding_path.write_text(json.dumps(payload), encoding="utf-8")

    # Prereq stages
    run_capability_compile(artifacts)
    run_contract_extract(artifacts)
    run_skill_synthesis(artifacts)
    return artifacts


def test_bootstrap_happy_path(tmp_path):
    ws_root = tmp_path
    artifacts = _setup_fixture(ws_root)
    _write_palette(ws_root)

    result = run_workspace_bootstrap(artifacts, workspace_root=ws_root)

    scaffold_root = artifacts / "scaffolds" / "v1"
    assert (scaffold_root / "SCAFFOLD_MANIFEST.yaml").exists()
    assert (scaffold_root / "EXECUTION_MANIFEST.yaml").exists()
    assert (scaffold_root / "DISPATCH_HINTS.yaml").exists()
    assert (scaffold_root / "verification" / "REQ_TRACE.yaml").exists()

    em = yaml.safe_load((scaffold_root / "EXECUTION_MANIFEST.yaml").read_text(encoding="utf-8"))
    assert em["execution"]["project_type"] == "hybrid"
    assert em["execution"]["capabilities_path"] == "capabilities.yaml"
    assert "orchestrator_path" not in em["execution"]  # legacy field removed

    dh = yaml.safe_load((scaffold_root / "DISPATCH_HINTS.yaml").read_text(encoding="utf-8"))
    assert dh["dispatch_hints"]["agent_palette"] == list(PALETTE_AGENTS)
    assert dh["dispatch_hints"]["dispatch_policy"] == "capability-keyed"

    sm = yaml.safe_load((scaffold_root / "SCAFFOLD_MANIFEST.yaml").read_text(encoding="utf-8"))
    assert sm["scaffold"]["agent_palette"] == list(PALETTE_AGENTS)
    assert sm["scaffold"]["capability_count"] >= 1
    assert sm["scaffold"]["root"].endswith("scaffolds/v1")

    assert result["verification_hook_count"] >= 1


def test_bootstrap_missing_palette_raises(tmp_path):
    ws_root = tmp_path
    artifacts = _setup_fixture(ws_root)
    # No palette written.
    with pytest.raises(RuntimeError, match="palette not found"):
        run_workspace_bootstrap(artifacts, workspace_root=ws_root)


def test_bootstrap_output_buckets_match_project_type(tmp_path):
    ws_root = tmp_path
    artifacts = _setup_fixture(ws_root)
    _write_palette(ws_root)
    run_workspace_bootstrap(artifacts, workspace_root=ws_root)

    scaffold_root = artifacts / "scaffolds" / "v1"
    expected = scaffold_subdirs_for("hybrid")
    assert expected == frozenset({"code", "tests", "report", "references"})
    for name in expected:
        target = scaffold_root / name
        assert target.is_dir(), f"expected bucket missing: {name}"
    # Workflow-only buckets should NOT be created for hybrid.
    assert not (scaffold_root / "inbox").exists()


def test_bootstrap_verification_stubs_per_hook(tmp_path):
    ws_root = tmp_path
    artifacts = _setup_fixture(ws_root)
    _write_palette(ws_root)
    result = run_workspace_bootstrap(artifacts, workspace_root=ws_root)

    verification_dir = artifacts / "scaffolds" / "v1" / "verification"
    stubs = list(verification_dir.glob("ver-*.py"))
    assert stubs, "no verification stubs written"
    # Stubs are importable
    for stub in stubs:
        code = stub.read_text(encoding="utf-8")
        assert "pytest.xfail" in code
        assert "CAPABILITY" in code
        assert "CONTRACT" in code


def test_bootstrap_req_trace_maps_requirements(tmp_path):
    ws_root = tmp_path
    artifacts = _setup_fixture(ws_root)
    _write_palette(ws_root)
    run_workspace_bootstrap(artifacts, workspace_root=ws_root)

    trace_path = artifacts / "scaffolds" / "v1" / "verification" / "REQ_TRACE.yaml"
    trace = yaml.safe_load(trace_path.read_text(encoding="utf-8"))
    req_ids = [e["requirement_id"] for e in trace["requirement_trace"]["entries"]]
    assert "REQ-001" in req_ids


def test_bootstrap_advances_workspace_manifest_stage(tmp_path):
    ws_root = tmp_path
    artifacts = _setup_fixture(ws_root)
    _write_palette(ws_root)
    run_workspace_bootstrap(artifacts, workspace_root=ws_root)

    manifest_path = artifacts / "manifests" / "workspace_manifest.yaml"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert payload["workspace_manifest"]["research"]["last_completed_stage"] == "3"


def test_bootstrap_refreshes_provenance(tmp_path):
    ws_root = tmp_path
    artifacts = _setup_fixture(ws_root)
    _write_palette(ws_root)
    run_workspace_bootstrap(artifacts, workspace_root=ws_root)

    provenance = (artifacts / "wiki" / "provenance" / "what_i_built.md").read_text(encoding="utf-8")
    assert "Decision Log v1" in provenance
    assert "Capabilities compiled:" in provenance
    assert "Project type: `hybrid`" in provenance


def test_scaffold_subdirs_for_algorithm(tmp_path):
    assert scaffold_subdirs_for("algorithm") == frozenset({"code", "tests"})


def test_scaffold_subdirs_for_report(tmp_path):
    assert scaffold_subdirs_for("report") == frozenset({"report", "references"})


def test_scaffold_subdirs_for_workflow(tmp_path):
    assert scaffold_subdirs_for("workflow") == frozenset({"inbox", "outbox", "state", "kb_brief", "tests"})


def test_scaffold_subdirs_for_unknown_returns_empty(tmp_path):
    assert scaffold_subdirs_for("unknown_type") == frozenset()
