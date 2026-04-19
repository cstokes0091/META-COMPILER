"""Edit-tracking manifest for v2 wiki pages.

Stage 1B's `_sync_v1_to_v2` and the Phase C enrichment passes write through
this module. Each system write records the post-write SHA-256 of the page so a
later sync can distinguish "system wrote it last; safe to overwrite" from
"user (or another LLM pass) edited it; preserve".

The manifest lives at `<artifacts_root>/wiki/v2/edit_manifest.yaml` and has the
shape:

    wiki_v2_edit_manifest:
      version: 1
      last_updated: <iso>
      pages:
        <page_filename>:
          last_system_write_sha: <hex>
          source: depth_baseline | enrichment | wiki_linker | relationship_mapper | gap_remediation
          last_system_write_at: <iso>

Use `record_write` after writing a page; use `is_user_edited` before
overwriting one. Sources are recorded so reports can attribute preservation
correctly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import ArtifactPaths
from .io import dump_yaml, load_yaml
from .utils import iso_now, sha256_file

VALID_SOURCES = {
    "depth_baseline",
    "enrichment",
    "wiki_linker",
    "relationship_mapper",
    "gap_remediation",
}


def manifest_path(paths: ArtifactPaths) -> Path:
    return paths.wiki_v2_dir / "edit_manifest.yaml"


def load(paths: ArtifactPaths) -> dict[str, Any]:
    raw = load_yaml(manifest_path(paths))
    if not isinstance(raw, dict) or "wiki_v2_edit_manifest" not in raw:
        return {
            "wiki_v2_edit_manifest": {
                "version": 1,
                "last_updated": "",
                "pages": {},
            }
        }
    root = raw["wiki_v2_edit_manifest"]
    root.setdefault("version", 1)
    root.setdefault("last_updated", "")
    pages = root.setdefault("pages", {})
    if not isinstance(pages, dict):
        root["pages"] = {}
    return raw


def save(paths: ArtifactPaths, manifest: dict[str, Any]) -> None:
    manifest["wiki_v2_edit_manifest"]["last_updated"] = iso_now()
    dump_yaml(manifest_path(paths), manifest)


def entry_for(paths: ArtifactPaths, page_path: Path) -> dict[str, Any] | None:
    manifest = load(paths)
    return manifest["wiki_v2_edit_manifest"]["pages"].get(page_path.name)


def record_write(paths: ArtifactPaths, page_path: Path, source: str) -> None:
    if source not in VALID_SOURCES:
        raise ValueError(
            f"Invalid edit-manifest source {source!r}. Valid: {sorted(VALID_SOURCES)}"
        )
    if not page_path.exists():
        raise FileNotFoundError(
            f"Cannot record write for missing page: {page_path}"
        )
    manifest = load(paths)
    manifest["wiki_v2_edit_manifest"]["pages"][page_path.name] = {
        "last_system_write_sha": sha256_file(page_path),
        "source": source,
        "last_system_write_at": iso_now(),
    }
    save(paths, manifest)


def record_writes(paths: ArtifactPaths, writes: list[tuple[Path, str]]) -> None:
    """Batched variant — one manifest read+write for many pages."""
    if not writes:
        return
    manifest = load(paths)
    pages = manifest["wiki_v2_edit_manifest"]["pages"]
    now = iso_now()
    for page_path, source in writes:
        if source not in VALID_SOURCES:
            raise ValueError(
                f"Invalid edit-manifest source {source!r}. Valid: {sorted(VALID_SOURCES)}"
            )
        if not page_path.exists():
            raise FileNotFoundError(
                f"Cannot record write for missing page: {page_path}"
            )
        pages[page_path.name] = {
            "last_system_write_sha": sha256_file(page_path),
            "source": source,
            "last_system_write_at": now,
        }
    save(paths, manifest)


def is_user_edited(paths: ArtifactPaths, page_path: Path) -> bool:
    """True iff the page exists and its current SHA differs from the recorded one.

    A page with no manifest entry is not considered user-edited; callers are
    responsible for first-write registration.
    """
    if not page_path.exists():
        return False
    entry = entry_for(paths, page_path)
    if not entry:
        return False
    recorded = entry.get("last_system_write_sha")
    if not recorded:
        return False
    return sha256_file(page_path) != recorded


def forget(paths: ArtifactPaths, page_filename: str) -> None:
    manifest = load(paths)
    pages = manifest["wiki_v2_edit_manifest"]["pages"]
    if page_filename in pages:
        del pages[page_filename]
        save(paths, manifest)


def prune_missing(paths: ArtifactPaths) -> int:
    """Remove manifest entries for pages no longer in the v2 pages dir."""
    manifest = load(paths)
    pages = manifest["wiki_v2_edit_manifest"]["pages"]
    existing = {p.name for p in paths.wiki_v2_pages_dir.glob("*.md")}
    removed = [name for name in list(pages.keys()) if name not in existing]
    for name in removed:
        del pages[name]
    if removed:
        save(paths, manifest)
    return len(removed)
