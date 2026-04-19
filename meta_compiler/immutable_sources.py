from __future__ import annotations

import subprocess
from pathlib import Path

from .artifacts import ArtifactPaths, list_seed_files
from .io import dump_yaml, load_yaml
from .utils import iso_now, sha256_file


def _load_bindings(paths: ArtifactPaths) -> dict:
    payload = load_yaml(paths.source_bindings_path)
    if not payload:
        return {"bindings": {}, "code_bindings": {}}
    bindings = payload.get("bindings")
    if not isinstance(bindings, dict):
        bindings = {}
    code_bindings = payload.get("code_bindings")
    if not isinstance(code_bindings, dict):
        code_bindings = {}
    return {"bindings": bindings, "code_bindings": code_bindings}


def _save_bindings(paths: ArtifactPaths, payload: dict) -> None:
    dump_yaml(paths.source_bindings_path, payload)


def _git_head(repo_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return proc.stdout.strip() or None


def validate_seed_immutability(paths: ArtifactPaths) -> list[str]:
    payload = _load_bindings(paths)
    bindings = payload.get("bindings", {})
    code_bindings = payload.get("code_bindings", {})

    issues: list[str] = []

    # Commit-SHA check for registered code repos. Prefixes captured here are
    # excluded from per-file SHA checks so the commit pin is the immutability
    # boundary for code seeds.
    code_prefixes: list[str] = []
    for relative, entry in code_bindings.items():
        if not isinstance(entry, dict):
            continue
        prefix = str(relative).rstrip("/") + "/"
        code_prefixes.append(prefix)
        repo_root = (paths.root / str(relative)).resolve()
        if not repo_root.exists():
            issues.append(
                f"Code seed missing on disk: {relative}. Re-run add-code-seed."
            )
            continue
        expected = entry.get("commit_sha")
        actual = _git_head(repo_root)
        if actual is None:
            issues.append(
                f"Code seed not a git repo or git unavailable: {relative}."
            )
            continue
        if expected and actual != expected:
            issues.append(
                f"Code repo drift at {relative}: bound {expected[:12]}..., HEAD {actual[:12]}...."
            )

    for seed in list_seed_files(paths):
        relative = str(seed.relative_to(paths.root).as_posix())
        if any(relative.startswith(prefix) for prefix in code_prefixes):
            continue
        existing = bindings.get(relative)
        if not isinstance(existing, dict):
            continue

        current_hash = sha256_file(seed)
        previous_hash = existing.get("sha256")
        if previous_hash and previous_hash != current_hash:
            issues.append(
                f"Seed mutation detected for {relative}. Previously {previous_hash}, now {current_hash}."
            )
    return issues


def register_source_binding(
    paths: ArtifactPaths,
    seed_path: Path,
    citation_id: str,
    file_hash: str,
) -> None:
    payload = _load_bindings(paths)
    bindings = payload["bindings"]
    code_bindings = payload.get("code_bindings", {})

    relative = str(seed_path.relative_to(paths.root).as_posix())
    code_prefixes = [
        str(prefix).rstrip("/") + "/" for prefix in code_bindings if isinstance(prefix, str)
    ]
    is_code_path = any(relative.startswith(prefix) for prefix in code_prefixes)

    existing = bindings.get(relative)
    now = iso_now()

    if isinstance(existing, dict):
        existing_hash = existing.get("sha256")
        if existing_hash and existing_hash != file_hash and not is_code_path:
            raise RuntimeError(
                f"Immutable seed violation for {relative}: expected {existing_hash}, got {file_hash}"
            )
        existing["citation_id"] = citation_id
        existing["sha256"] = file_hash
        existing["last_seen"] = now
        bindings[relative] = existing
    else:
        bindings[relative] = {
            "citation_id": citation_id,
            "sha256": file_hash,
            "first_seen": now,
            "last_seen": now,
        }

    _save_bindings(paths, payload)


def snapshot_seed_inventory(paths: ArtifactPaths) -> dict:
    payload = _load_bindings(paths)
    bindings = payload.get("bindings", {})

    inventory: list[dict] = []
    for seed in list_seed_files(paths):
        relative = str(seed.relative_to(paths.root).as_posix())
        current_hash = sha256_file(seed)
        binding = bindings.get(relative) if isinstance(bindings, dict) else None
        inventory.append(
            {
                "path": relative,
                "sha256": current_hash,
                "citation_id": (binding or {}).get("citation_id") if isinstance(binding, dict) else None,
            }
        )

    return {
        "generated_at": iso_now(),
        "items": inventory,
    }
