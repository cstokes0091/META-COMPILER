def test_unknown_check_fails_open(run_hook):
    """Unknown check name emits fail-open warning, continues."""
    rc, out, err = run_hook("nonexistent_check", {})
    assert rc == 0
    assert out.get("continue") is True
    assert "systemMessage" in out
    assert "nonexistent_check" in out["systemMessage"]


def test_missing_check_arg_fails_open(run_hook):
    """No argv → fail-open warning."""
    import subprocess, sys, json
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "meta_hook.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        input="{}",
        capture_output=True, text=True, timeout=10,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    assert out.get("continue") is True
