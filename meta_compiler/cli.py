from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .artifacts import build_paths
from .stages.breadth_stage import run_research_breadth
from .stages.depth_stage import run_research_depth
from .stages.elicit_stage import run_elicit_vision
from .stages.init_stage import run_meta_init
from .stages.review_stage import run_review
from .stages.scaffold_stage import run_scaffold
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
        description="META-COMPILER orchestration CLI for stages 0/1A/1B/1C/2/3",
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
    init_parser.add_argument("--force", action="store_true", help="Overwrite initial templates")

    breadth_parser = subparsers.add_parser("research-breadth", help="Run Stage 1A breadth research")
    _add_common_paths(breadth_parser)

    depth_parser = subparsers.add_parser("research-depth", help="Run Stage 1B depth pass")
    _add_common_paths(depth_parser)

    review_parser = subparsers.add_parser("review", help="Run Stage 1C review panel")
    _add_common_paths(review_parser)

    elicit_parser = subparsers.add_parser("elicit-vision", help="Run Stage 2 vision elicitation")
    _add_common_paths(elicit_parser)
    elicit_parser.add_argument("--use-case", required=True, help="Decision log use case summary")
    elicit_parser.add_argument("--resume", action="store_true", help="Resume from Decision Log draft")
    elicit_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Auto-generate Decision Log from context note without interactive prompts",
    )
    elicit_parser.add_argument(
        "--context-note",
        default="",
        help="Context note used for non-interactive Stage 2 generation",
    )

    scaffold_parser = subparsers.add_parser("scaffold", help="Run Stage 3 scaffold generation")
    _add_common_paths(scaffold_parser)
    scaffold_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to scaffold (default: latest)",
    )

    wiki_update_parser = subparsers.add_parser("wiki-update", help="Incremental wiki expansion from new seeds")
    _add_common_paths(wiki_update_parser)

    wiki_browser_parser = subparsers.add_parser("wiki-browse", help="Open the local wiki browser")
    _add_common_paths(wiki_browser_parser)
    wiki_browser_parser.add_argument("--port", type=int, default=7777, help="Preferred local port")
    wiki_browser_parser.add_argument("--no-open", action="store_true", help="Start the server without opening a browser")
    wiki_browser_parser.add_argument("--prefer-v1", action="store_true", help="Prefer wiki v1 even when wiki v2 exists")

    reentry_parser = subparsers.add_parser("stage2-reentry", help="Revise Decision Log for changed scope")
    _add_common_paths(reentry_parser)
    reentry_parser.add_argument("--reason", required=True, help="Reason for revision")
    reentry_parser.add_argument(
        "--sections",
        required=True,
        help="Comma-separated sections to revise (conventions,architecture,scope,requirements,open_items,agents_needed)",
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
            "manifest",
            "1a",
            "1b",
            "1c",
            "2",
            "3",
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
                force=args.force,
            )
        elif args.command == "research-breadth":
            result = run_research_breadth(artifacts_root=artifacts_root, workspace_root=workspace_root)
        elif args.command == "research-depth":
            result = run_research_depth(artifacts_root=artifacts_root, workspace_root=workspace_root)
        elif args.command == "review":
            result = run_review(artifacts_root=artifacts_root)
        elif args.command == "elicit-vision":
            result = run_elicit_vision(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                use_case=args.use_case,
                resume=args.resume,
                non_interactive=args.non_interactive,
                context_note=args.context_note,
            )
        elif args.command == "scaffold":
            result = run_scaffold(
                artifacts_root=artifacts_root,
                decision_log_version=args.decision_log_version,
            )
        elif args.command == "wiki-update":
            result = run_wiki_update(artifacts_root=artifacts_root, workspace_root=workspace_root)
        elif args.command == "wiki-browse":
            result = run_wiki_browser(
                artifacts_root=artifacts_root,
                port=args.port,
                no_open=args.no_open,
                prefer_v1=args.prefer_v1,
            )
        elif args.command == "stage2-reentry":
            sections = [s.strip() for s in args.sections.split(",") if s.strip()]
            result = run_stage2_reentry(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                reason=args.reason,
                sections=sections,
            )
        elif args.command == "finalize-reentry":
            result = run_finalize_reentry(
                artifacts_root=artifacts_root,
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
