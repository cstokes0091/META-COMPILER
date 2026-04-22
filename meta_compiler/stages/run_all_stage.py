"""run-all command: Execute META-COMPILER through the Stage 2 preflight handoff.

Runs Stages 0 → 1A → 1B → 1C → 2-start sequentially, validating after the
major handoff stages. Stops on the first validation failure so the user
can fix issues before continuing.

The pipeline intentionally stops after `elicit-vision --start` writes the
Stage 2 brief and transcript skeleton. The Stage 2 dialog itself must
happen in a chat runtime driven by `.github/prompts/stage-2-dialog.prompt.md`
— `run-all` cannot hold a conversation, so it hands off to the prompt.
After the dialog, the operator runs `meta-compiler elicit-vision --finalize`
and `meta-compiler audit-requirements` manually.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import build_paths, list_seed_files
from ..utils import iso_now
from .breadth_stage import run_research_breadth
from .clean_stage import run_clean_workspace
from .depth_stage import run_research_depth
from .elicit_stage import run_elicit_vision_start
from .ingest_stage import run_ingest, run_ingest_precheck
from .init_stage import run_meta_init
from .review_stage import run_review
from .seed_tracker import check_and_update_seeds
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
    """Run META-COMPILER from Stage 0 through the Stage 2 handoff.

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
        Summary of the pipeline run through the human-review handoff.
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

    # --- Ingest prep: prepare work plan for ingest-orchestrator agent ---
    # The CLI does deterministic prep. The orchestrator agent (invoked outside
    # run-all, driven by the Stage 1A prompt) does the LLM fan-out.
    ingest_prep = run_ingest(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        scope="all",
    )
    _log_step("1a-ingest-prep", ingest_prep, log)

    # --- Ingest preflight: write precheck request for the orchestrator ---
    # The CLI does mechanical readiness checks; the orchestrator agent does
    # the semantic seed-coverage judgment outside run-all.
    if seeds:
        ingest_precheck = run_ingest_precheck(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            scope="all",
        )
        _log_step("1a-ingest-precheck", ingest_precheck, log)

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

    # --- Seed tracking: auto-detect new seeds and prep ingest for them ---
    seed_status = check_and_update_seeds(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    if seed_status.get("new_seeds_found"):
        _log_step("seed-auto-update", seed_status, log)
        new_ingest_prep = run_ingest(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            scope="new",
        )
        _log_step("seed-auto-ingest-prep", new_ingest_prep, log)

    # --- Stage 2 preflight: write brief + transcript skeleton ---
    # The dialog itself happens outside run-all — it requires a chat runtime
    # reading .github/prompts/stage-2-dialog.prompt.md. run-all's job is to
    # prepare the preflight artifacts and hand off.
    #
    # Step 0 of elicit-vision --start auto-fires the wiki-search preflight. If
    # work_plan.yaml lists topics, --start returns
    # status="ready_for_wiki_search_orchestrator" and run-all hands off there.
    elicit_start_result = run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    _log_step("2-preflight", elicit_start_result, log)

    if elicit_start_result.get("status") == "ready_for_wiki_search_orchestrator":
        return {
            "status": "wiki-search-handoff",
            "started": started,
            "finished": iso_now(),
            "stages_completed": len(
                [
                    e
                    for e in log
                    if e["status"] == "ok" and not e["stage"].startswith("validate-")
                ]
            ),
            "handoff_stage": "2-step0-wiki-search",
            "handoff_ready": True,
            "wiki_search_request_path": elicit_start_result.get(
                "wiki_search_request_path"
            ),
            "wiki_search_work_plan_path": elicit_start_result.get(
                "wiki_search_work_plan_path"
            ),
            "topic_count": elicit_start_result.get("topic_count"),
            "next_steps": [
                "Open .github/prompts/wiki-search-orchestrator.prompt.md and invoke "
                "@wiki-search-orchestrator. It fans out wiki-searcher subagents "
                "(<=4 parallel) per topic in the work plan.",
                "Run `meta-compiler wiki-search --apply` to consolidate the "
                "per-topic results into runtime/stage2/wiki_search/results.yaml.",
                "Re-run `meta-compiler elicit-vision --start` to render brief.md "
                "with the populated '## Wiki Evidence' section, then proceed to "
                "@stage2-orchestrator preflight.",
            ],
            "pipeline_log": log,
            "use_case": use_case,
            "message": (
                "Pipeline paused at Stage 2 Step 0 (wiki-search). Walk the "
                "next_steps to enrich the brief, then re-run `meta-compiler "
                "elicit-vision --start` to enter the dialog."
            ),
        }

    return {
        "status": "stage-2-preflight-handoff",
        "started": started,
        "finished": iso_now(),
        "stages_completed": len(
            [
                e
                for e in log
                if e["status"] == "ok" and not e["stage"].startswith("validate-")
            ]
        ),
        "handoff_stage": "2-preflight",
        "handoff_ready": True,
        "brief_path": elicit_start_result.get("brief_path"),
        "transcript_path": elicit_start_result.get("transcript_path"),
        "precheck_request_path": elicit_start_result.get("precheck_request_path"),
        "next_steps": [
            "Open .github/prompts/stage-2-dialog.prompt.md in your LLM runtime "
            "(Copilot Chat, Claude Code, etc.).",
            "Walk the prompt's five steps: it invokes "
            "@stage2-orchestrator for preflight, conducts the dialog, appends "
            "decision blocks to workspace-artifacts/runtime/stage2/transcript.md, "
            "then calls meta-compiler elicit-vision --finalize.",
            "After --finalize writes decision_log_v{N}.yaml, the prompt invokes "
            "@stage2-orchestrator for postflight and then runs "
            "meta-compiler audit-requirements.",
            "Review workspace-artifacts/decision-logs/decision_log_v{N}.yaml and "
            "workspace-artifacts/decision-logs/requirements_audit.yaml before "
            "running meta-compiler scaffold.",
        ],
        "pipeline_log": log,
        "use_case": use_case,
        "message": (
            "Pipeline completed through Stage 2 preflight. The Stage 2 dialog "
            "is handed off to the stage-2-dialog prompt; run-all cannot hold "
            "the conversation itself."
        ),
    }
