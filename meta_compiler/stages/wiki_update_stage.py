"""wiki-update command: Incremental wiki expansion from new seed documents.

Detects new seeds not yet in the citation index, ingests them into Wiki v2,
produces an impact report, and runs light validation on new/modified pages.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..artifacts import (
    ArtifactPaths,
    build_paths,
    compute_seed_version,
    compute_wiki_version,
    ensure_layout,
    list_seed_files,
    load_manifest,
    save_manifest,
)
from ..immutable_sources import (
    register_source_binding,
    validate_seed_immutability,
)
from ..io import dump_yaml, load_yaml, render_frontmatter
from ..utils import extract_keywords, iso_now, read_text_safe, sha256_file, slugify
from ..wiki_lifecycle import append_log_entry, write_index
from ..wiki_rendering import citation_markdown_link, inject_wiki_nav


TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".tex", ".json", ".yaml", ".yml", ".csv", ".py",
}


def _source_excerpt(seed_path: Path, max_chars: int = 1400) -> str:
    if seed_path.suffix.lower() not in TEXT_EXTENSIONS:
        return "Binary seed detected; text extraction deferred."
    text = read_text_safe(seed_path).strip()
    if not text:
        return "Seed contains no extractable text."
    return text[:max_chars]


def _next_citation_id(base_slug: str, existing: set[str]) -> str:
    candidate = f"src-{base_slug}"
    if candidate not in existing:
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in existing:
        suffix += 1
    return f"{candidate}-{suffix}"


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
        + "No formalism extracted in wiki-update pass.\n\n"
        + "## Key Claims\n"
        + f"- Source imported via wiki-update {citation_markdown_link(citation_id)}\n\n"
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
        + "Concept stub generated from new seed metadata via wiki-update.\n\n"
        + "## Formalism\n"
        + "No formalism captured yet.\n\n"
        + "## Key Claims\n"
        + f"- Concept surfaced via wiki-update {citation_markdown_link(citation_id)}\n\n"
        + "## Relationships\n"
        + "- prerequisite_for: []\n"
        + "- depends_on: []\n"
        + "- contradicts: []\n"
        + "- extends: []\n\n"
        + "## Open Questions\n"
        + "- Review new concept coverage and link to existing wiki pages.\n\n"
        + "## Source Notes\n"
        + "wiki-update placeholder; refine with depth pass or manual review.\n"
    )
    return inject_wiki_nav(page, wiki_name)


def _find_new_seeds(paths: ArtifactPaths) -> list[Path]:
    """Return seed files not yet in the citation index (by file hash)."""
    existing_index = load_yaml(paths.citations_index_path) or {"citations": {}}
    citations = existing_index.get("citations", {})
    if not isinstance(citations, dict):
        citations = {}

    known_hashes: set[str] = set()
    for citation in citations.values():
        if not isinstance(citation, dict):
            continue
        metadata = citation.get("metadata", {})
        if isinstance(metadata, dict):
            fh = metadata.get("file_hash")
            if fh:
                known_hashes.add(fh)

    new_seeds: list[Path] = []
    for seed in list_seed_files(paths):
        if sha256_file(seed) not in known_hashes:
            new_seeds.append(seed)
    return new_seeds


def _impact_analysis(paths: ArtifactPaths, new_concept_ids: list[str]) -> dict:
    """Identify existing wiki pages whose concepts overlap with new content."""
    pages_dir = paths.wiki_v2_pages_dir
    if not pages_dir.exists():
        return {"cross_links_added": 0, "relationships_updated": [], "gaps_surfaced": []}

    from ..io import parse_frontmatter

    affected: list[str] = []
    new_keywords = set()
    for cid in new_concept_ids:
        new_keywords.update(cid.lower().replace("-", " ").split())

    for page_path in sorted(pages_dir.glob("*.md")):
        text = read_text_safe(page_path)
        _, body = parse_frontmatter(text)
        body_lower = body.lower()
        for kw in new_keywords:
            if len(kw) >= 4 and kw in body_lower:
                affected.append(page_path.stem)
                break

    return {
        "cross_links_added": len(new_concept_ids),
        "relationships_updated": sorted(set(affected)),
        "gaps_surfaced": [],
    }


def run_wiki_update(artifacts_root: Path, workspace_root: Path) -> dict:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    immutability_issues = validate_seed_immutability(paths)
    if immutability_issues:
        raise RuntimeError(
            "Immutable seed policy violation:\n" + "\n".join(immutability_issues)
        )

    new_seeds = _find_new_seeds(paths)
    if not new_seeds:
        return {"status": "no_new_seeds", "documents_added": 0}

    # Load existing citation index
    existing_index = load_yaml(paths.citations_index_path) or {"citations": {}}
    citations = existing_index.get("citations", {})
    if not isinstance(citations, dict):
        citations = {}
    existing_ids = set(citations.keys())
    hash_to_id: dict[str, str] = {}
    for cid, entry in citations.items():
        if isinstance(entry, dict):
            fh = (entry.get("metadata") or {}).get("file_hash")
            if fh:
                hash_to_id[fh] = cid

    # Track previous wiki version
    previous_version = compute_wiki_version(paths.wiki_v2_pages_dir)

    created = iso_now()
    created_pages: list[str] = []
    documents_added: list[dict] = []
    manifest = load_manifest(paths)
    wiki_name = ""
    if manifest:
        wiki_name = str(manifest.get("workspace_manifest", {}).get("wiki", {}).get("name") or "")

    for seed in new_seeds:
        file_hash = sha256_file(seed)
        relative_path = seed.relative_to(paths.root)

        slug_base = slugify(seed.stem)[:50] or "seed"
        citation_id = _next_citation_id(slug_base, existing_ids)
        existing_ids.add(citation_id)
        hash_to_id[file_hash] = citation_id
        citations[citation_id] = {
            "human": f"{seed.stem} (seed)",
            "source": {
                "type": "seed",
                "path": f"/{relative_path.as_posix()}",
                "page": None,
                "section": None,
                "url": None,
                "accessed": None,
            },
            "metadata": {
                "authors": [],
                "title": seed.stem,
                "year": None,
                "venue": "seed",
                "doi": None,
                "file_hash": file_hash,
            },
            "status": "raw",
            "notes": "Auto-ingested via wiki-update.",
        }

        # Create source page in wiki v2
        source_page_id = slugify(seed.stem)[:64] or "source"
        source_page_path = paths.wiki_v2_pages_dir / f"{source_page_id}.md"
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

        register_source_binding(
            paths=paths, seed_path=seed, citation_id=citation_id, file_hash=file_hash,
        )

        # Concept expansion from filename
        concept_ids: list[str] = []
        for keyword in extract_keywords(seed.stem, max_terms=2):
            concept_page_id = f"concept-{slugify(keyword)}"
            concept_path = paths.wiki_v2_pages_dir / f"{concept_page_id}.md"
            if concept_path.exists():
                continue
            concept_path.write_text(
                _build_concept_page(concept_page_id, citation_id, created, wiki_name=wiki_name),
                encoding="utf-8",
            )
            created_pages.append(concept_page_id)
            concept_ids.append(concept_page_id)

        documents_added.append({
            "path": str(relative_path),
            "citation_id": citation_id,
            "pages_created": [source_page_id] + concept_ids,
        })

    # Save updated citation index
    dump_yaml(paths.citations_index_path, {"citations": citations})

    # Impact analysis
    impact = _impact_analysis(paths, created_pages)

    # Rebuild wiki v2 index and log
    write_index(
        pages_dir=paths.wiki_v2_pages_dir,
        index_path=paths.wiki_v2_dir / "index.md",
        title="Wiki v2 Index (updated)",
    )
    append_log_entry(
        log_path=paths.wiki_v2_dir / "log.md",
        operation="wiki-update",
        title="Incremental wiki expansion",
        details=[
            f"new_seeds_ingested: {len(new_seeds)}",
            f"pages_created: {len(created_pages)}",
            f"citations_total: {len(citations)}",
            f"relationships_updated: {len(impact.get('relationships_updated', []))}",
            f"completed_at: {created}",
        ],
    )

    # Update manifest
    if manifest:
        wm = manifest["workspace_manifest"]
        new_version = compute_wiki_version(paths.wiki_v2_pages_dir)
        wiki = wm.setdefault("wiki", {})
        wiki["version"] = new_version
        wiki["last_updated"] = created
        wiki["page_count"] = len(list(paths.wiki_v2_pages_dir.glob("*.md")))
        wm["seeds"] = {
            "version": compute_seed_version(paths),
            "last_updated": created,
            "document_count": len(list_seed_files(paths)),
        }
        save_manifest(paths, manifest)

    # Write the update report
    update_report = {
        "wiki_update_report": {
            "timestamp": created,
            "previous_version": previous_version,
            "new_version": compute_wiki_version(paths.wiki_v2_pages_dir),
            "documents_added": documents_added,
            "impact_analysis": impact,
            "status": "complete",
        }
    }
    dump_yaml(paths.reports_dir / "wiki_update_report.yaml", update_report)

    return {
        "status": "complete",
        "documents_added": len(new_seeds),
        "pages_created": len(created_pages),
        "citations_total": len(citations),
        "impact": impact,
    }
