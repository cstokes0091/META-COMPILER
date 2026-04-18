import pytest

from .test_utils import _build_workspace

# (manifest_stage_1a fixture has last_completed_stage: "1a")


def _bash_input(cmd: str) -> dict:
    return {"hookEventName": "PreToolUse", "tool_input": {"command": cmd}}


def test_gate_cli_allows_correct_order(run_hook, tmp_path):
    _build_workspace(tmp_path)
    # research-depth requires stage 1a — satisfied
    rc, out, _ = run_hook("gate_cli", _bash_input("meta-compiler research-depth"), cwd=tmp_path)
    assert out.get("permissionDecision") == "allow"


def test_gate_cli_denies_wrong_order(run_hook, tmp_path):
    _build_workspace(tmp_path)
    # scaffold requires stage 2 — manifest only at 1a
    rc, out, _ = run_hook("gate_cli", _bash_input("meta-compiler scaffold"), cwd=tmp_path)
    assert out.get("permissionDecision") == "deny"
    assert "2" in out.get("reason", "")


def test_gate_cli_passthrough_for_non_meta_commands(run_hook, tmp_path):
    _build_workspace(tmp_path)
    rc, out, _ = run_hook("gate_cli", _bash_input("ls -la"), cwd=tmp_path)
    assert out.get("permissionDecision", "allow") == "allow"


def test_gate_cli_env_override_allows(run_hook, tmp_path):
    _build_workspace(tmp_path)
    # Env var simulates being set on the tool call
    import os
    os.environ["META_COMPILER_SKIP_HOOK"] = "1"
    try:
        rc, out, _ = run_hook(
            "gate_cli",
            _bash_input("meta-compiler scaffold"),
            cwd=tmp_path,
            env={"META_COMPILER_SKIP_HOOK": "1"},
        )
        assert out.get("permissionDecision") == "allow"
    finally:
        os.environ.pop("META_COMPILER_SKIP_HOOK", None)


def test_gate_cli_missing_manifest_denies_with_init_remediation(run_hook, tmp_path):
    # No workspace at all
    rc, out, _ = run_hook("gate_cli", _bash_input("meta-compiler scaffold"), cwd=tmp_path)
    assert out.get("permissionDecision") == "deny"
    assert "meta-init" in (out.get("remediation") or "").lower()
