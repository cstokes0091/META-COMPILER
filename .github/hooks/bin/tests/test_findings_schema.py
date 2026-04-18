import json


def _write_input(path: str) -> dict:
    return {
        "hookEventName": "PostToolUse",
        "tool_input": {"file_path": path},
    }


def _valid_findings(tmp_path, filename="src-abc.json"):
    p = tmp_path / "workspace-artifacts" / "wiki" / "findings" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "source_id": "src-abc",
        "title": "abc",
        "findings": [
            {"claim": "x", "quote": "y", "location": "p.1"},
        ],
    }))
    return p


def test_valid_findings_allows(run_hook, tmp_path):
    p = _valid_findings(tmp_path)
    rc, out, _ = run_hook("validate_findings_schema", _write_input(str(p)), cwd=tmp_path)
    assert out.get("permissionDecision", "allow") == "allow"


def test_malformed_json_denies(run_hook, tmp_path):
    p = tmp_path / "workspace-artifacts" / "wiki" / "findings" / "bad.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    rc, out, _ = run_hook("validate_findings_schema", _write_input(str(p)), cwd=tmp_path)
    assert out.get("permissionDecision") == "deny"


def test_missing_required_field_denies(run_hook, tmp_path):
    p = tmp_path / "workspace-artifacts" / "wiki" / "findings" / "x.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"source_id": "x"}))  # missing findings list
    rc, out, _ = run_hook("validate_findings_schema", _write_input(str(p)), cwd=tmp_path)
    assert out.get("permissionDecision") == "deny"


def test_unrelated_write_ignored(run_hook, tmp_path):
    p = tmp_path / "README.md"
    p.write_text("# readme")
    rc, out, _ = run_hook("validate_findings_schema", _write_input(str(p)), cwd=tmp_path)
    assert out.get("permissionDecision", "allow") == "allow"
