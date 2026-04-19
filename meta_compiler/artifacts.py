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
    seeds_code_dir: Path
    wiki_dir: Path
    wiki_v1_dir: Path
    wiki_v1_pages_dir: Path
    wiki_v2_dir: Path
    wiki_v2_pages_dir: Path
    wiki_provenance_dir: Path
    citations_index_path: Path
    findings_dir: Path
    findings_index_path: Path
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
    # Stage 2 prompt-as-conductor runtime artifacts.
    # See .github/docs/stage-2-hardening.md §6.
    stage2_runtime_dir: Path
    stage2_brief_path: Path
    stage2_transcript_path: Path
    stage2_precheck_request_path: Path
    stage2_precheck_verdict_path: Path
    stage2_postcheck_request_path: Path
    stage2_postcheck_verdict_path: Path
    # Ingest-orchestrator prompt-as-conductor runtime artifacts.
    # Mirrors the Stage 2 hardening pattern; see Phase A of the
    # missing-features plan.
    ingest_runtime_dir: Path
    ingest_precheck_request_path: Path
    ingest_precheck_verdict_path: Path
    ingest_postcheck_request_path: Path
    ingest_postcheck_verdict_path: Path
    ingest_report_path: Path
    # Code ingestion: two-pass orchestration persists repo-mapper outputs here
    # before per-file code-reader fan-out consumes them.
    runtime_repo_map_dir: Path
    # Stage 4 prompt-as-conductor runtime artifacts.
    phase4_runtime_dir: Path
    phase4_execution_request_path: Path
    phase4_preflight_verdict_path: Path
    phase4_postcheck_request_path: Path
    phase4_postcheck_verdict_path: Path


def build_paths(root: Path) -> ArtifactPaths:
    resolved = root.resolve()
    wiki_dir = resolved / "wiki"
    manifests_dir = resolved / "manifests"
    runtime_dir = resolved / "runtime"
    stage2_runtime_dir = runtime_dir / "stage2"
    ingest_runtime_dir = runtime_dir / "ingest"
    phase4_runtime_dir = runtime_dir / "phase4"
    return ArtifactPaths(
        root=resolved,
        seeds_dir=resolved / "seeds",
        seeds_code_dir=resolved / "seeds" / "code",
        wiki_dir=wiki_dir,
        wiki_v1_dir=wiki_dir / "v1",
        wiki_v1_pages_dir=wiki_dir / "v1" / "pages",
        wiki_v2_dir=wiki_dir / "v2",
        wiki_v2_pages_dir=wiki_dir / "v2" / "pages",
        wiki_provenance_dir=wiki_dir / "provenance",
        citations_index_path=wiki_dir / "citations" / "index.yaml",
        findings_dir=wiki_dir / "findings",
        findings_index_path=wiki_dir / "findings" / "index.yaml",
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
        runtime_dir=runtime_dir,
        stage2_runtime_dir=stage2_runtime_dir,
        stage2_brief_path=stage2_runtime_dir / "brief.md",
        stage2_transcript_path=stage2_runtime_dir / "transcript.md",
        stage2_precheck_request_path=stage2_runtime_dir / "precheck_request.yaml",
        stage2_precheck_verdict_path=stage2_runtime_dir / "precheck_verdict.yaml",
        stage2_postcheck_request_path=stage2_runtime_dir / "postcheck_request.yaml",
        stage2_postcheck_verdict_path=stage2_runtime_dir / "postcheck_verdict.yaml",
        ingest_runtime_dir=ingest_runtime_dir,
        ingest_precheck_request_path=ingest_runtime_dir / "precheck_request.yaml",
        ingest_precheck_verdict_path=ingest_runtime_dir / "precheck_verdict.yaml",
        ingest_postcheck_request_path=ingest_runtime_dir / "postcheck_request.yaml",
        ingest_postcheck_verdict_path=ingest_runtime_dir / "postcheck_verdict.yaml",
        ingest_report_path=wiki_dir / "reports" / "ingest_report.yaml",
        runtime_repo_map_dir=ingest_runtime_dir / "repo_map",
        phase4_runtime_dir=phase4_runtime_dir,
        phase4_execution_request_path=phase4_runtime_dir / "execution_request.yaml",
        phase4_preflight_verdict_path=phase4_runtime_dir / "preflight_verdict.yaml",
        phase4_postcheck_request_path=phase4_runtime_dir / "postcheck_request.yaml",
        phase4_postcheck_verdict_path=phase4_runtime_dir / "postcheck_verdict.yaml",
    )


