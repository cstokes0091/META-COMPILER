"""Verify Stage 4 lockstep with the Commit 8 scaffold rewrite + Change E.

`run_phase4_start` reads DISPATCH_HINTS.yaml (not AGENT_REGISTRY.yaml) and
produces capability-keyed assignments. Change E removed the
`assigned_agent: "planner"` field — there is no Stage 4 planner agent any
more; planning lives upstream in Stage 2.5 and `run_phase4_start` writes
per-capability `_dispatch.yaml` files alongside `dispatch_plan.yaml`. The
legacy orchestrator subprocess fallback in run_phase4_finalize is gone.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from meta_compiler.stages.phase4_stage import run_phase4_finalize, run_phase4_start
from meta_compiler.stages.scaffold_stage import run_scaffold
from meta_compiler.stages.workspace_bootstrap_stage import PALETTE_AGENTS


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


def _seed_and_scaffold(tmp_path: Path) -> tuple[Path, Path]:
    ws_root = tmp_path
    artifacts = ws_root / "workspace-artifacts"
    _write(ws_root / "PROBLEM_STATEMENT.md", "# Stub\n")
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
                        "human": "seed",
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
    payload = {
        "citation_id": "src-decision-seed",
        "seed_path": "seeds/decision-seed.md",
        "file_hash": "seedhashAseedhashA",
        "extracted_at": "2026-04-22T00:00:00+00:00",
        "extractor": "test",
        "document_metadata": {"title": "seed"},
        "concepts": [{"name": "Decision Log Schema"}, {"name": "Workflow Orchestrator"}],
        "quotes": [
            {"text": "Decision logs must be schema-valid and citation-traceable.", "locator": {"page": 1}}
        ],
        "equations": [],
        "claims": [{"statement": "Every requirement has a citation.", "locator": {"page": 2}}],
        "tables_figures": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {},
    }
    finding_path = artifacts / "wiki" / "findings" / "src-decision-seed.json"
    finding_path.parent.mkdir(parents=True, exist_ok=True)
    finding_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_palette(ws_root)
    run_scaffold(artifacts)
    return ws_root, artifacts


def test_phase4_start_reads_new_dispatch_hints(tmp_path):
    ws_root, artifacts = _seed_and_scaffold(tmp_path)
    result = run_phase4_start(artifacts_root=artifacts, workspace_root=ws_root)

    assert result["status"] == "ready_for_orchestrator"
    assert result["capability_count"] >= 1

    dispatch_plan_path = artifacts / "executions" / "v1" / "dispatch_plan.yaml"
    assert dispatch_plan_path.exists()
    plan = yaml.safe_load(dispatch_plan_path.read_text(encoding="utf-8"))["dispatch_plan"]

    for assignment in plan["assignments"]:
        # Change E: assigned_agent: "planner" was dropped — Stage 4 has no
        # planner agent. Routing is deterministic in the orchestrator.
        assert "assigned_agent" not in assignment
        # expected_work_dir uses executions/v1/work/<capability_id>/ naming.
        assert assignment["expected_work_dir"].startswith("executions/v1/work/")
        assert "capability" in assignment
        # Per-cap _dispatch.yaml is the new denormalized routing target.
        assert assignment["dispatch_path"].endswith("_dispatch.yaml")
        assert (artifacts / assignment["dispatch_path"]).exists()


def test_phase4_finalize_requires_work_or_manifest(tmp_path):
    """The legacy orchestrator/run_stage4.py subprocess fallback was removed.
    When both the work dir and the pre-written manifest are absent, finalize
    must raise a clear error pointing at the LLM conductor flow, NOT hunt for
    a run_stage4.py file."""
    ws_root, artifacts = _seed_and_scaffold(tmp_path)
    with pytest.raises(RuntimeError, match="work/ is empty"):
        run_phase4_finalize(artifacts_root=artifacts, workspace_root=ws_root)
    # Double-check: the old orchestrator script is not even expected anymore.
    scaffold_root = artifacts / "scaffolds" / "v1"
    assert not (scaffold_root / "orchestrator" / "run_stage4.py").exists()


def test_phase4_start_fans_out_one_entry_per_capability(tmp_path):
    ws_root, artifacts = _seed_and_scaffold(tmp_path)
    run_phase4_start(artifacts_root=artifacts, workspace_root=ws_root)

    cap_count = len(
        yaml.safe_load(
            (artifacts / "scaffolds" / "v1" / "capabilities.yaml").read_text(encoding="utf-8")
        )["capability_graph"]["capabilities"]
    )
    plan = yaml.safe_load(
        (artifacts / "executions" / "v1" / "dispatch_plan.yaml").read_text(encoding="utf-8")
    )["dispatch_plan"]
    assert len(plan["assignments"]) == cap_count
