from __future__ import annotations

from pathlib import Path

from .artifacts import ArtifactPaths, list_seed_files
from .io import dump_yaml, load_yaml
from .utils import iso_now, sha256_file


def _load_bindings(paths: ArtifactPaths) -> dict:
    payload = load_yaml(paths.source_bindings_path)
    if not payload:
        return {"bindings": {}}
    bindings = payload.get("bindings")
    if not isinstance(bindings, dict):
        return {"bindings": {}}
    return {"bindings": bindings}


def _save_bindings(paths: ArtifactPaths, payload: dict) -> None:
    dump_yaml(paths.source_bindings_path, payload)


def validate_seed_immutability(paths: ArtifactPaths) -> list[str]:
    payload = _load_bindings(paths)
    bindings = payload.get("bindings", {})

    issues: list[str] = []
    for seed in list_seed_files(paths):
        relative = str(seed.relative_to(paths.root).as_posix())
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

    relative = str(seed_path.relative_to(paths.root).as_posix())
    existing = bindings.get(relative)
    now = iso_now()

    if isinstance(existing, dict):
        existing_hash = existing.get("sha256")
        if existing_hash and existing_hash != file_hash:
            raise RuntimeError(
                f"Immutable seed violation for {relative}: expected {existing_hash}, got {file_hash}"
            )
        existing["citation_id"] = citation_id
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
