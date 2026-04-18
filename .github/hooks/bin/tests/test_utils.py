import shutil
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _build_workspace(tmp_path, manifest_fixture="manifest_stage_1a.yaml"):
    (tmp_path / "workspace-artifacts" / "manifests").mkdir(parents=True)
    shutil.copy(
        FIXTURES / manifest_fixture,
        tmp_path / "workspace-artifacts" / "manifests" / "workspace_manifest.yaml",
    )
    return tmp_path


def test_read_manifest_via_check(run_hook, tmp_path):
    """A test check that echoes manifest.last_completed_stage proves the read works."""
    _build_workspace(tmp_path)
    rc, out, err = run_hook("_echo_stage_for_test", {}, cwd=tmp_path)
    assert rc == 0
    assert out.get("additionalContext") == "1a"


def test_read_manifest_missing_returns_empty(run_hook, tmp_path):
    """No manifest → returns empty dict, no crash."""
    rc, out, err = run_hook("_echo_stage_for_test", {}, cwd=tmp_path)
    assert rc == 0
    assert out.get("additionalContext") == "(none)"
