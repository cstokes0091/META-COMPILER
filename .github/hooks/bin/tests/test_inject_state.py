from .test_utils import _build_workspace


def test_inject_state_surfaces_current_stage(run_hook, tmp_path):
    _build_workspace(tmp_path)
    rc, out, _ = run_hook("inject_state", {"hookEventName": "SessionStart"}, cwd=tmp_path)
    ctx = out.get("additionalContext") or ""
    assert "1a" in ctx
    assert "next" in ctx.lower() or "stage" in ctx.lower()


def test_inject_state_fresh_workspace(run_hook, tmp_path):
    rc, out, _ = run_hook("inject_state", {"hookEventName": "SessionStart"}, cwd=tmp_path)
    ctx = out.get("additionalContext") or ""
    assert "meta-init" in ctx.lower() or "none" in ctx.lower()


def test_inject_state_reentry_state_flagged(run_hook, tmp_path):
    import shutil
    from .test_utils import FIXTURES
    (tmp_path / "workspace-artifacts" / "manifests").mkdir(parents=True)
    # Build a reentry fixture inline
    (tmp_path / "workspace-artifacts" / "manifests" / "workspace_manifest.yaml").write_text(
        """workspace_manifest:
  research:
    last_completed_stage: "2-reentry-seeded"
    reentry_version: 2
"""
    )
    rc, out, _ = run_hook("inject_state", {"hookEventName": "SessionStart"}, cwd=tmp_path)
    ctx = out.get("additionalContext") or ""
    assert "re-entry" in ctx.lower() or "reentry" in ctx.lower()
