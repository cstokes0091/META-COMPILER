"""Tests for `meta_compiler.stages.wiki_search_stage` and the Step 0 auto-fire
inside `run_elicit_vision_start`."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meta_compiler.artifacts import build_paths
from meta_compiler.io import load_yaml
from meta_compiler.stages.elicit_stage import run_elicit_vision_start
from meta_compiler.stages.wiki_search_stage import (
    render_wiki_evidence_section,
    run_wiki_search_apply,
    run_wiki_search_preflight,
    validate_wiki_search_results,
)

from tests.test_elicit_vision_start import _seed_workspace  # noqa: E402


def _write_topic_result(path: Path, topic_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "wiki_search_topic_result": {
            "topic_id": topic_id,
            "generated_at": "2026-04-21T00:00:00+00:00",
            "decision_areas": ["scope-in", "requirements"],
            "concepts": [
                {
                    "slug": "concept-core",
                    "definition_excerpt": "A test concept that anchors the topic.",
                    "citations": ["src-test"],
                }
            ],
            "equations": [
                {"label": "E1", "latex": "x = y + 1", "citations": ["src-test"]}
            ],
            "citations": ["src-test"],
            "related_pages": ["concept-core"],
            "cross_source_notes": [
                {"summary": "Both sources agree", "source_citation_ids": ["src-test"]}
            ],
        }
    }
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)


# ----- preflight --------------------------------------------------------------


def test_preflight_writes_work_plan_and_request(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    result = run_wiki_search_preflight(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert result["status"] == "ready_for_orchestrator"
    assert paths.wiki_search_work_plan_path.exists()
    assert paths.wiki_search_request_path.exists()

    plan = load_yaml(paths.wiki_search_work_plan_path)
    assert "wiki_search_work_plan" in plan
    topics = plan["wiki_search_work_plan"]["topics"]
    assert len(topics) >= 1
    for topic in topics:
        assert topic["id"].startswith("T-")
        assert "decision_areas" in topic
        assert isinstance(topic.get("seed_concepts"), list)


def test_preflight_freshness_cache_skips_when_unchanged(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    first = run_wiki_search_preflight(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    # Simulate orchestrator -> apply round trip so results.yaml exists
    _write_topic_result(paths.wiki_search_results_dir / "T-001.yaml", "T-001")
    run_wiki_search_apply(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    cached = run_wiki_search_preflight(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert cached["status"] == "cached"
    # --force bypasses the cache
    forced = run_wiki_search_preflight(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        force=True,
    )
    assert forced["status"] == "ready_for_orchestrator"
    # Cached/preflight share the same hashes for an unchanged workspace.
    assert cached["problem_statement_hash"] == first["problem_statement_hash"]


# ----- apply ------------------------------------------------------------------


def test_apply_consolidates_topic_files(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_wiki_search_preflight(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    _write_topic_result(paths.wiki_search_results_dir / "T-001.yaml", "T-001")
    _write_topic_result(paths.wiki_search_results_dir / "T-002.yaml", "T-002")

    result = run_wiki_search_apply(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert result["status"] == "applied"
    assert result["topic_count"] == 2
    payload = load_yaml(paths.wiki_search_results_path)
    assert validate_wiki_search_results(payload) == []
    assert set(payload["wiki_search_results"]["topics"].keys()) == {"T-001", "T-002"}


def test_apply_rejects_malformed_topic_file(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_wiki_search_preflight(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    bad_path = paths.wiki_search_results_dir / "T-001.yaml"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    with bad_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump({"wrong_root": {}}, handle)

    with pytest.raises(ValueError) as excinfo:
        run_wiki_search_apply(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )
    assert "schema validation" in str(excinfo.value)


def test_apply_without_topic_files_raises(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"

    run_wiki_search_preflight(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    with pytest.raises(FileNotFoundError):
        run_wiki_search_apply(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )


# ----- Step 0 auto-fire from elicit-vision --start ---------------------------


def test_elicit_start_auto_fires_wiki_search(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    result = run_elicit_vision_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert result["status"] == "ready_for_wiki_search_orchestrator"
    assert paths.wiki_search_work_plan_path.exists()
    # Brief is not yet written — Step 0 returns before brief rendering.
    assert not paths.stage2_brief_path.exists()


def test_elicit_start_resumes_after_results_apply(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    _write_topic_result(paths.wiki_search_results_dir / "T-001.yaml", "T-001")
    run_wiki_search_apply(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    result = run_elicit_vision_start(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    assert result["status"] == "ready_for_orchestrator"
    assert paths.stage2_brief_path.exists()
    brief = paths.stage2_brief_path.read_text(encoding="utf-8")
    assert "## Wiki Evidence" in brief
    assert "[wiki:concept-core]" in brief


def test_elicit_start_skip_wiki_search_writes_brief_with_placeholder(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    result = run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
    )
    assert result["status"] == "ready_for_orchestrator"
    brief = paths.stage2_brief_path.read_text(encoding="utf-8")
    assert "## Wiki Evidence" in brief
    assert "Wiki search has not run yet" in brief


# ----- brief renderer --------------------------------------------------------


def test_render_wiki_evidence_returns_empty_without_results(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    assert render_wiki_evidence_section(paths) == ""


def test_render_wiki_evidence_contains_citation_tags(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    run_wiki_search_preflight(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    _write_topic_result(paths.wiki_search_results_dir / "T-001.yaml", "T-001")
    run_wiki_search_apply(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )
    rendered = render_wiki_evidence_section(paths)
    assert "## Wiki Evidence" in rendered
    assert "[wiki:concept-core]" in rendered
    assert "[cit:src-test]" in rendered
    assert "Cross-source synthesis" in rendered
