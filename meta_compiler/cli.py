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
from .stages.relationship_stage import (
    run_apply_relationships,
    run_propose_relationships,
)
from .wiki_linking import run_wiki_link
from .stages.elicit_stage import (
    run_elicit_vision_finalize,
    run_elicit_vision_start,
)
from .stages.code_seed_stage import run_add_code_seed, run_bind_code_seed
from .stages.ingest_stage import (
    run_ingest,
    run_ingest_postcheck,
    run_ingest_precheck,
    validate_all_findings,
)
from .stages.init_stage import run_meta_init
from .stages.migrate_decision_log_stage import (
    run_migrate_decision_log_apply,
    run_migrate_decision_log_plan,
)
from .stages.capability_compile_stage import run_capability_compile
from .stages.contract_extract_stage import run_contract_extract
from .stages.phase4_stage import run_phase4_finalize, run_phase4_start
from .stages.final_synthesis_stage import (
    run_final_synthesize_finalize,
    run_final_synthesize_start,
)
from .stages.plan_implementation_stage import (
    run_plan_implementation_finalize,
    run_plan_implementation_start,
)
from .stages.wiki_update_stage import run_wiki_update
from .stages.skill_synthesis_stage import run_skill_synthesis
from .stages.workspace_bootstrap_stage import run_workspace_bootstrap
from .stages.review_stage import run_review
from .stages.run_all_stage import run_all
from .stages.scaffold_stage import run_scaffold
from .stages.seed_tracker import check_and_update_seeds
from .stages.stage2_reentry import run_finalize_reentry, run_stage2_reentry
from .stages.concept_reconciliation_stage import (
    run_wiki_apply_cross_source_synthesis,
    run_wiki_apply_reconciliation,
    run_wiki_cross_source_synthesize,
    run_wiki_reconcile_concepts,
)
from .stages.wiki_search_stage import (
    run_wiki_search_apply,
    run_wiki_search_preflight,
)
from .project_types import project_type_choices
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
        choices=project_type_choices(),
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

    wiki_update_parser = subparsers.add_parser(
        "wiki-update",
        help=(
            "Refresh the wiki index after new seeds land. "
            "Chains `ingest --scope new` + `research-breadth`."
        ),
    )
    _add_common_paths(wiki_update_parser)
    wiki_update_parser.add_argument(
        "--scope",
        default="new",
        choices=["all", "new"],
        help="Seed scope to scan during the ingest preflight (default: new)",
    )
    wiki_update_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Run research-breadth even when ingest reports new work items "
            "pending the orchestrator. Refreshes the index from existing "
            "findings without waiting for extraction."
        ),
    )

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

    add_code_seed_parser = subparsers.add_parser(
        "add-code-seed",
        help="Clone a git repo under seeds/code/<name>/ and pin it by commit SHA",
    )
    _add_common_paths(add_code_seed_parser)
    add_code_seed_parser.add_argument("--repo", required=True, help="Git remote URL to clone")
    add_code_seed_parser.add_argument(
        "--ref",
        required=True,
        help="Git ref to pin (tag, branch, or full commit SHA)",
    )
    add_code_seed_parser.add_argument(
        "--name",
        required=True,
        help="Slug for the seed directory (seeds/code/<name>/)",
    )
    add_code_seed_parser.add_argument(
        "--depth",
        type=int,
        default=None,
        help="Optional shallow-clone depth",
    )
    add_code_seed_parser.add_argument(
        "--submodules",
        action="store_true",
        help="Recursively initialise submodules after checkout",
    )
    add_code_seed_parser.add_argument(
        "--author-role",
        choices=["external", "user_authored"],
        default="external",
        help="Tag the binding so wiki-build-style-corpus can find user-authored seeds",
    )

    bind_code_seed_parser = subparsers.add_parser(
        "bind-code-seed",
        help="Record the current HEAD of an existing clone under seeds/code/<name>/",
    )
    _add_common_paths(bind_code_seed_parser)
    bind_code_seed_parser.add_argument(
        "--path",
        required=True,
        help="Workspace-relative path to the existing repo under seeds/code/",
    )
    bind_code_seed_parser.add_argument(
        "--name",
        default=None,
        help="Override the seed name (defaults to the directory name)",
    )
    bind_code_seed_parser.add_argument(
        "--ref",
        default=None,
        help="Symbolic ref to record (defaults to the resolved commit SHA)",
    )
    bind_code_seed_parser.add_argument(
        "--author-role",
        choices=["external", "user_authored"],
        default="external",
        help="Tag the binding so wiki-build-style-corpus can find user-authored seeds",
    )

    tag_seed_parser = subparsers.add_parser(
        "tag-seed",
        help="Set author_role on an existing doc seed in source_bindings.yaml",
    )
    _add_common_paths(tag_seed_parser)
    tag_seed_parser.add_argument(
        "--path",
        required=True,
        help="Workspace-relative path of the seed (must already be tracked in source_bindings.bindings)",
    )
    tag_seed_parser.add_argument(
        "--author-role",
        required=True,
        choices=["external", "user_authored"],
        help="Author role to record on this seed binding",
    )

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

    plan_parser = subparsers.add_parser(
        "plan-implementation",
        help=(
            "Stage 2.5: bundle planning brief (--start) or extract the plan "
            "markdown (--finalize) for Stage 3 capability compile"
        ),
    )
    _add_common_paths(plan_parser)
    plan_mode_group = plan_parser.add_mutually_exclusive_group(required=True)
    plan_mode_group.add_argument(
        "--start",
        action="store_true",
        help="Preflight: render runtime/plan/brief.md from the decision log",
    )
    plan_mode_group.add_argument(
        "--finalize",
        action="store_true",
        help=(
            "Postflight: validate decision-logs/implementation_plan_v{N}.md "
            "and extract the capability_plan YAML"
        ),
    )
    plan_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to plan against (default: latest)",
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
    elicit_parser.add_argument(
        "--skip-wiki-search",
        action="store_true",
        help=(
            "Skip the Step 0 wiki-search auto-fire (escape hatch when results.yaml "
            "is unreachable; brief.md will note the missing evidence)."
        ),
    )

    scaffold_parser = subparsers.add_parser("scaffold", help="Run Stage 3 scaffold generation")
    _add_common_paths(scaffold_parser)
    scaffold_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to scaffold (default: latest)",
    )

    compile_caps_parser = subparsers.add_parser(
        "compile-capabilities",
        help=(
            "Stage 3.1: compile decision log + findings into scaffolds/v{N}/capabilities.yaml. "
            "Runnable standalone for debugging; also invoked by `scaffold` as of Commit 8."
        ),
    )
    _add_common_paths(compile_caps_parser)
    compile_caps_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to compile (default: latest)",
    )
    compile_caps_parser.add_argument(
        "--allow-empty-findings",
        action="store_true",
        help=(
            "Allow compiling against an empty wiki/findings/ directory. "
            "Normally permitted only for decision_log_v1 (bootstrap); pass this "
            "flag when testing against a fixture that intentionally lacks findings."
        ),
    )

    extract_contracts_parser = subparsers.add_parser(
        "extract-contracts",
        help=(
            "Stage 3.2: extract IO contracts from the decision log + capability graph. "
            "Writes scaffolds/v{N}/contracts/ and rewrites capabilities.yaml with "
            "real io_contract_ref values."
        ),
    )
    _add_common_paths(extract_contracts_parser)
    extract_contracts_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to extract contracts for (default: latest)",
    )

    synth_skills_parser = subparsers.add_parser(
        "synthesize-skills",
        help=(
            "Stage 3.3: render scaffolds/v{N}/skills/{name}/SKILL.md files + INDEX.md from "
            "the capability graph + contract library + findings."
        ),
    )
    _add_common_paths(synth_skills_parser)
    synth_skills_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to synthesize skills for (default: latest)",
    )

    bootstrap_parser = subparsers.add_parser(
        "workspace-bootstrap",
        help=(
            "Stage 3.4: wire the static agent palette, verification harness, "
            "execution/dispatch manifests, and output buckets."
        ),
    )
    _add_common_paths(bootstrap_parser)
    bootstrap_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version to bootstrap (default: latest)",
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
    # Pitch sub-loop controls. The deck is built in four steps; --pitch-step
    # lets the operator drive each independently when working with the
    # @pitch-writer agent. --pptx-template overrides the manifest's
    # `workspace_manifest.pitch.template_path` for one invocation.
    phase4_parser.add_argument(
        "--pitch-step",
        choices=["all", "evidence", "draft", "verify", "render"],
        default="all",
        help=(
            "Run only one phase of the pitch sub-loop. 'evidence' (alias 'draft') "
            "writes the evidence pack + pitch_request and stops; 'verify' checks "
            "slides.yaml fidelity without rendering; 'render' renders the .pptx; "
            "'all' (default) runs end-to-end and stops at the pitch-writer handoff "
            "when slides.yaml is absent."
        ),
    )
    phase4_parser.add_argument(
        "--pptx-template",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to a .pptx or .potx template the renderer inherits styling "
            "from. Overrides workspace_manifest.pitch.template_path. Relative "
            "paths resolve against --workspace-root."
        ),
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
        choices=project_type_choices(),
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

    reconcile_parser = subparsers.add_parser(
        "wiki-reconcile-concepts",
        help="Phase A preflight: write work plan + reconcile_request.yaml for concept reconciliation",
    )
    _add_common_paths(reconcile_parser)
    reconcile_parser.add_argument(
        "--version",
        type=int,
        default=2,
        choices=[2],
        help="Wiki version to reconcile. Only 2 is supported.",
    )

    apply_reconcile_parser = subparsers.add_parser(
        "wiki-apply-reconciliation",
        help="Phase A postflight: merge reconciler proposal into v2 pages (canonicals + alias stubs)",
    )
    _add_common_paths(apply_reconcile_parser)
    apply_reconcile_parser.add_argument(
        "--version",
        type=int,
        default=2,
        choices=[2],
        help="Wiki version. Only 2 is supported.",
    )

    wiki_search_parser = subparsers.add_parser(
        "wiki-search",
        help="Auto-fired Stage 2 wiki evidence pull (preflight or --apply postflight)",
    )
    _add_common_paths(wiki_search_parser)
    wiki_search_mx = wiki_search_parser.add_mutually_exclusive_group(required=True)
    wiki_search_mx.add_argument(
        "--scope",
        choices=["stage2"],
        help="Run preflight: write work_plan.yaml + wiki_search_request.yaml",
    )
    wiki_search_mx.add_argument(
        "--apply",
        action="store_true",
        help="Run postflight: consolidate per-topic results into results.yaml",
    )
    wiki_search_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the freshness cache and re-run preflight",
    )

    cross_source_parser = subparsers.add_parser(
        "wiki-cross-source-synthesize",
        help="Phase B preflight: write work plan for cross-source definition synthesis",
    )
    _add_common_paths(cross_source_parser)
    cross_source_parser.add_argument(
        "--version",
        type=int,
        default=2,
        choices=[2],
        help="Wiki version. Only 2 is supported.",
    )

    apply_cross_source_parser = subparsers.add_parser(
        "wiki-apply-cross-source-synthesis",
        help=(
            "Phase B postflight: validate per-page subagent JSON returns and rewrite v2 "
            "Definition / Key Claims / Open Questions sections deterministically"
        ),
    )
    _add_common_paths(apply_cross_source_parser)
    apply_cross_source_parser.add_argument(
        "--version",
        type=int,
        default=2,
        choices=[2],
        help="Wiki version. Only 2 is supported.",
    )

    final_synthesize_start_parser = subparsers.add_parser(
        "final-synthesize-start",
        help=(
            "Stage 4 final-synthesis preflight: walk executions/v{N}/work/, classify "
            "fragments, write work_plan + synthesis_request for the orchestrator"
        ),
    )
    _add_common_paths(final_synthesize_start_parser)
    final_synthesize_start_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version (default: latest)",
    )

    final_synthesize_finalize_parser = subparsers.add_parser(
        "final-synthesize-finalize",
        help=(
            "Stage 4 final-synthesis postflight: validate per-modality subagent "
            "returns and assemble executions/v{N}/final/<bucket>/"
        ),
    )
    _add_common_paths(final_synthesize_finalize_parser)
    final_synthesize_finalize_parser.add_argument(
        "--decision-log-version",
        type=int,
        default=None,
        help="Decision log version (default: read from work plan)",
    )
    final_synthesize_finalize_parser.add_argument(
        "--allow-req-drop",
        action="append",
        default=[],
        help=(
            "REQ-NNN id(s) that may be absent from the assembled tree even though "
            "they appear in work fragments. Repeatable; comma-separated values "
            "also accepted (e.g. --allow-req-drop REQ-007 --allow-req-drop "
            "REQ-009,REQ-012)."
        ),
    )
    final_synthesize_finalize_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite executions/v{N}/final/ even if it contains files edited "
            "after the last final_synthesis_report.yaml"
        ),
    )

    style_corpus_parser = subparsers.add_parser(
        "wiki-build-style-corpus",
        help="Scan user_authored seeds + findings; emit wiki/style/style_corpus.md",
    )
    _add_common_paths(style_corpus_parser)

    run_workflow_parser = subparsers.add_parser(
        "run-workflow",
        help="Invoke a workflow scaffold's orchestrator/run_workflow.py against an input docx",
    )
    _add_common_paths(run_workflow_parser)
    run_workflow_parser.add_argument(
        "--input", required=True, help="Path to a .docx (typically under inbox/)"
    )
    run_workflow_parser.add_argument(
        "--task", default="reply-to-comments", help="Task name (default: reply-to-comments)"
    )
    run_workflow_parser.add_argument(
        "--scaffold-version",
        type=int,
        default=None,
        help="Pin a specific scaffold version (default: latest)",
    )

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

    migrate_parser = subparsers.add_parser(
        "migrate-decision-log",
        help=(
            "Migrate a v1 Decision Log to the typed-IO + code-architecture schema. "
            "Use --plan to draft the proposal, --apply to compile the new version."
        ),
    )
    _add_common_paths(migrate_parser)
    migrate_mode_group = migrate_parser.add_mutually_exclusive_group(required=True)
    migrate_mode_group.add_argument(
        "--plan",
        action="store_true",
        help="Write runtime/migration/proposal.yaml from the latest Decision Log",
    )
    migrate_mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Compile the proposal (and code_architecture_blocks.md) into a new Decision Log version",
    )

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
        elif args.command == "add-code-seed":
            result = run_add_code_seed(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                repo=args.repo,
                ref=args.ref,
                name=args.name,
                depth=args.depth,
                submodules=args.submodules,
                author_role=args.author_role,
            )
        elif args.command == "bind-code-seed":
            result = run_bind_code_seed(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                path=args.path,
                name=args.name,
                ref=args.ref,
                author_role=args.author_role,
            )
        elif args.command == "tag-seed":
            from .stages.style_corpus_stage import run_tag_seed

            result = run_tag_seed(
                artifacts_root=artifacts_root,
                seed_path=args.path,
                author_role=args.author_role,
            )
        elif args.command == "wiki-build-style-corpus":
            from .stages.style_corpus_stage import run_wiki_build_style_corpus

            result = run_wiki_build_style_corpus(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
            )
        elif args.command == "run-workflow":
            from .stages.workflow_stage import run_workflow

            result = run_workflow(
                artifacts_root=artifacts_root,
                input_path=args.input,
                task=args.task,
                scaffold_version=args.scaffold_version,
            )
        elif args.command == "research-breadth":
            result = run_research_breadth(artifacts_root=artifacts_root, workspace_root=workspace_root)
        elif args.command == "wiki-update":
            result = run_wiki_update(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                scope=args.scope,
                force=args.force,
            )
        elif args.command == "research-depth":
            result = run_research_depth(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                force_regenerate_v2=args.force_regenerate_v2,
            )
        elif args.command == "review":
            result = run_review(artifacts_root=artifacts_root)
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
        elif args.command == "plan-implementation":
            if args.start:
                result = run_plan_implementation_start(
                    artifacts_root=artifacts_root,
                    workspace_root=workspace_root,
                    decision_log_version=args.decision_log_version,
                )
            else:
                result = run_plan_implementation_finalize(
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
                    skip_wiki_search=args.skip_wiki_search,
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
        elif args.command == "compile-capabilities":
            result = run_capability_compile(
                artifacts_root=artifacts_root,
                decision_log_version=args.decision_log_version,
                allow_empty_findings=args.allow_empty_findings,
            )
        elif args.command == "extract-contracts":
            result = run_contract_extract(
                artifacts_root=artifacts_root,
                decision_log_version=args.decision_log_version,
            )
        elif args.command == "synthesize-skills":
            result = run_skill_synthesis(
                artifacts_root=artifacts_root,
                decision_log_version=args.decision_log_version,
            )
        elif args.command == "workspace-bootstrap":
            result = run_workspace_bootstrap(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
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
                    pitch_step=args.pitch_step,
                    pptx_template=args.pptx_template,
                )
        elif args.command == "wiki-reconcile-concepts":
            result = run_wiki_reconcile_concepts(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                version=args.version,
            )
        elif args.command == "wiki-apply-reconciliation":
            result = run_wiki_apply_reconciliation(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                version=args.version,
            )
        elif args.command == "wiki-search":
            if args.apply:
                result = run_wiki_search_apply(
                    artifacts_root=artifacts_root,
                    workspace_root=workspace_root,
                )
            else:
                result = run_wiki_search_preflight(
                    artifacts_root=artifacts_root,
                    workspace_root=workspace_root,
                    force=args.force,
                )
        elif args.command == "wiki-cross-source-synthesize":
            result = run_wiki_cross_source_synthesize(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                version=args.version,
            )
        elif args.command == "wiki-apply-cross-source-synthesis":
            result = run_wiki_apply_cross_source_synthesis(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                version=args.version,
            )
        elif args.command == "final-synthesize-start":
            result = run_final_synthesize_start(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                decision_log_version=args.decision_log_version,
            )
        elif args.command == "final-synthesize-finalize":
            allow_req_drop: list[str] = []
            for entry in args.allow_req_drop or []:
                for token in str(entry).split(","):
                    token = token.strip()
                    if token:
                        allow_req_drop.append(token)
            result = run_final_synthesize_finalize(
                artifacts_root=artifacts_root,
                workspace_root=workspace_root,
                decision_log_version=args.decision_log_version,
                allow_req_drop=tuple(allow_req_drop),
                force=args.force,
            )
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
        elif args.command == "migrate-decision-log":
            if args.plan:
                result = run_migrate_decision_log_plan(
                    artifacts_root=artifacts_root,
                    workspace_root=workspace_root,
                )
            else:
                result = run_migrate_decision_log_apply(
                    artifacts_root=artifacts_root,
                    workspace_root=workspace_root,
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
