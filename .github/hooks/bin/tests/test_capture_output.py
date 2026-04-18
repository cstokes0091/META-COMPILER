def test_capture_output_parses_json(run_hook, tmp_path):
    payload = {
        "hookEventName": "PostToolUse",
        "tool_input": {"command": "meta-compiler ingest --scope all"},
        "tool_result": {"stdout": '{"status": "ingested", "count": 3}'},
    }
    rc, out, _ = run_hook("capture_output", payload, cwd=tmp_path)
    ctx = out.get("additionalContext") or ""
    assert '"status": "ingested"' in ctx or "ingested" in ctx
    assert "count" in ctx


def test_capture_output_wraps_plain_text(run_hook, tmp_path):
    payload = {
        "hookEventName": "PostToolUse",
        "tool_input": {"command": "meta-compiler validate-stage --stage 1a"},
        "tool_result": {"stdout": "VALIDATION PASSED\n3 checks OK"},
    }
    rc, out, _ = run_hook("capture_output", payload, cwd=tmp_path)
    ctx = out.get("additionalContext") or ""
    assert "VALIDATION PASSED" in ctx


def test_capture_output_ignores_non_meta_commands(run_hook, tmp_path):
    payload = {
        "hookEventName": "PostToolUse",
        "tool_input": {"command": "ls -la"},
        "tool_result": {"stdout": "total 8\n..."},
    }
    rc, out, _ = run_hook("capture_output", payload, cwd=tmp_path)
    assert not out.get("additionalContext")
