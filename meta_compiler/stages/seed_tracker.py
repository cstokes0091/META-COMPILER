"""Seed file tracker: Detects new seed files and triggers wiki-update automatically.

This module provides objective tracking of seed files and their references in the
wiki, with automatic wiki-update when a new seed file is found. It is called
by the run-all pipeline and can also be invoked standalone.
"""
from __future__ import annotations

from pathlib import Path

from ..artifacts import (
    build_paths,
    compute_seed_version,
    list_seed_files,
    load_manifest,
    save_manifest,
)
from ..immutable_sources import snapshot_seed_inventory
from ..io import dump_yaml, load_yaml
from ..utils import iso_now, sha256_file


def _load_tracked_hashes(paths) -> set[str]:
    """Load the set of seed file hashes already tracked in the citation index."""
    existing_index = load_yaml(paths.citations_index_path) or {"citations": {}}
    citations = existing_index.get("citations", {})
    if not isinstance(citations, dict):
        return set()
    hashes: set[str] = set()
    for entry in citations.values():
        if not isinstance(entry, dict):
            continue
        metadata = entry.get("metadata", {})
        if isinstance(metadata, dict):
            fh = metadata.get("file_hash")
            if fh:
                hashes.add(fh)
    return hashes


def check_and_update_seeds(
    artifacts_root: Path,
    workspace_root: Path,
) -> dict:
    """Check for new seed files and trigger wiki-update if any are found.

    Returns a summary dict indicating whether new seeds were found and
    what action was taken.
    """
    paths = build_paths(artifacts_root)
    seeds = list_seed_files(paths)

    if not seeds:
        return {
            "new_seeds_found": False,
            "total_seeds": 0,
            "message": "No seed files present.",
        }

    tracked_hashes = _load_tracked_hashes(paths)
    new_seeds: list[str] = []

    for seed in seeds:
        file_hash = sha256_file(seed)
        if file_hash not in tracked_hashes:
            new_seeds.append(str(seed.relative_to(paths.root)))

    if not new_seeds:
        return {
            "new_seeds_found": False,
            "total_seeds": len(seeds),
            "message": "All seeds are already tracked in the wiki.",
        }

    # New seeds detected — run wiki-update
    from .wiki_update_stage import run_wiki_update

    update_result = run_wiki_update(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )

    # Write a seed tracking report
    tracking_report = {
        "seed_tracking_report": {
            "timestamp": iso_now(),
            "new_seeds_detected": new_seeds,
            "total_seeds": len(seeds),
            "wiki_update_triggered": True,
            "update_result": {
                "documents_added": update_result.get("documents_added", 0),
                "pages_created": update_result.get("pages_created", 0),
            },
        }
    }
    report_path = paths.reports_dir / "seed_tracking_report.yaml"
    dump_yaml(report_path, tracking_report)

    # Save an inventory snapshot
    inventory = snapshot_seed_inventory(paths)
    dump_yaml(paths.manifests_dir / "seed_inventory.yaml", inventory)

    return {
        "new_seeds_found": True,
        "new_seed_paths": new_seeds,
        "total_seeds": len(seeds),
        "documents_added": update_result.get("documents_added", 0),
        "pages_created": update_result.get("pages_created", 0),
        "message": (
            f"Detected {len(new_seeds)} new seed(s). "
            f"Wiki-update added {update_result.get('documents_added', 0)} documents "
            f"and created {update_result.get('pages_created', 0)} pages."
        ),
    }
