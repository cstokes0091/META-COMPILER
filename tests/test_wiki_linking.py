"""Phase C1b tests: wiki_linking inserts inline links between v2 pages."""
from __future__ import annotations

from pathlib import Path

import pytest

from meta_compiler import wiki_edit_manifest
from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import load_yaml, parse_frontmatter
from meta_compiler.wiki_linking import run_wiki_link


def _bootstrap(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    return workspace_root, artifacts_root, paths


def _v2_page(paths, name: str, body: str, *, page_type: str = "concept", related=None) -> Path:
    page = paths.wiki_v2_pages_dir / name
    page.parent.mkdir(parents=True, exist_ok=True)
    related_yaml = "[]" if not related else "\n  - " + "\n  - ".join(related)
    page.write_text(
        "---\n"
        f"id: {name.replace('.md', '')}\n"
        f"type: {page_type}\n"
        "created: 2026-01-01T00:00:00Z\n"
        "sources: []\n"
        f"related: {related_yaml if related else '[]'}\n"
        "status: raw\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return page


def test_run_wiki_link_no_pages_returns_no_pages_status(tmp_path: Path):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    result = run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)
    assert result["status"] == "no_pages"
    assert result["pages_changed"] == 0


def test_run_wiki_link_rejects_v1(tmp_path: Path):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    with pytest.raises(ValueError):
        run_wiki_link(
            artifacts_root=artifacts_root, workspace_root=workspace_root, version=1
        )


def test_run_wiki_link_inserts_link_on_first_mention_per_section(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "concept-alpha.md",
        "# Concept Alpha\n\n## Definition\nThe Beta Concept is central. The Beta Concept matters.\n",
    )
    _v2_page(paths, "beta-concept.md", "# Beta Concept\n\n## Definition\nx\n")

    result = run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    alpha = (paths.wiki_v2_pages_dir / "concept-alpha.md").read_text()
    assert "[Beta Concept](beta-concept.md)" in alpha
    # Only first mention is linked.
    assert alpha.count("[Beta Concept](beta-concept.md)") == 1
    assert result["links_inserted"] == 1
    assert result["pages_changed"] == 1


def test_run_wiki_link_handles_plural_and_possessive(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "concept-host.md",
        "# Host\n\n## Definition\nThe widgets are everywhere.\n",
    )
    _v2_page(paths, "widget.md", "# Widget\n\n## Definition\nx\n")

    run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    host = (paths.wiki_v2_pages_dir / "concept-host.md").read_text()
    assert "[widgets](widget.md)" in host


def test_run_wiki_link_skips_self_links(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "alpha.md",
        "# Alpha\n\n## Definition\nAlpha is fundamental to alpha behavior.\n",
    )

    run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    alpha = (paths.wiki_v2_pages_dir / "alpha.md").read_text()
    assert "[Alpha](alpha.md)" not in alpha
    assert "[alpha](alpha.md)" not in alpha


def test_run_wiki_link_skips_existing_links(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "host.md",
        "# Host\n\n## Definition\nSee [Widget](widget.md) for details. The Widget is here.\n",
    )
    _v2_page(paths, "widget.md", "# Widget\n\n## Definition\nx\n")

    run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    host = (paths.wiki_v2_pages_dir / "host.md").read_text()
    # Existing link preserved; no second link inserted in same section
    # because the first mention is already linked.
    assert host.count("[Widget](widget.md)") == 1


def test_run_wiki_link_one_link_per_section(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "host.md",
        "# Host\n\n## Definition\nWidget here.\n\n## Key Claims\nWidget there.\n",
    )
    _v2_page(paths, "widget.md", "# Widget\n\n## Definition\nx\n")

    run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    host = (paths.wiki_v2_pages_dir / "host.md").read_text()
    # One per section = two total.
    assert host.count("[Widget](widget.md)") == 2


def test_run_wiki_link_skips_code_blocks(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "host.md",
        "# Host\n\n## Definition\n```\nThe Widget code here\n```\nThe Widget elsewhere.\n",
    )
    _v2_page(paths, "widget.md", "# Widget\n\n## Definition\nx\n")

    run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    host = (paths.wiki_v2_pages_dir / "host.md").read_text()
    # Code-block mention untouched; prose mention linked.
    assert "Widget code here" in host
    assert "[Widget](widget.md)" in host


def test_run_wiki_link_updates_related_frontmatter(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "host.md",
        "# Host\n\n## Definition\nThe Widget is central.\n",
    )
    _v2_page(paths, "widget.md", "# Widget\n\n## Definition\nx\n")

    run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    host_text = (paths.wiki_v2_pages_dir / "host.md").read_text()
    frontmatter, _ = parse_frontmatter(host_text)
    assert "widget" in frontmatter["related"]


def test_run_wiki_link_is_idempotent(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "host.md",
        "# Host\n\n## Definition\nThe Widget is central.\n",
    )
    _v2_page(paths, "widget.md", "# Widget\n\n## Definition\nx\n")

    first = run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)
    second = run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    assert first["pages_changed"] == 1
    assert second["pages_changed"] == 0
    assert second["links_inserted"] == 0


def test_run_wiki_link_records_writes_in_edit_manifest(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "host.md",
        "# Host\n\n## Definition\nThe Widget is central.\n",
    )
    _v2_page(paths, "widget.md", "# Widget\n\n## Definition\nx\n")

    run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    host_path = paths.wiki_v2_pages_dir / "host.md"
    entry = wiki_edit_manifest.entry_for(paths, host_path)
    assert entry is not None
    assert entry["source"] == "wiki_linker"


def test_run_wiki_link_writes_report(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(
        paths,
        "host.md",
        "# Host\n\n## Definition\nThe Widget is central.\n",
    )
    _v2_page(paths, "widget.md", "# Widget\n\n## Definition\nx\n")

    result = run_wiki_link(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["report_path"].endswith("wiki_linking_report.yaml")
    report = load_yaml(paths.reports_dir / "wiki_linking_report.yaml")
    body = report["wiki_linking_report"]
    assert body["pages_changed"] == 1
    assert body["links_inserted"] == 1
    per_page = {entry["page"]: entry for entry in body["per_page"]}
    assert per_page["host.md"]["changed"] is True
    assert per_page["widget.md"]["changed"] is False
