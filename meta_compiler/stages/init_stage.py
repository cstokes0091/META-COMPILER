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


def _source_prompts_dir() -> Path:
    """Canonical prompt source: .github/prompts/.

    The root prompts/ directory is no longer the source of truth — it is a
    generated mirror provisioned for non-Copilot LLM runtimes. Provisioning
    reads from .github/prompts/ and writes to BOTH .github/prompts/ and
    prompts/ in the target workspace.
    """
    return Path(__file__).resolve().parents[2] / ".github" / "prompts"


def _source_customizations_dir() -> Path:
    return Path(__file__).resolve().parents[2] / ".github"


_EXCLUDED_CUSTOMIZATION_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
}


def iter_source_customization_files(source_dir: Path):
    """Yield every non-transient file under `.github/` that should be
    provisioned into a workspace. Skips bytecode caches and other
    build/test artifacts that may be present on disk but are not part of
    the workspace-customization template set."""
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        try:
            relative = path.relative_to(source_dir)
        except ValueError:
            continue
        if any(part in _EXCLUDED_CUSTOMIZATION_DIR_NAMES for part in relative.parts):
            continue
        yield path


def _provision_workspace_prompts(workspace_root: Path, force: bool) -> list[str]:
    """Copy canonical prompts to BOTH workspace prompts/ and .github/prompts/.

    Source is .github/prompts/. The root mirror exists for non-Copilot
    runtimes that read from `prompts/`.
    """
    source_dir = _source_prompts_dir()
    if not source_dir.exists():
        raise RuntimeError(f"Prompt templates directory not found: {source_dir}")

    prompt_templates = sorted(source_dir.glob("*.prompt.md"))
    if not prompt_templates:
        raise RuntimeError(f"No prompt templates found in: {source_dir}")

    copied: list[str] = []
    for target_dir_name in ("prompts", ".github/prompts"):
        target_dir = workspace_root / target_dir_name
        target_dir.mkdir(parents=True, exist_ok=True)
        for source in prompt_templates:
            target = target_dir / source.name
            if source.resolve() == target.resolve():
                continue
            if not force and target.exists():
                continue
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            copied.append(str(target))
    return copied


def _provision_workspace_customizations(workspace_root: Path, force: bool) -> list[str]:
    source_dir = _source_customizations_dir()
    if not source_dir.exists():
        raise RuntimeError(f"Workspace customization templates directory not found: {source_dir}")

    source_files = sorted(iter_source_customization_files(source_dir))
    if not source_files:
        raise RuntimeError(f"No workspace customization templates found in: {source_dir}")

    target_dir = workspace_root / ".github"
    target_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for source in source_files:
        relative_path = source.relative_to(source_dir)
        target = target_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)

        if source.resolve() == target.resolve():
            continue
        if not force and target.exists():
            continue

        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(str(target))
    return copied


def _problem_statement_template(
    project_name: str,
    problem_domain: str,
    project_type: str,
    problem_statement: str | None = None,
) -> str:
    if problem_statement and problem_statement.strip():
        return problem_statement.rstrip() + "\n"

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
    problem_statement: str | None = None,
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
            _problem_statement_template(
                project_name,
                problem_domain,
                project_type,
                problem_statement=problem_statement,
            ),
            encoding="utf-8",
        )
    copied_prompts = _provision_workspace_prompts(workspace_root=workspace_root, force=force)
    copied_customizations = _provision_workspace_customizations(workspace_root=workspace_root, force=force)

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
                "name": "",
            },
            "decision_logs": [],
            "executions": [],
            "pitches": [],
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
        "prompt_dir": str(workspace_root / "prompts"),
        "prompt_count": len(list((workspace_root / "prompts").glob("*.prompt.md"))),
        "prompts_copied": copied_prompts,
        "customization_dir": str(workspace_root / ".github"),
        "customization_asset_count": len(list((workspace_root / ".github").rglob("*"))),
        "customizations_copied": copied_customizations,
        "seed_count": seed_count,
    }
