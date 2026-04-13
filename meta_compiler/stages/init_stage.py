from __future__ import annotations

from pathlib import Path

from ..artifacts import (
    build_paths,
    compute_seed_version,
    ensure_layout,
    list_seed_files,
    save_manifest,
)
from ..io import dump_yaml
from ..utils import iso_now


def _problem_statement_template(project_name: str, problem_domain: str, project_type: str) -> str:
    return f"""# PROBLEM_STATEMENT

## Domain and Problem Space
{problem_domain}

## Goals and Success Criteria
Define the measurable outcomes that indicate project success.

## Constraints
List technical constraints, timeline constraints, and resource constraints.

## Project Type
{project_type}

## Additional Context
Capture assumptions, prior work references, and any known risks.
"""


def run_meta_init(
    workspace_root: Path,
    artifacts_root: Path,
    project_name: str,
    problem_domain: str,
    project_type: str,
    force: bool = False,
) -> dict:
    if project_type not in {"algorithm", "report", "hybrid"}:
        raise ValueError("project_type must be one of: algorithm, report, hybrid")

    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    if force or not paths.source_bindings_path.exists():
        dump_yaml(paths.source_bindings_path, {"bindings": {}})

    problem_statement_path = workspace_root / "PROBLEM_STATEMENT.md"
    if force or not problem_statement_path.exists():
        problem_statement_path.write_text(
            _problem_statement_template(project_name, problem_domain, project_type),
            encoding="utf-8",
        )

    seed_count = len(list_seed_files(paths))
    now = iso_now()
    manifest = {
        "workspace_manifest": {
            "name": project_name,
            "created": now,
            "last_modified": now,
            "problem_domain": problem_domain,
            "project_type": project_type,
            "seeds": {
                "version": compute_seed_version(paths),
                "last_updated": now,
                "document_count": seed_count,
            },
            "wiki": {
                "version": "",
                "last_updated": now,
                "page_count": 0,
            },
            "decision_logs": [],
            "status": "initialized",
            "research": {
                "iteration_count": 0,
                "freshness_policy": "mixed",
                "last_completed_stage": "0",
            },
        }
    }

    save_manifest(paths, manifest)
    return {
        "manifest_path": str(paths.manifest_path),
        "problem_statement_path": str(problem_statement_path),
        "seed_count": seed_count,
    }
