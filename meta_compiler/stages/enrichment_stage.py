"""enrich-wiki: Prepare a work plan for the wiki-synthesizer agent (Phase C1).

Deterministic prep only. Walks every v2 concept page, identifies the findings
JSON files referenced via the page's `sources:` frontmatter, and writes a YAML
work plan listing one item per page. The `wiki-synthesizer` agent consumes the
work plan and rewrites each page's prose body with cross-source synthesis,
preserving frontmatter, citation IDs, and source attribution.

Pages already touched by the user (per `wiki_edit_manifest.is_user_edited`) are
flagged but not removed from the plan — the prompt is responsible for honoring
the preservation hint.

Per design (see `.github/docs/stage-2-hardening.md` §13 and the Phase C plan),
enrichment writes only to v2 so Stage 1A re-runs never clobber synthesized
prose. Every write must be registered via
`wiki_edit_manifest.record_write(paths, page_path, "enrichment")`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactPaths, build_paths, ensure_layout
from ..io import dump_yaml, parse_frontmatter
from ..utils import iso_now, read_text_safe
from .. import wiki_edit_manifest


def _list_concept_pages(paths: ArtifactPaths) -> list[Path]:
    if not paths.wiki_v2_pages_dir.exists():
        return []
    return sorted(paths.wiki_v2_pages_dir.glob("*.md"))


def _findings_path_for(paths: ArtifactPaths, citation_id: str) -> Path | None:
    candidate = paths.findings_dir / f"{citation_id}.json"
    return candidate if candidate.exists() else None


def _read_page_sources(page_path: Path) -> tuple[dict[str, Any], list[str]]:
    text = read_text_safe(page_path)
    frontmatter, _body = parse_frontmatter(text)
    sources = frontmatter.get("sources", []) if isinstance(frontmatter, dict) else []
    if not isinstance(sources, list):
        sources = []
    return frontmatter, [str(item) for item in sources if str(item).strip()]


def _related_index(pages: list[Path]) -> list[dict[str, str]]:
    """List of {id, file, display_name} for cross-link hints to the synthesizer."""
    index: list[dict[str, str]] = []
    for page in pages:
        text = read_text_safe(page)
        frontmatter, body = parse_frontmatter(text)
        page_id = str(frontmatter.get("id") or page.stem)
        display = page_id
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                display = stripped[2:].strip() or page_id
                break
        index.append(
            {
                "id": page_id,
                "file": page.name,
                "display_name": display,
            }
        )
    return index


def run_enrich_wiki(
    artifacts_root: Path,
    workspace_root: Path,
    version: int = 2,
) -> dict[str, Any]:
    """Prepare the work plan for the wiki-synthesizer agent.

    Parameters
    ----------
    artifacts_root : Path
        Path to the workspace-artifacts directory.
    workspace_root : Path
        Workspace root (kept for symmetry with other stages).
    version : int
        Wiki version to enrich. Only `2` is supported — v1 is the regenerable
        baseline and must remain templated.
    """
    if version != 2:
        raise ValueError(
            f"enrich-wiki only supports --version 2, got {version}. "
            "v1 is the regenerable baseline and must remain templated."
        )

    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    pages = _list_concept_pages(paths)
    if not pages:
        return {
            "status": "no_pages",
            "version": version,
            "work_items": 0,
            "work_plan_path": None,
        }

    related_index = _related_index(pages)
    work_items: list[dict[str, Any]] = []
    pages_without_findings: list[str] = []
    pages_user_edited: list[str] = []

    for page in pages:
        frontmatter, source_ids = _read_page_sources(page)
        page_id = str(frontmatter.get("id") or page.stem)
        page_type = str(frontmatter.get("type") or "concept")

        # Source pages and meta pages (gap-remediation, etc.) are not concept
        # pages and skip the synthesis pass — they are either auto-managed or
        # purely structural.
        if page_type != "concept":
            continue

        findings_paths: list[str] = []
        missing_findings: list[str] = []
        for source_id in source_ids:
            found = _findings_path_for(paths, source_id)
            if found is None:
                missing_findings.append(source_id)
            else:
                findings_paths.append(found.relative_to(paths.root).as_posix())

        if not findings_paths:
            pages_without_findings.append(page.name)

        is_edited = wiki_edit_manifest.is_user_edited(paths, page)
        if is_edited:
            pages_user_edited.append(page.name)

        work_items.append(
            {
                "page_id": page_id,
                "page_file": page.name,
                "page_path": page.relative_to(paths.root).as_posix(),
                "source_citation_ids": source_ids,
                "findings_paths": findings_paths,
                "missing_findings_for": missing_findings,
                "user_edited": is_edited,
            }
        )

    work_plan = {
        "wiki_enrichment_work_plan": {
            "version": 1,
            "generated_at": iso_now(),
            "wiki_version": version,
            "artifacts_root": str(paths.root),
            "v2_pages_dir": str(paths.wiki_v2_pages_dir.relative_to(paths.root).as_posix()),
            "edit_manifest_path": str(
                wiki_edit_manifest.manifest_path(paths).relative_to(paths.root).as_posix()
            ),
            "related_pages": related_index,
            "work_items": work_items,
            "counts": {
                "pages_total": len(pages),
                "concept_pages": len(work_items),
                "pages_without_findings": len(pages_without_findings),
                "pages_user_edited": len(pages_user_edited),
            },
        }
    }

    plan_dir = paths.runtime_dir / "wiki_enrichment"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "work_plan.yaml"
    dump_yaml(plan_path, work_plan)

    return {
        "status": "ready_for_orchestrator",
        "version": version,
        "work_items": len(work_items),
        "pages_without_findings": pages_without_findings,
        "pages_user_edited": pages_user_edited,
        "work_plan_path": str(plan_path.relative_to(paths.root).as_posix()),
        "instruction": (
            "Invoke the wiki-enrichment prompt. It will fan out wiki-synthesizer "
            "subagents per page, rewrite each page's prose body with cross-source "
            "synthesis, and register every write in the v2 edit manifest. "
            "Pages flagged user_edited=true must not be overwritten without "
            "explicit operator approval."
        ),
    }


def validate_synthesis_payload(
    payload: dict[str, Any],
    *,
    page_id: str,
    expected_citation_ids: set[str],
) -> list[str]:
    """Schema validator for the JSON the wiki-synthesizer subagent returns.

    Used both at runtime by the orchestrator (recommended) and in tests. The
    synthesizer must return a JSON object with keys for each enriched section
    and a `citations_used` list. Every cited ID must be present in
    `expected_citation_ids` (the page's existing `sources:` frontmatter) so
    citation IDs survive the transformation as required by the project's
    evidence rules.
    """
    issues: list[str] = []
    if not isinstance(payload, dict):
        return [f"{page_id}: synthesis payload must be a JSON object"]

    required_sections = {"definition", "formalism", "key_claims", "open_questions"}
    missing = required_sections - payload.keys()
    if missing:
        issues.append(f"{page_id}: missing synthesis sections: {sorted(missing)}")

    for section in required_sections:
        value = payload.get(section)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{page_id}: section {section!r} must be a non-empty string")

    citations_used = payload.get("citations_used", [])
    if not isinstance(citations_used, list):
        issues.append(f"{page_id}: citations_used must be a list")
        citations_used = []

    cited = {str(item).strip() for item in citations_used if str(item).strip()}
    extraneous = cited - expected_citation_ids
    if extraneous:
        issues.append(
            f"{page_id}: synthesis cited unknown sources {sorted(extraneous)}; "
            f"only {sorted(expected_citation_ids)} are registered for this page"
        )

    if expected_citation_ids and not cited:
        issues.append(
            f"{page_id}: synthesis must cite at least one source from "
            f"{sorted(expected_citation_ids)}"
        )

    return issues


__all__ = ["run_enrich_wiki", "validate_synthesis_payload"]
