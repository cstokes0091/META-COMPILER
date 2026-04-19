"""Phase G regression tests: v2 wiki edits survive Stage 1B re-runs.

Covers the wiki_edit_manifest module directly and the integration with
`run_research_depth` (the `_sync_v1_to_v2` path).
"""
from pathlib import Path

import pytest

from meta_compiler import wiki_edit_manifest
from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.depth_stage import _sync_v1_to_v2, run_research_depth
from meta_compiler.utils import sha256_file


def _bootstrap_workspace(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    return workspace_root, artifacts_root, paths


def _seed_v1_page(paths, name: str, body: str = "v1 content") -> Path:
    page = paths.wiki_v1_pages_dir / name
    page.write_text(
        "---\n"
        "id: " + name.replace(".md", "") + "\n"
        "type: concept\n"
        "created: 2026-01-01T00:00:00Z\n"
        "sources: []\n"
        "related: []\n"
        "status: raw\n"
        "---\n"
        f"# {name}\n\n## Definition\n{body}\n\n"
        "## Formalism\n-\n\n## Key Claims\n-\n\n"
        "## Relationships\n- prerequisite_for: []\n- depends_on: []\n"
        "- contradicts: []\n- extends: []\n\n"
        "## Open Questions\n-\n\n## Source Notes\n-\n",
        encoding="utf-8",
    )
    return page


# ---------------------------------------------------------------------------
# Direct module tests
# ---------------------------------------------------------------------------


def test_manifest_default_when_missing(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)

    manifest = wiki_edit_manifest.load(paths)

    assert manifest["wiki_v2_edit_manifest"]["pages"] == {}
    assert manifest["wiki_v2_edit_manifest"]["version"] == 1


def test_record_write_then_is_user_edited_false(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)
    page = paths.wiki_v2_pages_dir / "concept-x.md"
    page.write_text("hello", encoding="utf-8")

    wiki_edit_manifest.record_write(paths, page, "depth_baseline")

    assert not wiki_edit_manifest.is_user_edited(paths, page)
    entry = wiki_edit_manifest.entry_for(paths, page)
    assert entry is not None
    assert entry["source"] == "depth_baseline"
    assert entry["last_system_write_sha"] == sha256_file(page)


def test_is_user_edited_true_after_modification(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)
    page = paths.wiki_v2_pages_dir / "concept-x.md"
    page.write_text("hello", encoding="utf-8")
    wiki_edit_manifest.record_write(paths, page, "depth_baseline")

    page.write_text("hello + user edit", encoding="utf-8")

    assert wiki_edit_manifest.is_user_edited(paths, page)


def test_record_write_rejects_invalid_source(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)
    page = paths.wiki_v2_pages_dir / "x.md"
    page.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError):
        wiki_edit_manifest.record_write(paths, page, "not_a_real_source")


def test_prune_missing_drops_dead_entries(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)
    page = paths.wiki_v2_pages_dir / "alive.md"
    page.write_text("alive", encoding="utf-8")
    wiki_edit_manifest.record_write(paths, page, "depth_baseline")

    ghost = paths.wiki_v2_pages_dir / "ghost.md"
    ghost.write_text("ghost", encoding="utf-8")
    wiki_edit_manifest.record_write(paths, ghost, "depth_baseline")
    ghost.unlink()

    removed = wiki_edit_manifest.prune_missing(paths)

    assert removed == 1
    assert wiki_edit_manifest.entry_for(paths, page) is not None
    assert wiki_edit_manifest.entry_for(paths, ghost) is None


# ---------------------------------------------------------------------------
# _sync_v1_to_v2 unit tests
# ---------------------------------------------------------------------------


def test_sync_first_run_copies_all_and_records(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)
    _seed_v1_page(paths, "concept-a.md")
    _seed_v1_page(paths, "concept-b.md")

    result = _sync_v1_to_v2(paths)

    assert result == {
        "copied": 2,
        "overwritten": 0,
        "preserved": 0,
        "preserved_pages": [],
        "removed": 0,
        "force_regenerate": False,
    }
    assert (paths.wiki_v2_pages_dir / "concept-a.md").exists()
    assert (paths.wiki_v2_pages_dir / "concept-b.md").exists()
    assert wiki_edit_manifest.entry_for(paths, paths.wiki_v2_pages_dir / "concept-a.md")


def test_sync_overwrites_unedited_v2_pages(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)
    _seed_v1_page(paths, "concept-a.md")
    _sync_v1_to_v2(paths)  # first run: copy + record

    # Modify v1 (simulating an upstream change), then re-sync.
    _seed_v1_page(paths, "concept-a.md", body="v1 second pass")
    result = _sync_v1_to_v2(paths)

    assert result["copied"] == 0
    assert result["overwritten"] == 1
    assert result["preserved"] == 0
    assert "v1 second pass" in (paths.wiki_v2_pages_dir / "concept-a.md").read_text()


