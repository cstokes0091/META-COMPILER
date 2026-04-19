"""Phase A tests: ingest precheck CLI bookend."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.ingest_stage import run_ingest, run_ingest_precheck


def _bootstrap(tmp_path: Path, *, with_pdf_script: bool = True) -> tuple[Path, Path]:
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    (workspace_root / "scripts").mkdir(parents=True, exist_ok=True)
    if with_pdf_script:
        (workspace_root / "scripts" / "pdf_to_text.py").write_text("# stub")
    (workspace_root / "scripts" / "read_document.py").write_text("# stub")
    (workspace_root / "PROBLEM_STATEMENT.md").write_text("Problem.")
    return workspace_root, artifacts_root


def _add_text_seed(artifacts_root: Path, name: str, body: str = "seed body") -> Path:
    seeds_dir = artifacts_root / "seeds"
    seeds_dir.mkdir(parents=True, exist_ok=True)
    seed = seeds_dir / name
    seed.write_text(body, encoding="utf-8")
    return seed


def _run_ingest_prep(workspace_root: Path, artifacts_root: Path, scope: str = "all") -> dict:
    return run_ingest(
        artifacts_root=artifacts_root, workspace_root=workspace_root, scope=scope
    )


# ---------------------------------------------------------------------------
# Behavior
# ---------------------------------------------------------------------------


def test_run_ingest_precheck_writes_request_and_passes_with_clean_workplan(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _add_text_seed(artifacts_root, "alpha.md")
    _run_ingest_prep(workspace_root, artifacts_root, scope="all")

    result = run_ingest_precheck(
        artifacts_root=artifacts_root, workspace_root=workspace_root, scope="all"
    )

    assert result["status"] == "ready_for_orchestrator"
    paths = build_paths(artifacts_root)
    payload = load_yaml(paths.ingest_precheck_request_path)
    body = payload["ingest_precheck_request"]
    check_names = {c["name"] for c in body["mechanical_checks"]}
    assert "seeds_present" in check_names
    assert "work_plan_present" not in check_names  # only WHEN missing
    assert "work_plan_scope_matches" in check_names
    assert "preextract_clean" in check_names
    assert all(c["result"] == "PASS" for c in body["mechanical_checks"])
    assert body["scope"] == "all"
    assert body["verdict_output_path"].endswith("precheck_verdict.yaml")


def test_run_ingest_precheck_blocks_when_no_seeds(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    # No seeds, no work plan — both fail.
    with pytest.raises(RuntimeError, match="Ingest preflight blocked"):
        run_ingest_precheck(
            artifacts_root=artifacts_root, workspace_root=workspace_root, scope="all"
        )
    # The request file is still written even on block, so the operator has
    # the evidence.
    paths = build_paths(artifacts_root)
    assert paths.ingest_precheck_request_path.exists()
    body = load_yaml(paths.ingest_precheck_request_path)["ingest_precheck_request"]
    seeds_check = next(c for c in body["mechanical_checks"] if c["name"] == "seeds_present")
    assert seeds_check["result"] == "FAIL"


def test_run_ingest_precheck_blocks_on_missing_workplan(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _add_text_seed(artifacts_root, "alpha.md")
    # Skip ingest prep — work plan missing.
    with pytest.raises(RuntimeError, match="work_plan.yaml missing"):
        run_ingest_precheck(
            artifacts_root=artifacts_root, workspace_root=workspace_root, scope="all"
        )


def test_run_ingest_precheck_blocks_on_scope_mismatch(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _add_text_seed(artifacts_root, "alpha.md")
    _run_ingest_prep(workspace_root, artifacts_root, scope="all")
    # Now precheck with a different scope.
    with pytest.raises(RuntimeError, match="scope mismatch"):
        run_ingest_precheck(
            artifacts_root=artifacts_root, workspace_root=workspace_root, scope="new"
        )


def test_run_ingest_precheck_blocks_on_preextract_failures(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    _add_text_seed(artifacts_root, "alpha.md")
    _run_ingest_prep(workspace_root, artifacts_root, scope="all")
    # Inject a synthetic pre-extract failure into the work plan.
    paths = build_paths(artifacts_root)
    plan_path = paths.ingest_runtime_dir / "work_plan.yaml"
    plan = load_yaml(plan_path)
    plan["work_plan"]["preextract_failures"] = [
        {"seed_path": "seeds/alpha.md", "citation_id": "src-alpha", "reason": "boom"}
    ]
    dump_yaml(plan_path, plan)

    with pytest.raises(RuntimeError, match="pre-extraction failures"):
        run_ingest_precheck(
            artifacts_root=artifacts_root, workspace_root=workspace_root, scope="all"
        )


def test_run_ingest_precheck_rejects_invalid_scope(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    with pytest.raises(ValueError):
        run_ingest_precheck(
            artifacts_root=artifacts_root, workspace_root=workspace_root, scope="bogus"
        )
