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
    wiki_provenance_dir: Path
    citations_index_path: Path
    reports_dir: Path
    reviews_dir: Path
    reviews_search_dir: Path
    decision_logs_dir: Path
    scaffolds_dir: Path
    executions_dir: Path
    pitches_dir: Path
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
        wiki_provenance_dir=wiki_dir / "provenance",
        citations_index_path=wiki_dir / "citations" / "index.yaml",
        reports_dir=wiki_dir / "reports",
        reviews_dir=wiki_dir / "reviews",
        reviews_search_dir=wiki_dir / "reviews" / "search",
        decision_logs_dir=resolved / "decision-logs",
        scaffolds_dir=resolved / "scaffolds",
        executions_dir=resolved / "executions",
        pitches_dir=resolved / "pitches",
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
        paths.wiki_provenance_dir,
        paths.citations_index_path.parent,
        paths.reports_dir,
        paths.reviews_dir,
        paths.reviews_search_dir,
        paths.decision_logs_dir,
        paths.scaffolds_dir,
        paths.executions_dir,
        paths.pitches_dir,
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
    return ensure_manifest_defaults(raw)


def derive_wiki_name(project_name: str, project_type: str) -> str:
    base = project_name.strip() or "META-COMPILER"
    suffix = {
        "algorithm": "Build Atlas",
        "report": "Research Atlas",
        "hybrid": "Project Atlas",
    }.get(project_type, "Knowledge Atlas")
    if base.lower().endswith(suffix.lower()):
        return base
    return f"{base} {suffix}"


def ensure_manifest_defaults(manifest: dict) -> dict:
    manifest.setdefault("workspace_manifest", {})
    wm = manifest["workspace_manifest"]

    seeds = wm.setdefault("seeds", {})
    seeds.setdefault("version", "")
    seeds.setdefault("last_updated", "")
    seeds.setdefault("document_count", 0)

    wiki = wm.setdefault("wiki", {})
    wiki.setdefault("version", "")
    wiki.setdefault("last_updated", "")
    wiki.setdefault("page_count", 0)
    wiki.setdefault("name", "")

    wm.setdefault("decision_logs", [])
    wm.setdefault("executions", [])
    wm.setdefault("pitches", [])

    research = wm.setdefault("research", {})
    research.setdefault("iteration_count", 0)
    research.setdefault("last_completed_stage", "0")

    return manifest


def save_manifest(paths: ArtifactPaths, manifest: dict) -> None:
    ensure_manifest_defaults(manifest)
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


def list_scaffold_versions(paths: ArtifactPaths) -> list[int]:
    versions: list[int] = []
    for path in sorted(paths.scaffolds_dir.glob("v*")):
        if not path.is_dir():
            continue
        stem = path.name
        if not stem.startswith("v"):
            continue
        try:
            versions.append(int(stem[1:]))
        except ValueError:
            continue
    return sorted(versions)


def latest_scaffold_path(paths: ArtifactPaths) -> tuple[int, Path] | None:
    versions = list_scaffold_versions(paths)
    if not versions:
        return None
    latest_version = versions[-1]
    return latest_version, paths.scaffolds_dir / f"v{latest_version}"
