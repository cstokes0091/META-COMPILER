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

    (workspace_root / "PROBLEM_STATEMENT.md").write_text(
        "# PROBLEM_STATEMENT\n\n## Domain and Problem Space\n"
        "Demo project for the phase4 finalize tests.\n",
        encoding="utf-8",
    )

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
                "capabilities_path": "capabilities.yaml",
            }
        },
    )
    # Empty dispatch hints are fine for these tests; finalize doesn't need them.
    dump_yaml(
        scaffold_root / "DISPATCH_HINTS.yaml",
        {
            "dispatch_hints": {
                "decision_log_version": decision_log_version,
                "project_type": "hybrid",
                "agent_palette": ["planner", "implementer", "reviewer", "researcher"],
                "dispatch_policy": "capability-keyed",
                "assignments": [],
            }
        },
    )
    return workspace_root, artifacts_root


def _seed_work_dir(artifacts_root: Path, files: dict[str, str]) -> Path:
    """Populate executions/v1/work/<capability>/<file> with given content."""
    work_dir = artifacts_root / "executions" / "v1" / "work"
    for rel, content in files.items():
        target = work_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return work_dir


def _author_minimal_slides(artifacts_root: Path, *, decision_log_version: int = 1) -> Path:
    """Write a hand-crafted slides.yaml that satisfies the fidelity gate
    against the deterministic evidence pack the bootstrap workspace produces.

    The bootstrap workspace's evidence pack always emits at least:
      - ev-project (project metadata)
      - ev-problem (problem statement)
      - ev-exec    (execution summary)
    and may add ev-deliv-001+ when work/ is populated. The slides below cite
    only IDs guaranteed to exist regardless of work/ contents.
    """
    paths = build_paths(artifacts_root)
    slides_path = paths.phase4_slides_path
    slides_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pitch_deck": {
            "decision_log_version": decision_log_version,
            "slides": [
                {
                    "role": "title",
                    "title": "Demo Project: pitch deck v2 smoke",
                    "subtitle": "End-to-end render check.",
                    "evidence_ids": ["ev-project", "ev-problem"],
                },
                {
                    "role": "problem",
                    "title": "Problem",
                    "bullets": [
                        {
                            "text": "The deck must render cleanly with strict layout guards.",
                            "evidence_ids": ["ev-problem"],
                        }
                    ],
                },
                {
                    "role": "approach",
                    "title": "Approach",
                    "bullets": [
                        {
                            "text": "Typed evidence pack drives the slides.",
                            "evidence_ids": ["ev-project"],
                        }
                    ],
                },
                {
                    "role": "built",
                    "title": "What was built",
                    "bullets": [
                        {
                            "text": "Deterministic evidence pack and renderer.",
                            "evidence_ids": ["ev-project", "ev-exec"],
                        }
                    ],
                },
                {
                    "role": "evidence",
                    "title": "Evidence",
                    "bullets": [
                        {
                            "text": "Fidelity gate refuses unanchored claims.",
                            "evidence_ids": ["ev-exec"],
                        }
                    ],
                },
                {
                    "role": "why",
                    "title": "Why it matters",
                    "bullets": [
                        {
                            "text": "Accuracy + advocacy without overflow.",
                            "evidence_ids": ["ev-project"],
                        }
                    ],
                },
                {
                    "role": "cta",
                    "title": "Next steps",
                    "bullets": [
                        {
                            "text": "Wire a brand template and re-render.",
                            "evidence_ids": ["ev-project"],
                        }
                    ],
                },
            ],
        }
    }
    dump_yaml(slides_path, payload)
    return slides_path


def test_finalize_compiles_manifest_from_populated_work_dir(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _seed_work_dir(
        artifacts_root,
        {
            "req-001-alpha/output.py": "print('alpha')\n",
            "req-001-alpha/notes.md": "alpha notes\n",
            "req-002-beta/report.md": "beta report\n",
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
    # New shape: deliverables keyed by `capability`, not `agent`.
    deliverable_capabilities = {row["capability"] for row in final["deliverables"]}
    assert deliverable_capabilities == {"req-001-alpha", "req-002-beta"}
    assert len(final["deliverables"]) == 3


def test_finalize_without_slides_returns_pitch_writer_handoff(tmp_path: Path):
    """Default --finalize (== --pitch-step=all) without slides.yaml stops at
    the @pitch-writer handoff and surfaces the next-step instruction."""
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _seed_work_dir(
        artifacts_root,
        {"req-001-alpha/output.py": "print('hi')\n"},
    )

    result = run_phase4_finalize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["pitch_status"] == "pending_pitch_writer"
    assert "pitch-writer" in result["next_step"]
    paths = build_paths(artifacts_root)
    assert paths.phase4_evidence_pack_path.exists()
    assert paths.phase4_pitch_request_path.exists()
    # Deck not yet rendered.
    assert not (paths.pitches_dir / "pitch_v1.pptx").exists()


def test_finalize_renders_pitch_artifacts_when_slides_present(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _seed_work_dir(
        artifacts_root,
        {"req-001-alpha/output.py": "print('hi')\n"},
    )
    # Step 1: build evidence + pitch_request.
    run_phase4_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        pitch_step="evidence",
    )
    # Step 2: hand-author slides.yaml (the LLM agent's role in production).
    _author_minimal_slides(artifacts_root)
    # Step 3: render.
    result = run_phase4_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        pitch_step="render",
    )

    assert result["pitch_status"] == "rendered"
    pitch_md = Path(result["pitch_markdown_path"])
    assert pitch_md.exists()
    pptx = Path(result["pitch_pptx_path"])
    assert pptx.exists()
    assert pptx.stat().st_size > 0


def test_finalize_writes_postcheck_request(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _seed_work_dir(
        artifacts_root,
        {"req-001-alpha/output.py": "print('hi')\n"},
    )
    run_phase4_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        pitch_step="evidence",
    )
    _author_minimal_slides(artifacts_root)
    result = run_phase4_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        pitch_step="render",
    )

    postcheck_path = Path(result["postcheck_request_path"])
    assert postcheck_path.exists()
    body = load_yaml(postcheck_path)["phase4_postcheck_request"]
    assert body["decision_log_version"] == 1
    assert body["verdict_output_path"].endswith("postcheck_verdict.yaml")
    assert body["final_output_manifest_path"].endswith("FINAL_OUTPUT_MANIFEST.yaml")
    assert body["pitch_pptx_path"].endswith("pitch_v1.pptx")


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


def test_finalize_raises_without_work_or_manifest(tmp_path: Path):
    # Commit 8 removed the legacy orchestrator subprocess fallback. When both
    # the work_dir and the pre-written manifest are absent, we expect a clear
    # error pointing the operator at the LLM conductor flow.
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    with pytest.raises(RuntimeError, match="work/ is empty"):
        run_phase4_finalize(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )


def test_finalize_records_execution_in_workspace_manifest(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _seed_work_dir(
        artifacts_root,
        {"req-001-alpha/output.py": "print('hi')\n"},
    )
    run_phase4_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        pitch_step="evidence",
    )
    _author_minimal_slides(artifacts_root)
    run_phase4_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        pitch_step="render",
    )

    paths = build_paths(artifacts_root)
    manifest = load_yaml(paths.manifest_path)["workspace_manifest"]
    versions = [row.get("version") for row in manifest.get("executions", [])]
    assert 1 in versions
    pitch_versions = [row.get("version") for row in manifest.get("pitches", [])]
    assert 1 in pitch_versions
    assert manifest["research"]["last_completed_stage"] == "4"
