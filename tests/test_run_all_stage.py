from pathlib import Path

from meta_compiler.stages.run_all_stage import run_all


def _patch_run_all_dependencies(monkeypatch, seed_status: dict[str, object]) -> list[str]:
    calls: list[str] = []

    def _record(name: str, result: dict[str, object]):
        def _inner(*args, **kwargs):
            calls.append(name)
            return result

        return _inner

    def ingest_stub(*args, **kwargs):
        calls.append(f"ingest:{kwargs['scope']}")
        return {"scope": kwargs["scope"]}

    def validate_stub(paths, stage: str):
        calls.append(f"validate:{stage}")
        return []

    monkeypatch.setattr(
        "meta_compiler.stages.run_all_stage.run_meta_init",
        _record("init", {"status": "initialized"}),
    )
    monkeypatch.setattr("meta_compiler.stages.run_all_stage.run_ingest", ingest_stub)
    monkeypatch.setattr(
        "meta_compiler.stages.run_all_stage.run_research_breadth",
        _record("breadth", {"status": "breadth"}),
    )
    monkeypatch.setattr(
        "meta_compiler.stages.run_all_stage.run_research_depth",
        _record("depth", {"status": "depth"}),
    )
    monkeypatch.setattr(
        "meta_compiler.stages.run_all_stage.run_review",
        _record("review", {"status": "review"}),
    )
    monkeypatch.setattr(
        "meta_compiler.stages.run_all_stage.check_and_update_seeds",
        _record("track-seeds", seed_status),
    )
    monkeypatch.setattr(
        "meta_compiler.stages.run_all_stage.run_elicit_vision",
        _record("elicit", {"status": "elicit"}),
    )
    monkeypatch.setattr(
        "meta_compiler.stages.run_all_stage.run_audit_requirements",
        _record("audit", {"status": "audit"}),
    )
    monkeypatch.setattr("meta_compiler.stages.run_all_stage.validate_stage", validate_stub)
    monkeypatch.setattr(
        "meta_compiler.stages.run_all_stage.list_seed_files",
        lambda paths: [paths.seeds_dir / "seed.txt"],
    )
    return calls


def test_run_all_stops_at_stage2_handoff(tmp_path: Path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    artifacts_root = workspace_root / "workspace-artifacts"

    calls = _patch_run_all_dependencies(monkeypatch, {"new_seeds_found": False})

    result = run_all(
        workspace_root=workspace_root,
        artifacts_root=artifacts_root,
        project_name="Test Project",
        problem_domain="Test Domain",
        project_type="hybrid",
        problem_statement="# Problem\n",
    )

    assert result["status"] == "stage-2-handoff"
    assert result["handoff_ready"] is True
    assert result["handoff_stage"] == "2"
    assert result["next_steps"][-1] == "Run meta-compiler scaffold after human review."
    assert calls == [
        "init",
        "validate:0",
        "ingest:all",
        "breadth",
        "validate:1a",
        "depth",
        "review",
        "track-seeds",
        "elicit",
        "validate:2",
        "audit",
    ]


def test_run_all_prepares_new_seed_ingest_when_tracker_detects_changes(tmp_path: Path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    artifacts_root = workspace_root / "workspace-artifacts"

    calls = _patch_run_all_dependencies(
        monkeypatch,
        {"new_seeds_found": True, "new_seed_count": 1},
    )

    run_all(
        workspace_root=workspace_root,
        artifacts_root=artifacts_root,
        project_name="Test Project",
        problem_domain="Test Domain",
        project_type="hybrid",
        problem_statement="# Problem\n",
    )

    ingest_calls = [call for call in calls if call.startswith("ingest:")]
    assert ingest_calls == ["ingest:all", "ingest:new"]