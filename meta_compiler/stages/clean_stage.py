"""clean-workspace command: Reset workspace to a specific stage.

Removes artifacts produced by stages **after** the target stage, letting the
user re-run from that point without manually deleting files.

Stage reset points:
  0  — keep only manifest + problem statement + seeds
  1a — keep wiki v1 baseline structure
  1b — keep through depth pass
  1c — keep through review
  2  — keep through decision log
  3  — keep through scaffold
  4  — full reset to post-init (alias for 0)
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..artifacts import (
    build_paths,
    ensure_layout,
    load_manifest,
    save_manifest,
)
from ..utils import iso_now


_STAGE_ORDER = ["0", "1a", "1b", "1c", "2", "3", "4"]


def _safe_rmtree(path: Path) -> int:
    """Remove a directory tree, returning the count of removed items."""
    if not path.exists():
        return 0
    count = sum(1 for _ in path.rglob("*") if _.is_file())
    shutil.rmtree(path)
    return count


def _safe_rm_glob(directory: Path, pattern: str) -> int:
    """Remove files matching a glob pattern, returning count removed."""
    count = 0
    if not directory.exists():
        return count
    for path in list(directory.glob(pattern)):
        if path.is_file():
            path.unlink()
            count += 1
        elif path.is_dir():
            count += sum(1 for _ in path.rglob("*") if _.is_file())
            shutil.rmtree(path)
    return count


def run_clean_workspace(
    artifacts_root: Path,
    workspace_root: Path,
    target_stage: str,
) -> dict:
    """Reset the workspace to just after the given stage completed."""
    if target_stage not in _STAGE_ORDER:
        raise ValueError(
            f"Invalid target stage: {target_stage}. "
            f"Must be one of: {', '.join(_STAGE_ORDER)}"
        )

    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    target_idx = _STAGE_ORDER.index(target_stage)
    removed_count = 0
    removed_areas: list[str] = []

    # Stage 4 artifacts: executions/ pitches/
    if target_idx < _STAGE_ORDER.index("4"):
        n = _safe_rmtree(paths.executions_dir)
        if n:
            removed_areas.append(f"executions ({n} files)")
            removed_count += n
        n = _safe_rmtree(paths.pitches_dir)
        if n:
            removed_areas.append(f"pitches ({n} files)")
            removed_count += n

    # Stage 3 artifacts: scaffolds/
    if target_idx < _STAGE_ORDER.index("3"):
        n = _safe_rmtree(paths.scaffolds_dir)
        if n:
            removed_areas.append(f"scaffolds ({n} files)")
            removed_count += n

    # Stage 2 artifacts: decision-logs/
    if target_idx < _STAGE_ORDER.index("2"):
        n = _safe_rmtree(paths.decision_logs_dir)
        if n:
            removed_areas.append(f"decision-logs ({n} files)")
            removed_count += n

    # Stage 1C artifacts: reviews/
    if target_idx < _STAGE_ORDER.index("1c"):
        n = _safe_rmtree(paths.reviews_dir)
        if n:
            removed_areas.append(f"reviews ({n} files)")
            removed_count += n

    # Stage 1B artifacts: wiki/v2/ reports/
    if target_idx < _STAGE_ORDER.index("1b"):
        n = _safe_rmtree(paths.wiki_v2_dir)
        if n:
            removed_areas.append(f"wiki-v2 ({n} files)")
            removed_count += n
        n = _safe_rmtree(paths.reports_dir)
        if n:
            removed_areas.append(f"reports ({n} files)")
            removed_count += n

    # Stage 1A artifacts: wiki/v1/ citations/
    if target_idx < _STAGE_ORDER.index("1a"):
        n = _safe_rmtree(paths.wiki_v1_dir)
        if n:
            removed_areas.append(f"wiki-v1 ({n} files)")
            removed_count += n
        n = _safe_rmtree(paths.citations_index_path.parent)
        if n:
            removed_areas.append(f"citations ({n} files)")
            removed_count += n
        n = _safe_rmtree(paths.wiki_provenance_dir)
        if n:
            removed_areas.append(f"provenance ({n} files)")
            removed_count += n

    # Re-create the directory layout
    ensure_layout(paths)

    # Update manifest to reflect the reset
    manifest = load_manifest(paths)
    if manifest:
        wm = manifest.get("workspace_manifest", {})
        research = wm.get("research", {})
        research["last_completed_stage"] = target_stage
        wm["status"] = "reset" if target_stage == "0" else "active"
        wm["last_modified"] = iso_now()
        save_manifest(paths, manifest)

    return {
        "status": "clean",
        "target_stage": target_stage,
        "files_removed": removed_count,
        "areas_cleaned": removed_areas,
        "message": (
            f"Workspace reset to Stage {target_stage}. "
            f"Removed {removed_count} files from {len(removed_areas)} areas. "
            f"You can now re-run from Stage {target_stage} onward."
        ),
    }
