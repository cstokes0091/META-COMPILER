from .test_utils import _build_workspace


def test_unrelated_prompt_noop(run_hook, tmp_path):
    _build_workspace(tmp_path)
    payload = {
        "hookEventName": "UserPromptSubmit",
        "tool_input": {"prompt": "thanks, that worked"},
    }
    rc, out, _ = run_hook("user_prompt_submit_dispatch", payload, cwd=tmp_path)
    assert not out.get("additionalContext")


def test_stage1a_trigger_matches(run_hook, tmp_path):
    _build_workspace(tmp_path)
    payload = {
        "hookEventName": "UserPromptSubmit",
        "tool_input": {"prompt": "/stage-1a-breadth please start"},
    }
    # Use the test-chain env to avoid invoking real meta-compiler
    rc, out, _ = run_hook("user_prompt_submit_dispatch", payload, cwd=tmp_path,
                           env={"META_COMPILER_TEST_CHAIN": "echo simulated-ingest"})
    ctx = out.get("additionalContext") or ""
    assert "simulated-ingest" in ctx
