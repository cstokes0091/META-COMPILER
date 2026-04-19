"""Phase B tests: phase4-finalize --finalize compiles manifest from work/."""
from __future__ import annotations

from pathlib import Path

import pytest

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.phase4_stage import run_phase4_finalize


def _bootstrap(tmp_path: Path, *, decision_log_version: int = 1) -> tuple[Path, Path]:
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    dump_yaml(
        paths.decision_logs_dir / f"decision_log_v{decision_log_version}.yaml",
        {"decision_log": {"version": decision_log_version, "decisions": {}}},
    )
    dump_yaml(
        paths.manifest_path,
        {
            "workspace_manifest": {
                "name": "Test Project",
                "wiki": {"name": "Test Atlas"},
            }
        },
    )

    scaffold_root = paths.scaffolds_dir / f"v{decision_log_version}"
    scaffold_root.mkdir(parents=True, exist_ok=True)
    dump_yaml(
        scaffold_root / "EXECUTION_MANIFEST.yaml",
        {
            "execution": {
                "decision_log_version": decision_log_version,
                "project_type": "hybrid",
                "orchestrator_path": "orchestrator/run_stage4.py",
            }
        },
    )

    # Empty agent registry is fine for these tests; finalize doesn't need it.
    dump_yaml(
        scaffold_root / "AGENT_REGISTRY.yaml",
        {
            "agent_registry": {
                "decision_log_version": decision_log_version,
                "project_type": "hybrid",
                "entries": [],
            }
        },
    )
    return workspace_root, artifacts_root


def _seed_work_dir(artifacts_root: Path, files: dict[str, str]) -> Path:
    """Populate executions/v1/work/<agent>/<file> with given content."""
    work_dir = artifacts_root / "executions" / "v1" / "work"
    for rel, content in files.items():
        target = work_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return work_dir


def test_finalize_compiles_manifest_from_populated_work_dir(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _seed_work_dir(
        artifacts_root,
        {
            "alpha-agent/output.py": "print('alpha')\n",
            "alpha-agent/notes.md": "alpha notes\n",
            "beta-agent/report.md": "beta report\n",
        },
    )

    result = run_phase4_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["decision_log_version"] == 1
    final_manifest_path = (
        artifacts_root / "executions" / "v1" / "FINAL_OUTPUT_MANIFEST.yaml"
    )
    assert final_manifest_path.exists()
    final = load_yaml(final_manifest_path)["final_output"]
    deliverable_agents = {row["agent"] for row in final["deliverables"]}
    assert deliverable_agents == {"alpha-agent", "beta-agent"}
    assert len(final["deliverables"]) == 3


def test_finalize_writes_pitch_artifacts(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _seed_work_dir(
        artifacts_root,
        {"alpha-agent/output.py": "print('hi')\n"},
    )

    result = run_phase4_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    pitch_md = Path(result["pitch_markdown_path"])
    assert pitch_md.exists()
    pptx = Path(result["pitch_pptx_path"])
    assert pptx.exists()
    assert pptx.stat().st_size > 0


def test_finalize_writes_postcheck_request(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _seed_work_dir(
        artifacts_root,
        {"alpha-agent/output.py": "print('hi')\n"},
    )

    result = run_phase4_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    postcheck_path = Path(result["postcheck_request_path"])
    assert postcheck_path.exists()
    body = load_yaml(postcheck_path)["phase4_postcheck_request"]
    assert body["decision_log_version"] == 1
    assert body["verdict_output_path"].endswith("postcheck_verdict.yaml")
    assert body["final_output_manifest_path"].endswith("FINAL_OUTPUT_MANIFEST.yaml")


def test_finalize_uses_existing_manifest_when_no_work(tmp_path: Path):
    """If FINAL_OUTPUT_MANIFEST.yaml already exists and no work/, use it."""
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    output_dir = artifacts_root / "executions" / "v1"
    output_dir.mkdir(parents=True, exist_ok=True)
    dump_yaml(
        output_dir / "FINAL_OUTPUT_MANIFEST.yaml",
        {
            "final_output": {
                "decision_log_version": 1,
                "project_type": "hybrid",
                "deliverables": [
                    {"agent": "manual", "kind": "md", "path": "preexisting.md"}
                ],
                "execution_notes": ["pre-written"],
            }
        },
    )

    result = run_phase4_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    # The manifest should be left intact (not overwritten when work/ is absent).
    final = load_yaml(output_dir / "FINAL_OUTPUT_MANIFEST.yaml")["final_output"]
    deliverables = final["deliverables"]
    assert any(row.get("agent") == "manual" for row in deliverables)
    assert result["decision_log_version"] == 1


def test_finalize_raises_without_work_or_manifest_or_orchestrator(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    # No work/, no manifest, no orchestrator script => fall-through fails.
    with pytest.raises(RuntimeError, match="Stage 4 orchestrator missing"):
        run_phase4_finalize(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )


def test_finalize_records_execution_in_workspace_manifest(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _seed_work_dir(
        artifacts_root,
        {"alpha-agent/output.py": "print('hi')\n"},
    )

    run_phase4_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    paths = build_paths(artifacts_root)
    manifest = load_yaml(paths.manifest_path)["workspace_manifest"]
    versions = [row.get("version") for row in manifest.get("executions", [])]
    assert 1 in versions
    pitch_versions = [row.get("version") for row in manifest.get("pitches", [])]
    assert 1 in pitch_versions
    assert manifest["research"]["last_completed_stage"] == "4"
