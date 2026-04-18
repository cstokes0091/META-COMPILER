from .test_utils import _build_workspace


def test_audit_suppressed_in_test_mode(tmp_path, run_hook):
    """With META_COMPILER_HOOK_TEST=1, hook_audit.log is not written."""
    _build_workspace(tmp_path)
    run_hook("_demo_always_deny", {}, cwd=tmp_path)
    audit_path = tmp_path / "workspace-artifacts" / "runtime" / "hook_audit.log"
    assert not audit_path.exists()


def test_audit_written_when_not_in_test_mode(tmp_path, run_hook, monkeypatch):
    """Without HOOK_TEST env, audit log is appended to."""
    _build_workspace(tmp_path)
    run_hook("_demo_always_deny", {}, cwd=tmp_path,
             env={"META_COMPILER_HOOK_TEST": "0"})
    audit_path = tmp_path / "workspace-artifacts" / "runtime" / "hook_audit.log"
    assert audit_path.exists()
    content = audit_path.read_text()
    assert "_demo_always_deny" in content
    assert "deny" in content
