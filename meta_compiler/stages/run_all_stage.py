"""run-all command: Execute the entire META-COMPILER pipeline with a single prompt.

Runs Stages 0 → 1A → 1B → 1C → 2 → 3 → 4 sequentially, validating after each
stage. Stops on the first validation failure so the user can fix issues before
continuing.

This is the "single-prompt" wrapper. It is designed for users who want to execute
the complete pipeline without manually invoking each stage.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..artifacts import build_paths, list_seed_files
from ..utils import iso_now
from .breadth_stage import run_research_breadth
from .clean_stage import run_clean_workspace
from .depth_stage import run_research_depth
from .elicit_stage import run_elicit_vision
from .init_stage import run_meta_init
from .phase4_stage import run_phase4_finalize
from .review_stage import run_review
from .scaffold_stage import run_scaffold
from .seed_tracker import check_and_update_seeds
from .wiki_update_stage import run_wiki_update
from ..validation import validate_stage


def _log_step(step_name: str, result: dict, log: list[dict]) -> None:
    log.append({
        "stage": step_name,
        "timestamp": iso_now(),
        "status": "ok",
        "summary": {k: v for k, v in result.items() if isinstance(v, (str, int, float, bool))},
    })


def _validate_or_raise(artifacts_root: Path, stage: str, log: list[dict]) -> None:
    paths = build_paths(artifacts_root)
    issues = validate_stage(paths, stage=stage)
    if issues:
        log.append({
            "stage": f"validate-{stage}",
            "timestamp": iso_now(),
            "status": "failed",
            "issues": issues,
        })
        raise RuntimeError(
            f"Validation failed at Stage {stage} with {len(issues)} issue(s):\n"
            + "\n".join(f"  - {i}" for i in issues[:10])
        )
    log.append({
        "stage": f"validate-{stage}",
        "timestamp": iso_now(),
        "status": "ok",
        "issues": [],
    })


def run_all(
    workspace_root: Path,
    artifacts_root: Path,
    project_name: str,
    problem_domain: str,
    project_type: str,
    problem_statement: str | None = None,
    problem_statement_file: str | None = None,
    use_case: str = "initial scaffold",
    clean_first: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Run the complete META-COMPILER pipeline from Stage 0 through Stage 4.

    Parameters
    ----------
    workspace_root : Path
        Root directory of the workspace.
    artifacts_root : Path
        Path to the workspace-artifacts directory.
    project_name : str
        Name of the project.
    problem_domain : str
        Description of the problem domain.
    project_type : str
        One of: algorithm, report, hybrid.
    problem_statement : str | None
        Inline problem statement body (alternative to file).
    problem_statement_file : str | None
        Path to a file containing the problem statement.
    use_case : str
        Use-case label for the decision log.
    clean_first : bool
        If True, reset workspace to Stage 0 before running.
    force : bool
        Overwrite existing artifacts.

    Returns
    -------
    dict
        Summary of the full pipeline run with per-stage results.
    """
    log: list[dict] = []
    started = iso_now()

    # Resolve problem statement from file if needed
    resolved_statement = problem_statement
    if not resolved_statement and problem_statement_file:
        resolved_statement = Path(problem_statement_file).read_text(encoding="utf-8")

    # Optional clean
    if clean_first:
        clean_result = run_clean_workspace(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            target_stage="0",
        )
        _log_step("clean", clean_result, log)

    # --- Stage 0: Initialize ---
    init_result = run_meta_init(
        workspace_root=workspace_root,
        artifacts_root=artifacts_root,
        project_name=project_name,
        problem_domain=problem_domain,
        project_type=project_type,
        problem_statement=resolved_statement,
        force=force,
    )
    _log_step("0-init", init_result, log)
    _validate_or_raise(artifacts_root, "0", log)

    # Check that seeds exist before proceeding
    paths = build_paths(artifacts_root)
    seeds = list_seed_files(paths)
    if not seeds:
        log.append({
            "stage": "seed-check",
            "timestamp": iso_now(),
            "status": "warning",
            "message": (
                "No seed documents found in workspace-artifacts/seeds/. "
                "Add seed documents before continuing. The pipeline will "
                "proceed but breadth research will have nothing to ingest."
            ),
        })

    # --- Stage 1A: Breadth Research ---
    breadth_result = run_research_breadth(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    _log_step("1a-breadth", breadth_result, log)
    _validate_or_raise(artifacts_root, "1a", log)

    # --- Stage 1B: Depth Pass ---
    depth_result = run_research_depth(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    _log_step("1b-depth", depth_result, log)

    # --- Stage 1C: Review ---
    review_result = run_review(artifacts_root=artifacts_root)
    _log_step("1c-review", review_result, log)

    # --- Seed tracking: auto-detect and wiki-update ---
    seed_status = check_and_update_seeds(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    if seed_status.get("new_seeds_found"):
        _log_step("seed-auto-update", seed_status, log)

    # --- Stage 2: Vision Elicitation ---
    elicit_result = run_elicit_vision(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        use_case=use_case,
        resume=False,
        non_interactive=True,
        context_note="",
    )
    _log_step("2-elicit", elicit_result, log)
    _validate_or_raise(artifacts_root, "2", log)

    # --- Stage 3: Scaffold ---
    scaffold_result = run_scaffold(
        artifacts_root=artifacts_root,
        decision_log_version=None,
    )
    _log_step("3-scaffold", scaffold_result, log)
    _validate_or_raise(artifacts_root, "3", log)

    # --- Stage 4: Execute + Pitch ---
    phase4_result = run_phase4_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        decision_log_version=None,
    )
    _log_step("4-finalize", phase4_result, log)
    _validate_or_raise(artifacts_root, "4", log)

    return {
        "status": "complete",
        "started": started,
        "finished": iso_now(),
        "stages_completed": len([e for e in log if e["status"] == "ok" and not e["stage"].startswith("validate-")]),
        "pipeline_log": log,
        "message": (
            "Full pipeline completed successfully. "
            "Review workspace-artifacts/ for all generated outputs."
        ),
    }
