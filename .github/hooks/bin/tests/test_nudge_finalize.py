from .test_utils import _build_workspace


def test_nudge_blocks_when_reentry_seeded(run_hook, tmp_path):
    (tmp_path / "workspace-artifacts" / "manifests").mkdir(parents=True)
    (tmp_path / "workspace-artifacts" / "manifests" / "workspace_manifest.yaml").write_text(
        """workspace_manifest:
  research:
    last_completed_stage: "2-reentry-seeded"
"""
    )
    rc, out, _ = run_hook("nudge_finalize", {"hookEventName": "Stop"}, cwd=tmp_path)
    assert out.get("decision") == "block"
    assert "finalize" in (out.get("reason") or "").lower()


def test_nudge_allows_clean_state(run_hook, tmp_path):
    _build_workspace(tmp_path)  # stage 1a
    rc, out, _ = run_hook("nudge_finalize", {"hookEventName": "Stop"}, cwd=tmp_path)
    assert out.get("decision") != "block"


def test_nudge_no_workspace_allows(run_hook, tmp_path):
    rc, out, _ = run_hook("nudge_finalize", {"hookEventName": "Stop"}, cwd=tmp_path)
    assert out.get("decision") != "block"
