"""Phase C1b tests: propose-relationships request + apply-relationships merge."""
from __future__ import annotations

from pathlib import Path

import pytest

from meta_compiler import wiki_edit_manifest
from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml, parse_frontmatter
from meta_compiler.stages.relationship_stage import (
    VALID_RELATIONSHIP_TYPES,
    run_apply_relationships,
    run_propose_relationships,
)


def _bootstrap(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    return workspace_root, artifacts_root, paths


def _v2_page(paths, name: str, sources: list[str], page_type: str = "concept") -> Path:
    page = paths.wiki_v2_pages_dir / name
    page.parent.mkdir(parents=True, exist_ok=True)
    sources_block = (
        "sources: []"
        if not sources
        else "sources:\n" + "\n".join([f"  - {s}" for s in sources])
    )
    page.write_text(
        "---\n"
        f"id: {name.replace('.md', '')}\n"
        f"type: {page_type}\n"
        "created: 2026-01-01T00:00:00Z\n"
        f"{sources_block}\n"
        "related: []\n"
        "status: raw\n"
        "---\n"
        f"# {name.replace('.md', '').title()}\n\n## Definition\nstub\n\n"
        "## Relationships\n- prerequisite_for: []\n- depends_on: []\n"
        "- contradicts: []\n- extends: []\n\n## Source Notes\nNone.\n",
        encoding="utf-8",
    )
    return page


# ---------------------------------------------------------------------------
# propose-relationships
# ---------------------------------------------------------------------------


def test_propose_relationships_no_pages(tmp_path: Path):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    result = run_propose_relationships(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert result["status"] == "no_pages"


def test_propose_relationships_writes_request(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(paths, "concept-a.md", sources=["src-x"])
    _v2_page(paths, "concept-b.md", sources=["src-y"])

    result = run_propose_relationships(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ready_for_agent"
    assert result["concept_pages"] == 2
    request = load_yaml(paths.runtime_dir / "wiki_relationships" / "request.yaml")
    body = request["relationship_mapper_request"]
    assert sorted(body["valid_relationship_types"]) == sorted(VALID_RELATIONSHIP_TYPES)
    assert {entry["id"] for entry in body["concept_pages"]} == {"concept-a", "concept-b"}


# ---------------------------------------------------------------------------
# apply-relationships
# ---------------------------------------------------------------------------


def _write_proposals(paths, proposals: list[dict]) -> None:
    dump_yaml(
        paths.reports_dir / "relationship_proposals.yaml",
        {
            "relationship_proposals": {
                "generated_at": "2026-01-01T00:00:00Z",
                "proposed_by": "relationship-mapper",
                "proposals": proposals,
            }
        },
    )


def test_apply_relationships_no_proposals_file(tmp_path: Path):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    result = run_apply_relationships(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert result["status"] == "no_proposals"
    assert result["applied"] == 0


def test_apply_relationships_rejects_v1(tmp_path: Path):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    with pytest.raises(ValueError):
        run_apply_relationships(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            version=1,
        )


def test_apply_relationships_merges_valid_proposal(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    a = _v2_page(paths, "concept-a.md", sources=["src-x"])
    b = _v2_page(paths, "concept-b.md", sources=["src-y"])
    _write_proposals(
        paths,
        [
            {
                "subject": "concept-a",
                "target": "concept-b",
                "relationship_type": "extends",
                "rationale": "test",
                "evidence": [
                    {"citation_id": "src-x", "locator": {"page": 1}, "quote": "q1"},
                    {"citation_id": "src-y", "locator": {"page": 2}, "quote": "q2"},
                ],
            }
        ],
    )

    result = run_apply_relationships(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ok"
    assert result["applied"] == 1
    assert result["pages_changed"] == 1
    assert result["rejected"] == 0

    a_text = a.read_text()
    fm, body = parse_frontmatter(a_text)
    assert "concept-b" in fm["related"]
    assert "extends:" in body
    assert "concept-b" in body


def test_apply_relationships_rejects_single_source(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(paths, "concept-a.md", sources=["src-x"])
    _v2_page(paths, "concept-b.md", sources=["src-x"])
    _write_proposals(
        paths,
        [
            {
                "subject": "concept-a",
                "target": "concept-b",
                "relationship_type": "extends",
                "rationale": "test",
                "evidence": [
                    {"citation_id": "src-x", "locator": {"page": 1}, "quote": "q1"},
                    {"citation_id": "src-x", "locator": {"page": 2}, "quote": "q2"},
                ],
            }
        ],
    )

    result = run_apply_relationships(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["applied"] == 0
    assert result["rejected"] == 1


def test_apply_relationships_rejects_unknown_pages(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(paths, "concept-a.md", sources=["src-x"])
    _write_proposals(
        paths,
        [
            {
                "subject": "concept-a",
                "target": "concept-ghost",
                "relationship_type": "extends",
                "rationale": "test",
                "evidence": [
                    {"citation_id": "src-x", "locator": {"page": 1}, "quote": "q"},
                    {"citation_id": "src-y", "locator": {"page": 2}, "quote": "q"},
                ],
            }
        ],
    )

    result = run_apply_relationships(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["applied"] == 0
    assert result["rejected"] == 1


def test_apply_relationships_rejects_invalid_type(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(paths, "concept-a.md", sources=["src-x"])
    _v2_page(paths, "concept-b.md", sources=["src-y"])
    _write_proposals(
        paths,
        [
            {
                "subject": "concept-a",
                "target": "concept-b",
                "relationship_type": "is_friends_with",
                "rationale": "test",
                "evidence": [
                    {"citation_id": "src-x", "locator": {"page": 1}, "quote": "q1"},
                    {"citation_id": "src-y", "locator": {"page": 2}, "quote": "q2"},
                ],
            }
        ],
    )

    result = run_apply_relationships(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["applied"] == 0
    assert result["rejected"] == 1


def test_apply_relationships_records_provenance_and_manifest(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    a = _v2_page(paths, "concept-a.md", sources=["src-x"])
    _v2_page(paths, "concept-b.md", sources=["src-y"])
    _write_proposals(
        paths,
        [
            {
                "subject": "concept-a",
                "target": "concept-b",
                "relationship_type": "depends_on",
                "rationale": "test",
                "evidence": [
                    {"citation_id": "src-x", "locator": {"page": 1}, "quote": "q1"},
                    {"citation_id": "src-y", "locator": {"page": 2}, "quote": "q2"},
                ],
            }
        ],
    )

    run_apply_relationships(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    entry = wiki_edit_manifest.entry_for(paths, a)
    assert entry is not None
    assert entry["source"] == "relationship_mapper"

    provenance = load_yaml(paths.reports_dir / "relationship_provenance.yaml")
    log_entries = provenance["relationship_provenance"]["entries"]
    assert log_entries[-1]["page"] == "concept-a.md"
    added_types = {row["relationship_type"] for row in log_entries[-1]["added"]}
    assert "depends_on" in added_types


def test_apply_relationships_writes_report(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _v2_page(paths, "concept-a.md", sources=["src-x"])
    _v2_page(paths, "concept-b.md", sources=["src-y"])
    _write_proposals(
        paths,
        [
            {
                "subject": "concept-a",
                "target": "concept-b",
                "relationship_type": "extends",
                "rationale": "test",
                "evidence": [
                    {"citation_id": "src-x", "locator": {"page": 1}, "quote": "q1"},
                    {"citation_id": "src-y", "locator": {"page": 2}, "quote": "q2"},
                ],
            }
        ],
    )

    result = run_apply_relationships(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["report_path"].endswith("apply_relationships_report.yaml")
    report = load_yaml(paths.reports_dir / "apply_relationships_report.yaml")
    body = report["apply_relationships_report"]
    assert body["applied"] == 1
    assert body["pages_changed"] == 1
    assert body["rejected"] == 0
