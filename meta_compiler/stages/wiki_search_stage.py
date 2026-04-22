"""Stage 2 wiki-search: auto-fired evidence injection.

Mirrors the concept-reconciliation preflight/orchestrator/postflight pattern.

Two CLI-facing entry points:

* ``run_wiki_search_preflight`` — extracts topics from the problem statement,
  the merged-gap report, and the Stage 1C handoff; writes
  ``runtime/stage2/wiki_search/work_plan.yaml`` plus
  ``wiki_search_request.yaml``. Returns ``status="ready_for_orchestrator"``
  when there is work for the ``wiki-search-orchestrator`` agent, or
  ``status="no_work"`` when the wiki is too sparse to search.

* ``run_wiki_search_apply`` — consumes per-topic ``T-NNN.yaml`` files emitted
  by ``wiki-searcher`` subagents, validates each against the topic-result
  schema, and consolidates them into ``runtime/stage2/wiki_search/results.yaml``.
  Records the consolidation via the wiki edit manifest with
  ``source: wiki_search``.

Freshness check: if a prior ``results.yaml`` exists whose
``problem_statement_hash`` and ``wiki_version`` match the current state, the
preflight returns ``status="cached"`` so callers (notably ``elicit-vision
--start``) can skip straight to brief rendering.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..artifacts import (
    ArtifactPaths,
    build_paths,
    compute_wiki_version,
    ensure_layout,
)
from ..io import dump_yaml, load_yaml, parse_frontmatter
from ..utils import iso_now, read_text_safe, sha256_strings, slugify


_DECISION_AREAS = (
    "conventions",
    "architecture",
    "code-architecture",
    "scope-in",
    "scope-out",
    "requirements",
    "open_items",
    "agents_needed",
)

_GAP_TYPE_TO_AREA = {
    "structural": "architecture",
    "connection": "architecture",
    "coverage": "scope-in",
    "evidence": "requirements",
    "epistemic": "open_items",
}

_TOPIC_RESULT_REQUIRED = {
    "topic_id",
    "generated_at",
    "concepts",
    "equations",
    "citations",
    "related_pages",
    "cross_source_notes",
}


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _problem_statement_hash(workspace_root: Path) -> str:
    ps = workspace_root / "PROBLEM_STATEMENT.md"
    if not ps.exists():
        return sha256_strings([])
    return sha256_strings([ps.read_text(encoding="utf-8")])


def _topic_seeds_from_problem_statement(workspace_root: Path) -> list[str]:
    """Extract candidate topic seeds (~3–8 distinct phrases) from the statement.

    Cheap: pull bullet items under ## Domain and Problem Space and
    ## Goals and Success Criteria sections; fall back to longest sentences.
    """
    ps = workspace_root / "PROBLEM_STATEMENT.md"
    if not ps.exists():
        return []
    text = ps.read_text(encoding="utf-8")
    seeds: list[str] = []
    seen: set[str] = set()

    interesting = ("## Domain and Problem Space", "## Goals and Success Criteria")
    for header in interesting:
        idx = text.find(header)
        if idx == -1:
            continue
        end = text.find("\n## ", idx + len(header))
        chunk = text[idx + len(header) : end if end != -1 else None]
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            value = stripped[2:].strip()
            if not value or value.lower() in seen:
                continue
            seeds.append(value)
            seen.add(value.lower())

    if seeds:
        return seeds[:12]

    # Fallback: take the longest sentences. Useful when the problem statement
    # is prose rather than a bulleted list.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    sentences.sort(key=len, reverse=True)
    return sentences[:6]


def _load_handoff_sources(paths: ArtifactPaths) -> list[str]:
    """Stage 1C handoff sometimes lists suggested external sources to look up."""
    handoff = paths.reviews_dir / "1a2_handoff.yaml"
    if not handoff.exists():
        return []
    payload = load_yaml(handoff) or {}
    if not isinstance(payload, dict):
        return []
    block = payload.get("handoff") if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        return []
    suggestions = block.get("suggested_sources") or block.get("sources") or []
    if not isinstance(suggestions, list):
        return []
    out: list[str] = []
    for item in suggestions:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            cid = item.get("citation_id") or item.get("id")
            if isinstance(cid, str):
                out.append(cid.strip())
    return out


def _gap_topics_by_area(paths: ArtifactPaths) -> dict[str, list[str]]:
    """Group merged-gap-report descriptions by Decision Log area."""
    gap_report_path = paths.reports_dir / "merged_gap_report.yaml"
    if not gap_report_path.exists():
        return {}
    payload = load_yaml(gap_report_path) or {}
    if not isinstance(payload, dict):
        return {}
    gaps = (payload.get("gap_report") or {}).get("gaps", [])
    out: dict[str, list[str]] = {}
    if not isinstance(gaps, list):
        return out
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        description = str(gap.get("description", "")).strip()
        if not description:
            continue
        area = _GAP_TYPE_TO_AREA.get(str(gap.get("type", "")), "architecture")
        out.setdefault(area, []).append(description)
    return out


def _seed_concepts_for_topic(paths: ArtifactPaths, topic: str) -> list[str]:
    """Return up to 5 canonical wiki page slugs whose display name matches the topic."""
    if not paths.wiki_v2_pages_dir.exists():
        return []
    topic_lower = topic.lower()
    matches: list[tuple[int, str]] = []
    for page in sorted(paths.wiki_v2_pages_dir.glob("*.md")):
        frontmatter, _ = parse_frontmatter(read_text_safe(page))
        if not isinstance(frontmatter, dict):
            continue
        if frontmatter.get("type") == "alias":
            continue
        slug = str(frontmatter.get("id") or page.stem)
        names = [str(frontmatter.get("display_name") or "")]
        aliases = frontmatter.get("aliases") or []
        if isinstance(aliases, list):
            names.extend(str(a) for a in aliases)
        score = 0
        for name in names:
            if not name:
                continue
            name_lower = name.lower()
            if name_lower == topic_lower:
                score = max(score, 100)
            elif name_lower in topic_lower or topic_lower in name_lower:
                score = max(score, 50)
            elif any(word for word in name_lower.split() if word in topic_lower):
                score = max(score, 10)
        if score > 0:
            matches.append((score, slug))
    matches.sort(reverse=True)
    return [slug for _, slug in matches[:5]]


def _build_work_items(
    paths: ArtifactPaths,
    workspace_root: Path,
) -> list[dict[str, Any]]:
    seeds = _topic_seeds_from_problem_statement(workspace_root)
    gap_topics = _gap_topics_by_area(paths)
    handoff_sources = _load_handoff_sources(paths)

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def emit(title: str, decision_areas: list[str]) -> None:
        slug = slugify(title)[:48] or f"topic-{len(items) + 1}"
        topic_id = f"T-{len(items) + 1:03d}"
        if topic_id in seen_ids:
            return
        seen_ids.add(topic_id)
        items.append(
            {
                "id": topic_id,
                "title": title,
                "slug": slug,
                "decision_areas": decision_areas,
                "seed_concepts": _seed_concepts_for_topic(paths, title),
                "suggested_sources": handoff_sources[:5],
                "output_path": (
                    paths.wiki_search_results_dir.relative_to(paths.root).as_posix()
                    + f"/{topic_id}.yaml"
                ),
            }
        )

    for seed in seeds:
        emit(seed, ["scope-in", "requirements"])
    for area, descriptions in gap_topics.items():
        for description in descriptions[:3]:
            emit(description, [area])

    return items


def _wiki_version_int(paths: ArtifactPaths) -> int:
    """Best-effort integer wiki version (v2 dir presence is enough for now)."""
    if paths.wiki_v2_pages_dir.exists():
        return 2
    if paths.wiki_v1_pages_dir.exists():
        return 1
    return 0


def _existing_results_fresh(
    paths: ArtifactPaths,
    problem_hash: str,
    wiki_pages_hash: str,
) -> bool:
    if not paths.wiki_search_results_path.exists():
        return False
    payload = load_yaml(paths.wiki_search_results_path) or {}
    block = payload.get("wiki_search_results") if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        return False
    return (
        block.get("problem_statement_hash") == problem_hash
        and block.get("wiki_pages_hash") == wiki_pages_hash
    )


def run_wiki_search_preflight(
    artifacts_root: Path,
    workspace_root: Path,
    force: bool = False,
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    problem_hash = _problem_statement_hash(workspace_root)
    wiki_pages_hash = compute_wiki_version(paths.wiki_v2_pages_dir)

    if not force and _existing_results_fresh(paths, problem_hash, wiki_pages_hash):
        return {
            "status": "cached",
            "results_path": paths.wiki_search_results_path.relative_to(
                paths.root
            ).as_posix(),
            "problem_statement_hash": problem_hash,
            "wiki_pages_hash": wiki_pages_hash,
        }

    work_items = _build_work_items(paths, workspace_root)

    generated_at = iso_now()
    plan = {
        "wiki_search_work_plan": {
            "generated_at": generated_at,
            "problem_statement_hash": problem_hash,
            "wiki_version": _wiki_version_int(paths),
            "wiki_pages_hash": wiki_pages_hash,
            "topic_count": len(work_items),
            "topics": work_items,
        }
    }
    dump_yaml(paths.wiki_search_work_plan_path, plan)

    request = {
        "wiki_search_request": {
            "generated_at": generated_at,
            "problem_statement_hash": problem_hash,
            "wiki_pages_hash": wiki_pages_hash,
            "work_plan_path": paths.wiki_search_work_plan_path.relative_to(
                paths.root
            ).as_posix(),
            "results_dir": paths.wiki_search_results_dir.relative_to(
                paths.root
            ).as_posix(),
            "topic_count": len(work_items),
            "instruction": (
                "Invoke @wiki-search-orchestrator next. It will fan out "
                "wiki-searcher subagents (max 4 parallel) per topic in "
                "work_plan.yaml. Each subagent writes its findings to the "
                "topic's output_path. Then run "
                "`meta-compiler wiki-search --apply` to consolidate."
            ),
        }
    }
    dump_yaml(paths.wiki_search_request_path, request)

    return {
        "status": "ready_for_orchestrator" if work_items else "no_work",
        "work_plan_path": paths.wiki_search_work_plan_path.relative_to(
            paths.root
        ).as_posix(),
        "request_path": paths.wiki_search_request_path.relative_to(
            paths.root
        ).as_posix(),
        "topic_count": len(work_items),
        "problem_statement_hash": problem_hash,
        "wiki_pages_hash": wiki_pages_hash,
    }


# ---------------------------------------------------------------------------
# Validation (also imported by validation.py for validate-stage --stage 2)
# ---------------------------------------------------------------------------


def validate_wiki_search_results(payload: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        issues.append("wiki_search_results: payload missing root object")
        return issues
    block = payload.get("wiki_search_results")
    if not isinstance(block, dict):
        issues.append("wiki_search_results: missing root key 'wiki_search_results'")
        return issues
    for key in ("generated_at", "problem_statement_hash", "topics"):
        if key not in block:
            issues.append(f"wiki_search_results: missing required field '{key}'")
    topics = block.get("topics", {})
    if not isinstance(topics, dict):
        issues.append("wiki_search_results.topics: must be a mapping")
        return issues
    for topic_id, entry in topics.items():
        if not isinstance(entry, dict):
            issues.append(f"topics[{topic_id}]: must be a mapping")
            continue
        missing = _TOPIC_RESULT_REQUIRED - set(entry.keys())
        for key in sorted(missing):
            issues.append(f"topics[{topic_id}]: missing required field '{key}'")
        for list_field in ("concepts", "equations", "citations", "related_pages", "cross_source_notes"):
            value = entry.get(list_field)
            if value is not None and not isinstance(value, list):
                issues.append(f"topics[{topic_id}].{list_field}: must be a list")
    return issues


def _validate_topic_result(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        return ["topic result: not a mapping"]
    block = payload.get("wiki_search_topic_result")
    if not isinstance(block, dict):
        return ["topic result: missing root key 'wiki_search_topic_result'"]
    missing = _TOPIC_RESULT_REQUIRED - set(block.keys())
    for key in sorted(missing):
        issues.append(f"missing required field '{key}'")
    for list_field in ("concepts", "equations", "citations", "related_pages", "cross_source_notes"):
        value = block.get(list_field)
        if value is not None and not isinstance(value, list):
            issues.append(f"{list_field}: must be a list")
    return issues


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def run_wiki_search_apply(
    artifacts_root: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    if not paths.wiki_search_work_plan_path.exists():
        raise FileNotFoundError(
            "wiki_search/work_plan.yaml missing. Run "
            "`meta-compiler wiki-search --scope stage2` first."
        )
    plan = load_yaml(paths.wiki_search_work_plan_path) or {}
    plan_block = plan.get("wiki_search_work_plan") if isinstance(plan, dict) else None
    if not isinstance(plan_block, dict):
        raise ValueError("wiki_search/work_plan.yaml is malformed (missing root key)")

    topic_files = sorted(paths.wiki_search_results_dir.glob("T-*.yaml"))
    if not topic_files:
        raise FileNotFoundError(
            f"No topic result files found under "
            f"{paths.wiki_search_results_dir.relative_to(paths.root).as_posix()}/. "
            "Invoke @wiki-search-orchestrator before running --apply."
        )

    consolidated: dict[str, dict[str, Any]] = {}
    schema_errors: list[str] = []
    for topic_file in topic_files:
        payload = load_yaml(topic_file) or {}
        errors = _validate_topic_result(payload)
        if errors:
            for err in errors:
                schema_errors.append(f"{topic_file.name}: {err}")
            continue
        block = payload["wiki_search_topic_result"]
        consolidated[str(block["topic_id"])] = block

    if schema_errors:
        raise ValueError(
            "wiki-search topic results failed schema validation:\n  - "
            + "\n  - ".join(schema_errors)
        )

    generated_at = iso_now()
    out = {
        "wiki_search_results": {
            "generated_at": generated_at,
            "problem_statement_hash": plan_block.get("problem_statement_hash"),
            "wiki_pages_hash": plan_block.get("wiki_pages_hash"),
            "wiki_version": plan_block.get("wiki_version"),
            "topic_count": len(consolidated),
            "topics": consolidated,
        }
    }
    dump_yaml(paths.wiki_search_results_path, out)

    return {
        "status": "applied",
        "results_path": paths.wiki_search_results_path.relative_to(
            paths.root
        ).as_posix(),
        "topic_count": len(consolidated),
        "schema_errors": [],
    }


# ---------------------------------------------------------------------------
# Brief.md "Wiki Evidence" section renderer
# ---------------------------------------------------------------------------


_MAX_BRIEF_BYTES = 8 * 1024


def render_wiki_evidence_section(paths: ArtifactPaths) -> str:
    """Render the "## Wiki Evidence" section for inclusion in brief.md.

    Returns an empty string if results are missing.
    """
    if not paths.wiki_search_results_path.exists():
        return ""
    payload = load_yaml(paths.wiki_search_results_path) or {}
    block = payload.get("wiki_search_results") if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        return ""
    topics = block.get("topics", {}) or {}
    if not isinstance(topics, dict):
        return ""

    by_area: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for topic_id, entry in topics.items():
        if not isinstance(entry, dict):
            continue
        for area in entry.get("decision_areas", []) or ["architecture"]:
            by_area.setdefault(str(area), []).append((str(topic_id), entry))

    lines = ["## Wiki Evidence", ""]
    for area in _DECISION_AREAS:
        bucket = by_area.get(area, [])
        if not bucket:
            continue
        lines.append(f"### {area}")
        seen_concepts: set[str] = set()
        seen_equations: set[str] = set()
        concept_lines: list[str] = []
        equation_lines: list[str] = []
        for _topic_id, entry in bucket:
            for concept in (entry.get("concepts") or [])[:3]:
                if not isinstance(concept, dict):
                    continue
                slug = str(concept.get("slug") or "")
                if slug in seen_concepts:
                    continue
                seen_concepts.add(slug)
                snippet = str(concept.get("definition_excerpt") or "").strip()
                citations = ",".join(str(c) for c in (concept.get("citations") or [])[:3])
                tag = f"[wiki:{slug}]" if slug else "[wiki:unknown]"
                cites = f" [cit:{citations}]" if citations else ""
                concept_lines.append(f"- {snippet[:240]} {tag}{cites}")
                if len(concept_lines) >= 3:
                    break
            for eq in (entry.get("equations") or [])[:2]:
                if not isinstance(eq, dict):
                    continue
                label = str(eq.get("label") or "")
                if label in seen_equations:
                    continue
                seen_equations.add(label)
                latex = str(eq.get("latex") or "").strip()
                citations = ",".join(str(c) for c in (eq.get("citations") or [])[:3])
                cites = f" [cit:{citations}]" if citations else ""
                equation_lines.append(
                    f"- `${latex[:140]}$`{cites}"
                    + (f" — {label}" if label else "")
                )
                if len(equation_lines) >= 2:
                    break
            if len(concept_lines) >= 3 and len(equation_lines) >= 2:
                break
        lines.extend(concept_lines)
        lines.extend(equation_lines)
        lines.append("")

    cross_lines: list[str] = []
    for entry in topics.values():
        if not isinstance(entry, dict):
            continue
        for note in (entry.get("cross_source_notes") or [])[:2]:
            if not isinstance(note, dict):
                continue
            summary = str(note.get("summary") or "").strip()
            cites = ",".join(
                str(c) for c in (note.get("source_citation_ids") or [])[:3]
            )
            cite_str = f" [cit:{cites}]" if cites else ""
            if summary:
                cross_lines.append(f"- {summary[:200]}{cite_str}")
            if len(cross_lines) >= 5:
                break
        if len(cross_lines) >= 5:
            break

    if cross_lines:
        lines.append("### Cross-source synthesis")
        lines.extend(cross_lines)
        lines.append("")

    rendered = "\n".join(lines)
    if len(rendered.encode("utf-8")) > _MAX_BRIEF_BYTES:
        rendered = rendered.encode("utf-8")[:_MAX_BRIEF_BYTES].decode("utf-8", "ignore")
        rendered += "\n\n_(Wiki Evidence truncated — see results.yaml for full payload)_\n"
    return rendered