def test_sync_preserves_user_edited_v2_pages(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)
    _seed_v1_page(paths, "concept-a.md")
    _sync_v1_to_v2(paths)

    target = paths.wiki_v2_pages_dir / "concept-a.md"
    target.write_text(target.read_text() + "\n## My human edit\n", encoding="utf-8")

    _seed_v1_page(paths, "concept-a.md", body="v1 third pass")
    result = _sync_v1_to_v2(paths)

    assert result["preserved"] == 1
    assert result["overwritten"] == 0
    assert "## My human edit" in target.read_text()
    assert "v1 third pass" not in target.read_text()


def test_sync_force_regenerate_wipes_edits(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)
    _seed_v1_page(paths, "concept-a.md")
    _sync_v1_to_v2(paths)

    target = paths.wiki_v2_pages_dir / "concept-a.md"
    target.write_text(target.read_text() + "\n## My human edit\n", encoding="utf-8")

    _seed_v1_page(paths, "concept-a.md", body="v1 forced pass")
    result = _sync_v1_to_v2(paths, force_regenerate=True)

    assert result["preserved"] == 0
    assert result["copied"] == 1
    assert result["force_regenerate"] is True
    assert "## My human edit" not in target.read_text()
    assert "v1 forced pass" in target.read_text()


def test_sync_does_not_delete_orphan_v2_pages(tmp_path: Path):
    _, _, paths = _bootstrap_workspace(tmp_path)
    _seed_v1_page(paths, "concept-a.md")

    # Pre-existing v2-only page (e.g. from enrichment that proposed a new concept)
    orphan = paths.wiki_v2_pages_dir / "concept-enrichment-only.md"
    orphan.write_text("# Synthesized concept", encoding="utf-8")
    wiki_edit_manifest.record_write(paths, orphan, "enrichment")

    _sync_v1_to_v2(paths)

    assert orphan.exists()


# ---------------------------------------------------------------------------
# Integration: run_research_depth honors the flag
# ---------------------------------------------------------------------------


def _bootstrap_for_depth(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap_workspace(tmp_path)
    (workspace_root / "PROBLEM_STATEMENT.md").write_text(
        "# Problem\n\nGoal: build something useful.\n", encoding="utf-8"
    )
    dump_yaml(
        paths.manifest_path,
        {
            "workspace_manifest": {
                "name": "Test",
                "created": "2026-01-01T00:00:00Z",
                "last_modified": "2026-01-01T00:00:00Z",
                "problem_domain": "test",
                "project_type": "algorithm",
                "seeds": {"version": "", "last_updated": "", "document_count": 0},
                "wiki": {"version": "", "last_updated": "", "page_count": 0, "name": ""},
                "decision_logs": [],
                "executions": [],
                "pitches": [],
                "status": "researched",
                "research": {"iteration_count": 0, "last_completed_stage": "1A"},
            }
        },
    )
    _seed_v1_page(paths, "concept-a.md")
    return workspace_root, artifacts_root, paths


def test_run_research_depth_default_preserves_edits(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap_for_depth(tmp_path)

    run_research_depth(artifacts_root=artifacts_root, workspace_root=workspace_root)
    target = paths.wiki_v2_pages_dir / "concept-a.md"
    target.write_text(target.read_text() + "\n## Operator edit\n", encoding="utf-8")

    result = run_research_depth(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["v2_sync"]["preserved"] == 1
    assert "## Operator edit" in target.read_text()
    report = load_yaml(paths.runtime_dir / "depth" / "preserved_pages.yaml")
    assert report["preserved_pages_report"]["preserved_count"] == 1
    assert report["preserved_pages_report"]["pages"][0]["page"] == "concept-a.md"


def test_run_research_depth_force_regenerate_wipes_edits(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap_for_depth(tmp_path)

    run_research_depth(artifacts_root=artifacts_root, workspace_root=workspace_root)
    target = paths.wiki_v2_pages_dir / "concept-a.md"
    target.write_text(target.read_text() + "\n## Operator edit\n", encoding="utf-8")

    result = run_research_depth(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        force_regenerate_v2=True,
    )

    assert result["v2_sync"]["preserved"] == 0
    assert result["v2_sync"]["force_regenerate"] is True
    assert "## Operator edit" not in target.read_text()


def test_run_research_depth_preserves_edited_gap_remediation(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap_for_depth(tmp_path)

    run_research_depth(artifacts_root=artifacts_root, workspace_root=workspace_root)
    remediation = paths.wiki_v2_pages_dir / "gap-remediation-v2.md"
    edited = remediation.read_text() + "\n## Manual notes\n"
    remediation.write_text(edited, encoding="utf-8")

    run_research_depth(artifacts_root=artifacts_root, workspace_root=workspace_root)

    assert "## Manual notes" in remediation.read_text()