def ensure_layout(paths: ArtifactPaths) -> None:
    for directory in [
        paths.root,
        paths.seeds_dir,
        paths.seeds_code_dir,
        paths.wiki_v1_pages_dir,
        paths.wiki_v2_pages_dir,
        paths.wiki_provenance_dir,
        paths.citations_index_path.parent,
        paths.findings_dir,
        paths.reports_dir,
        paths.reviews_dir,
        paths.reviews_search_dir,
        paths.decision_logs_dir,
        paths.scaffolds_dir,
        paths.executions_dir,
        paths.pitches_dir,
        paths.manifests_dir,
        paths.runtime_dir,
        paths.stage2_runtime_dir,
        paths.ingest_runtime_dir,
        paths.runtime_repo_map_dir,
        paths.phase4_runtime_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


# Directories that should never surface as ingestable seeds even when they
# live under seeds/code/<repo>/. These are build output, dependency caches,
# or VCS metadata — never source material.
_SEED_EXCLUDED_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    "target",
    "build",
    "dist",
    ".venv",
    "venv",
    ".next",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".ruff_cache",
    ".idea",
    ".vscode",
}


def _is_excluded_seed_path(seed: Path, seeds_dir: Path) -> bool:
    try:
        relative = seed.relative_to(seeds_dir)
    except ValueError:
        return False
    for part in relative.parts[:-1]:
        if part in _SEED_EXCLUDED_DIR_NAMES:
            return True
    return False


def _git_tracked_files(repo_root: Path) -> list[Path] | None:
    """Return files the repo considers source (cached + untracked, .gitignore-aware).

    Returns None when git is unavailable or the directory is not a repo; callers
    should fall back to glob+exclusion. Uses subprocess for stdlib-only portability.
    """
    import subprocess

    if not (repo_root / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    entries: list[Path] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(repo_root / line)
    return entries


def _code_repo_roots(paths: ArtifactPaths) -> list[Path]:
    if not paths.seeds_code_dir.exists():
        return []
    roots: list[Path] = []
    for child in sorted(paths.seeds_code_dir.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            roots.append(child)
    return roots


def list_seed_files(paths: ArtifactPaths) -> list[Path]:
    if not paths.seeds_dir.exists():
        return []

    code_roots = _code_repo_roots(paths)
    code_root_set = {root.resolve() for root in code_roots}
    git_tracked: list[Path] = []
    tracked_code_roots: set[Path] = set()
    for root in code_roots:
        tracked = _git_tracked_files(root)
        if tracked is None:
            continue
        tracked_code_roots.add(root.resolve())
        for entry in tracked:
            if entry.is_file() and not _is_excluded_seed_path(entry, paths.seeds_dir):
                git_tracked.append(entry)

    files: list[Path] = []
    for path in paths.seeds_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if _is_excluded_seed_path(path, paths.seeds_dir):
            continue
        # Skip paths under code roots that were walked via git; we already
        # collected them above (respecting .gitignore).
        resolved_parents = {parent.resolve() for parent in path.parents}
        if resolved_parents & tracked_code_roots:
            continue
        files.append(path)
    files.extend(git_tracked)

    # Deduplicate by resolved path while preserving sort order.
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in sorted(files):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(path)

    # Suppress the code_root_set variable warning — it is available for future
    # callers that want to distinguish code vs doc paths.
    _ = code_root_set
    return deduped


def list_code_repos(paths: ArtifactPaths) -> list[dict[str, str | None]]:
    """Return registered code repos from source_bindings.yaml code_bindings."""
    payload = load_yaml(paths.source_bindings_path) or {}
    raw = payload.get("code_bindings") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return []
    rows: list[dict[str, str | None]] = []
    for relative, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        repo_root = (paths.root / relative).resolve()
        rows.append(
            {
                "relative_path": str(relative).rstrip("/") + "/",
                "absolute_path": str(repo_root),
                "name": entry.get("name") or Path(relative).name,
                "remote": entry.get("remote"),
                "ref": entry.get("ref"),
                "commit_sha": entry.get("commit_sha"),
                "citation_id": entry.get("citation_id"),
            }
        )
    return rows


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
