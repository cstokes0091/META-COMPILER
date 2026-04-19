"""Tests for code ingestion: seed kind detection, work plan shape, and validation."""
import json
from pathlib import Path

from meta_compiler.artifacts import build_paths, ensure_layout, list_code_repos, list_seed_files
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.ingest_stage import (
    _seed_kind_for_path,
    run_ingest,
    validate_all_findings,
    validate_findings_file,
)


def _write_code_binding(
    paths,
    relative: str,
    *,
    name: str,
    commit_sha: str = "a" * 40,
    citation_id: str | None = None,
    remote: str = "https://example.invalid/widget.git",
    ref: str = "main",
) -> None:
    payload = load_yaml(paths.source_bindings_path) or {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("bindings", {})
    payload.setdefault("code_bindings", {})
    payload["code_bindings"][relative] = {
        "type": "code-repo",
        "name": name,
        "remote": remote,
        "ref": ref,
        "commit_sha": commit_sha,
        "cloned_at": "2026-04-19T00:00:00+00:00",
        "clone_depth": None,
        "submodules": False,
        "citation_id": citation_id or f"src-repo-{name}",
    }
    dump_yaml(paths.source_bindings_path, payload)


def test_run_ingest_detects_code_seeds_and_emits_repo_map_items(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    repo_root = paths.seeds_code_dir / "widget"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (repo_root / "src" / "util.py").write_text("def util():\n    return 1\n", encoding="utf-8")
    _write_code_binding(paths, "seeds/code/widget/", name="widget")

    result = run_ingest(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        scope="all",
    )

    assert result["status"] == "ready_for_orchestrator"
    assert result["code_items"] == 2
    assert result["doc_items"] == 0
    assert result["repo_map_items"] == 1

    work_plan = load_yaml(paths.ingest_runtime_dir / "work_plan.yaml")
    body = work_plan["work_plan"]
    assert body["counts"]["code_items"] == 2
    assert body["counts"]["repo_map_items"] == 1

    repo_map_items = body["repo_map_items"]
    assert len(repo_map_items) == 1
    assert repo_map_items[0]["repo_name"] == "widget"
    assert repo_map_items[0]["repo_citation_id"] == "src-repo-widget"
    assert repo_map_items[0]["map_output_path"].endswith("runtime/ingest/repo_map/widget.yaml")

    for item in body["work_items"]:
        assert item["seed_kind"] == "code"
        assert item["repo_name"] == "widget"
        assert item["repo_citation_id"] == "src-repo-widget"
        assert item["repo_relative_path"].startswith("src/")
        assert item["citation_id"].startswith("src-widget-")
        assert item["extracted_path"] is None


def test_run_ingest_excludes_git_and_build_dirs(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    repo_root = paths.seeds_code_dir / "widget"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "main.py").write_text("pass\n", encoding="utf-8")
    (repo_root / "node_modules").mkdir()
    (repo_root / "node_modules" / "lib.js").write_text("// vendored\n", encoding="utf-8")
    (repo_root / "__pycache__").mkdir()
    (repo_root / "__pycache__" / "main.cpython-313.pyc").write_text("bc\n", encoding="utf-8")
    (repo_root / ".venv").mkdir()
    (repo_root / ".venv" / "leftover.py").write_text("pass\n", encoding="utf-8")
    _write_code_binding(paths, "seeds/code/widget/", name="widget")

    # Git-tracked lookup should fall back to glob when .git is absent.
    seeds = list_seed_files(paths)
    relative = sorted(path.relative_to(paths.root).as_posix() for path in seeds)
    assert relative == ["seeds/code/widget/src/main.py"]


def test_seed_kind_routes_manifest_files_as_code(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    repo_root = paths.seeds_code_dir / "widget"
    repo_root.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='widget'\n", encoding="utf-8")
    (repo_root / "main.py").write_text("pass\n", encoding="utf-8")
    (repo_root / "README.md").write_text("# widget\n", encoding="utf-8")
    _write_code_binding(paths, "seeds/code/widget/", name="widget")

    code_repos = list_code_repos(paths)
    assert len(code_repos) == 1

    kind_toml, _ = _seed_kind_for_path(repo_root / "pyproject.toml", paths, code_repos)
    kind_py, _ = _seed_kind_for_path(repo_root / "main.py", paths, code_repos)
    # README inside a code repo is still a doc — the existing seed-reader handles it.
    kind_md, _ = _seed_kind_for_path(repo_root / "README.md", paths, code_repos)

    assert kind_toml == "code"
    assert kind_py == "code"
    assert kind_md == "doc"


def test_validate_code_findings_accepts_valid_payload(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    findings_path = paths.findings_dir / "src-widget-src-main-py.json"
    payload = {
        "source_type": "code",
        "citation_id": "src-widget-src-main-py",
        "seed_path": "workspace-artifacts/seeds/code/widget/src/main.py",
        "file_hash": "sha256:" + "0" * 56,
        "extracted_at": "2026-04-19T12:00:00+00:00",
        "extractor": {"agent_type": "code-reader", "model": "m", "pass_type": "full-read"},
        "file_metadata": {
            "language": "python",
            "loc": 2,
            "module_path": "widget.main",
            "repo_citation_id": "src-repo-widget",
        },
        "concepts": [],
        "symbols": [
            {
                "kind": "function",
                "name": "main",
                "signature": "def main()",
                "locator": {"file": "src/main.py", "line_start": 1, "line_end": 2},
                "visibility": "public",
            }
        ],
        "claims": [
            {
                "statement": "main returns 0",
                "locator": {"file": "src/main.py", "line_start": 2},
            }
        ],
        "quotes": [
            {"text": "return 0", "locator": {"file": "src/main.py", "line_start": 2}}
        ],
        "dependencies": [],
        "call_edges": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {"completeness": "full"},
    }
    findings_path.write_text(json.dumps(payload), encoding="utf-8")

    assert validate_findings_file(findings_path) == []


def test_validate_code_findings_rejects_missing_line_locator(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    findings_path = paths.findings_dir / "src-widget-src-main-py.json"
    payload = {
        "source_type": "code",
        "citation_id": "src-widget-src-main-py",
        "seed_path": "seeds/code/widget/src/main.py",
        "file_hash": "sha256:zero",
        "extracted_at": "2026-04-19T12:00:00+00:00",
        "extractor": {"agent_type": "code-reader", "model": "m", "pass_type": "full-read"},
        "file_metadata": {
            "language": "python",
            "loc": 2,
            "module_path": "widget.main",
            "repo_citation_id": "src-repo-widget",
        },
        "concepts": [],
        "symbols": [
            {
                "kind": "function",
                "name": "main",
                # locator missing line_start
                "locator": {"file": "src/main.py"},
            }
        ],
        "claims": [],
        "quotes": [],
        "dependencies": [],
        "call_edges": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {"completeness": "full"},
    }
    findings_path.write_text(json.dumps(payload), encoding="utf-8")

    issues = validate_findings_file(findings_path)
    assert any("line_start" in msg for msg in issues)


def test_validate_code_findings_detects_stale_hash(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    seed = paths.seeds_code_dir / "widget" / "src" / "main.py"
    seed.parent.mkdir(parents=True)
    seed.write_text("print('v1')\n", encoding="utf-8")

    findings_path = paths.findings_dir / "src-widget-src-main-py.json"
    payload = {
        "source_type": "code",
        "citation_id": "src-widget-src-main-py",
        "seed_path": seed.relative_to(artifacts_root).as_posix(),
        "file_hash": "sha256:" + "b" * 56,  # pretend stale
        "extracted_at": "2026-04-19T12:00:00+00:00",
        "extractor": {"agent_type": "code-reader", "model": "m", "pass_type": "full-read"},
        "file_metadata": {
            "language": "python",
            "loc": 1,
            "module_path": "widget.main",
            "repo_citation_id": "src-repo-widget",
        },
        "concepts": [],
        "symbols": [
            {
                "kind": "function",
                "name": "main",
                "locator": {"file": "src/main.py", "line_start": 1, "line_end": 1},
            }
        ],
        "claims": [],
        "quotes": [],
        "dependencies": [],
        "call_edges": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {"completeness": "full"},
    }
    findings_path.write_text(json.dumps(payload), encoding="utf-8")

    issues = validate_findings_file(findings_path)
    assert any("file_hash stale" in msg for msg in issues)


def test_validate_all_findings_mixes_doc_and_code(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    # Doc finding (will naturally be missing required fields)
    (paths.findings_dir / "src-doc.json").write_text("{}", encoding="utf-8")

    # Code finding
    code_path = paths.findings_dir / "src-widget-src-main-py.json"
    code_payload = {
        "source_type": "code",
        "citation_id": "src-widget-src-main-py",
        "seed_path": "seeds/code/widget/src/main.py",
        "file_hash": "sha256:abc",
        "extracted_at": "2026-04-19T12:00:00+00:00",
        "extractor": {"agent_type": "code-reader", "model": "m", "pass_type": "full-read"},
        "file_metadata": {
            "language": "python",
            "loc": 1,
            "module_path": "widget.main",
            "repo_citation_id": "src-repo-widget",
        },
        "concepts": [],
        "symbols": [
            {
                "kind": "function",
                "name": "main",
                "locator": {"file": "src/main.py", "line_start": 1, "line_end": 1},
            }
        ],
        "claims": [],
        "quotes": [],
        "dependencies": [],
        "call_edges": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {"completeness": "full"},
    }
    code_path.write_text(json.dumps(code_payload), encoding="utf-8")

    result = validate_all_findings(artifacts_root=artifacts_root)
    per_file = {row["path"]: row for row in result["per_file"]}
    assert per_file["wiki/findings/src-doc.json"]["issue_count"] > 0
    assert per_file["wiki/findings/src-widget-src-main-py.json"]["issue_count"] == 0
