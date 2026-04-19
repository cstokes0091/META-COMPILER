"""Seed file tracker: detect new seed files and report for ingest handoff.

Hashes every seed under `workspace-artifacts/seeds/` against the file-hash
set recorded in `citations/index.yaml` and reports any not-yet-ingested
seeds. No wiki mutation happens here — new seeds are handed off to
`meta-compiler ingest --scope new` for full-fidelity extraction, then
`research-breadth` to render new pages from the resulting findings.

Used by `run-all` (it invokes this, then conditionally runs ingest) and
can also be invoked directly via `meta-compiler track-seeds`.
"""
from __future__ import annotations

from pathlib import Path

from ..artifacts import build_paths, list_seed_files
from ..immutable_sources import snapshot_seed_inventory
from ..io import dump_yaml, load_yaml
from ..utils import iso_now, sha256_file


def _load_tracked_hashes(paths) -> set[str]:
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
    """Detect new seed files and report whether ingest needs to run.

    Returns a dict. When `new_seeds_found` is True, the caller should run
    `meta-compiler ingest --scope new` followed by `research-breadth` to
    produce findings + pages for the new seeds, then (optionally)
    `wiki-reconcile-concepts` if any of the new concepts may alias
    existing canonical pages.
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

    tracking_report = {
        "seed_tracking_report": {
            "timestamp": iso_now(),
            "new_seeds_detected": new_seeds,
            "total_seeds": len(seeds),
            "next_steps": [
                "meta-compiler ingest --scope new",
                "meta-compiler research-breadth",
                "meta-compiler wiki-reconcile-concepts --version 2 "
                "(when new concepts may alias existing canonical pages)",
            ],
        }
    }
    report_path = paths.reports_dir / "seed_tracking_report.yaml"
    dump_yaml(report_path, tracking_report)

    inventory = snapshot_seed_inventory(paths)
    dump_yaml(paths.manifests_dir / "seed_inventory.yaml", inventory)

    return {
        "new_seeds_found": True,
        "new_seed_paths": new_seeds,
        "total_seeds": len(seeds),
        "message": (
            f"Detected {len(new_seeds)} new seed(s). "
            "Run `meta-compiler ingest --scope new` followed by "
            "`meta-compiler research-breadth` to extract and render them."
        ),
    }
