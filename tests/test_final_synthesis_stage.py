"""Tests for the Stage 4 final-synthesis sub-stage (preflight + postflight)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.final_synthesis_stage import (
    run_final_synthesize_finalize,
    run_final_synthesize_start,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _bootstrap(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    return workspace_root, artifacts_root, paths


def _seed_workspace(
    paths,
    *,
    project_type: str = "algorithm",
    project_name: str = "Demo Project",
    work_files: dict[str, str] | None = None,
    citations: dict[str, dict] | None = None,
    requirements: list[dict] | None = None,
) -> Path:
    """Set up the minimum on-disk state run_final_synthesize_start expects.

    Returns the scaffold root path.
    """
    work_files = work_files or {}
    citations = citations or {}
    requirements = requirements or []

    # Workspace manifest
    manifest = {
        "workspace_manifest": {
            "status": "scaffolded",
            "research": {
                "iteration_count": 0,
                "last_completed_stage": "3",
            },
            "seeds": {"version": "", "last_updated": "", "document_count": 0},
            "wiki": {"version": "", "last_updated": "", "page_count": 0, "name": ""},
            "decision_logs": [],
            "executions": [],
            "pitches": [],
            "pitch": {"template_path": ""},
        }
    }
    dump_yaml(paths.manifest_path, manifest)

    # Decision log v1
    decision_log = {
        "decision_log": {
            "meta": {
                "project_name": project_name,
                "project_type": project_type,
                "version": 1,
            },
            "requirements": requirements,
            "open_items": [],
            "scope": {"in_scope": [], "out_of_scope": []},
            "architecture": [],
            "code_architecture": [],
        }
    }
    dump_yaml(paths.decision_logs_dir / "decision_log_v1.yaml", decision_log)

    # Scaffold v1 with EXECUTION_MANIFEST + REQ_TRACE
    scaffold_root = paths.scaffolds_dir / "v1"
    (scaffold_root / "verification").mkdir(parents=True, exist_ok=True)
    dump_yaml(
        scaffold_root / "EXECUTION_MANIFEST.yaml",
        {"execution": {"project_type": project_type, "decision_log_version": 1}},
    )
    dump_yaml(
        scaffold_root / "verification" / "REQ_TRACE.yaml",
        {
            "req_trace": {
                "entries": [
                    {"req_id": req.get("id"), "capabilities": []}
                    for req in requirements
                ]
            }
        },
    )

    # Citations index
    if citations:
        paths.citations_index_path.parent.mkdir(parents=True, exist_ok=True)
        dump_yaml(
            paths.citations_index_path,
            {"citations": citations},
        )

    # Work files
    work_dir = paths.executions_dir / "v1" / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    for path_str, content in work_files.items():
        # path_str is "cap-001/main.py" form
        full = work_dir / path_str
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    return scaffold_root


# ---------------------------------------------------------------------------
# run_final_synthesize_start
# ---------------------------------------------------------------------------


def test_start_emits_work_plan_for_algorithm_project(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="algorithm",
        work_files={
            "cap-001/main.py": "def main():\n    return 0  # REQ-001\n",
            "cap-001/_plan.yaml": "plan: stuff",  # excluded as bucket file
        },
        requirements=[
            {"id": "REQ-001", "description": "It runs"},
        ],
    )

    result = run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ready_for_orchestrator"
    assert result["modality_keys"] == ["library"]
    assert result["fragment_count"] == 1  # _plan.yaml excluded
    assert result["fragments_per_modality"]["library"] == 1

    plan = load_yaml(paths.final_synthesis_work_plan_path)
    assert plan["final_synthesis_work_plan"]["project_type"] == "algorithm"
    library_slice = plan["final_synthesis_work_plan"]["modalities"]["library"]
    assert library_slice["expected_fragment_tokens"] == ["cap-001:main.py"]
    fragment = library_slice["fragments"][0]
    assert fragment["modality"] == "code"
    assert fragment["req_mentions"] == ["REQ-001"]


def test_start_branches_for_hybrid_into_two_modalities(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="hybrid",
        work_files={
            "cap-001/main.py": "def main(): pass\n",
            "cap-002/findings.md": "# Findings\nText.\n",
        },
    )

    result = run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ready_for_orchestrator"
    assert sorted(result["modality_keys"]) == ["document", "library"]
    assert result["fragments_per_modality"]["library"] == 1
    assert result["fragments_per_modality"]["document"] == 1


def test_start_branches_for_workflow(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="workflow",
        work_files={
            "cap-001/handler.py": "def handle(x): return x\n",
            "cap-001/sample.docx": "stub bytes",  # other modality is OK; app sees all
        },
    )

    result = run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ready_for_orchestrator"
    assert result["modality_keys"] == ["application"]
    plan = load_yaml(paths.final_synthesis_work_plan_path)
    expected_buckets = set(plan["final_synthesis_work_plan"]["workflow_buckets"])
    # Should include the workflow scaffold buckets + orchestrator.
    assert {"inbox", "outbox", "state", "kb_brief", "tests", "orchestrator"} <= expected_buckets


def test_start_excludes_capability_bucket_files(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="algorithm",
        work_files={
            "cap-001/main.py": "def main(): pass\n",
            "cap-001/_plan.yaml": "plan: ...",
            "cap-001/_verdict.yaml": "verdict: PROCEED",
            "cap-001/_manifest.yaml": "manifest: ...",
        },
    )

    result = run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert result["fragment_count"] == 1


def test_start_raises_when_work_dir_empty(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(paths, project_type="algorithm", work_files={})

    with pytest.raises(RuntimeError, match="empty"):
        run_final_synthesize_start(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )


def test_start_sets_manifest_to_synthesis_pending(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="algorithm",
        work_files={"cap-001/main.py": "def main(): pass\n"},
    )

    run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    manifest = load_yaml(paths.manifest_path)
    research = manifest["workspace_manifest"]["research"]
    assert research["last_completed_stage"] == "4-synthesis-pending"


# ---------------------------------------------------------------------------
# run_final_synthesize_finalize
# ---------------------------------------------------------------------------


def _well_formed_library_return(package_name: str = "demoproj") -> dict:
    return {
        "modality": "library",
        "package_name": package_name,
        "module_layout": [
            {
                "target_path": f"{package_name}/main.py",
                "sources": [{"capability": "cap-001", "relative_path": "main.py"}],
                "header_prose": '"""Module docstring."""\n',
                "footer_prose": "",
            }
        ],
        "exports": ["main"],
        "public_api": [
            {"symbol": "main", "summary": "entry", "source_capability": "cap-001"}
        ],
        "entry_points": [{"name": package_name, "target": f"{package_name}.main:main"}],
        "readme_sections": [
            {"heading": "Overview", "body": "Synthesized library."},
            {"heading": "Installation", "body": "pip install ."},
            {"heading": "Usage", "body": f"from {package_name} import main"},
            {"heading": "Capabilities", "body": "cap-001"},
        ],
        "deduplications_applied": [],
    }


def test_finalize_assembles_library_for_algorithm_project(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="algorithm",
        work_files={
            "cap-001/main.py": "def main():\n    return 0  # REQ-001\n",
        },
        requirements=[{"id": "REQ-001", "description": "runs"}],
    )

    run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    # Persist the library subagent return
    return_path = paths.final_synthesis_subagent_returns_dir / "library.json"
    return_path.parent.mkdir(parents=True, exist_ok=True)
    return_path.write_text(
        json.dumps(_well_formed_library_return("demoproj")),
        encoding="utf-8",
    )

    result = run_final_synthesize_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "synthesized"
    final_dir = paths.final_dir_for(1)
    assert final_dir.exists()
    main_path = final_dir / "library" / "demoproj" / "main.py"
    assert main_path.exists()
    body = main_path.read_text(encoding="utf-8")
    # Original REQ-NNN annotation must survive.
    assert "REQ-001" in body
    assert (final_dir / "library" / "README.md").exists()
    assert (final_dir / "library" / "demoproj" / "__init__.py").exists()

    report = load_yaml(paths.final_synthesis_report_path(1))
    assert report["final_synthesis_report"]["modality_keys"] == ["library"]
    assert report["final_synthesis_report"]["req_trace_diff"]["synthesis_drops"] == []

    manifest = load_yaml(paths.manifest_path)
    assert manifest["workspace_manifest"]["research"]["last_completed_stage"] == "4-synthesized"


def test_finalize_blocks_on_req_drop_without_override(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="algorithm",
        work_files={
            # Two REQs in fragments — synthesizer will only carry one through.
            "cap-001/main.py": "def main():\n    pass  # REQ-001\n",
            "cap-002/extra.py": "def extra():\n    pass  # REQ-007\n",
        },
        requirements=[
            {"id": "REQ-001", "description": "main"},
            {"id": "REQ-007", "description": "extra"},
        ],
    )

    run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    # Synthesizer drops cap-002/extra.py via deduplications_applied, losing REQ-007.
    payload = _well_formed_library_return("demoproj")
    payload["deduplications_applied"] = [
        {
            "kept": "cap-001:main.py",
            "dropped": ["cap-002:extra.py"],
            "reason": "experiment scoped out",
        }
    ]
    return_path = paths.final_synthesis_subagent_returns_dir / "library.json"
    return_path.parent.mkdir(parents=True, exist_ok=True)
    return_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="REQ-007"):
        run_final_synthesize_finalize(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )

    # No final/ tree created.
    assert not paths.final_dir_for(1).exists()


def test_finalize_allows_explicit_req_drop(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="algorithm",
        work_files={
            "cap-001/main.py": "def main():\n    pass  # REQ-001\n",
            "cap-002/extra.py": "def extra():\n    pass  # REQ-007\n",
        },
        requirements=[
            {"id": "REQ-001", "description": "main"},
            {"id": "REQ-007", "description": "extra"},
        ],
    )

    run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    payload = _well_formed_library_return("demoproj")
    payload["deduplications_applied"] = [
        {
            "kept": "cap-001:main.py",
            "dropped": ["cap-002:extra.py"],
            "reason": "experiment scoped out",
        }
    ]
    return_path = paths.final_synthesis_subagent_returns_dir / "library.json"
    return_path.parent.mkdir(parents=True, exist_ok=True)
    return_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_final_synthesize_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        allow_req_drop=("REQ-007",),
    )

    assert result["status"] == "synthesized"
    diff = result["req_trace_diff"]
    assert diff["allowed_drops"] == ["REQ-007"]
    assert diff["synthesis_drops"] == ["REQ-007"]


def test_finalize_validation_failure_leaves_final_dir_untouched(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="algorithm",
        work_files={"cap-001/main.py": "def main(): pass\n"},
    )

    run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    # Malformed: missing modality + bad package_name
    bad_payload = {
        "package_name": "Json",  # wrong case + collides with stdlib
        "module_layout": [],
        "exports": [],
        "readme_sections": [],
    }
    return_path = paths.final_synthesis_subagent_returns_dir / "library.json"
    return_path.parent.mkdir(parents=True, exist_ok=True)
    return_path.write_text(json.dumps(bad_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="failed validation"):
        run_final_synthesize_finalize(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )
    assert not paths.final_dir_for(1).exists()


def test_finalize_swap_atomic_after_success(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="algorithm",
        work_files={"cap-001/main.py": "def main(): pass\n"},
    )

    run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    return_path = paths.final_synthesis_subagent_returns_dir / "library.json"
    return_path.parent.mkdir(parents=True, exist_ok=True)
    return_path.write_text(
        json.dumps(_well_formed_library_return("demoproj")),
        encoding="utf-8",
    )

    run_final_synthesize_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    # No leftover .tmp dir.
    final_dir = paths.final_dir_for(1)
    tmp_dir = final_dir.with_suffix(".tmp")
    assert final_dir.exists()
    assert not tmp_dir.exists()


def test_finalize_missing_subagent_returns_raises(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="hybrid",  # needs library + document
        work_files={
            "cap-001/main.py": "def main(): pass\n",
            "cap-001/findings.md": "# x\n",
        },
    )

    run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    # Only library return persisted; document missing.
    return_path = paths.final_synthesis_subagent_returns_dir / "library.json"
    return_path.parent.mkdir(parents=True, exist_ok=True)
    return_path.write_text(
        json.dumps(_well_formed_library_return("demoproj")),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="document"):
        run_final_synthesize_finalize(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )


def test_finalize_assembles_document_for_report_project(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="report",
        work_files={
            "cap-001/findings.md": "# Findings\nClaim. [src-foo, p.3]\n",
        },
        citations={
            "src-foo": {"human": "Foo (2024). Title.", "source": {"type": "paper"}},
        },
    )

    run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    plan = load_yaml(paths.final_synthesis_work_plan_path)
    project_slug = plan["final_synthesis_work_plan"]["project_slug"]

    payload = {
        "modality": "document",
        "title": "Demo Project Report",
        "abstract": "A short abstract.",
        "section_order": [
            {
                "heading": "Background",
                "source": {"synthesizer_prose": "Background paragraph. [src-foo, p.1]"},
                "transitions_after": None,
                "citations_inline": ["src-foo"],
            },
            {
                "heading": "Findings",
                "source": {"capability": "cap-001", "file": "findings.md"},
                "transitions_after": None,
                "citations_inline": [],
            },
        ],
        "intro_prose": "Opening.",
        "conclusion_prose": "Closing.",
        "references_unified": [
            {"id": "src-foo", "human": "Foo (2024). Title."}
        ],
        "deduplications_applied": [],
    }
    return_path = paths.final_synthesis_subagent_returns_dir / "document.json"
    return_path.parent.mkdir(parents=True, exist_ok=True)
    return_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_final_synthesize_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert result["status"] == "synthesized"

    final_dir = paths.final_dir_for(1)
    md_path = final_dir / "document" / f"{project_slug}.md"
    assert md_path.exists()
    body = md_path.read_text(encoding="utf-8")
    assert "Demo Project Report" in body
    assert "[src-foo, p.3]" in body  # fragment body preserved
    assert (final_dir / "document" / "references.md").exists()


def test_finalize_assembles_application_for_workflow_project(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _seed_workspace(
        paths,
        project_type="workflow",
        work_files={
            "cap-001/handler.py": "def handle(x):\n    return x  # REQ-001\n",
            "cap-002/test_handler.py": "def test_handle(): assert True\n",
        },
        requirements=[{"id": "REQ-001", "description": "handler runs"}],
    )

    run_final_synthesize_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    payload = {
        "modality": "application",
        "application_name": "demo-flow",
        "directory_layout": {
            "inbox": [],
            "outbox": [],
            "state": [],
            "kb_brief": [],
            "tests": [
                {
                    "source": "cap-002:test_handler.py",
                    "target": "tests/test_handler.py",
                }
            ],
            "orchestrator": [
                {
                    "source": "cap-001:handler.py",
                    "target": "orchestrator/handler.py",
                }
            ],
        },
        "entry_point": {
            "filename": "run.py",
            "body": "from orchestrator.handler import handle\n\n\ndef main():\n    handle(None)\n\n\nif __name__ == '__main__':\n    main()\n",
            "invocation": "python run.py",
        },
        "environment_variables": [
            {"name": "API_KEY", "purpose": "auth", "required": True}
        ],
        "dependencies": ["pyyaml"],
        "readme_sections": [
            {"heading": "Overview", "body": "..."},
            {"heading": "Run", "body": "..."},
            {"heading": "Configuration", "body": "..."},
        ],
        "deduplications_applied": [],
    }
    return_path = paths.final_synthesis_subagent_returns_dir / "application.json"
    return_path.parent.mkdir(parents=True, exist_ok=True)
    return_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_final_synthesize_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert result["status"] == "synthesized"

    final_dir = paths.final_dir_for(1)
    app_root = final_dir / "application"
    assert (app_root / "run.py").exists()
    assert (app_root / "orchestrator" / "handler.py").exists()
    assert (app_root / "tests" / "test_handler.py").exists()
    assert (app_root / "requirements.txt").exists()
    assert (app_root / "README.md").exists()
    # Env example because we declared an env var.
    assert (app_root / ".env.example").exists()
    # REQ preserved
    assert "REQ-001" in (app_root / "orchestrator" / "handler.py").read_text(encoding="utf-8")
