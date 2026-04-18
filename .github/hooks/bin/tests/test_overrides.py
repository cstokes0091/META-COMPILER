import json
from pathlib import Path

from .test_utils import _build_workspace


def test_override_disables_named_check(tmp_path, run_hook, monkeypatch):
    monkeypatch.delenv("META_COMPILER_HOOK_TEST", raising=False)
    _build_workspace(tmp_path)
    overrides_path = tmp_path / ".github" / "hooks" / "overrides.json"
    overrides_path.parent.mkdir(parents=True)
    overrides_path.write_text(json.dumps({
        "disable_checks": ["_demo_always_deny"],
        "disable_until": "2099-01-01T00:00:00Z",
        "reason": "test",
        "approved_by": "test",
    }))
    rc, out, err = run_hook("_demo_always_deny", {}, cwd=tmp_path,
                            env={"META_COMPILER_HOOK_TEST": "1"})
    assert rc == 0
    # Disabled → allow with systemMessage noting override
    assert out.get("permissionDecision") == "allow"
    assert "override" in (out.get("systemMessage") or "").lower()


def test_expired_override_is_ignored(tmp_path, run_hook):
    _build_workspace(tmp_path)
    overrides_path = tmp_path / ".github" / "hooks" / "overrides.json"
    overrides_path.parent.mkdir(parents=True)
    overrides_path.write_text(json.dumps({
        "disable_checks": ["_demo_always_deny"],
        "disable_until": "2000-01-01T00:00:00Z",
        "reason": "test",
        "approved_by": "test",
    }))
    rc, out, err = run_hook("_demo_always_deny", {}, cwd=tmp_path)
    assert out.get("permissionDecision") == "deny"


def test_no_override_file_check_runs_normally(tmp_path, run_hook):
    _build_workspace(tmp_path)
    rc, out, err = run_hook("_demo_always_deny", {}, cwd=tmp_path)
    assert out.get("permissionDecision") == "deny"
