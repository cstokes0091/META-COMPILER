"""`meta-compiler run-workflow` — invoke a scaffold-emitted workflow orchestrator.

Resolves the requested scaffold (default: latest), confirms it was generated
for a workflow project type, and shells out to
``scaffolds/v{N}/orchestrator/run_workflow.py``. Records each invocation as a
row under ``executions/v{N}/runs/<run_id>.yaml`` for audit.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from ..artifacts import (
    ArtifactPaths,
    build_paths,
    ensure_layout,
    latest_scaffold_path,
)
from ..io import dump_yaml, load_yaml
from ..utils import iso_now


def _resolve_scaffold(
    paths: ArtifactPaths, scaffold_version: int | None
) -> tuple[int, Path]:
    if scaffold_version is None:
        latest = latest_scaffold_path(paths)
        if latest is None:
            raise FileNotFoundError(
                "No scaffold found under workspace-artifacts/scaffolds/. "
                "Run `meta-compiler scaffold` first."
            )
        return latest
    candidate = paths.scaffolds_dir / f"v{scaffold_version}"
    if not candidate.exists():
        raise FileNotFoundError(f"Scaffold v{scaffold_version} not found at {candidate}")
    return scaffold_version, candidate


def _scaffold_project_type(scaffold_root: Path) -> str | None:
    manifest_path = scaffold_root / "SCAFFOLD_MANIFEST.yaml"
    if not manifest_path.exists():
        return None
    payload = load_yaml(manifest_path) or {}
    block = payload.get("scaffold") if isinstance(payload, dict) else None
    if isinstance(block, dict):
        return block.get("project_type")
    return None


def run_workflow(
    artifacts_root: Path,
    input_path: str,
    task: str,
    scaffold_version: int | None = None,
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    version, scaffold_root = _resolve_scaffold(paths, scaffold_version)
    project_type = _scaffold_project_type(scaffold_root)
    if project_type != "workflow":
        raise RuntimeError(
            f"Scaffold v{version} project_type is {project_type!r}, expected 'workflow'. "
            "Use `--scaffold-version` to pick a workflow scaffold."
        )

    runner = scaffold_root / "orchestrator" / "run_workflow.py"
    if not runner.exists():
        raise FileNotFoundError(
            f"orchestrator/run_workflow.py missing in {scaffold_root.relative_to(paths.root)} "
            "— re-run `meta-compiler scaffold`."
        )

    docx = Path(input_path).resolve()
    if not docx.exists():
        raise FileNotFoundError(f"input docx not found: {docx}")

    run_id = uuid.uuid4().hex[:12]
    started = iso_now()
    env = {**os.environ}
    edit_document = (
        Path(__file__).resolve().parents[2] / "scripts" / "edit_document.py"
    )
    if edit_document.exists():
        env["META_COMPILER_EDIT_DOCUMENT"] = str(edit_document)
    proc = subprocess.run(
        [sys.executable, str(runner), "--input", str(docx), "--task", task],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    finished = iso_now()

    runs_dir = paths.executions_dir / f"v{version}" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_path = runs_dir / f"{run_id}.yaml"
    dump_yaml(
        run_path,
        {
            "workflow_run": {
                "run_id": run_id,
                "scaffold_version": version,
                "task": task,
                "input_path": str(docx),
                "started": started,
                "finished": finished,
                "exit_code": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-2000:],
                "stderr_tail": (proc.stderr or "")[-2000:],
            }
        },
    )

    return {
        "status": "ok" if proc.returncode == 0 else "failed",
        "run_id": run_id,
        "scaffold_version": version,
        "exit_code": proc.returncode,
        "run_log_path": run_path.relative_to(paths.root).as_posix(),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
