from pathlib import Path

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import load_yaml
from meta_compiler.stages.ingest_stage import run_ingest, validate_all_findings


def test_run_ingest_routes_pdf_preextract_to_pdf_wrapper(tmp_path: Path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    seed_path = paths.seeds_dir / "paper.pdf"
    seed_path.write_bytes(b"%PDF-1.4 fake")

    scripts_dir = workspace_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "pdf_to_text.py").write_text("print('stub')\n", encoding="utf-8")
    (scripts_dir / "read_document.py").write_text("print('stub')\n", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(command: list[str], check: bool, capture_output: bool):
        commands.append(command)
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_text("extracted", encoding="utf-8")

        class CompletedProcess:
            stdout = b""
            stderr = b""

        return CompletedProcess()

    monkeypatch.setattr("meta_compiler.stages.ingest_stage.subprocess.run", fake_run)

    result = run_ingest(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        scope="all",
    )

    assert result["status"] == "ready_for_orchestrator"
    assert result["work_items"] == 1
    assert commands
    assert commands[0][1].endswith("pdf_to_text.py")

    work_plan = load_yaml(paths.runtime_dir / "ingest" / "work_plan.yaml")
    work_item = work_plan["work_plan"]["work_items"][0]
    assert work_item["citation_id"] == "src-paper"
    assert work_item["extracted_path"] == "runtime/ingest/src-paper.md"


def test_validate_all_findings_reports_schema_issues(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    findings_path = paths.findings_dir / "src-bad.json"
    findings_path.write_text("{}", encoding="utf-8")

    result = validate_all_findings(artifacts_root=artifacts_root)

    assert result["findings_scanned"] == 1
    assert result["total_issues"] > 0
    assert result["per_file"][0]["path"] == "wiki/findings/src-bad.json"