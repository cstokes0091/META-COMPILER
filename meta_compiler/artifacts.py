from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .io import dump_yaml, load_yaml
from .utils import iso_now, sha256_strings, sha256_file


MANIFEST_NAME = "workspace_manifest.yaml"


@dataclass(frozen=True)
class ArtifactPaths:
    root: Path
    seeds_dir: Path
    wiki_dir: Path
    wiki_v1_dir: Path
    wiki_v1_pages_dir: Path
    wiki_v2_dir: Path
    wiki_v2_pages_dir: Path
    citations_index_path: Path
    reports_dir: Path
    reviews_dir: Path
    decision_logs_dir: Path
    scaffolds_dir: Path
    manifests_dir: Path
    manifest_path: Path
    source_bindings_path: Path
    runtime_dir: Path


def build_paths(root: Path) -> ArtifactPaths:
    resolved = root.resolve()
    wiki_dir = resolved / "wiki"
    manifests_dir = resolved / "manifests"
    return ArtifactPaths(
        root=resolved,
        seeds_dir=resolved / "seeds",
        wiki_dir=wiki_dir,
        wiki_v1_dir=wiki_dir / "v1",
        wiki_v1_pages_dir=wiki_dir / "v1" / "pages",
        wiki_v2_dir=wiki_dir / "v2",
        wiki_v2_pages_dir=wiki_dir / "v2" / "pages",
        citations_index_path=wiki_dir / "citations" / "index.yaml",
        reports_dir=wiki_dir / "reports",
        reviews_dir=wiki_dir / "reviews",
        decision_logs_dir=resolved / "decision-logs",
        scaffolds_dir=resolved / "scaffolds",
        manifests_dir=manifests_dir,
        manifest_path=manifests_dir / MANIFEST_NAME,
        source_bindings_path=manifests_dir / "source_bindings.yaml",
        runtime_dir=resolved / "runtime",
    )


def ensure_layout(paths: ArtifactPaths) -> None:
    for directory in [
        paths.root,
        paths.seeds_dir,
        paths.wiki_v1_pages_dir,
        paths.wiki_v2_pages_dir,
        paths.citations_index_path.parent,
        paths.reports_dir,
        paths.reviews_dir,
        paths.decision_logs_dir,
        paths.scaffolds_dir,
        paths.manifests_dir,
        paths.runtime_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def list_seed_files(paths: ArtifactPaths) -> list[Path]:
    if not paths.seeds_dir.exists():
        return []
    files = [
        path
        for path in paths.seeds_dir.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    ]
    return sorted(files)


def seed_inventory(paths: ArtifactPaths) -> list[dict[str, str | int]]:
    inventory: list[dict[str, str | int]] = []
    for seed in list_seed_files(paths):
        inventory.append(
            {
                "path": str(seed.relative_to(paths.root)),
                "sha256": sha256_file(seed),
                "size": seed.stat().st_size,
            }
        )
    return inventory


def compute_seed_version(paths: ArtifactPaths) -> str:
    records = [f"{row['path']}:{row['sha256']}" for row in seed_inventory(paths)]
    return sha256_strings(records)


def compute_wiki_version(pages_dir: Path) -> str:
    if not pages_dir.exists():
        return sha256_strings([])

    records: list[str] = []
    for page in sorted(pages_dir.glob("*.md")):
        content_hash = sha256_file(page)
        records.append(f"{page.name}:{content_hash}")
    return sha256_strings(records)


def load_manifest(paths: ArtifactPaths) -> dict:
    raw = load_yaml(paths.manifest_path)
    if not raw:
        return {}
    return raw


def save_manifest(paths: ArtifactPaths, manifest: dict) -> None:
    manifest.setdefault("workspace_manifest", {})
    manifest["workspace_manifest"]["last_modified"] = iso_now()
    dump_yaml(paths.manifest_path, manifest)


def list_decision_log_versions(paths: ArtifactPaths) -> list[int]:
    versions: list[int] = []
    for path in sorted(paths.decision_logs_dir.glob("decision_log_v*.yaml")):
        stem = path.stem
        prefix = "decision_log_v"
        if not stem.startswith(prefix):
            continue
        try:
            versions.append(int(stem[len(prefix) :]))
        except ValueError:
            continue
    return sorted(versions)


def latest_decision_log_path(paths: ArtifactPaths) -> tuple[int, Path] | None:
    versions = list_decision_log_versions(paths)
    if not versions:
        return None
    latest_version = versions[-1]
    return latest_version, paths.decision_logs_dir / f"decision_log_v{latest_version}.yaml"
