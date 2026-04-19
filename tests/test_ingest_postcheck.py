"""Phase A tests: ingest postcheck CLI bookend."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.ingest_stage import run_ingest_postcheck


def _bootstrap(tmp_path: Path) -> tuple[Path, Path]:
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    (workspace_root / "scripts").mkdir(parents=True, exist_ok=True)
    (workspace_root / "PROBLEM_STATEMENT.md").write_text("Problem.")
    return workspace_root, artifacts_root


def _write_findings_file(paths, citation_id: str, *, valid: bool = True) -> Path:
    path = paths.findings_dir / f"{citation_id}.json"
    if valid:
        body = {
            "citation_id": citation_id,
            "seed_path": f"workspace-artifacts/seeds/{citation_id}.md",
            "file_hash": "sha256:abc",
            "extracted_at": "2026-01-01T00:00:00Z",
            "extractor": {"agent_type": "seed-reader", "model": "x", "pass_type": "full"},
            "document_metadata": {"title": "T"},
            "concepts": [],
            "quotes": [
                {"text": "x", "locator": {"page": 1}, "topic": "t", "significance": "s"}
            ],
            "equations": [],
            "claims": [],
            "tables_figures": [],
            "relationships": [],
            "open_questions": [],
            "extraction_stats": {"completeness": "full"},
        }
    else:
        # Missing required fields → schema-invalid.
        body = {"citation_id": citation_id, "concepts": []}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _write_ingest_report(paths) -> Path:
    payload = {
        "ingest_report": {
            "timestamp": "2026-01-01T00:00:00Z",
            "scope": "all",
            "seeds_considered": 1,
            "seeds_processed": 1,
            "seeds_skipped_already_processed": 0,
            "seeds_failed": 0,
            "partial_extractions": 0,
            "findings_written": [
                {
                    "citation_id": "src-alpha",
                    "seed_path": "workspace-artifacts/seeds/alpha.md",
                    "quote_count": 1,
                    "equation_count": 0,
                    "claim_count": 0,
                    "completeness": "full",
                }
            ],
            "failures": [],
        }
    }
    dump_yaml(paths.ingest_report_path, payload)
    return paths.ingest_report_path


# ---------------------------------------------------------------------------
# Behavior
# ---------------------------------------------------------------------------


def test_run_ingest_postcheck_writes_request_when_clean(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    paths = build_paths(artifacts_root)
    _write_ingest_report(paths)
    _write_findings_file(paths, "src-alpha")

    result = run_ingest_postcheck(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ready_for_orchestrator"
    body = load_yaml(paths.ingest_postcheck_request_path)["ingest_postcheck_request"]
    check_names = {c["name"] for c in body["mechanical_checks"]}
    assert check_names == {
        "ingest_report_present",
        "findings_files_present",
        "findings_schema_valid",
    }
    assert all(c["result"] == "PASS" for c in body["mechanical_checks"])
    assert body["verdict_output_path"].endswith("postcheck_verdict.yaml")


def test_run_ingest_postcheck_blocks_when_report_missing(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    paths = build_paths(artifacts_root)
    _write_findings_file(paths, "src-alpha")
    # No ingest_report.yaml.
    with pytest.raises(RuntimeError, match="ingest_report.yaml missing"):
        run_ingest_postcheck(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )
    assert paths.ingest_postcheck_request_path.exists()
    body = load_yaml(paths.ingest_postcheck_request_path)["ingest_postcheck_request"]
    fail_check = next(
        c for c in body["mechanical_checks"] if c["name"] == "ingest_report_present"
    )
    assert fail_check["result"] == "FAIL"


def test_run_ingest_postcheck_blocks_when_findings_missing(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    paths = build_paths(artifacts_root)
    _write_ingest_report(paths)
    # No findings JSON files at all.
    with pytest.raises(RuntimeError, match="no findings JSON files on disk"):
        run_ingest_postcheck(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )


def test_run_ingest_postcheck_blocks_on_schema_invalid(tmp_path: Path):
    workspace_root, artifacts_root = _bootstrap(tmp_path)
    paths = build_paths(artifacts_root)
    _write_ingest_report(paths)
    _write_findings_file(paths, "src-alpha", valid=False)

    with pytest.raises(RuntimeError, match="findings schema issues"):
        run_ingest_postcheck(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )
    body = load_yaml(paths.ingest_postcheck_request_path)["ingest_postcheck_request"]
    schema_check = next(
        c for c in body["mechanical_checks"] if c["name"] == "findings_schema_valid"
    )
    assert schema_check["result"] == "FAIL"
