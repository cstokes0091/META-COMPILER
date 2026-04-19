from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..artifacts import (
    ArtifactPaths,
    build_paths,
    compute_seed_version,
    compute_wiki_version,
    ensure_layout,
    list_code_repos,
    list_seed_files,
    load_manifest,
    save_manifest,
)
from ..immutable_sources import (
    register_source_binding,
    snapshot_seed_inventory,
    validate_seed_immutability,
)
from ..io import dump_yaml, load_yaml, parse_frontmatter, render_frontmatter
from ..utils import extract_keywords, iso_now, read_text_safe, sha256_file, slugify
from ..wiki_lifecycle import append_log_entry, write_index
from ..wiki_rendering import citation_markdown_link, inject_wiki_nav


TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".rst",
    ".tex",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".py",
}

RELATION_TYPES = ["prerequisite_for", "depends_on", "contradicts", "extends"]


def _next_citation_id(base_slug: str, existing: set[str]) -> str:
    candidate = f"src-{base_slug}"
    if candidate not in existing:
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in existing:
        suffix += 1
    return f"{candidate}-{suffix}"


def _source_excerpt(seed_path: Path, max_chars: int = 1400) -> str:
    if seed_path.suffix.lower() not in TEXT_EXTENSIONS:
        return "Binary seed detected; text extraction deferred."
    text = read_text_safe(seed_path).strip()
    if not text:
        return "Seed contains no extractable text."
    return text[:max_chars]


def _build_source_page(
    page_id: str,
    citation_id: str,
    seed_relpath: str,
    created: str,
    excerpt: str,
    wiki_name: str = "",
) -> str:
    frontmatter = {
        "id": page_id,
        "type": "source",
        "created": created,
        "sources": [citation_id],
        "related": [],
        "status": "raw",
    }
    page = (
        "---\n"
        + render_frontmatter(frontmatter)
        + "\n---\n"
        + f"# {page_id}\n\n"
        + "## Definition\n"
        + f"Auto-ingested source page for {seed_relpath}.\n\n"
        + "## Formalism\n"
        + "No formalism extracted in Stage 1A baseline pass.\n\n"
        + "## Key Claims\n"
        + f"- Source imported into wiki baseline {citation_markdown_link(citation_id)}\n\n"
        + "## Relationships\n"
        + "- prerequisite_for: []\n"
        + "- depends_on: []\n"
        + "- contradicts: []\n"
        + "- extends: []\n\n"
        + "## Open Questions\n"
        + "- What additional extraction is required for this seed?\n\n"
        + "## Source Notes\n"
        + f"{excerpt}\n"
    )
    return inject_wiki_nav(page, wiki_name)


def _build_concept_page(page_id: str, citation_id: str, created: str, wiki_name: str = "") -> str:
    frontmatter = {
        "id": page_id,
        "type": "concept",
        "created": created,
        "sources": [citation_id],
        "related": [],
        "status": "raw",
    }
    title = page_id.replace("-", " ").title()
    page = (
        "---\n"
        + render_frontmatter(frontmatter)
        + "\n---\n"
        + f"# {title}\n\n"
        + "## Definition\n"
        + "Initial concept stub generated from seed metadata.\n\n"
        + "## Formalism\n"
        + "No formalism captured yet.\n\n"
        + "## Key Claims\n"
        + f"- Concept surfaced in Stage 1A and linked to a seed source {citation_markdown_link(citation_id)}\n\n"
        + "## Relationships\n"
        + "- prerequisite_for: []\n"
        + "- depends_on: []\n"
        + "- contradicts: []\n"
        + "- extends: []\n\n"
        + "## Open Questions\n"
        + "- Should this concept remain in scope after Stage 1B depth checks?\n\n"
        + "## Source Notes\n"
        + "Stage 1A placeholder page; refine in Stage 1B.\n"
    )
    return inject_wiki_nav(page, wiki_name)


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        candidate = value.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def _format_locator(locator: Any) -> str:
    if not isinstance(locator, dict):
        return ""

    if locator.get("file"):
        file_marker = str(locator["file"])
        line_start = locator.get("line_start")
        line_end = locator.get("line_end")
        line_piece = ""
        if isinstance(line_start, int):
            line_piece = f":{line_start}"
            if isinstance(line_end, int) and line_end != line_start:
                line_piece += f"-{line_end}"
        parts = [f"{file_marker}{line_piece}"]
        symbol = locator.get("symbol")
        if symbol:
            parts.append(f"in {symbol}")
        return f" ({', '.join(parts)})"

    parts: list[str] = []
    if locator.get("page") is not None:
        parts.append(f"p.{locator['page']}")
    if locator.get("section"):
        parts.append(f"sec. {locator['section']}")
    if locator.get("paragraph") is not None:
        parts.append(f"para. {locator['paragraph']}")
    return f" ({', '.join(parts)})" if parts else ""


def _normalize_relationship_page_id(raw_value: str) -> str:
    slug = slugify(raw_value)
    if not slug:
        return ""
    if slug.startswith("concept-"):
        return slug
    return f"concept-{slug}"


