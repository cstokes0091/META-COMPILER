from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .artifacts import build_paths
from .stages.audit_stage import run_audit_requirements
from .stages.breadth_stage import run_research_breadth
from .stages.clean_stage import run_clean_workspace
from .stages.depth_stage import run_research_depth
from .stages.enrichment_stage import run_enrich_wiki
from .stages.relationship_stage import (
    run_apply_relationships,
    run_propose_relationships,
)
from .wiki_linking import run_wiki_link
from .stages.elicit_stage import (
    run_elicit_vision_finalize,
    run_elicit_vision_start,
)
from .stages.ingest_stage import (
    run_ingest,
    run_ingest_postcheck,
    run_ingest_precheck,
    validate_all_findings,
)
from .stages.init_stage import run_meta_init
from .stages.phase4_stage import run_phase4_finalize, run_phase4_start
from .stages.review_stage import run_review
from .stages.run_all_stage import run_all
from .stages.scaffold_stage import run_scaffold
from .stages.seed_tracker import check_and_update_seeds
from .stages.stage2_reentry import run_finalize_reentry, run_stage2_reentry
from .stages.wiki_update_stage import run_wiki_update
from .validation import validate_stage
from .wiki_browser import run_wiki_browser


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="Workspace root directory (default: current directory)",
    )
    parser.add_argument(
        "--artifacts-root",
        default="workspace-artifacts",
        help="Relative or absolute artifact root path",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta-compiler",
        description="META-COMPILER orchestration CLI for stages 0/1A/1B/1C/2/3/4",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("meta-init", help="Initialize workspace artifacts and manifest")
    _add_common_paths(init_parser)
    init_parser.add_argument("--project-name", required=True)
    init_parser.add_argument("--problem-domain", required=True)
    init_parser.add_argument(
        "--project-type",
        required=True,
        choices=["algorithm", "report", "hybrid"],
    )
    init_parser.add_argument(
        "--problem-statement",
        default=None,
        help="Optional problem statement body to write into PROBLEM_STATEMENT.md during init",
    )
    init_parser.add_argument(
        "--problem-statement-file",
        default=None,
        help="Optional file containing a problem statement body to write during init",
    )
    init_parser.add_argument("--force", action="store_true", help="Overwrite initial templates")

    breadth_parser = subparsers.add_parser("research-breadth", help="Run Stage 1A breadth research")
    _add_common_paths(breadth_parser)

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Prepare a work plan for ingest-orchestrator seed extraction",
    )
    _add_common_paths(ingest_parser)
    ingest_parser.add_argument(
        "--scope",
        default="new",
        choices=["all", "new"],
        help="Seed scope to prepare for the ingest orchestrator",
    )

    ingest_precheck_parser = subparsers.add_parser(
        "ingest-precheck",
        help="Write the ingest preflight request for the orchestrator (Step 2)",
    )
    _add_common_paths(ingest_precheck_parser)
    ingest_precheck_parser.add_argument(
        "--scope",
        default="new",
        choices=["all", "new"],
        help="Scope the preflight checks should validate against",
    )

    ingest_postcheck_parser = subparsers.add_parser(
        "ingest-postcheck",
        help="Write the ingest postflight request for the orchestrator (Step 5)",
    )
    _add_common_paths(ingest_postcheck_parser)

    ingest_validate_parser = subparsers.add_parser(
        "ingest-validate",
        help="Validate findings JSON files produced by ingest orchestration",
    )
    _add_common_paths(ingest_validate_parser)

    depth_parser = subparsers.add_parser("research-depth", help="Run Stage 1B depth pass")
    _add_common_paths(depth_parser)
    depth_parser.add_argument(
        "--force-regenerate-v2",
        action="store_true",
        help=(
            "Wipe v2 pages and re-copy from v1, discarding any enrichment or "
            "manual edits. Default: preserve edits via the v2 edit manifest."
        ),
    )

    review_parser = subparsers.add_parser("review", help="Run Stage 1C review panel")
    _add_common_paths(review_parser)

    enrich_parser = subparsers.add_parser(
        "enrich-wiki",
        help="Prepare a work plan for the wiki-synthesizer agent (v2 only)",
    )
    _add_common_paths(enrich_parser)
    enrich_parser.add_argument(
        "--version",
        type=int,
        default=2,
        choices=[2],
        help="Wiki version to enrich. Only 2 is supported (v1 stays templated).",
    )

    wiki_link_parser = subparsers.add_parser(
        "wiki-link",
        help="Insert inline links between v2 concept pages (deterministic, idempotent)",
    )
    _add_common_paths(wiki_link_parser)
    wiki_link_parser.add_argument(
        "--version",
        type=int,
        default=2,
        choices=[2],
        help="Wiki version to link. Only 2 is supported.",
    )

    propose_rel_parser = subparsers.add_parser(
        "propose-relationships",
        help="Prepare a request for the relationship-mapper agent (v2 only)",
    )
    _add_common_paths(propose_rel_parser)

    apply_rel_parser = subparsers.add_parser(
        "apply-relationships",
        help="Merge accepted relationship-mapper proposals into v2 pages",
    )
    _add_common_paths(apply_rel_parser)
    apply_rel_parser.add_argument(
        "--version",
        type=int,
        default=2,
        choices=[2],
        help="Wiki version. Only 2 is supported.",
    )

    audit_parser = subparsers.add_parser(
        "audit-requirements",
        help="Run the baseline Stage 2 requirements audit",
    )
    _add_common_paths(audit_parser)
    audit_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to audit (default: latest)",
    )

    elicit_parser = subparsers.add_parser(
        "elicit-vision",
        help="Stage 2 vision elicitation bookends (prompt-as-conductor)",
    )
    _add_common_paths(elicit_parser)
    # Prompt-as-conductor bookends — see .github/docs/stage-2-hardening.md.
    # Exactly one of --start or --finalize is required; the dialog itself
    # happens between them in the LLM runtime, driven by
    # .github/prompts/stage-2-dialog.prompt.md.
    elicit_mode_group = elicit_parser.add_mutually_exclusive_group(required=True)
    elicit_mode_group.add_argument(
        "--start",
        action="store_true",
        help="Stage 2 preflight: write brief.md, transcript.md skeleton, precheck_request.yaml",
    )
    elicit_mode_group.add_argument(
        "--finalize",
        action="store_true",
        help="Stage 2 finalize: parse transcript decision blocks, compile decision_log_v<N>.yaml",
    )
    elicit_parser.add_argument(
        "--override-iterate",
        default=None,
        metavar="REASON",
        help="Override a Stage 1C ITERATE handoff when running --start (reason is recorded)",
    )

    scaffold_parser = subparsers.add_parser("scaffold", help="Run Stage 3 scaffold generation")
    _add_common_paths(scaffold_parser)
    scaffold_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to scaffold (default: latest)",
    )

    phase4_parser = subparsers.add_parser(
        "phase4-finalize",
        help="Run Stage 4 execution and pitch generation (prompt-as-conductor)",
    )
    _add_common_paths(phase4_parser)
    phase4_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to finalize (default: latest scaffold/decision log)",
    )
    # Prompt-as-conductor bookends. --start prepares the dispatch plan + execution
    # request and stops; the LLM ralph loop populates executions/v{N}/work/.
    # --finalize compiles FINAL_OUTPUT_MANIFEST.yaml from work/ and emits the
    # pitch deck. If neither flag is passed, falls back to the legacy
    # subprocess-based execution.
    phase4_mode_group = phase4_parser.add_mutually_exclusive_group()
    phase4_mode_group.add_argument(
        "--start",
        action="store_true",
        help="Stage 4 preflight: write dispatch_plan.yaml + execution_request.yaml",
    )
    phase4_mode_group.add_argument(
        "--finalize",
        action="store_true",
        help="Stage 4 finalize: compile FINAL_OUTPUT_MANIFEST + pitch deck from work/",
    )

    run_all_parser = subparsers.add_parser(
        "run-all",
        help="Run the pipeline through the Stage 2 human-review handoff",
    )
    _add_common_paths(run_all_parser)
    run_all_parser.add_argument("--project-name", required=True)
    run_all_parser.add_argument("--problem-domain", required=True)
    run_all_parser.add_argument(
        "--project-type",
        required=True,
        choices=["algorithm", "report", "hybrid"],
    )
    run_all_parser.add_argument(
        "--problem-statement",
        default=None,
        help="Inline problem statement body",
    )
    run_all_parser.add_argument(
        "--problem-statement-file",
        default=None,
        help="File containing a problem statement body",
    )
    run_all_parser.add_argument(
        "--use-case",
        default="initial scaffold",
        help="Use-case label for the decision log (default: 'initial scaffold')",
    )
    run_all_parser.add_argument(
        "--clean-first",
        action="store_true",
        help="Reset workspace to Stage 0 before running",
    )
    run_all_parser.add_argument("--force", action="store_true", help="Overwrite existing artifacts")

    clean_parser = subparsers.add_parser(
        "clean-workspace",
        help="Reset workspace to a specific stage",
    )
    _add_common_paths(clean_parser)
    clean_parser.add_argument(
        "--target-stage",
        required=True,
        choices=["0", "1a", "1b", "1c", "2", "3", "4"],
        help="Reset to just after this stage completed",
    )

    seed_track_parser = subparsers.add_parser(
        "track-seeds",
        help="Check for new seed files and auto-update wiki if found",
    )
    _add_common_paths(seed_track_parser)

    wiki_update_parser = subparsers.add_parser("wiki-update", help="Incremental wiki expansion from new seeds")
    _add_common_paths(wiki_update_parser)

    wiki_browser_parser = subparsers.add_parser("wiki-browse", help="Open the local wiki browser")
    _add_common_paths(wiki_browser_parser)
    wiki_browser_parser.add_argument("--port", type=int, default=7777, help="Preferred local port")
    wiki_browser_parser.add_argument("--no-open", action="store_true", help="Start the server without opening a browser")
    wiki_browser_parser.add_argument("--prefer-v1", action="store_true", help="Prefer wiki v1 even when wiki v2 exists")

    reentry_parser = subparsers.add_parser("stage2-reentry", help="Revise Decision Log for changed scope")
    _add_common_paths(reentry_parser)
    reentry_parser.add_argument("--reason", default=None, help="Reason for revision (optional when --from-request is used)")
    reentry_parser.add_argument(
        "--sections",
        default=None,
        help="Comma-separated sections to revise (conventions,architecture,scope,requirements,open_items,agents_needed). Optional when --from-request is used.",
    )
    reentry_parser.add_argument(
        "--from-request",
        type=Path,
        default=None,
        help="Path to stage2 reentry_request.yaml. When passed, --reason and --sections are derived from the artifact.",
    )

    finalize_parser = subparsers.add_parser("finalize-reentry", help="Finalize a re-entry Decision Log after editing")
    _add_common_paths(finalize_parser)
    finalize_parser.add_argument("--version", type=int, default=None, help="Decision log version to finalize")

    validate_parser = subparsers.add_parser("validate-stage", help="Validate stage artifacts")
    _add_common_paths(validate_parser)
    validate_parser.add_argument(
        "--stage",
        default="all",
        choices=[
            "all",
            "0",
            "manifest",
            "init",
            "1a",
            "1b",
            "1c",
            "2",
            "3",
            "4",
            "phase4",
            "pitch",
            "citations",
            "depth",
            "review",
            "decision-log",
            "scaffold",
        ],
    )

    return parser


