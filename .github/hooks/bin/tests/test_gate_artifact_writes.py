def _write_input(path: str) -> dict:
    return {"hookEventName": "PreToolUse", "tool_input": {"file_path": path}}


def test_deny_writes_to_decision_logs(run_hook, tmp_path):
    (tmp_path / "workspace-artifacts" / "decision-logs").mkdir(parents=True)
    rc, out, _ = run_hook(
        "gate_artifact_writes",
        _write_input(str(tmp_path / "workspace-artifacts" / "decision-logs" / "decision_log_v2.yaml")),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision") == "deny"


def test_deny_writes_to_seeds(run_hook, tmp_path):
    (tmp_path / "workspace-artifacts" / "seeds").mkdir(parents=True)
    rc, out, _ = run_hook(
        "gate_artifact_writes",
        _write_input(str(tmp_path / "workspace-artifacts" / "seeds" / "paper.pdf")),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision") == "deny"


def test_allow_write_to_wiki(run_hook, tmp_path):
    rc, out, _ = run_hook(
        "gate_artifact_writes",
        _write_input(str(tmp_path / "workspace-artifacts" / "wiki" / "v2" / "pages" / "page.md")),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision", "allow") == "allow"


def test_allow_write_to_transcript(run_hook, tmp_path):
    rc, out, _ = run_hook(
        "gate_artifact_writes",
        _write_input(str(tmp_path / "workspace-artifacts" / "runtime" / "stage2" / "transcript.md")),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision", "allow") == "allow"


def test_allow_write_to_unrelated_path(run_hook, tmp_path):
    rc, out, _ = run_hook(
        "gate_artifact_writes",
        _write_input(str(tmp_path / "README.md")),
        cwd=tmp_path,
    )
    assert out.get("permissionDecision", "allow") == "allow"