def _relationship_buckets(relationships: list[dict[str, Any]], subject_slug: str | None = None) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {name: [] for name in RELATION_TYPES}
    inverse_map = {
        "depends_on": "prerequisite_for",
        "prerequisite_for": "depends_on",
        "contradicts": "contradicts",
        "extends": "extends",
    }

    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        rel_type = str(relationship.get("type") or "").strip()
        if rel_type not in buckets:
            continue

        from_page = _normalize_relationship_page_id(str(relationship.get("from") or ""))
        to_page = _normalize_relationship_page_id(str(relationship.get("to") or ""))
        if not from_page and not to_page:
            continue

        if subject_slug is None:
            for page_id in [from_page, to_page]:
                if page_id:
                    buckets[rel_type].append(page_id)
            continue

        subject_page = _normalize_relationship_page_id(subject_slug)
        if from_page == subject_page and to_page:
            buckets[rel_type].append(to_page)
        elif to_page == subject_page and from_page:
            buckets[inverse_map[rel_type]].append(from_page)

    return {name: _ordered_unique(values) for name, values in buckets.items()}


def _should_enrich_page(page_path: Path, placeholder_markers: list[str]) -> bool:
    if not page_path.exists():
        return True

    frontmatter, body = parse_frontmatter(read_text_safe(page_path))
    if frontmatter.get("status") == "raw":
        return True
    return any(marker in body for marker in placeholder_markers)


def _source_page_id_from_findings(payload: dict[str, Any]) -> str:
    seed_path = str(payload.get("seed_path") or payload.get("citation_id") or "source")
    seed_stem = Path(seed_path).stem or seed_path
    return slugify(seed_stem)[:64] or "source"


def _render_relationship_lines(relationship_buckets: dict[str, list[str]]) -> list[str]:
    lines = ["## Relationships"]
    for rel_type in RELATION_TYPES:
        lines.append(f"- {rel_type}: [{', '.join(relationship_buckets.get(rel_type, []))}]")
    return lines


def _render_source_page_from_findings(payload: dict[str, Any], wiki_name: str) -> str:
    citation_id = str(payload.get("citation_id") or "src-unknown")
    seed_path = str(payload.get("seed_path") or "unknown-seed")
    created = str(payload.get("extracted_at") or iso_now())
    completeness = str((payload.get("extraction_stats") or {}).get("completeness") or "raw")
    concept_page_ids = [
        _normalize_relationship_page_id(str(concept.get("name") or ""))
        for concept in payload.get("concepts", [])
        if isinstance(concept, dict)
    ]
    concept_page_ids = _ordered_unique([page_id for page_id in concept_page_ids if page_id])

    claims = [claim for claim in payload.get("claims", []) if isinstance(claim, dict)]
    equations = [equation for equation in payload.get("equations", []) if isinstance(equation, dict)]
    quotes = [quote for quote in payload.get("quotes", []) if isinstance(quote, dict)]
    relationships = [row for row in payload.get("relationships", []) if isinstance(row, dict)]
    open_questions = _ordered_unique([str(item) for item in payload.get("open_questions", []) if str(item).strip()])

    frontmatter = {
        "id": _source_page_id_from_findings(payload),
        "type": "source",
        "created": created,
        "sources": [citation_id],
        "related": concept_page_ids,
        "status": "reviewed" if completeness == "full" else "raw",
    }
    relationship_buckets = _relationship_buckets(relationships)

    lines = [
        "---",
        render_frontmatter(frontmatter),
        "---",
        f"# {Path(seed_path).stem or frontmatter['id']}",
        "",
        "## Definition",
        f"Findings-backed source summary for {seed_path}.",
        "",
        "## Formalism",
    ]

    if equations:
        for equation in equations:
            label = str(equation.get("label") or "Equation")
            latex = str(equation.get("latex") or "")
            purpose = str(equation.get("purpose") or "").strip()
            lines.append(
                f"- {label}: `{latex}`{_format_locator(equation.get('locator'))}"
                + (f" — {purpose}" if purpose else "")
            )
    else:
        lines.append("- No equations extracted from findings.")

    lines.extend(["", "## Key Claims"])
    if claims:
        for claim in claims:
            statement = str(claim.get("statement") or "").strip()
            if not statement:
                continue
            evidence = str(claim.get("evidence") or "").strip()
            lines.append(
                f"- {statement}{_format_locator(claim.get('locator'))} {citation_markdown_link(citation_id)}"
                + (f" — {evidence}" if evidence else "")
            )
    else:
        lines.append(f"- Findings recorded for this seed {citation_markdown_link(citation_id)}")

    lines.extend(["", *_render_relationship_lines(relationship_buckets), "", "## Open Questions"])
    if open_questions:
        lines.extend([f"- {question}" for question in open_questions])
    else:
        partial_reason = str((payload.get("extraction_stats") or {}).get("partial_reason") or "").strip()
        if partial_reason:
            lines.append(f"- Extraction was partial: {partial_reason}")
        else:
            lines.append("- No open questions recorded in findings.")

    lines.extend(["", "## Source Notes"])
    abstract = str((payload.get("document_metadata") or {}).get("abstract") or "").strip()
    if abstract:
        lines.append(f"- Abstract: {abstract}")
    if quotes:
        for quote in quotes[:6]:
            text = str(quote.get("text") or "").strip()
            if text:
                lines.append(f"- Quote{_format_locator(quote.get('locator'))}: {text}")
    elif not abstract:
        lines.append("- No verbatim quotes extracted from findings.")

    return inject_wiki_nav("\n".join(lines) + "\n", wiki_name)


