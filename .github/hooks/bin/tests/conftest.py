import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_SCRIPT = Path(__file__).resolve().parents[1] / "meta_hook.py"


@pytest.fixture
def run_hook(tmp_path, monkeypatch):
    """Invoke meta_hook.py as a subprocess with given check + stdin JSON.

    Returns (exit_code, stdout_json, stderr_text).
    """
    def _run(check_name: str, stdin_obj: dict, cwd: Path | None = None, env: dict | None = None):
        work_cwd = cwd if cwd is not None else tmp_path
        merged_env = {**os.environ, "META_COMPILER_HOOK_TEST": "1"}
        if env:
            merged_env.update(env)
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT), check_name],
            input=json.dumps(stdin_obj),
            capture_output=True,
            text=True,
            cwd=str(work_cwd),
            env=merged_env,
            timeout=10,
        )
        try:
            out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            out = {"_raw": proc.stdout}
        return proc.returncode, out, proc.stderr
    return _run