def _resolve_artifact_root(workspace_root: Path, artifacts_root: str) -> Path:
    root_path = Path(artifacts_root)
    if root_path.is_absolute():
        return root_path
    return workspace_root / root_path


def _resolve_problem_statement_text(args: argparse.Namespace) -> str | None:
    if getattr(args, "problem_statement", None):
        return str(args.problem_statement)
    file_path = getattr(args, "problem_statement_file", None)
    if not file_path:
        return None
    return Path(file_path).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    workspace_root = Path(args.workspace_root).resolve()
    artifacts_root = _resolve_artifact_root(workspace_root, args.artifacts_root)

    try:
        if args.command == "meta-init":
            result = run_meta_init(
                workspace_root=workspace_root,
                artifacts_root=artifacts_root,
                project_name=args.project_name,
                problem_domain=args.problem_domain,
                project_type=args.project_type,
                problem_statement=_resolve_problem_statement_text(args),
                force=args.force,
            )
        elif args.command == "ingest":
            result = run_ingest(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                scope=args.scope,
            )
        elif args.command == "ingest-precheck":
            result = run_ingest_precheck(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                scope=args.scope,
            )
        elif args.command == "ingest-postcheck":
            result = run_ingest_postcheck(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
            )
        elif args.command == "ingest-validate":
            result = validate_all_findings(artifacts_root=artifacts_root)
            if result["total_issues"]:
                print(json.dumps(result, indent=2))
                return 2
        elif args.command == "research-breadth":
            result = run_research_breadth(artifacts_root=artifacts_root, workspace_root=workspace_root)
        elif args.command == "research-depth":
            result = run_research_depth(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                force_regenerate_v2=args.force_regenerate_v2,
            )
        elif args.command == "review":
            result = run_review(artifacts_root=artifacts_root)
        elif args.command == "enrich-wiki":
            result = run_enrich_wiki(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                version=args.version,
            )
        elif args.command == "wiki-link":
            result = run_wiki_link(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                version=args.version,
            )
        elif args.command == "propose-relationships":
            result = run_propose_relationships(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
            )
        elif args.command == "apply-relationships":
            result = run_apply_relationships(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                version=args.version,
            )
        elif args.command == "audit-requirements":
            result = run_audit_requirements(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                decision_log_version=args.decision_log_version,
            )
        elif args.command == "elicit-vision":
            if args.start:
                result = run_elicit_vision_start(
                    artifacts_root=artifacts_root,
                    workspace_root=workspace_root,
                    override_iterate_reason=args.override_iterate,
                )
            else:  # --finalize (argparse enforces one of --start/--finalize)
                result = run_elicit_vision_finalize(
                    artifacts_root=artifacts_root,
                    workspace_root=workspace_root,
                )
        elif args.command == "scaffold":
            result = run_scaffold(
                artifacts_root=artifacts_root,
                decision_log_version=args.decision_log_version,
            )
        elif args.command == "phase4-finalize":
            if args.start:
                result = run_phase4_start(
                    artifacts_root=artifacts_root,
                    workspace_root=workspace_root,
                    decision_log_version=args.decision_log_version,
                )
            else:
                # --finalize or neither (legacy fallback inside run_phase4_finalize).
                result = run_phase4_finalize(
                    artifacts_root=artifacts_root,
                    workspace_root=workspace_root,
                    decision_log_version=args.decision_log_version,
                )
        elif args.command == "wiki-update":
            result = run_wiki_update(artifacts_root=artifacts_root, workspace_root=workspace_root)
        elif args.command == "run-all":
            result = run_all(
                workspace_root=workspace_root,
                artifacts_root=artifacts_root,
                project_name=args.project_name,
                problem_domain=args.problem_domain,
                project_type=args.project_type,
                problem_statement=_resolve_problem_statement_text(args),
                use_case=args.use_case,
                clean_first=args.clean_first,
                force=args.force,
            )
        elif args.command == "clean-workspace":
            result = run_clean_workspace(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                target_stage=args.target_stage,
            )
        elif args.command == "track-seeds":
            result = check_and_update_seeds(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
            )
        elif args.command == "wiki-browse":
            result = run_wiki_browser(
                artifacts_root=artifacts_root,
                port=args.port,
                no_open=args.no_open,
                prefer_v1=args.prefer_v1,
            )
        elif args.command == "stage2-reentry":
            sections = (
                [s.strip() for s in args.sections.split(",") if s.strip()]
                if args.sections
                else None
            )
            result = run_stage2_reentry(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                reason=args.reason,
                sections=sections,
                from_request=args.from_request,
            )
        elif args.command == "finalize-reentry":
            result = run_finalize_reentry(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                version=args.version,
            )
        elif args.command == "validate-stage":
            paths = build_paths(artifacts_root)
            issues = validate_stage(paths, stage=args.stage)
            result = {"stage": args.stage, "issue_count": len(issues), "issues": issues}
            if issues:
                print(json.dumps(result, indent=2))
                return 2
        else:  # pragma: no cover
            parser.error(f"Unsupported command: {args.command}")
            return 1

        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