def _is_code_finding_payload(payload: dict[str, Any]) -> bool:
    if payload.get("source_type") == "code":
        return True
    return isinstance(payload.get("file_metadata"), dict)


def _code_page_id_from_findings(payload: dict[str, Any]) -> str:
    citation_id = str(payload.get("citation_id") or "")
    if citation_id:
        return slugify(citation_id)[:96] or "code"
    seed_path = str(payload.get("seed_path") or "source")
    return slugify(seed_path)[:96] or "code"


def _render_code_page_from_findings(payload: dict[str, Any], wiki_name: str) -> str:
    citation_id = str(payload.get("citation_id") or "src-unknown")
    seed_path = str(payload.get("seed_path") or "unknown-seed")
    created = str(payload.get("extracted_at") or iso_now())
    metadata = payload.get("file_metadata") if isinstance(payload.get("file_metadata"), dict) else {}
    language = str(metadata.get("language") or "").strip() or "unknown"
    loc = metadata.get("loc")
    module_path = str(metadata.get("module_path") or "").strip()
    repo_citation_id = str(metadata.get("repo_citation_id") or "").strip()
    completeness = str((payload.get("extraction_stats") or {}).get("completeness") or "raw")

    concept_page_ids = [
        _normalize_relationship_page_id(str(concept.get("name") or ""))
        for concept in payload.get("concepts", [])
        if isinstance(concept, dict)
    ]
    concept_page_ids = _ordered_unique([page_id for page_id in concept_page_ids if page_id])

    symbols = [row for row in payload.get("symbols", []) if isinstance(row, dict)]
    claims = [row for row in payload.get("claims", []) if isinstance(row, dict)]
    quotes = [row for row in payload.get("quotes", []) if isinstance(row, dict)]
    dependencies = [row for row in payload.get("dependencies", []) if isinstance(row, dict)]
    relationships = [row for row in payload.get("relationships", []) if isinstance(row, dict)]
    open_questions = _ordered_unique(
        [str(item) for item in payload.get("open_questions", []) if str(item).strip()]
    )

    related_pages = list(concept_page_ids)
    if repo_citation_id:
        repo_page_id = slugify(repo_citation_id)[:64]
        if repo_page_id and repo_page_id not in related_pages:
            related_pages.append(repo_page_id)

    frontmatter = {
        "id": _code_page_id_from_findings(payload),
        "type": "code",
        "language": language,
        "created": created,
        "sources": [citation_id],
        "related": related_pages,
        "status": "reviewed" if completeness == "full" else "raw",
    }
    if repo_citation_id:
        frontmatter["repo"] = repo_citation_id

    relationship_buckets = _relationship_buckets(relationships)

    title = Path(seed_path).name or frontmatter["id"]
    lines = [
        "---",
        render_frontmatter(frontmatter),
        "---",
        f"# {title}",
        "",
        "## Definition",
        f"Code extraction summary for `{seed_path}`.",
        "",
        "## File Overview",
        f"- Language: {language}",
    ]
    if isinstance(loc, int):
        lines.append(f"- Lines of code: {loc}")
    if module_path:
        lines.append(f"- Module: `{module_path}`")
    if repo_citation_id:
        lines.append(f"- Repo: {citation_markdown_link(repo_citation_id)}")

    lines.extend(["", "## Symbols"])
    if symbols:
        for symbol in symbols:
            kind = str(symbol.get("kind") or "symbol")
            name = str(symbol.get("name") or "").strip() or "(unnamed)"
            signature = str(symbol.get("signature") or "").strip()
            visibility = str(symbol.get("visibility") or "").strip()
            suffix_parts: list[str] = []
            if visibility:
                suffix_parts.append(visibility)
            suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
            sig_piece = f" `{signature}`" if signature else ""
            lines.append(
                f"- [{kind}] {name}{sig_piece}{_format_locator(symbol.get('locator'))}{suffix}"
            )
    else:
        lines.append("- No symbols extracted from findings.")

    lines.extend(["", "## Dependencies"])
    if dependencies:
        for dep in dependencies:
            kind = str(dep.get("kind") or "import")
            target = str(dep.get("target") or "").strip() or "(unknown)"
            lines.append(f"- {kind}: `{target}`{_format_locator(dep.get('locator'))}")
    else:
        lines.append("- No external dependencies recorded.")

    lines.extend(["", "## Key Claims"])
    if claims:
        for claim in claims:
            statement = str(claim.get("statement") or "").strip()
            if not statement:
                continue
            evidence = str(claim.get("evidence") or "").strip()
            lines.append(
                f"- {statement}{_format_locator(claim.get('locator'))} {citation_markdown_link(citation_id)}"
                + (f" — {evidence}" if evidence else "")
            )
    else:
        lines.append(f"- Extraction recorded for this file {citation_markdown_link(citation_id)}")

    lines.extend(["", *_render_relationship_lines(relationship_buckets), "", "## Open Questions"])
    if open_questions:
        lines.extend([f"- {question}" for question in open_questions])
    else:
        partial_reason = str((payload.get("extraction_stats") or {}).get("partial_reason") or "").strip()
        if partial_reason:
            lines.append(f"- Extraction was partial: {partial_reason}")
        else:
            lines.append("- No open questions recorded in findings.")

    lines.extend(["", "## Source Notes"])
    if quotes:
        for quote in quotes[:6]:
            text = str(quote.get("text") or "").strip()
            if not text:
                continue
            locator_line = _format_locator(quote.get("locator"))
            lines.append(f"- Quote{locator_line}:")
            lines.append("")
            lines.append(f"```{language if language != 'unknown' else ''}".rstrip())
            lines.append(text)
            lines.append("```")
    else:
        lines.append("- No verbatim snippets extracted from findings.")

    return inject_wiki_nav("\n".join(lines) + "\n", wiki_name)


