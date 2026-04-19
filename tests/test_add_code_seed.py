"""Tests for add-code-seed / bind-code-seed CLI verbs.

`git clone` and `git rev-parse` are faked via monkeypatch so the tests run
offline. The side effect we care about is the code_bindings entry in
source_bindings.yaml plus the slugged seeds/code/<name>/ directory.
"""
from pathlib import Path

import pytest

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import load_yaml
from meta_compiler.stages import code_seed_stage


def _fake_run_factory(repo_dir_holder: dict[str, Path]):
    """Return a _run_git replacement that simulates clone/fetch/checkout/rev-parse."""

    def fake_run_git(args, cwd=None):
        if args[0] == "clone":
            # args = ["clone", ..., remote, target]
            target = Path(args[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            (target / "src").mkdir()
            (target / "src" / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
            repo_dir_holder["target"] = target
            return ""
        if args[0] == "fetch":
            return ""
        if args[0] == "checkout":
            return ""
        if args[:2] == ["rev-parse", "HEAD"]:
            return "a" * 40
        if args[:2] == ["remote", "get-url"]:
            return "https://example.invalid/widget.git"
        if args[0] == "submodule":
            return ""
        raise AssertionError(f"unexpected git args: {args}")

    return fake_run_git


def test_add_code_seed_writes_code_bindings(tmp_path: Path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    holder: dict[str, Path] = {}
    monkeypatch.setattr(code_seed_stage, "_run_git", _fake_run_factory(holder))

    result = code_seed_stage.run_add_code_seed(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        repo="https://example.invalid/widget.git",
        ref="v1.0.0",
        name="Widget Lib",
    )

    assert result["status"] == "code_seed_added"
    assert result["name"] == "widget-lib"
    assert result["citation_id"] == "src-repo-widget-lib"
    assert result["commit_sha"] == "a" * 40
    assert holder["target"] == paths.seeds_code_dir / "widget-lib"

    payload = load_yaml(paths.source_bindings_path)
    code_bindings = payload["code_bindings"]
    assert "seeds/code/widget-lib/" in code_bindings
    entry = code_bindings["seeds/code/widget-lib/"]
    assert entry["type"] == "code-repo"
    assert entry["ref"] == "v1.0.0"
    assert entry["commit_sha"] == "a" * 40
    assert entry["citation_id"] == "src-repo-widget-lib"


def test_add_code_seed_rejects_existing_non_empty_target(tmp_path: Path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    target = paths.seeds_code_dir / "widget"
    target.mkdir(parents=True)
    (target / "sentinel").write_text("hi", encoding="utf-8")

    monkeypatch.setattr(code_seed_stage, "_run_git", lambda *a, **kw: "")

    with pytest.raises(RuntimeError, match="already exists and is non-empty"):
        code_seed_stage.run_add_code_seed(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            repo="https://example.invalid/widget.git",
            ref="main",
            name="widget",
        )


def test_bind_code_seed_records_head(tmp_path: Path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    repo_root = paths.seeds_code_dir / "widget"
    (repo_root / ".git").mkdir(parents=True)
    (repo_root / "src").mkdir()
    (repo_root / "src" / "main.py").write_text("pass\n", encoding="utf-8")

    def fake_run_git(args, cwd=None):
        if args[:2] == ["rev-parse", "HEAD"]:
            return "d" * 40
        if args[:2] == ["remote", "get-url"]:
            return "https://example.invalid/widget.git"
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(code_seed_stage, "_run_git", fake_run_git)

    result = code_seed_stage.run_bind_code_seed(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        path="seeds/code/widget",
    )

    assert result["status"] == "code_seed_bound"
    assert result["commit_sha"] == "d" * 40
    assert result["ref"] == "d" * 40  # defaulted to HEAD

    payload = load_yaml(paths.source_bindings_path)
    entry = payload["code_bindings"]["seeds/code/widget/"]
    assert entry["citation_id"] == "src-repo-widget"
    assert entry["remote"] == "https://example.invalid/widget.git"


def test_bind_code_seed_rejects_non_seeds_code_path(tmp_path: Path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    outsider = paths.seeds_dir / "not-code"
    (outsider / ".git").mkdir(parents=True)

    monkeypatch.setattr(code_seed_stage, "_run_git", lambda *a, **kw: "d" * 40)

    with pytest.raises(RuntimeError, match="must live under seeds/code/"):
        code_seed_stage.run_bind_code_seed(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            path="seeds/not-code",
        )
