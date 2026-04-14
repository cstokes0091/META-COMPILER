from __future__ import annotations

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
    snapshot_seed_inventory,
    validate_seed_immutability,
)
from ..io import dump_yaml, load_yaml, render_frontmatter
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

    for seed in seeds:
        file_hash = sha256_file(seed)
        relative_path = seed.relative_to(paths.root)

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
                "notes": "Auto-ingested in Stage 1A.",
            }

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

        register_source_binding(
            paths=paths,
            seed_path=seed,
            citation_id=citation_id,
            file_hash=file_hash,
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
            f"ingest_completed_at: {created}",
        ],
    )

    _update_manifest(paths, stage="1A", page_count=len(all_pages))

    return {
        "seed_count": len(seeds),
        "citation_count": len(citations),
        "page_count": len(all_pages),
    }