def _render_repo_overview_page(
    repo_map: dict[str, Any],
    wiki_name: str,
    repo_file_pages: list[dict[str, str]],
) -> str:
    """Render a per-repo landing page from a repo-mapper YAML payload."""
    repo_name = str(repo_map.get("repo_name") or "repo")
    repo_citation_id = str(repo_map.get("repo_citation_id") or "")
    remote = str(repo_map.get("remote") or "").strip()
    commit_sha = str(repo_map.get("commit_sha") or "").strip()
    cloned_at = str(repo_map.get("cloned_at") or "").strip()
    languages = repo_map.get("languages") if isinstance(repo_map.get("languages"), list) else []
    entry_points = repo_map.get("entry_points") if isinstance(repo_map.get("entry_points"), list) else []
    modules = repo_map.get("modules") if isinstance(repo_map.get("modules"), list) else []
    priority_files = (
        repo_map.get("priority_files") if isinstance(repo_map.get("priority_files"), list) else []
    )
    test_dirs = repo_map.get("test_dirs") if isinstance(repo_map.get("test_dirs"), list) else []

    page_id = slugify(repo_citation_id)[:64] if repo_citation_id else slugify(f"repo-{repo_name}")[:64]
    frontmatter = {
        "id": page_id or "code-repo",
        "type": "code-repo",
        "created": cloned_at or iso_now(),
        "sources": [repo_citation_id] if repo_citation_id else [],
        "related": [row.get("page_id") for row in repo_file_pages if row.get("page_id")],
        "status": "reviewed" if languages and priority_files else "raw",
    }

    lines = [
        "---",
        render_frontmatter(frontmatter),
        "---",
        f"# {repo_name}",
        "",
        "## Definition",
        f"Repository atlas for code seed `{repo_name}`.",
        "",
        "## Overview",
    ]
    if remote:
        lines.append(f"- Remote: `{remote}`")
    if commit_sha:
        lines.append(f"- Commit: `{commit_sha}`")
    if cloned_at:
        lines.append(f"- Cloned at: {cloned_at}")

    lines.extend(["", "## Languages"])
    if languages:
        for lang in languages:
            if not isinstance(lang, dict):
                continue
            name = str(lang.get("name") or "").strip() or "unknown"
            file_count = lang.get("file_count")
            total_lines = lang.get("total_lines")
            suffix_parts: list[str] = []
            if isinstance(file_count, int):
                suffix_parts.append(f"{file_count} files")
            if isinstance(total_lines, int):
                suffix_parts.append(f"{total_lines} lines")
            detail = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
            lines.append(f"- {name}{detail}")
    else:
        lines.append("- Languages not yet enumerated.")

    lines.extend(["", "## Entry Points"])
    if entry_points:
        for entry in entry_points:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").strip()
            role = str(entry.get("role") or "").strip()
            role_piece = f" — {role}" if role else ""
            if path:
                lines.append(f"- `{path}`{role_piece}")
    else:
        lines.append("- No entry points recorded.")

    lines.extend(["", "## Modules"])
    if modules:
        for module in modules:
            if not isinstance(module, dict):
                continue
            path = str(module.get("path") or "").strip()
            role = str(module.get("role") or "").strip()
            api = module.get("public_api") if isinstance(module.get("public_api"), list) else []
            api_piece = ""
            if api:
                api_piece = f" — public API: {', '.join(str(item) for item in api[:6])}"
            role_piece = f" [{role}]" if role else ""
            if path:
                lines.append(f"- `{path}`{role_piece}{api_piece}")
    else:
        lines.append("- No modules recorded.")

    lines.extend(["", "## Priority Files"])
    if priority_files:
        for entry in priority_files[:25]:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").strip()
            rank = entry.get("rank")
            reason = str(entry.get("reason") or "").strip()
            rank_piece = f"[{rank}] " if rank is not None else ""
            reason_piece = f" — {reason}" if reason else ""
            if path:
                lines.append(f"- {rank_piece}`{path}`{reason_piece}")
    else:
        lines.append("- Priority files pending; repo-mapper has not been invoked yet.")

    if test_dirs:
        lines.extend(["", "## Tests"])
        for test_dir in test_dirs:
            if test_dir:
                lines.append(f"- `{test_dir}`")

    lines.extend(["", "## File Atlas"])
    if repo_file_pages:
        for row in repo_file_pages:
            title = row.get("title") or row.get("page_id")
            page_ref = row.get("page_id")
            if page_ref and title:
                lines.append(f"- [{title}]({page_ref}.md)")
    else:
        lines.append("- No per-file code pages rendered yet.")

    lines.extend(
        [
            "",
            "## Key Claims",
            "- Repository anchor page" + (
                f" {citation_markdown_link(repo_citation_id)}" if repo_citation_id else ""
            ),
            "",
            "## Relationships",
            "- prerequisite_for: []",
            "- depends_on: []",
            "- contradicts: []",
            "- extends: []",
            "",
            "## Open Questions",
            "- Track remaining files the orchestrator should ingest at higher fan-out.",
        ]
    )

    lines.extend(["", "## Source Notes"])
    if repo_citation_id:
        lines.append(f"- Repo citation: {citation_markdown_link(repo_citation_id)}")
    else:
        lines.append("- Repo citation pending Stage 1A registration.")

    return inject_wiki_nav("\n".join(lines) + "\n", wiki_name)


