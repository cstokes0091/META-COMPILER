from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import (
    build_paths,
    derive_wiki_name,
    ensure_layout,
    latest_decision_log_path,
    load_manifest,
    save_manifest,
)
from ..io import dump_yaml, load_yaml, parse_frontmatter, render_frontmatter
from ..utils import iso_now, read_text_safe, sha256_bytes
from ..validation import validate_decision_log
from ..wiki_interface import WikiQueryInterface
from ..wiki_lifecycle import write_index
from ..wiki_rendering import inject_wiki_nav


def _problem_statement_hash(workspace_root: Path) -> str:
    statement_path = workspace_root / "PROBLEM_STATEMENT.md"
    if not statement_path.exists():
        return sha256_bytes(b"")
    return sha256_bytes(read_text_safe(statement_path).encode("utf-8"))


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{text}{suffix}: ").strip()
    return raw if raw else default


def _yes_no(text: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    raw = input(f"{text} ({marker}): ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _collect_citations(wiki: WikiQueryInterface, topic_hint: str) -> list[str]:
    query = _prompt("Wiki query for citation support", topic_hint).strip()
    if query:
        results = wiki.search_wiki(query, limit=5)
        if results:
            print("\nTop wiki matches:")
            for idx, row in enumerate(results, start=1):
                print(f"  {idx}. {row['concept_id']} (score={row['relevance_score']})")
            print("")

            suggested: list[str] = []
            for row in results[:3]:
                for citation in wiki.get_citations(row["concept_id"]):
                    citation_id = citation.get("citation_id")
                    if isinstance(citation_id, str) and citation_id not in suggested:
                        suggested.append(citation_id)
            if suggested:
                print("Suggested citation IDs:", ", ".join(suggested))

    raw = _prompt("Citation IDs (comma-separated)", "")
    return _csv(raw)


def _save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    dump_yaml(path, payload)


def _apply_wiki_name_to_pages(paths, wiki_name: str) -> None:
    page_sets = [
        (paths.wiki_v1_pages_dir, paths.wiki_v1_dir / "index.md", "Wiki v1 Index"),
        (paths.wiki_v2_pages_dir, paths.wiki_v2_dir / "index.md", "Wiki v2 Index"),
    ]

    for pages_dir, index_path, title in page_sets:
        if not pages_dir.exists():
            continue

        page_paths = sorted(pages_dir.glob("*.md"))
        for page_path in page_paths:
            text = read_text_safe(page_path)
            frontmatter, body = parse_frontmatter(text)
            if not frontmatter:
                continue

            updated_body = inject_wiki_nav(body, wiki_name)
            updated_text = "---\n" + render_frontmatter(frontmatter) + "\n---\n" + updated_body.rstrip() + "\n"
            if updated_text != text:
                page_path.write_text(updated_text, encoding="utf-8")

        if page_paths:
            write_index(
                pages_dir=pages_dir,
                index_path=index_path,
                title=title,
            )


def _new_decision_log(
    manifest: dict,
    workspace_root: Path,
    use_case: str,
    version: int,
    parent_version: int | None,
    reason_for_revision: str | None,
) -> dict[str, Any]:
    wm = manifest["workspace_manifest"]
    return {
        "decision_log": {
            "meta": {
                "project_name": wm.get("name", "META-COMPILER Project"),
                "project_type": wm.get("project_type", "algorithm"),
                "created": iso_now(),
                "version": version,
                "parent_version": parent_version,
                "reason_for_revision": reason_for_revision,
                "problem_statement_hash": _problem_statement_hash(workspace_root),
                "wiki_version": wm.get("wiki", {}).get("version", ""),
                "use_case": use_case,
            },
            "conventions": [],
            "architecture": [],
            "scope": {
                "in_scope": [],
                "out_of_scope": [],
            },
            "requirements": [],
            "open_items": [],
            "agents_needed": [],
        }
    }


def _add_conventions(log: dict[str, Any], wiki: WikiQueryInterface) -> None:
    while _yes_no("Add a convention", default=False):
        convention = {
            "name": _prompt("Convention name", "Default Convention"),
            "domain": _prompt("Convention domain (math|code|citation|terminology)", "code"),
            "choice": _prompt("Chosen convention", ""),
            "rationale": _prompt("Rationale", ""),
            "citations": _collect_citations(wiki, "convention"),
        }
        log["decision_log"]["conventions"].append(convention)


def _add_architecture(log: dict[str, Any], wiki: WikiQueryInterface) -> None:
    while _yes_no("Add an architecture component", default=False):
        alternatives_rejected: list[dict[str, str]] = []
        for alt in _csv(_prompt("Rejected alternatives (comma-separated)", "")):
            alternatives_rejected.append(
                {
                    "name": alt,
                    "reason": _prompt(f"Reason for rejecting {alt}", "Not selected for current constraints."),
                }
            )

        row = {
            "component": _prompt("Component name", "core-component"),
            "approach": _prompt("Chosen approach", ""),
            "alternatives_rejected": alternatives_rejected,
            "constraints_applied": _csv(_prompt("Constraints applied (comma-separated)", "")),
            "citations": _collect_citations(wiki, "architecture"),
        }
        log["decision_log"]["architecture"].append(row)


def _add_scope(log: dict[str, Any]) -> None:
    while _yes_no("Add an in-scope item", default=False):
        log["decision_log"]["scope"]["in_scope"].append(
            {
                "item": _prompt("In-scope item", ""),
                "rationale": _prompt("Rationale", ""),
            }
        )

    while _yes_no("Add an out-of-scope item", default=False):
        log["decision_log"]["scope"]["out_of_scope"].append(
            {
                "item": _prompt("Out-of-scope item", ""),
                "rationale": _prompt("Rationale", ""),
                "revisit_if": _prompt("Revisit condition", ""),
            }
        )


def _next_req_id(requirements: list[dict[str, Any]]) -> str:
    return f"REQ-{len(requirements) + 1:03d}"


def _add_requirements(log: dict[str, Any], wiki: WikiQueryInterface) -> None:
    requirements = log["decision_log"]["requirements"]
    while _yes_no("Add a requirement", default=False):
        row = {
            "id": _next_req_id(requirements),
            "description": _prompt("Requirement description", ""),
            "source": _prompt("Requirement source (user|derived)", "user"),
            "citations": _collect_citations(wiki, "requirement"),
            "verification": _prompt("Verification method", ""),
        }
        requirements.append(row)


def _add_open_items(log: dict[str, Any]) -> None:
    while _yes_no("Add an open item", default=False):
        log["decision_log"]["open_items"].append(
            {
                "description": _prompt("Open item description", ""),
                "deferred_to": _prompt("Deferred to (implementation|future_work)", "implementation"),
                "owner": _prompt("Owner", "human"),
            }
        )


def _add_agents(log: dict[str, Any]) -> None:
    while _yes_no("Add an agent role", default=False):
        log["decision_log"]["agents_needed"].append(
            {
                "role": _prompt("Agent role", "implementer"),
                "responsibility": _prompt("Responsibility", ""),
                "reads": _csv(_prompt("Reads artifact types (comma-separated)", "wiki,decision_log")),
                "writes": _csv(_prompt("Writes artifact types (comma-separated)", "scaffold")),
                "key_constraints": _csv(_prompt("Key constraints (comma-separated)", "")),
            }
        )


def _auto_fill(log: dict[str, Any], wiki: WikiQueryInterface, context_note: str) -> None:
    base_query = context_note if context_note.strip() else "core"
    hits = wiki.search_wiki(base_query, limit=3)

    citations: list[str] = []
    for hit in hits:
        for citation in wiki.get_citations(hit["concept_id"]):
            cid = citation.get("citation_id")
            if isinstance(cid, str) and cid not in citations:
                citations.append(cid)

    log["decision_log"]["conventions"] = [
        {
            "name": "Code style",
            "domain": "code",
            "choice": "Prefer clear modular Python with explicit validation",
            "rationale": "Maintain deterministic, auditable stage artifacts.",
            "citations": citations[:2],
        }
    ]
    log["decision_log"]["architecture"] = [
        {
            "component": "workflow-orchestrator",
            "approach": "Artifact-driven stage transitions with strict schema checks",
            "alternatives_rejected": [
                {"name": "chat-history-coupled flow", "reason": "Violates fresh-context constraint."}
            ],
            "constraints_applied": ["fresh context", "artifact-only handoff", "strict validation"],
            "citations": citations[:3],
        }
    ]
    log["decision_log"]["scope"]["in_scope"] = [
        {"item": "Stage 2 decision capture", "rationale": "Required for scaffold generation."},
        {"item": "Stage 3 scaffold generation", "rationale": "Build reusable project workshop output."},
    ]
    log["decision_log"]["scope"]["out_of_scope"] = [
        {
            "item": "Full implementation execution",
            "rationale": "Current tranche focuses on setup workshop, not final algorithm/report execution.",
            "revisit_if": "When scaffolded project begins active implementation phase.",
        }
    ]
    log["decision_log"]["requirements"] = [
        {
            "id": "REQ-001",
            "description": "Decision log must be schema-valid and citation-traceable.",
            "source": "derived",
            "citations": citations[:2],
            "verification": "Run validate-stage --stage 2 with zero issues.",
        },
        {
            "id": "REQ-002",
            "description": "Scaffold generator must consume Decision Log only.",
            "source": "derived",
            "citations": citations[:2],
            "verification": "Run scaffold command and verify generated files include decision traces.",
        },
    ]
    log["decision_log"]["open_items"] = [
        {
            "description": "Decide whether to include post-scaffold wiki-update automation in this cycle.",
            "deferred_to": "future_work",
            "owner": "human",
        }
    ]
    log["decision_log"]["agents_needed"] = [
        {
            "role": "scaffold-generator",
            "responsibility": "Generate project structure and agent specs from Decision Log.",
            "reads": ["decision_log"],
            "writes": ["scaffold"],
            "key_constraints": ["no raw source access", "trace every instruction to decision log"],
        }
    ]


def run_elicit_vision(
    artifacts_root: Path,
    workspace_root: Path,
    use_case: str,
    resume: bool = False,
    non_interactive: bool = False,
    context_note: str = "",
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    manifest = load_manifest(paths)
    if not manifest:
        raise RuntimeError("Manifest not found. Run meta-init first.")

    wm = manifest["workspace_manifest"]
    wiki = wm.setdefault("wiki", {})
    if not wiki.get("name"):
        wiki["name"] = derive_wiki_name(
            str(wm.get("name", "META-COMPILER")),
            str(wm.get("project_type", "hybrid")),
        )
        _apply_wiki_name_to_pages(paths, str(wiki["name"]))
        save_manifest(paths, manifest)

    latest = latest_decision_log_path(paths)
    if latest is None:
        version = 1
        parent_version = None
        reason_for_revision = None
    else:
        version = latest[0] + 1
        parent_version = latest[0]
        reason_for_revision = f"Revision for use case: {use_case}"

    draft_path = paths.runtime_dir / "decision_log_draft.yaml"
    if resume and draft_path.exists():
        log = load_yaml(draft_path)
        if not log:
            raise RuntimeError("Draft resume requested but draft is empty.")
    else:
        log = _new_decision_log(
            manifest=manifest,
            workspace_root=workspace_root,
            use_case=use_case,
            version=version,
            parent_version=parent_version,
            reason_for_revision=reason_for_revision,
        )

    wiki = WikiQueryInterface(paths=paths, prefer_v2=True)

    if non_interactive:
        _auto_fill(log, wiki, context_note=context_note)
        _save_checkpoint(draft_path, log)
    else:
        print("\nStage 2: Vision Elicitation")
        print("Answer prompts to build the Decision Log. Use Ctrl+C to stop and resume later.")

        _add_conventions(log, wiki)
        _save_checkpoint(draft_path, log)

        _add_architecture(log, wiki)
        _save_checkpoint(draft_path, log)

        _add_scope(log)
        _save_checkpoint(draft_path, log)

        _add_requirements(log, wiki)
        _save_checkpoint(draft_path, log)

        _add_open_items(log)
        _save_checkpoint(draft_path, log)

        _add_agents(log)
        _save_checkpoint(draft_path, log)

    issues = validate_decision_log(log)
    if issues:
        raise RuntimeError("Decision Log validation failed:\n" + "\n".join(issues))

    decision_log_path = paths.decision_logs_dir / f"decision_log_v{log['decision_log']['meta']['version']}.yaml"
    dump_yaml(decision_log_path, log)

    if draft_path.exists():
        draft_path.unlink()

    wm = manifest["workspace_manifest"]
    decision_logs = wm.setdefault("decision_logs", [])
    current_version = log["decision_log"]["meta"]["version"]
    existing = None
    for row in decision_logs:
        if isinstance(row, dict) and row.get("version") == current_version:
            existing = row
            break

    entry = {
        "version": current_version,
        "created": log["decision_log"]["meta"]["created"],
        "parent_version": log["decision_log"]["meta"].get("parent_version"),
        "reason_for_revision": log["decision_log"]["meta"].get("reason_for_revision"),
        "use_case": use_case,
        "scaffold_path": None,
    }
    if existing is None:
        decision_logs.append(entry)
    else:
        existing.update(entry)

    research = wm.setdefault("research", {})
    research["last_completed_stage"] = "2"
    save_manifest(paths, manifest)

    return {
        "decision_log_path": str(decision_log_path),
        "version": current_version,
        "conventions": len(log["decision_log"]["conventions"]),
        "architecture": len(log["decision_log"]["architecture"]),
        "requirements": len(log["decision_log"]["requirements"]),
    }
