"""Tests for `meta-compiler wiki-update`.

The command is a thin chain of `run_ingest` + `run_research_breadth`. We
verify the three flows it must distinguish:

1. New seeds present and need extraction → halt with remediation, do NOT
   run breadth.
2. New seeds with `--force` → run breadth anyway against current findings.
3. No new seeds (everything already extracted, or only breadth refresh
   requested) → run both back-to-back.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.stages import wiki_update_stage


def _bootstrap(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    return workspace_root, artifacts_root, paths


def test_wiki_update_halts_when_orchestrator_pending(tmp_path: Path, monkeypatch):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    breadth_calls: list[dict[str, Any]] = []

    def fake_ingest(*, artifacts_root, workspace_root, scope):
        return {
            "status": "ready_for_orchestrator",
            "scope": scope,
            "work_items": 3,
            "doc_items": 3,
            "code_items": 0,
            "repo_map_items": 0,
            "work_plan_path": "runtime/ingest/work_plan.yaml",
        }

    def fake_breadth(**kwargs):
        breadth_calls.append(kwargs)
        return {"status": "rebuilt"}

    monkeypatch.setattr(wiki_update_stage, "run_ingest", fake_ingest)
    monkeypatch.setattr(wiki_update_stage, "run_research_breadth", fake_breadth)

    result = wiki_update_stage.run_wiki_update(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )

    assert result["status"] == "ingest_pending_orchestrator"
    assert result["ingest"]["work_items"] == 3
    assert "ingest-orchestrator" in result["instruction"]
    # Breadth was NOT executed — the orchestrator must run first.
    assert breadth_calls == []


def test_wiki_update_force_runs_breadth_despite_pending_work(tmp_path: Path, monkeypatch):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    breadth_calls: list[dict[str, Any]] = []

    def fake_ingest(*, artifacts_root, workspace_root, scope):
        return {
            "status": "ready_for_orchestrator",
            "scope": scope,
            "work_items": 5,
            "repo_map_items": 0,
        }

    def fake_breadth(**kwargs):
        breadth_calls.append(kwargs)
        return {"status": "rebuilt"}

    monkeypatch.setattr(wiki_update_stage, "run_ingest", fake_ingest)
    monkeypatch.setattr(wiki_update_stage, "run_research_breadth", fake_breadth)

    result = wiki_update_stage.run_wiki_update(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        force=True,
    )

    assert result["status"] == "updated"
    assert result["forced"] is True
    assert breadth_calls and breadth_calls[0]["artifacts_root"] == artifacts_root


def test_wiki_update_runs_breadth_when_no_new_work(tmp_path: Path, monkeypatch):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    breadth_calls: list[dict[str, Any]] = []

    def fake_ingest(*, artifacts_root, workspace_root, scope):
        # Every seed already in findings index; preflight reports zero
        # work items and zero repo maps.
        return {
            "status": "ready_for_orchestrator",
            "scope": scope,
            "work_items": 0,
            "repo_map_items": 0,
            "skipped_already_extracted": 4,
        }

    def fake_breadth(**kwargs):
        breadth_calls.append(kwargs)
        return {"status": "rebuilt", "concept_pages_enriched": 2}

    monkeypatch.setattr(wiki_update_stage, "run_ingest", fake_ingest)
    monkeypatch.setattr(wiki_update_stage, "run_research_breadth", fake_breadth)

    result = wiki_update_stage.run_wiki_update(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )

    assert result["status"] == "updated"
    assert result["forced"] is False
    assert result["breadth"]["concept_pages_enriched"] == 2
    assert breadth_calls  # breadth was invoked


def test_wiki_update_runs_breadth_when_no_seeds_at_all(tmp_path: Path, monkeypatch):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    breadth_calls: list[dict[str, Any]] = []

    def fake_ingest(*, artifacts_root, workspace_root, scope):
        return {
            "status": "no_seeds",
            "scope": scope,
            "work_items": 0,
            "work_plan_path": None,
        }

    def fake_breadth(**kwargs):
        breadth_calls.append(kwargs)
        return {"status": "rebuilt"}

    monkeypatch.setattr(wiki_update_stage, "run_ingest", fake_ingest)
    monkeypatch.setattr(wiki_update_stage, "run_research_breadth", fake_breadth)

    result = wiki_update_stage.run_wiki_update(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )

    assert result["status"] == "updated"
    assert breadth_calls  # breadth still runs to rebuild index


def test_wiki_update_passes_scope_through(tmp_path: Path, monkeypatch):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    captured_scope: list[str] = []

    def fake_ingest(*, artifacts_root, workspace_root, scope):
        captured_scope.append(scope)
        return {"status": "ready_for_orchestrator", "work_items": 0, "repo_map_items": 0}

    def fake_breadth(**kwargs):
        return {"status": "rebuilt"}

    monkeypatch.setattr(wiki_update_stage, "run_ingest", fake_ingest)
    monkeypatch.setattr(wiki_update_stage, "run_research_breadth", fake_breadth)

    wiki_update_stage.run_wiki_update(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        scope="all",
    )

    assert captured_scope == ["all"]