def _aggregate_concepts_from_findings(findings_payloads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}

    for payload in findings_payloads:
        citation_id = str(payload.get("citation_id") or "src-unknown")
        extracted_at = str(payload.get("extracted_at") or iso_now())
        completeness = str((payload.get("extraction_stats") or {}).get("completeness") or "raw")
        relationships = [row for row in payload.get("relationships", []) if isinstance(row, dict)]
        claims = [row for row in payload.get("claims", []) if isinstance(row, dict)]
        quotes = [row for row in payload.get("quotes", []) if isinstance(row, dict)]
        equations = [row for row in payload.get("equations", []) if isinstance(row, dict)]
        open_questions = [str(item).strip() for item in payload.get("open_questions", []) if str(item).strip()]

        for concept in payload.get("concepts", []):
            if not isinstance(concept, dict):
                continue

            concept_name = str(concept.get("name") or "").strip()
            if not concept_name:
                continue

            page_id = _normalize_relationship_page_id(concept_name)
            aggregate = aggregates.setdefault(
                page_id,
                {
                    "page_id": page_id,
                    "display_name": concept_name,
                    "created": extracted_at,
                    "definitions": [],
                    "citations": [],
                    "claims": [],
                    "quotes": [],
                    "equations": [],
                    "relationships": [],
                    "open_questions": [],
                    "statuses": set(),
                },
            )

            definition = str(concept.get("definition") or "").strip()
            if definition:
                aggregate["definitions"].append(definition)
            aggregate["citations"].append(citation_id)
            aggregate["claims"].extend(claims)
            aggregate["quotes"].extend(quotes)
            aggregate["equations"].extend(equations)
            aggregate["relationships"].extend(relationships)
            aggregate["open_questions"].extend(open_questions)
            aggregate["statuses"].add(completeness)

    return aggregates


def _render_concept_page_from_findings(aggregate: dict[str, Any], wiki_name: str) -> str:
    page_id = str(aggregate["page_id"])
    citations = _ordered_unique([str(item) for item in aggregate.get("citations", [])])
    relationships = [row for row in aggregate.get("relationships", []) if isinstance(row, dict)]
    relationship_buckets = _relationship_buckets(relationships, subject_slug=page_id)
    related = _ordered_unique(
        [item for values in relationship_buckets.values() for item in values if item != page_id]
    )

    statuses = aggregate.get("statuses", set())
    frontmatter = {
        "id": page_id,
        "type": "concept",
        "created": str(aggregate.get("created") or iso_now()),
        "sources": citations,
        "related": related,
        "status": "reviewed" if statuses == {"full"} else "raw",
    }

    definitions = _ordered_unique([str(item) for item in aggregate.get("definitions", []) if str(item).strip()])
    claims = [row for row in aggregate.get("claims", []) if isinstance(row, dict)]
    equations = [row for row in aggregate.get("equations", []) if isinstance(row, dict)]
    quotes = [row for row in aggregate.get("quotes", []) if isinstance(row, dict)]
    open_questions = _ordered_unique([str(item) for item in aggregate.get("open_questions", []) if str(item).strip()])

    lines = [
        "---",
        render_frontmatter(frontmatter),
        "---",
        f"# {aggregate.get('display_name', page_id)}",
        "",
        "## Definition",
        definitions[0] if definitions else "Definition not yet extracted from findings.",
        "",
        "## Formalism",
    ]

    if equations:
        for equation in equations[:5]:
            label = str(equation.get("label") or "Equation")
            latex = str(equation.get("latex") or "")
            lines.append(f"- {label}: `{latex}`{_format_locator(equation.get('locator'))}")
    else:
        lines.append("- No formalism captured yet.")

    lines.extend(["", "## Key Claims"])
    if claims:
        for claim in claims[:6]:
            statement = str(claim.get("statement") or "").strip()
            if not statement:
                continue
            citation_id = citations[0] if citations else "src-unknown"
            lines.append(f"- {statement}{_format_locator(claim.get('locator'))} {citation_markdown_link(citation_id)}")
    else:
        lines.append("- No claims linked from findings yet.")

    lines.extend(["", *_render_relationship_lines(relationship_buckets), "", "## Open Questions"])
    if open_questions:
        lines.extend([f"- {question}" for question in open_questions])
    else:
        lines.append("- No open questions recorded in findings.")

    lines.extend(["", "## Source Notes"])
    if quotes:
        for quote in quotes[:4]:
            text = str(quote.get("text") or "").strip()
            if text:
                lines.append(f"- Quote{_format_locator(quote.get('locator'))}: {text}")
    else:
        lines.append("- No verbatim notes captured for this concept yet.")

    return inject_wiki_nav("\n".join(lines) + "\n", wiki_name)


