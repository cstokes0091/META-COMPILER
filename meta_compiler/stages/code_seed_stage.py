"""Code seed registration: clone or bind git repos under seeds/code/<name>/.

Companion CLI verbs: `meta-compiler add-code-seed` and `bind-code-seed`. Both
write a `code_bindings` entry into workspace-artifacts/manifests/source_bindings.yaml
so immutability enforcement can detect commit drift and downstream ingest can
emit per-repo RepoMap pre-work and per-file code-reader fan-out items.

Keeps reasoning out of this module — the LLM-side repo walk happens in the
repo-mapper agent; this layer only handles deterministic cloning and manifest
writes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactPaths, build_paths, ensure_layout
from ..io import dump_yaml, load_yaml
from ..utils import iso_now, slugify


def _load_bindings(paths: ArtifactPaths) -> dict[str, Any]:
    payload = load_yaml(paths.source_bindings_path) or {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("bindings", {})
    payload.setdefault("code_bindings", {})
    if not isinstance(payload["bindings"], dict):
        payload["bindings"] = {}
    if not isinstance(payload["code_bindings"], dict):
        payload["code_bindings"] = {}
    return payload


def _save_bindings(paths: ArtifactPaths, payload: dict[str, Any]) -> None:
    dump_yaml(paths.source_bindings_path, payload)


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _citation_id_for_repo(name: str) -> str:
    slug = slugify(name)[:50] or "repo"
    return f"src-repo-{slug}"


def _relative_prefix(paths: ArtifactPaths, repo_root: Path) -> str:
    return repo_root.resolve().relative_to(paths.root).as_posix().rstrip("/") + "/"


def run_add_code_seed(
    artifacts_root: Path,
    workspace_root: Path,
    repo: str,
    ref: str,
    name: str,
    depth: int | None = None,
    submodules: bool = False,
) -> dict[str, Any]:
    """Clone a git repo into seeds/code/<name>/ and pin it by commit SHA."""
    del workspace_root  # signature parity with other stage functions

    slug = slugify(name)
    if not slug:
        raise ValueError(f"--name must slugify to a non-empty value; got {name!r}")

    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    target = paths.seeds_code_dir / slug
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(
            f"Target directory {target.relative_to(paths.root)} already exists and is non-empty. "
            "Use bind-code-seed to re-register an existing clone."
        )

    target.mkdir(parents=True, exist_ok=True)

    clone_args = ["clone"]
    if depth and depth > 0:
        clone_args.extend(["--depth", str(depth), "--filter=blob:none"])
    clone_args.extend([repo, str(target)])
    _run_git(clone_args)

    # Ensure we can actually check out arbitrary refs when the clone was shallow.
    if depth:
        _run_git(["fetch", "--depth", str(depth), "origin", ref], cwd=target)
    else:
        _run_git(["fetch", "origin", ref], cwd=target)
    _run_git(["checkout", ref], cwd=target)

    if submodules:
        _run_git(["submodule", "update", "--init", "--recursive"], cwd=target)

    commit_sha = _run_git(["rev-parse", "HEAD"], cwd=target)
    try:
        remote = _run_git(["remote", "get-url", "origin"], cwd=target)
    except subprocess.CalledProcessError:
        remote = repo

    citation_id = _citation_id_for_repo(slug)
    relative = _relative_prefix(paths, target)
    now = iso_now()

    payload = _load_bindings(paths)
    payload["code_bindings"][relative] = {
        "type": "code-repo",
        "name": slug,
        "remote": remote,
        "ref": ref,
        "commit_sha": commit_sha,
        "cloned_at": now,
        "clone_depth": depth,
        "submodules": bool(submodules),
        "citation_id": citation_id,
    }
    _save_bindings(paths, payload)

    return {
        "status": "code_seed_added",
        "name": slug,
        "path": relative,
        "remote": remote,
        "ref": ref,
        "commit_sha": commit_sha,
        "citation_id": citation_id,
        "clone_depth": depth,
        "submodules": bool(submodules),
    }


def run_bind_code_seed(
    artifacts_root: Path,
    workspace_root: Path,
    path: str,
    name: str | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    """Bind an existing git repo already placed under seeds/code/."""
    del workspace_root

    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    repo_root = (paths.root / path).resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise RuntimeError(f"Repo path does not exist or is not a directory: {path}")
    if not (repo_root / ".git").exists():
        raise RuntimeError(f"Not a git repository (missing .git): {path}")
    if not str(repo_root).startswith(str(paths.seeds_code_dir.resolve())):
        raise RuntimeError(
            f"Bind path must live under seeds/code/; got {path}"
        )

    slug = slugify(name) if name else slugify(repo_root.name)
    if not slug:
        raise ValueError("Resolved name must slugify to a non-empty value")

    commit_sha = _run_git(["rev-parse", "HEAD"], cwd=repo_root)
    effective_ref = ref or commit_sha
    try:
        remote = _run_git(["remote", "get-url", "origin"], cwd=repo_root)
    except subprocess.CalledProcessError:
        remote = ""

    citation_id = _citation_id_for_repo(slug)
    relative = _relative_prefix(paths, repo_root)
    now = iso_now()

    payload = _load_bindings(paths)
    payload["code_bindings"][relative] = {
        "type": "code-repo",
        "name": slug,
        "remote": remote,
        "ref": effective_ref,
        "commit_sha": commit_sha,
        "cloned_at": now,
        "clone_depth": None,
        "submodules": False,
        "citation_id": citation_id,
    }
    _save_bindings(paths, payload)

    return {
        "status": "code_seed_bound",
        "name": slug,
        "path": relative,
        "remote": remote,
        "ref": effective_ref,
        "commit_sha": commit_sha,
        "citation_id": citation_id,
    }
