import json
from .test_utils import _build_workspace


def test_chain_runs_successful_commands(run_hook, tmp_path):
    _build_workspace(tmp_path)
    # Configure a test-mode chain via env var
    payload = {
        "hookEventName": "UserPromptSubmit",
        "tool_input": {"prompt": "/__test_chain__"},
    }
    rc, out, _ = run_hook("user_prompt_submit_dispatch", payload, cwd=tmp_path,
                           env={"META_COMPILER_TEST_CHAIN": "echo step1;echo step2"})
    ctx = out.get("additionalContext") or ""
    assert "step1" in ctx
    assert "step2" in ctx


def test_chain_stops_on_failure(run_hook, tmp_path):
    _build_workspace(tmp_path)
    payload = {
        "hookEventName": "UserPromptSubmit",
        "tool_input": {"prompt": "/__test_chain__"},
    }
    rc, out, _ = run_hook("user_prompt_submit_dispatch", payload, cwd=tmp_path,
                           env={"META_COMPILER_TEST_CHAIN": "false;echo should_not_run"})
    ctx = out.get("additionalContext") or ""
    msg = out.get("systemMessage") or ""
    assert "should_not_run" not in ctx
    assert "failed" in (msg + ctx).lower() or "nonzero" in (msg + ctx).lower()