def _mark_findings_used(paths: ArtifactPaths, citation_ids: set[str]) -> int:
    if not citation_ids or not paths.findings_index_path.exists():
        return 0

    findings_index = load_yaml(paths.findings_index_path)
    root = findings_index.get("findings_index") if isinstance(findings_index, dict) else None
    processed = root.get("processed_seeds") if isinstance(root, dict) else None
    if not isinstance(processed, list):
        return 0

    updated = 0
    for row in processed:
        if not isinstance(row, dict):
            continue
        if row.get("citation_id") in citation_ids and row.get("used_in_wiki") is not True:
            row["used_in_wiki"] = True
            updated += 1

    if updated:
        root["last_updated"] = iso_now()
        dump_yaml(paths.findings_index_path, findings_index)
    return updated


def _enrich_from_findings(paths: ArtifactPaths, wiki_name: str) -> dict[str, int]:
    if not paths.findings_dir.exists():
        return {
            "findings_count": 0,
            "invalid_findings": 0,
            "source_pages_enriched": 0,
            "concept_pages_enriched": 0,
            "findings_marked_used": 0,
        }

    findings_payloads: list[dict[str, Any]] = []
    invalid_findings = 0
    for findings_path in sorted(paths.findings_dir.glob("*.json")):
        try:
            payload = json.loads(findings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            invalid_findings += 1
            continue
        if isinstance(payload, dict):
            findings_payloads.append(payload)

    source_pages_enriched = 0
    code_pages_enriched = 0
    concept_pages_enriched = 0
    repo_pages_enriched = 0
    used_citation_ids: set[str] = set()
    repo_file_pages: dict[str, list[dict[str, str]]] = {}

    for payload in findings_payloads:
        citation_id = str(payload.get("citation_id") or "")
        if _is_code_finding_payload(payload):
            page_id = _code_page_id_from_findings(payload)
            page_path = paths.wiki_v1_pages_dir / f"{page_id}.md"
            if _should_enrich_page(
                page_path,
                [
                    "Code extraction summary for",
                    "Auto-ingested source page",
                ],
            ):
                page_path.write_text(
                    _render_code_page_from_findings(payload, wiki_name=wiki_name),
                    encoding="utf-8",
                )
                code_pages_enriched += 1
                used_citation_ids.add(citation_id)
            metadata = payload.get("file_metadata") if isinstance(payload.get("file_metadata"), dict) else {}
            repo_citation_id = str(metadata.get("repo_citation_id") or "")
            if repo_citation_id:
                repo_file_pages.setdefault(repo_citation_id, []).append(
                    {
                        "page_id": page_id,
                        "title": Path(str(payload.get("seed_path") or "")).name or page_id,
                    }
                )
        else:
            page_id = _source_page_id_from_findings(payload)
            source_page_path = paths.wiki_v1_pages_dir / f"{page_id}.md"
            if _should_enrich_page(
                source_page_path,
                ["Auto-ingested source page", "Binary seed detected; text extraction deferred."],
            ):
                source_page_path.write_text(
                    _render_source_page_from_findings(payload, wiki_name=wiki_name),
                    encoding="utf-8",
                )
                source_pages_enriched += 1
                used_citation_ids.add(citation_id)

    for page_id, aggregate in _aggregate_concepts_from_findings(findings_payloads).items():
        concept_page_path = paths.wiki_v1_pages_dir / f"{page_id}.md"
        if _should_enrich_page(
            concept_page_path,
            [
                "Initial concept stub generated from seed metadata.",
                "Stage 1A placeholder page; refine in Stage 1B.",
            ],
        ):
            concept_page_path.write_text(
                _render_concept_page_from_findings(aggregate, wiki_name=wiki_name),
                encoding="utf-8",
            )
            concept_pages_enriched += 1
            used_citation_ids.update(_ordered_unique([str(item) for item in aggregate.get("citations", [])]))

    # Per-repo overview pages, rendered from runtime/ingest/repo_map/*.yaml.
    if paths.runtime_repo_map_dir.exists():
        for repo_map_path in sorted(paths.runtime_repo_map_dir.glob("*.yaml")):
            repo_map = load_yaml(repo_map_path) or {}
            if not isinstance(repo_map, dict) or not repo_map:
                continue
            repo_citation_id = str(repo_map.get("repo_citation_id") or "")
            files_for_repo = repo_file_pages.get(repo_citation_id, [])
            page_id = (
                slugify(repo_citation_id)[:64] if repo_citation_id
                else slugify(f"repo-{repo_map.get('repo_name') or 'repo'}")[:64]
            )
            if not page_id:
                continue
            page_path = paths.wiki_v1_pages_dir / f"{page_id}.md"
            if _should_enrich_page(
                page_path,
                [
                    "Repository atlas for code seed",
                    "Auto-ingested source page",
                ],
            ):
                page_path.write_text(
                    _render_repo_overview_page(
                        repo_map,
                        wiki_name=wiki_name,
                        repo_file_pages=files_for_repo,
                    ),
                    encoding="utf-8",
                )
                repo_pages_enriched += 1
                if repo_citation_id:
                    used_citation_ids.add(repo_citation_id)

    return {
        "findings_count": len(findings_payloads),
        "invalid_findings": invalid_findings,
        "source_pages_enriched": source_pages_enriched,
        "code_pages_enriched": code_pages_enriched,
        "repo_pages_enriched": repo_pages_enriched,
        "concept_pages_enriched": concept_pages_enriched,
        "findings_marked_used": _mark_findings_used(paths, used_citation_ids),
    }


def _update_manifest(paths: ArtifactPaths, stage: str, page_count: int) -> None:
    manifest = load_manifest(paths)
    if not manifest:
        raise RuntimeError("Manifest not found. Run meta-init first.")

    now = iso_now()
    wm = manifest["workspace_manifest"]
    wm["seeds"] = {
        "version": compute_seed_version(paths),
        "last_updated": now,
        "document_count": len(list_seed_files(paths)),
        "inventory_snapshot": snapshot_seed_inventory(paths)["items"],
    }
    wiki = wm.setdefault("wiki", {})
    wiki["version"] = compute_wiki_version(paths.wiki_v1_pages_dir)
    wiki["last_updated"] = now
    wiki["page_count"] = page_count
    wm["status"] = "researched"
    wm.setdefault("research", {})["last_completed_stage"] = stage
    save_manifest(paths, manifest)


def run_research_breadth(artifacts_root: Path, workspace_root: Path) -> dict:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    immutability_issues = validate_seed_immutability(paths)
    if immutability_issues:
        issue_text = "\n".join(immutability_issues)
        raise RuntimeError(
            "Immutable seed policy violation detected. "
            "Rename modified seed files or restore original content before ingest.\n"
            f"{issue_text}"
        )

    seeds = list_seed_files(paths)
    if not seeds:
        raise RuntimeError("No seed files found in workspace-artifacts/seeds")

    existing_index = load_yaml(paths.citations_index_path) or {"citations": {}}
    citations = existing_index.get("citations")
    if not isinstance(citations, dict):
        citations = {}

    existing_ids = set(citations.keys())
    hash_to_id = {}
    for citation_id, citation in citations.items():
        metadata = citation.get("metadata", {}) if isinstance(citation, dict) else {}
        file_hash = metadata.get("file_hash") if isinstance(metadata, dict) else None
        if file_hash:
            hash_to_id[file_hash] = citation_id

    created = iso_now()
    created_pages: list[str] = []
    manifest = load_manifest(paths)
    wiki_name = ""
    if manifest:
        wiki_name = str(manifest.get("workspace_manifest", {}).get("wiki", {}).get("name") or "")

    code_repos = list_code_repos(paths)
    code_prefixes = [
        str(row.get("relative_path") or "").rstrip("/") + "/" for row in code_repos
    ]
    code_prefixes = [prefix for prefix in code_prefixes if prefix and prefix != "/"]

    # Per-repo placeholder pages — one per registered code seed. These get
    # replaced by _enrich_from_findings when a RepoMap YAML exists.
    for row in code_repos:
        repo_citation_id = str(row.get("citation_id") or "")
        repo_name = str(row.get("name") or "")
        if not repo_citation_id or not repo_name:
            continue
        if repo_citation_id not in citations:
            citations[repo_citation_id] = {
                "human": f"{repo_name} (code repo)",
                "source": {
                    "type": "code-repo",
                    "path": f"/{str(row.get('relative_path') or '').rstrip('/')}/",
                    "page": None,
                    "section": None,
                    "url": row.get("remote"),
                    "accessed": None,
                },
                "metadata": {
                    "authors": [],
                    "title": repo_name,
                    "year": None,
                    "venue": "code-repo",
                    "doi": None,
                    "file_hash": row.get("commit_sha"),
                    "commit_sha": row.get("commit_sha"),
                    "ref": row.get("ref"),
                },
                "status": "raw",
                "notes": "Registered by add-code-seed / bind-code-seed.",
            }
            existing_ids.add(repo_citation_id)
        repo_page_id = slugify(repo_citation_id)[:64] or "code-repo"
        repo_page_path = paths.wiki_v1_pages_dir / f"{repo_page_id}.md"
        if not repo_page_path.exists():
            placeholder = {
                "repo_name": repo_name,
                "repo_citation_id": repo_citation_id,
                "remote": row.get("remote"),
                "commit_sha": row.get("commit_sha"),
                "cloned_at": created,
                "languages": [],
                "entry_points": [],
                "modules": [],
                "priority_files": [],
                "test_dirs": [],
            }
            repo_page_path.write_text(
                _render_repo_overview_page(placeholder, wiki_name=wiki_name, repo_file_pages=[]),
                encoding="utf-8",
            )
            created_pages.append(repo_page_id)

    for seed in seeds:
        file_hash = sha256_file(seed)
        relative_path = seed.relative_to(paths.root)
        relative_posix = relative_path.as_posix()
        is_code_seed = any(relative_posix.startswith(prefix) for prefix in code_prefixes)

        if file_hash in hash_to_id:
            citation_id = hash_to_id[file_hash]
        else:
            slug_base = slugify(seed.stem)[:50] or "seed"
            citation_id = _next_citation_id(slug_base, existing_ids)
            existing_ids.add(citation_id)
            hash_to_id[file_hash] = citation_id
            citations[citation_id] = {
                "human": f"{seed.stem} (seed)",
                "source": {
                    "type": "code" if is_code_seed else "seed",
                    "path": f"/{relative_posix}",
                    "page": None,
                    "section": None,
                    "url": None,
                    "accessed": None,
                },
                "metadata": {
                    "authors": [],
                    "title": seed.stem,
                    "year": None,
                    "venue": "code" if is_code_seed else "seed",
                    "doi": None,
                    "file_hash": file_hash,
                },
                "status": "raw",
                "notes": "Auto-ingested in Stage 1A.",
            }

        register_source_binding(
            paths=paths,
            seed_path=seed,
            citation_id=citation_id,
            file_hash=file_hash,
        )

        if is_code_seed:
            # Per-file code pages are rendered only from findings — skip the
            # filename-derived baseline that would blow up for large repos.
            continue

        source_page_id = slugify(seed.stem)[:64] or "source"
        source_page_path = paths.wiki_v1_pages_dir / f"{source_page_id}.md"
        source_page_path.write_text(
            _build_source_page(
                page_id=source_page_id,
                citation_id=citation_id,
                seed_relpath=str(relative_path),
                created=created,
                excerpt=_source_excerpt(seed),
                wiki_name=wiki_name,
            ),
            encoding="utf-8",
        )
        created_pages.append(source_page_id)

        # A lightweight concept expansion pass based on seed filename tokens.
        for keyword in extract_keywords(seed.stem, max_terms=2):
            concept_page_id = f"concept-{slugify(keyword)}"
            concept_path = paths.wiki_v1_pages_dir / f"{concept_page_id}.md"
            if concept_path.exists():
                continue
            concept_path.write_text(
                _build_concept_page(
                    page_id=concept_page_id,
                    citation_id=citation_id,
                    created=created,
                    wiki_name=wiki_name,
                ),
                encoding="utf-8",
            )
            created_pages.append(concept_page_id)

    dump_yaml(paths.citations_index_path, {"citations": citations})

    enrichment_result = _enrich_from_findings(paths=paths, wiki_name=wiki_name)

    all_pages = sorted(paths.wiki_v1_pages_dir.glob("*.md"))
    write_index(
        pages_dir=paths.wiki_v1_pages_dir,
        index_path=paths.wiki_v1_dir / "index.md",
        title="Wiki v1 Index",
    )
    append_log_entry(
        log_path=paths.wiki_v1_dir / "log.md",
        operation="ingest",
        title="Stage 1A breadth ingest",
        details=[
            f"seeds_processed: {len(seeds)}",
            f"pages_created_or_updated: {len(created_pages)}",
            f"citations_total: {len(citations)}",
            f"findings_count: {enrichment_result['findings_count']}",
            f"source_pages_enriched: {enrichment_result['source_pages_enriched']}",
            f"code_pages_enriched: {enrichment_result.get('code_pages_enriched', 0)}",
            f"repo_pages_enriched: {enrichment_result.get('repo_pages_enriched', 0)}",
            f"concept_pages_enriched: {enrichment_result['concept_pages_enriched']}",
            f"invalid_findings: {enrichment_result['invalid_findings']}",
            f"ingest_completed_at: {created}",
        ],
    )

    _update_manifest(paths, stage="1A", page_count=len(all_pages))

    return {
        "seed_count": len(seeds),
        "citation_count": len(citations),
        "page_count": len(all_pages),
        **enrichment_result,
    }
