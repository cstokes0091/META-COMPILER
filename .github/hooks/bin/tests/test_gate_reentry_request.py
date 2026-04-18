import hashlib
import json
from pathlib import Path

from .test_utils import _build_workspace


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_problem(tmp_path, content: str) -> str:
    p = tmp_path / "PROBLEM_STATEMENT.md"
    p.write_text(content)
    return _sha(content)


def _write_request(tmp_path, **kwargs):
    path = tmp_path / "workspace-artifacts" / "runtime" / "stage2" / "reentry_request.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Emit YAML-subset our parser accepts
    body = [
        "stage2_reentry_request:",
        f"  parent_version: {kwargs.get('parent_version', 1)}",
        f"  problem_change_summary: {kwargs.get('summary', 'changed')}",
        "  problem_statement:",
        f"    previously_ingested_sha256: {kwargs['prev_sha']}",
        f"    current_sha256: {kwargs['cur_sha']}",
        f"    updated: {str(kwargs.get('updated', False)).lower()}",
        "    update_rationale: rationale",
        "  reason: changed",
        "  revised_sections:",
        "    - architecture",
        "  carried_consistency_risks: []",
    ]
    path.write_text("\n".join(body) + "\n")


def _bash(cmd: str) -> dict:
    return {"hookEventName": "PreToolUse", "tool_input": {"command": cmd}}


def test_deny_when_request_missing(run_hook, tmp_path):
    _build_workspace(tmp_path)
    _write_problem(tmp_path, "problem v1")
    rc, out, _ = run_hook(
        "gate_reentry_request",
        _bash('meta-compiler stage2-reentry --reason x --sections architecture'),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision") == "deny"


def test_deny_when_current_sha_stale(run_hook, tmp_path):
    _build_workspace(tmp_path)
    sha = _write_problem(tmp_path, "problem v1")
    _write_request(tmp_path, prev_sha=sha, cur_sha="0" * 64, updated=False)
    rc, out, _ = run_hook(
        "gate_reentry_request",
        _bash("meta-compiler stage2-reentry --from-request workspace-artifacts/runtime/stage2/reentry_request.yaml"),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision") == "deny"


def test_allow_when_request_matches_and_unchanged(run_hook, tmp_path):
    _build_workspace(tmp_path)
    sha = _write_problem(tmp_path, "problem v1")
    _write_request(tmp_path, prev_sha=sha, cur_sha=sha, updated=False)
    rc, out, _ = run_hook(
        "gate_reentry_request",
        _bash("meta-compiler stage2-reentry --from-request workspace-artifacts/runtime/stage2/reentry_request.yaml"),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision") == "allow"


def test_deny_when_updated_true_but_sha_unchanged(run_hook, tmp_path):
    _build_workspace(tmp_path)
    sha = _write_problem(tmp_path, "problem v1")
    _write_request(tmp_path, prev_sha=sha, cur_sha=sha, updated=True)
    rc, out, _ = run_hook(
        "gate_reentry_request",
        _bash("meta-compiler stage2-reentry --from-request workspace-artifacts/runtime/stage2/reentry_request.yaml"),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision") == "deny"


def test_skip_env_var_does_not_override(run_hook, tmp_path):
    """gate_reentry_request is explicitly non-overridable."""
    _build_workspace(tmp_path)
    _write_problem(tmp_path, "x")
    rc, out, _ = run_hook(
        "gate_reentry_request",
        _bash("meta-compiler stage2-reentry --reason x --sections architecture"),
        cwd=tmp_path,
        env={"META_COMPILER_SKIP_HOOK": "1"},
    )
    assert out.get("permissionDecision") == "deny"


def test_passthrough_for_non_reentry_commands(run_hook, tmp_path):
    _build_workspace(tmp_path)
    rc, out, _ = run_hook(
        "gate_reentry_request",
        _bash("meta-compiler scaffold"),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision", "allow") == "allow"
