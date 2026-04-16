"""sync-agents command: Mirror scaffolded agents into the meta-compiler repo.

After Stage 3 scaffolds a downstream project under
`workspace-artifacts/scaffolds/v<N>/.github/`, this stage copies those
customizations into `<repo_root>/.github/` so the user can invoke them from
the META-COMPILER VS Code workspace without opening a second IDE.

Mirrored files are namespaced with a `scaffold-v<N>-` prefix so they never
overwrite META-COMPILER's native custom agents (ingest-orchestrator,
stage2-orchestrator, etc.). The mirror is idempotent: prior copies for the
same scaffold version are removed before re-copying.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..artifacts import build_paths, latest_scaffold_path


MIRROR_SUBDIRS = ("agents", "skills", "instructions")


def _clear_prior_mirror(repo_github: Path, version: int) -> int:
    removed = 0
    prefix = f"scaffold-v{version}-"

    agents_dir = repo_github / "agents"
    if agents_dir.exists():
        for path in agents_dir.glob(f"{prefix}*.agent.md"):
            path.unlink()
            removed += 1

    skills_dir = repo_github / "skills"
    if skills_dir.exists():
        for child in skills_dir.iterdir():
            if child.is_dir() and child.name.startswith(prefix):
                shutil.rmtree(child)
                removed += 1

    instructions_dir = repo_github / "instructions"
    if instructions_dir.exists():
        for path in instructions_dir.glob(f"{prefix}*.instructions.md"):
            path.unlink()
            removed += 1

    return removed


def _mirror_agents(scaffold_github: Path, repo_github: Path, version: int) -> list[str]:
    copied: list[str] = []
    src_dir = scaffold_github / "agents"
    if not src_dir.exists():
        return copied
    dst_dir = repo_github / "agents"
    dst_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"scaffold-v{version}-"

    for src in sorted(src_dir.glob("*.agent.md")):
        if src.name.startswith(prefix):
            new_name = src.name
        else:
            new_name = f"{prefix}{src.name}"
        dst = dst_dir / new_name
        shutil.copyfile(src, dst)
        copied.append(str(dst.as_posix()))
    return copied


def _mirror_skills(scaffold_github: Path, repo_github: Path, version: int) -> list[str]:
    copied: list[str] = []
    src_dir = scaffold_github / "skills"
    if not src_dir.exists():
        return copied
    dst_dir = repo_github / "skills"
    dst_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"scaffold-v{version}-"

    for src_skill in sorted(p for p in src_dir.iterdir() if p.is_dir()):
        skill_name = src_skill.name
        if skill_name.startswith(prefix):
            dst_name = skill_name
        else:
            dst_name = f"{prefix}{skill_name}"
        dst_skill = dst_dir / dst_name
        if dst_skill.exists():
            shutil.rmtree(dst_skill)
        shutil.copytree(src_skill, dst_skill)
        copied.append(str(dst_skill.as_posix()))
    return copied


def _mirror_instructions(scaffold_github: Path, repo_github: Path, version: int) -> list[str]:
    copied: list[str] = []
    src_dir = scaffold_github / "instructions"
    if not src_dir.exists():
        return copied
    dst_dir = repo_github / "instructions"
    dst_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"scaffold-v{version}-"

    for src in sorted(src_dir.glob("*.instructions.md")):
        if src.name.startswith(prefix):
            new_name = src.name
        else:
            new_name = f"{prefix}{src.name}"
        dst = dst_dir / new_name
        shutil.copyfile(src, dst)
        copied.append(str(dst.as_posix()))
    return copied


def run_sync_agents(
    artifacts_root: Path,
    workspace_root: Path,
    scaffold_version: int | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Copy scaffolded agent/skill/instruction files into the meta-compiler repo's .github/."""
    paths = build_paths(artifacts_root)

    if scaffold_version is not None:
        scaffold_root = paths.scaffolds_dir / f"v{scaffold_version}"
        if not scaffold_root.exists():
            raise RuntimeError(f"scaffold v{scaffold_version} not found at {scaffold_root}")
        version = scaffold_version
    else:
        latest = latest_scaffold_path(paths)
        if latest is None:
            raise RuntimeError("no scaffold found. Run `meta-compiler scaffold` first.")
        version, scaffold_root = latest

    scaffold_github = scaffold_root / ".github"
    if not scaffold_github.exists():
        raise RuntimeError(f"scaffold {scaffold_root} has no .github directory to mirror")

    target_repo = repo_root if repo_root is not None else workspace_root
    repo_github = target_repo / ".github"
    repo_github.mkdir(parents=True, exist_ok=True)

    removed = _clear_prior_mirror(repo_github, version)
    agents_copied = _mirror_agents(scaffold_github, repo_github, version)
    skills_copied = _mirror_skills(scaffold_github, repo_github, version)
    instructions_copied = _mirror_instructions(scaffold_github, repo_github, version)

    return {
        "status": "mirrored",
        "scaffold_version": version,
        "scaffold_root": str(scaffold_root),
        "repo_github": str(repo_github),
        "prior_mirror_entries_removed": removed,
        "agents_copied": len(agents_copied),
        "skills_copied": len(skills_copied),
        "instructions_copied": len(instructions_copied),
        "files": {
            "agents": agents_copied,
            "skills": skills_copied,
            "instructions": instructions_copied,
        },
    }
