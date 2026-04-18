# Hooks-Based Determinism + Stage 2 Re-entry Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace informal "prompts describe CLI calls" discipline in meta-compiler with VSCode Copilot hooks that gate, auto-fire, and audit the pipeline; apply prompt-as-conductor hardening to Stage 2 re-entry; close the problem-space dialog gap via a new Step 0 artifact.

**Architecture:** Four layered pieces — workspace hooks (`.github/hooks/main.json`), agent-scoped hooks (in `.agent.md` frontmatter), CLI hardening in `meta_compiler/stages/`, and a rewritten `stage2-reentry.prompt.md`. All hook logic lives in one stdlib-only helper at `.github/hooks/bin/meta_hook.py` invoked by every hook entry.

**Tech Stack:** Python 3.11+ (stdlib only for hooks, PyYAML for CLI), pytest, JSON for hook-state artifacts, YAML for legacy manifest/verdict files (subset parser in hook).

**Source spec:** `docs/superpowers/specs/2026-04-18-hooks-and-stage2-reentry-design.md`

---

## Phase 1 — `meta_hook.py` Foundation (dormant module, no behavior change)

Phase 1 lands the module and its unit tests. Nothing is registered as a hook yet; all existing flows remain identical. Tests pass in CI.

### Task 1: Create `meta_hook.py` skeleton with dispatch and shared utilities

**Files:**
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/meta_hook.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/__init__.py` (empty)
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/conftest.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_dispatch.py`

- [ ] **Step 1: Write the failing test for unknown check dispatch**

Create `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/conftest.py`:

```python
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
```

Create `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_dispatch.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/test_dispatch.py -v`

Expected: FAIL (`meta_hook.py` does not exist).

- [ ] **Step 3: Write minimal `meta_hook.py` implementation**

Create `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/meta_hook.py`:

```python
#!/usr/bin/env python3
"""Meta-compiler hook dispatcher.

Invoked by VSCode Copilot hook entries in .github/hooks/main.json and by
agent-scoped hooks in .github/agents/*.agent.md frontmatter. Reads hook-
event JSON from stdin, dispatches on sys.argv[1] to a named check, writes
result JSON to stdout, exits 0.

Stdlib only. See docs/superpowers/specs/2026-04-18-hooks-and-stage2-reentry-design.md.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Callable


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def fail_open(check_name: str, detail: str) -> None:
    emit({
        "continue": True,
        "systemMessage": (
            f"Hook {check_name} crashed: {detail}. "
            "Proceeding anyway; workspace integrity not enforced for this call."
        ),
    })


CHECKS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register(name: str):
    def _decorator(fn):
        CHECKS[name] = fn
        return fn
    return _decorator


def read_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        fail_open("(none)", "no check name provided as argv[1]")
        return 0
    check_name = argv[1]
    fn = CHECKS.get(check_name)
    if fn is None:
        fail_open(check_name, "check name not registered in meta_hook.py")
        return 0
    try:
        payload = read_input()
        result = fn(payload)
        emit(result or {})
        return 0
    except Exception:
        fail_open(check_name, traceback.format_exc().splitlines()[-1])
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

Make it executable:

```bash
chmod +x /Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/meta_hook.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/test_dispatch.py -v`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/__init__.py .github/hooks/bin/tests/conftest.py .github/hooks/bin/tests/test_dispatch.py
git commit -m "hooks: add meta_hook.py dispatcher skeleton with fail-open safety"
```

---

### Task 2: Add workspace-root resolution and manifest reader utilities

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/meta_hook.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_utils.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/fixtures/manifest_stage_1a.yaml`

- [ ] **Step 1: Create fixture manifest**

Create `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/fixtures/manifest_stage_1a.yaml`:

```yaml
workspace_manifest:
  project_name: test-project
  problem_domain: test-domain
  project_type: research
  research:
    last_completed_stage: "1a"
    reentry_version: null
  decision_logs: []
  seeds: []
```

- [ ] **Step 2: Write failing test for `read_manifest` and `resolve_workspace_root`**

Create `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_utils.py`:

```python
import shutil
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _build_workspace(tmp_path, manifest_fixture="manifest_stage_1a.yaml"):
    (tmp_path / "workspace-artifacts" / "manifests").mkdir(parents=True)
    shutil.copy(
        FIXTURES / manifest_fixture,
        tmp_path / "workspace-artifacts" / "manifests" / "workspace_manifest.yaml",
    )
    return tmp_path


def test_read_manifest_via_check(run_hook, tmp_path):
    """A test check that echoes manifest.last_completed_stage proves the read works."""
    _build_workspace(tmp_path)
    rc, out, err = run_hook("_echo_stage_for_test", {}, cwd=tmp_path)
    assert rc == 0
    assert out.get("additionalContext") == "1a"


def test_read_manifest_missing_returns_empty(run_hook, tmp_path):
    """No manifest → returns empty dict, no crash."""
    rc, out, err = run_hook("_echo_stage_for_test", {}, cwd=tmp_path)
    assert rc == 0
    assert out.get("additionalContext") == "(none)"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/test_utils.py -v`

Expected: FAIL (checks not implemented).

- [ ] **Step 4: Add utilities and test-only echo check to `meta_hook.py`**

Insert after the `register` decorator definition and before `main`:

```python
# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
from pathlib import Path


def resolve_workspace_root(payload: dict[str, Any]) -> Path:
    """Determine the workspace root from hook input or cwd."""
    cwd = payload.get("cwd") or os.getcwd()
    return Path(cwd).resolve()


_MANIFEST_CACHE: dict[str, dict[str, Any]] = {}


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    """Parse a narrow YAML subset: nested key:value dicts, lists, scalars.

    Supports:
      - key: value
      - key: |\n  literal\n  literal
      - key:\n  - item\n  - item
      - nested dicts via indentation
    Does NOT support: anchors, tags, flow style, multiline scalars beyond `|`.
    Raises ValueError on unsupported syntax.
    """
    lines = text.splitlines()
    idx = 0

    def parse_block(indent: int) -> Any:
        nonlocal idx
        result: dict[str, Any] | list[Any] | None = None
        while idx < len(lines):
            line = lines[idx]
            if not line.strip() or line.lstrip().startswith("#"):
                idx += 1
                continue
            stripped = line.lstrip(" ")
            cur_indent = len(line) - len(stripped)
            if cur_indent < indent:
                return result if result is not None else {}
            if cur_indent > indent and result is None:
                result = {}
            if stripped.startswith("- "):
                if result is None:
                    result = []
                if not isinstance(result, list):
                    return result
                idx += 1
                item_text = stripped[2:].strip()
                if ":" in item_text and not item_text.startswith("'") and not item_text.startswith('"'):
                    # Inline mapping in list: `- key: value`
                    k, _, v = item_text.partition(":")
                    item: dict[str, Any] = {k.strip(): _scalar(v.strip())}
                    nested = parse_block(cur_indent + 2)
                    if isinstance(nested, dict):
                        item.update(nested)
                    result.append(item)
                else:
                    result.append(_scalar(item_text))
                continue
            if ":" in stripped:
                key, _, rest = stripped.partition(":")
                key = key.strip()
                rest = rest.strip()
                idx += 1
                if result is None:
                    result = {}
                if not isinstance(result, dict):
                    return result
                if rest == "":
                    # Nested block follows
                    result[key] = parse_block(cur_indent + 2)
                elif rest == "|":
                    # Literal block scalar
                    buf: list[str] = []
                    while idx < len(lines):
                        nline = lines[idx]
                        if not nline.strip():
                            buf.append("")
                            idx += 1
                            continue
                        nstripped = nline.lstrip(" ")
                        nindent = len(nline) - len(nstripped)
                        if nindent <= cur_indent:
                            break
                        buf.append(nline[cur_indent + 2:] if len(nline) > cur_indent + 2 else "")
                        idx += 1
                    result[key] = "\n".join(buf).rstrip("\n")
                else:
                    result[key] = _scalar(rest)
                continue
            idx += 1
        return result if result is not None else {}

    def _scalar(text: str) -> Any:
        if text == "" or text == "null" or text == "~":
            return None
        if text == "true":
            return True
        if text == "false":
            return False
        if (text.startswith('"') and text.endswith('"')) or (
            text.startswith("'") and text.endswith("'")
        ):
            return text[1:-1]
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text

    return parse_block(0)  # type: ignore[return-value]


def read_manifest(workspace_root: Path) -> dict[str, Any]:
    """Load workspace manifest. Returns {} if missing. Cached per process."""
    key = str(workspace_root)
    if key in _MANIFEST_CACHE:
        return _MANIFEST_CACHE[key]
    path = workspace_root / "workspace-artifacts" / "manifests" / "workspace_manifest.yaml"
    if not path.exists():
        _MANIFEST_CACHE[key] = {}
        return {}
    try:
        parsed = _parse_yaml_subset(path.read_text(encoding="utf-8"))
    except Exception:
        _MANIFEST_CACHE[key] = {}
        return {}
    _MANIFEST_CACHE[key] = parsed
    return parsed


def manifest_stage(workspace_root: Path) -> str:
    """Return last_completed_stage or '(none)' if unset/missing."""
    m = read_manifest(workspace_root).get("workspace_manifest") or {}
    research = m.get("research") or {}
    return research.get("last_completed_stage") or "(none)"


# ---------------------------------------------------------------------------
# Test-only check (gated by env)
# ---------------------------------------------------------------------------

if os.environ.get("META_COMPILER_HOOK_TEST") == "1":
    @register("_echo_stage_for_test")
    def _echo_stage_for_test(payload: dict[str, Any]) -> dict[str, Any]:
        return {"additionalContext": manifest_stage(resolve_workspace_root(payload))}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/ -v`

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_utils.py .github/hooks/bin/tests/fixtures/manifest_stage_1a.yaml
git commit -m "hooks: add workspace-root resolution + manifest reader utilities"
```

---

### Task 3: Add audit log + override config loader

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/meta_hook.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_overrides.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_audit.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_overrides.py`:

```python
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
```

Create `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_audit.py`:

```python
from .test_utils import _build_workspace


def test_audit_suppressed_in_test_mode(tmp_path, run_hook):
    """With META_COMPILER_HOOK_TEST=1, hook_audit.log is not written."""
    _build_workspace(tmp_path)
    run_hook("_demo_always_deny", {}, cwd=tmp_path)
    audit_path = tmp_path / "workspace-artifacts" / "runtime" / "hook_audit.log"
    assert not audit_path.exists()


def test_audit_written_when_not_in_test_mode(tmp_path, run_hook, monkeypatch):
    """Without HOOK_TEST env, audit log is appended to."""
    _build_workspace(tmp_path)
    run_hook("_demo_always_deny", {}, cwd=tmp_path,
             env={"META_COMPILER_HOOK_TEST": "0"})
    audit_path = tmp_path / "workspace-artifacts" / "runtime" / "hook_audit.log"
    assert audit_path.exists()
    content = audit_path.read_text()
    assert "_demo_always_deny" in content
    assert "deny" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/test_overrides.py .github/hooks/bin/tests/test_audit.py -v`

Expected: FAIL (demo check + override logic not present).

- [ ] **Step 3: Add override loader, audit log, and demo deny check**

Insert in `meta_hook.py` after `manifest_stage`:

```python
# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------
import datetime as _dt


def load_overrides(workspace_root: Path) -> dict[str, Any]:
    """Load overrides.json if present and not expired."""
    path = workspace_root / ".github" / "hooks" / "overrides.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    expiry = data.get("disable_until")
    if expiry:
        try:
            exp_dt = _dt.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp_dt < _dt.datetime.now(_dt.timezone.utc):
                return {}
        except Exception:
            return {}
    return data


def is_disabled(check_name: str, workspace_root: Path) -> tuple[bool, str]:
    """Return (disabled, reason)."""
    overrides = load_overrides(workspace_root)
    if check_name in (overrides.get("disable_checks") or []):
        return True, overrides.get("reason") or "(no reason given)"
    return False, ""


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def audit(
    workspace_root: Path,
    check: str,
    event: str,
    decision: str,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> int | None:
    """Append one JSON line to hook_audit.log. Returns line number, or None
    if suppressed (test mode or write failure)."""
    if os.environ.get("META_COMPILER_HOOK_TEST") == "1":
        return None
    runtime_dir = workspace_root / "workspace-artifacts" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "hook_audit.log"
    entry = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "check": check,
        "event": event,
        "decision": decision,
        "reason": reason,
    }
    if extra:
        entry.update(extra)
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Demo check used only by tests
# ---------------------------------------------------------------------------

if os.environ.get("META_COMPILER_HOOK_TEST") == "1":
    @register("_demo_always_deny")
    def _demo_always_deny(payload: dict[str, Any]) -> dict[str, Any]:
        ws = resolve_workspace_root(payload)
        disabled, reason = is_disabled("_demo_always_deny", ws)
        if disabled:
            audit(ws, "_demo_always_deny", "PreToolUse", "allow_override",
                  reason=f"override: {reason}")
            return {
                "permissionDecision": "allow",
                "systemMessage": f"check _demo_always_deny disabled by override: {reason}",
            }
        audit(ws, "_demo_always_deny", "PreToolUse", "deny",
              reason="demo check always denies")
        return {
            "permissionDecision": "deny",
            "reason": "demo check always denies",
            "remediation": "(this is a test check; should not appear in production)",
        }
```

- [ ] **Step 4: Run all hook tests**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/ -v`

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_overrides.py .github/hooks/bin/tests/test_audit.py
git commit -m "hooks: add overrides loader + audit log with test-mode suppression"
```

---

### Task 4: Implement `gate_cli` check with `STAGE_PRECONDITIONS` table

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/meta_hook.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_gate_cli.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_gate_cli.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/test_gate_cli.py -v`

Expected: FAIL (`gate_cli` not registered).

- [ ] **Step 3: Implement `STAGE_PRECONDITIONS` and `gate_cli`**

Append to `meta_hook.py` (after the demo check section):

```python
# ---------------------------------------------------------------------------
# Stage ordering state machine
# ---------------------------------------------------------------------------

# Map from meta-compiler subcommand to allowed prior stage values.
# Stage strings match workspace_manifest.research.last_completed_stage.
# Derived from meta_compiler/stages/run_all_stage.py.
STAGE_PRECONDITIONS: dict[str, dict[str, Any]] = {
    "meta-init": {"allowed_prior": ["(none)"], "sets": "0"},
    "ingest": {"allowed_prior": ["0", "1a", "1b", "1c", "2"], "sets": None},
    "ingest-validate": {"allowed_prior": ["0", "1a", "1b", "1c", "2"], "sets": None},
    "research-breadth": {"allowed_prior": ["0"], "sets": None},
    "research-depth": {"allowed_prior": ["1a"], "sets": None},
    "review": {"allowed_prior": ["1b"], "sets": None},
    "elicit-vision--start": {"allowed_prior": ["1c"], "sets": None},
    "elicit-vision--finalize": {
        "allowed_prior": ["1c", "2-dialog-started", "2-reentry-seeded"],
        "sets": "2",
    },
    "audit-requirements": {"allowed_prior": ["2"], "sets": None},
    "stage2-reentry": {"allowed_prior": ["2"], "sets": "2-reentry-seeded"},
    "finalize-reentry": {"allowed_prior": ["2-reentry-seeded"], "sets": "2"},
    "scaffold": {"allowed_prior": ["2"], "sets": "3"},
    "phase4-finalize": {"allowed_prior": ["3"], "sets": "4"},
    "wiki-update": {"allowed_prior": ["1a", "1b", "1c", "2"], "sets": None},
    "track-seeds": {"allowed_prior": ["0", "1a", "1b", "1c", "2", "3", "4"], "sets": None},
    "clean-workspace": {"allowed_prior": ["(none)", "0", "1a", "1b", "1c", "2", "3", "4",
                                          "2-dialog-started", "2-reentry-seeded"], "sets": None},
    "validate-stage": {"allowed_prior": ["(none)", "0", "1a", "1b", "1c", "2", "3", "4",
                                          "2-dialog-started", "2-reentry-seeded"], "sets": None},
    "wiki-browse": {"allowed_prior": ["(none)", "0", "1a", "1b", "1c", "2", "3", "4"], "sets": None},
    "run-all": {"allowed_prior": ["(none)", "0"], "sets": None},
}


def _parse_meta_compiler_command(cmd: str) -> str | None:
    """Extract the meta-compiler subcommand key. Returns None if not a
    meta-compiler command. 'elicit-vision --start' → 'elicit-vision--start'."""
    parts = cmd.strip().split()
    if len(parts) < 2 or parts[0] != "meta-compiler":
        return None
    sub = parts[1]
    # Special-case elicit-vision mode flags
    if sub == "elicit-vision":
        if "--start" in parts:
            return "elicit-vision--start"
        if "--finalize" in parts:
            return "elicit-vision--finalize"
        return sub
    return sub


@register("gate_cli")
def gate_cli(payload: dict[str, Any]) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""

    # Env-var override
    if os.environ.get("META_COMPILER_SKIP_HOOK") == "1":
        audit(ws, "gate_cli", "PreToolUse", "allow_override",
              reason="META_COMPILER_SKIP_HOOK=1",
              extra={"command": cmd, "override": "env"})
        return {"permissionDecision": "allow"}

    disabled, ov_reason = is_disabled("gate_cli", ws)
    if disabled:
        audit(ws, "gate_cli", "PreToolUse", "allow_override",
              reason=f"overrides.json: {ov_reason}",
              extra={"command": cmd, "override": "config"})
        return {
            "permissionDecision": "allow",
            "systemMessage": f"gate_cli disabled by override: {ov_reason}",
        }

    sub = _parse_meta_compiler_command(cmd)
    if sub is None:
        return {"permissionDecision": "allow"}

    # Missing manifest is a distinct case: nothing to gate against
    if not (ws / "workspace-artifacts" / "manifests" / "workspace_manifest.yaml").exists():
        if sub == "meta-init" or sub == "run-all":
            return {"permissionDecision": "allow"}
        line = audit(ws, "gate_cli", "PreToolUse", "deny",
                     reason="manifest missing",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": "No workspace manifest found.",
            "remediation": "Run `meta-compiler meta-init ...` first to initialize the workspace.",
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    pre = STAGE_PRECONDITIONS.get(sub)
    if pre is None:
        # Unknown subcommand: don't gate
        return {"permissionDecision": "allow"}

    current = manifest_stage(ws)
    if current in pre["allowed_prior"]:
        return {"permissionDecision": "allow"}

    line = audit(ws, "gate_cli", "PreToolUse", "deny",
                 reason=f"last_completed_stage={current}, {sub} requires one of {pre['allowed_prior']}",
                 extra={"command": cmd})
    return {
        "permissionDecision": "deny",
        "reason": (
            f"last_completed_stage is '{current}'; '{sub}' requires one of "
            f"{pre['allowed_prior']}."
        ),
        "remediation": (
            f"Complete the prior stage first, or set META_COMPILER_SKIP_HOOK=1 "
            f"in the env if you explicitly want to bypass."
        ),
        "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/test_gate_cli.py -v`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_gate_cli.py
git commit -m "hooks: add gate_cli state-machine check with STAGE_PRECONDITIONS"
```

---

### Task 5: Implement `gate_artifact_writes`

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/meta_hook.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_gate_artifact_writes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gate_artifact_writes.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/test_gate_artifact_writes.py -v`

Expected: FAIL (check not registered).

- [ ] **Step 3: Implement `gate_artifact_writes`**

Append to `meta_hook.py`:

```python
@register("gate_artifact_writes")
def gate_artifact_writes(payload: dict[str, Any]) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    file_path_str = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path_str:
        return {"permissionDecision": "allow"}
    try:
        fp = Path(file_path_str).resolve()
        rel = fp.relative_to(ws)
    except (ValueError, OSError):
        return {"permissionDecision": "allow"}

    rel_posix = rel.as_posix()

    if os.environ.get("META_COMPILER_SKIP_HOOK") == "1":
        audit(ws, "gate_artifact_writes", "PreToolUse", "allow_override",
              reason="META_COMPILER_SKIP_HOOK=1",
              extra={"file_path": rel_posix, "override": "env"})
        return {"permissionDecision": "allow"}

    # Immutable seeds
    if rel_posix.startswith("workspace-artifacts/seeds/"):
        line = audit(ws, "gate_artifact_writes", "PreToolUse", "deny",
                     reason="seeds are immutable",
                     extra={"file_path": rel_posix})
        return {
            "permissionDecision": "deny",
            "reason": "workspace-artifacts/seeds/** is immutable once tracked.",
            "remediation": "If a new seed is needed, add it to the seeds directory before Stage 1A and re-run ingest.",
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    # Compiled Decision Log YAMLs (CLI-only)
    if (
        rel_posix.startswith("workspace-artifacts/decision-logs/")
        and rel_posix.endswith(".yaml")
    ):
        line = audit(ws, "gate_artifact_writes", "PreToolUse", "deny",
                     reason="decision-logs/*.yaml is CLI-compiled",
                     extra={"file_path": rel_posix})
        return {
            "permissionDecision": "deny",
            "reason": "workspace-artifacts/decision-logs/*.yaml is compiled by meta-compiler elicit-vision --finalize. Direct edits desynchronize the source transcript from the YAML.",
            "remediation": "Edit workspace-artifacts/runtime/stage2/transcript.md instead; re-run --finalize to regenerate the YAML.",
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    return {"permissionDecision": "allow"}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/test_gate_artifact_writes.py -v`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_gate_artifact_writes.py
git commit -m "hooks: add gate_artifact_writes for decision-logs and seeds protection"
```

---

### Task 6: Implement `inject_state` and `capture_output`

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/meta_hook.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_inject_state.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/bin/tests/test_capture_output.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_inject_state.py
from .test_utils import _build_workspace


def test_inject_state_surfaces_current_stage(run_hook, tmp_path):
    _build_workspace(tmp_path)
    rc, out, _ = run_hook("inject_state", {"hookEventName": "SessionStart"}, cwd=tmp_path)
    ctx = out.get("additionalContext") or ""
    assert "1a" in ctx
    assert "next" in ctx.lower() or "stage" in ctx.lower()


def test_inject_state_fresh_workspace(run_hook, tmp_path):
    rc, out, _ = run_hook("inject_state", {"hookEventName": "SessionStart"}, cwd=tmp_path)
    ctx = out.get("additionalContext") or ""
    assert "meta-init" in ctx.lower() or "none" in ctx.lower()


def test_inject_state_reentry_state_flagged(run_hook, tmp_path):
    import shutil
    from .test_utils import FIXTURES
    (tmp_path / "workspace-artifacts" / "manifests").mkdir(parents=True)
    # Build a reentry fixture inline
    (tmp_path / "workspace-artifacts" / "manifests" / "workspace_manifest.yaml").write_text(
        """workspace_manifest:
  research:
    last_completed_stage: "2-reentry-seeded"
    reentry_version: 2
"""
    )
    rc, out, _ = run_hook("inject_state", {"hookEventName": "SessionStart"}, cwd=tmp_path)
    ctx = out.get("additionalContext") or ""
    assert "re-entry" in ctx.lower() or "reentry" in ctx.lower()


# tests/test_capture_output.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/test_inject_state.py .github/hooks/bin/tests/test_capture_output.py -v`

Expected: FAIL (checks not registered).

- [ ] **Step 3: Implement both checks**

Append to `meta_hook.py`:

```python
@register("inject_state")
def inject_state(payload: dict[str, Any]) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    m = read_manifest(ws).get("workspace_manifest") or {}
    research = m.get("research") or {}
    stage = research.get("last_completed_stage") or "(none)"
    reentry_version = research.get("reentry_version")

    # Next-action hints (same ordering as run-all)
    NEXT: dict[str, str] = {
        "(none)": "Run `meta-compiler meta-init ...` to initialize the workspace.",
        "0": "Stage 1A: run `/stage-1a-breadth` (ingest + breadth research).",
        "1a": "Stage 1B: run `/stage-1b-evaluators` (depth via stage-1a2-orchestrator).",
        "1b": "Stage 1C: run `/stage-1c-review` (three-reviewer debate).",
        "1c": "Stage 2: run `/stage-2-dialog` (vision elicitation).",
        "2": "Stage 3: run `/stage-3-scaffold` (generate executor).",
        "2-dialog-started": "Stage 2 preflight complete; open the Stage 2 dialog prompt and converse.",
        "2-reentry-seeded": "Stage 2 re-entry in progress; conduct the scoped dialog, then run `meta-compiler elicit-vision --finalize`.",
        "3": "Stage 4: run `/stage-4-finalize`.",
        "4": "Pipeline complete. Use `/clean-workspace` or `/stage2-reentry` to iterate.",
    }

    lines = [
        "# Meta-compiler workspace state",
        f"- last_completed_stage: {stage}",
        f"- suggested next: {NEXT.get(stage, 'unknown')}",
    ]
    if reentry_version is not None and stage == "2-reentry-seeded":
        lines.append(f"- RE-ENTRY IN PROGRESS: revising toward v{reentry_version}. "
                     "Conduct the scoped dialog before finalize.")
    return {"additionalContext": "\n".join(lines)}


@register("capture_output")
def capture_output(payload: dict[str, Any]) -> dict[str, Any]:
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not cmd.strip().startswith("meta-compiler"):
        return {}
    stdout = (payload.get("tool_result") or {}).get("stdout") or ""
    if not stdout.strip():
        return {}
    # Try to render JSON compactly; otherwise wrap as code block.
    try:
        parsed = json.loads(stdout)
        rendered = "```json\n" + json.dumps(parsed, indent=2) + "\n```"
    except (json.JSONDecodeError, ValueError):
        rendered = "```\n" + stdout.rstrip() + "\n```"
    ws = resolve_workspace_root(payload)
    audit(ws, "capture_output", "PostToolUse", "inject",
          extra={"command": cmd, "stdout_bytes": len(stdout)})
    return {
        "additionalContext": f"Output of `{cmd}`:\n\n{rendered}",
    }
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest .github/hooks/bin/tests/ -v`

Expected: all passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_inject_state.py .github/hooks/bin/tests/test_capture_output.py
git commit -m "hooks: add inject_state + capture_output for context forcing"
```

---

### Task 7: Implement `nudge_finalize`

**Files:**
- Modify: `.github/hooks/bin/meta_hook.py`
- Create: `.github/hooks/bin/tests/test_nudge_finalize.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_nudge_finalize.py
from .test_utils import _build_workspace


def test_nudge_blocks_when_reentry_seeded(run_hook, tmp_path):
    (tmp_path / "workspace-artifacts" / "manifests").mkdir(parents=True)
    (tmp_path / "workspace-artifacts" / "manifests" / "workspace_manifest.yaml").write_text(
        """workspace_manifest:
  research:
    last_completed_stage: "2-reentry-seeded"
"""
    )
    rc, out, _ = run_hook("nudge_finalize", {"hookEventName": "Stop"}, cwd=tmp_path)
    assert out.get("decision") == "block"
    assert "finalize" in (out.get("reason") or "").lower()


def test_nudge_allows_clean_state(run_hook, tmp_path):
    _build_workspace(tmp_path)  # stage 1a
    rc, out, _ = run_hook("nudge_finalize", {"hookEventName": "Stop"}, cwd=tmp_path)
    assert out.get("decision") != "block"


def test_nudge_no_workspace_allows(run_hook, tmp_path):
    rc, out, _ = run_hook("nudge_finalize", {"hookEventName": "Stop"}, cwd=tmp_path)
    assert out.get("decision") != "block"
```

- [ ] **Step 2: Run to verify failure.** `pytest .github/hooks/bin/tests/test_nudge_finalize.py -v` → FAIL.

- [ ] **Step 3: Implement**

Append to `meta_hook.py`:

```python
@register("nudge_finalize")
def nudge_finalize(payload: dict[str, Any]) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    stage = manifest_stage(ws)
    MID_FLIGHT = {"2-reentry-seeded", "2-dialog-started"}
    if stage not in MID_FLIGHT:
        return {}
    disabled, ov = is_disabled("nudge_finalize", ws)
    if disabled:
        return {"systemMessage": f"nudge_finalize disabled: {ov}"}
    reasons = {
        "2-reentry-seeded": (
            "Stage 2 re-entry is seeded but not finalized. "
            "Complete the scoped dialog in transcript.md, then run "
            "`meta-compiler elicit-vision --finalize`. "
            "If you mean to pause and resume later, say so explicitly."
        ),
        "2-dialog-started": (
            "Stage 2 dialog is in progress. Finalize with "
            "`meta-compiler elicit-vision --finalize` or document the pause."
        ),
    }
    audit(ws, "nudge_finalize", "Stop", "block",
          reason=f"stage={stage}")
    return {"decision": "block", "reason": reasons[stage]}
```

- [ ] **Step 4: Run tests.** `pytest .github/hooks/bin/tests/ -v` → all pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_nudge_finalize.py
git commit -m "hooks: add nudge_finalize to block Stop on mid-flight Stage 2"
```

---

### Task 8: Implement `gate_reentry_request`

**Files:**
- Modify: `.github/hooks/bin/meta_hook.py`
- Create: `.github/hooks/bin/tests/test_gate_reentry_request.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gate_reentry_request.py
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
```

- [ ] **Step 2: Run to verify failure.** FAIL expected.

- [ ] **Step 3: Implement**

Append to `meta_hook.py`:

```python
import hashlib as _hashlib


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return _hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@register("gate_reentry_request")
def gate_reentry_request(payload: dict[str, Any]) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if "stage2-reentry" not in cmd or not cmd.strip().startswith("meta-compiler"):
        return {"permissionDecision": "allow"}

    # NOT honored by META_COMPILER_SKIP_HOOK — explicitly non-skippable.
    req_path = ws / "workspace-artifacts" / "runtime" / "stage2" / "reentry_request.yaml"
    ps_path = ws / "PROBLEM_STATEMENT.md"

    def _deny(msg: str) -> dict[str, Any]:
        line = audit(ws, "gate_reentry_request", "PreToolUse", "deny",
                     reason=msg, extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": msg,
            "remediation": (
                "Complete Step 0 of .github/prompts/stage2-reentry.prompt.md "
                "(problem-space dialog + reentry_request.yaml authoring) before invoking the CLI."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    if not req_path.exists():
        return _deny("reentry_request.yaml is missing.")

    try:
        parsed = _parse_yaml_subset(req_path.read_text(encoding="utf-8"))
        req = parsed.get("stage2_reentry_request") or {}
        ps_block = req.get("problem_statement") or {}
        prev_sha = ps_block.get("previously_ingested_sha256")
        cur_sha_claimed = ps_block.get("current_sha256")
        updated = bool(ps_block.get("updated"))
    except Exception as e:
        return _deny(f"reentry_request.yaml parse error: {e}")

    if not prev_sha or not cur_sha_claimed:
        return _deny("reentry_request.yaml is missing problem_statement sha256 fields.")

    live_sha = _sha256_file(ps_path)
    if live_sha is None:
        return _deny("PROBLEM_STATEMENT.md is missing on disk.")

    if live_sha != cur_sha_claimed:
        return _deny(
            f"reentry_request.yaml claims current_sha256={cur_sha_claimed[:8]}... "
            f"but PROBLEM_STATEMENT.md is {live_sha[:8]}.... "
            "Re-author the request after any edits."
        )

    if updated and live_sha == prev_sha:
        return _deny(
            "reentry_request.yaml says problem_statement.updated=true, "
            "but the live SHA equals previously_ingested_sha256. No edit actually occurred."
        )

    if not updated and live_sha != prev_sha:
        return _deny(
            "reentry_request.yaml says problem_statement.updated=false, "
            "but the live SHA differs from previously_ingested_sha256. Set updated=true "
            "and record rationale, or revert PROBLEM_STATEMENT.md."
        )

    audit(ws, "gate_reentry_request", "PreToolUse", "allow",
          extra={"command": cmd, "parent_version": req.get("parent_version")})
    return {"permissionDecision": "allow"}
```

- [ ] **Step 4: Run tests.** All pass.

- [ ] **Step 5: Commit**

```bash
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_gate_reentry_request.py
git commit -m "hooks: add non-overridable gate_reentry_request for Step 0 enforcement"
```

---

### Task 9: Implement artifact-presence checks (`gate_orchestrator_mode`, `require_verdict`, `gate_ingest_workplan`, `require_ingest_report`, `require_handoff`)

**Files:**
- Modify: `.github/hooks/bin/meta_hook.py`
- Create: `.github/hooks/bin/tests/test_artifact_presence.py`

Five very-similar checks. Grouped into one task because each is a ~15-line function with the same shape (deny if path missing, allow otherwise). Tests parametrized.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_artifact_presence.py
import pytest

from .test_utils import _build_workspace


@pytest.mark.parametrize("check,required_rel_path,event", [
    ("gate_orchestrator_mode_preflight",
     "workspace-artifacts/runtime/stage2/precheck_request.yaml",
     "PreToolUse"),
    ("gate_orchestrator_mode_postflight",
     "workspace-artifacts/runtime/stage2/postcheck_request.yaml",
     "PreToolUse"),
    ("require_verdict_preflight",
     "workspace-artifacts/runtime/stage2/precheck_verdict.yaml",
     "SubagentStop"),
    ("require_verdict_postflight",
     "workspace-artifacts/runtime/stage2/postcheck_verdict.yaml",
     "SubagentStop"),
    ("gate_ingest_workplan",
     "workspace-artifacts/runtime/ingest/work_plan.yaml",
     "PreToolUse"),
    ("require_ingest_report",
     "workspace-artifacts/wiki/reports/ingest_report.yaml",
     "SubagentStop"),
    ("require_handoff",
     "workspace-artifacts/wiki/reviews/1a2_handoff.yaml",
     "SubagentStop"),
])
def test_deny_when_missing(check, required_rel_path, event, run_hook, tmp_path):
    _build_workspace(tmp_path)
    rc, out, _ = run_hook(check, {"hookEventName": event}, cwd=tmp_path)
    assert out.get("permissionDecision") == "deny" or out.get("decision") == "block"


@pytest.mark.parametrize("check,required_rel_path,event,deny_mode", [
    ("gate_orchestrator_mode_preflight",
     "workspace-artifacts/runtime/stage2/precheck_request.yaml", "PreToolUse", "deny"),
    ("require_verdict_preflight",
     "workspace-artifacts/runtime/stage2/precheck_verdict.yaml", "SubagentStop", "block"),
    ("gate_ingest_workplan",
     "workspace-artifacts/runtime/ingest/work_plan.yaml", "PreToolUse", "deny"),
    ("require_handoff",
     "workspace-artifacts/wiki/reviews/1a2_handoff.yaml", "SubagentStop", "block"),
])
def test_allow_when_present(check, required_rel_path, event, deny_mode, run_hook, tmp_path):
    _build_workspace(tmp_path)
    target = tmp_path / required_rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if "verdict" in required_rel_path or "handoff" in required_rel_path:
        target.write_text("verdict: PROCEED\ndecision: PROCEED\n")
    else:
        target.write_text("placeholder: true\n")
    rc, out, _ = run_hook(check, {"hookEventName": event}, cwd=tmp_path)
    assert out.get("permissionDecision") != "deny"
    assert out.get("decision") != "block"


def test_require_verdict_rejects_missing_decision_field(run_hook, tmp_path):
    _build_workspace(tmp_path)
    target = tmp_path / "workspace-artifacts/runtime/stage2/precheck_verdict.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("something_else: true\n")
    rc, out, _ = run_hook("require_verdict_preflight", {"hookEventName": "SubagentStop"}, cwd=tmp_path)
    assert out.get("decision") == "block"
```

- [ ] **Step 2: Run to verify failure.** FAIL.

- [ ] **Step 3: Implement**

Append to `meta_hook.py`:

```python
def _presence_check(
    payload: dict[str, Any],
    check_name: str,
    rel_path: str,
    event: str,
    missing_reason: str,
    missing_remediation: str,
    require_field: str | None = None,
    deny_mode: str = "deny",
) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    target = ws / rel_path
    if not target.exists():
        line = audit(ws, check_name, event, deny_mode, reason=missing_reason)
        if deny_mode == "block":
            return {
                "decision": "block",
                "reason": f"{missing_reason} {missing_remediation}",
            }
        return {
            "permissionDecision": "deny",
            "reason": missing_reason,
            "remediation": missing_remediation,
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }
    if require_field:
        try:
            parsed = _parse_yaml_subset(target.read_text(encoding="utf-8"))
        except Exception as e:
            return {"decision": "block", "reason": f"{target} parse error: {e}"}
        if not _has_field_anywhere(parsed, require_field):
            audit(ws, check_name, event, deny_mode,
                  reason=f"{target.name} missing field '{require_field}'")
            if deny_mode == "block":
                return {
                    "decision": "block",
                    "reason": (
                        f"{target.name} is missing required field '{require_field}'. "
                        f"{missing_remediation}"
                    ),
                }
            return {
                "permissionDecision": "deny",
                "reason": f"{target.name} is missing required field '{require_field}'.",
                "remediation": missing_remediation,
            }
    return {"permissionDecision": "allow"} if deny_mode == "deny" else {}


def _has_field_anywhere(obj: Any, field: str) -> bool:
    if isinstance(obj, dict):
        if field in obj and obj[field] is not None:
            return True
        return any(_has_field_anywhere(v, field) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_field_anywhere(v, field) for v in obj)
    return False


@register("gate_orchestrator_mode_preflight")
def gate_orchestrator_mode_preflight(payload):
    return _presence_check(
        payload, "gate_orchestrator_mode_preflight",
        "workspace-artifacts/runtime/stage2/precheck_request.yaml",
        "PreToolUse",
        "precheck_request.yaml is missing.",
        "Run `meta-compiler elicit-vision --start` or `meta-compiler stage2-reentry --from-request ...` first.",
    )


@register("gate_orchestrator_mode_postflight")
def gate_orchestrator_mode_postflight(payload):
    return _presence_check(
        payload, "gate_orchestrator_mode_postflight",
        "workspace-artifacts/runtime/stage2/postcheck_request.yaml",
        "PreToolUse",
        "postcheck_request.yaml is missing.",
        "Run `meta-compiler elicit-vision --finalize` before invoking postflight.",
    )


@register("require_verdict_preflight")
def require_verdict_preflight(payload):
    return _presence_check(
        payload, "require_verdict_preflight",
        "workspace-artifacts/runtime/stage2/precheck_verdict.yaml",
        "SubagentStop",
        "precheck_verdict.yaml was not written.",
        "Preflight must write its verdict before stopping.",
        require_field="verdict",
        deny_mode="block",
    )


@register("require_verdict_postflight")
def require_verdict_postflight(payload):
    return _presence_check(
        payload, "require_verdict_postflight",
        "workspace-artifacts/runtime/stage2/postcheck_verdict.yaml",
        "SubagentStop",
        "postcheck_verdict.yaml was not written.",
        "Postflight must write its verdict before stopping.",
        require_field="verdict",
        deny_mode="block",
    )


@register("gate_ingest_workplan")
def gate_ingest_workplan(payload):
    return _presence_check(
        payload, "gate_ingest_workplan",
        "workspace-artifacts/runtime/ingest/work_plan.yaml",
        "PreToolUse",
        "ingest work_plan.yaml is missing.",
        "Run `meta-compiler ingest --scope all` (or `--scope new`) before the orchestrator fans out.",
    )


@register("require_ingest_report")
def require_ingest_report(payload):
    return _presence_check(
        payload, "require_ingest_report",
        "workspace-artifacts/wiki/reports/ingest_report.yaml",
        "SubagentStop",
        "ingest_report.yaml was not written.",
        "The ingest-orchestrator must write the run summary before stopping.",
        deny_mode="block",
    )


@register("require_handoff")
def require_handoff(payload):
    return _presence_check(
        payload, "require_handoff",
        "workspace-artifacts/wiki/reviews/1a2_handoff.yaml",
        "SubagentStop",
        "1a2_handoff.yaml was not written.",
        "The stage-1a2-orchestrator must record its handoff decision before stopping.",
        require_field="decision",
        deny_mode="block",
    )
```

- [ ] **Step 4: Run tests.** All pass.

- [ ] **Step 5: Commit**

```bash
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_artifact_presence.py
git commit -m "hooks: add 7 artifact-presence checks (orchestrator/verdict/ingest/handoff)"
```

---

### Task 10: Implement `validate_findings_schema`

**Files:**
- Modify: `.github/hooks/bin/meta_hook.py`
- Create: `.github/hooks/bin/tests/test_findings_schema.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_findings_schema.py
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
```

- [ ] **Step 2: Run to verify failure.** FAIL.

- [ ] **Step 3: Implement**

Append to `meta_hook.py`:

```python
FINDINGS_SCHEMA_REQUIRED: list[str] = ["source_id", "findings"]
FINDINGS_ITEM_REQUIRED: list[str] = ["claim", "quote", "location"]


@register("validate_findings_schema")
def validate_findings_schema(payload: dict[str, Any]) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    fp_str = (payload.get("tool_input") or {}).get("file_path") or ""
    if not fp_str:
        return {"permissionDecision": "allow"}
    try:
        fp = Path(fp_str).resolve()
        rel = fp.relative_to(ws).as_posix()
    except (ValueError, OSError):
        return {"permissionDecision": "allow"}
    if not rel.startswith("workspace-artifacts/wiki/findings/") or not rel.endswith(".json"):
        return {"permissionDecision": "allow"}
    if rel.endswith("/index.yaml") or rel.endswith("/index.json"):
        return {"permissionDecision": "allow"}

    def _deny(msg: str) -> dict[str, Any]:
        line = audit(ws, "validate_findings_schema", "PostToolUse", "deny",
                     reason=msg, extra={"file_path": rel})
        return {
            "permissionDecision": "deny",
            "reason": msg,
            "remediation": "Repair the findings JSON before the file is committed to wiki/findings/.",
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return _deny(f"{fp.name} is not valid JSON: {e}")

    if not isinstance(data, dict):
        return _deny(f"{fp.name} top-level is not an object.")
    for field in FINDINGS_SCHEMA_REQUIRED:
        if field not in data:
            return _deny(f"{fp.name} missing required field '{field}'.")
    findings = data.get("findings")
    if not isinstance(findings, list):
        return _deny(f"{fp.name}: 'findings' must be a list.")
    for i, item in enumerate(findings):
        if not isinstance(item, dict):
            return _deny(f"{fp.name}: findings[{i}] is not an object.")
        for field in FINDINGS_ITEM_REQUIRED:
            if field not in item:
                return _deny(f"{fp.name}: findings[{i}] missing '{field}'.")
    return {"permissionDecision": "allow"}
```

- [ ] **Step 4: Run tests.** All pass.

- [ ] **Step 5: Commit**

```bash
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_findings_schema.py
git commit -m "hooks: add validate_findings_schema for seed-reader write-time validation"
```

---

### Task 11: Implement chain runner + `user_prompt_submit_dispatch` + `subagent_stop_dispatch`

**Files:**
- Modify: `.github/hooks/bin/meta_hook.py`
- Create: `.github/hooks/bin/tests/test_chain_runner.py`
- Create: `.github/hooks/bin/tests/test_prompt_triggers.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chain_runner.py
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


# tests/test_prompt_triggers.py
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
```

- [ ] **Step 2: Run to verify failure.** FAIL.

- [ ] **Step 3: Implement**

Append to `meta_hook.py`:

```python
import subprocess as _subprocess


PROMPT_TRIGGERS: dict[str, list[str]] = {
    # Maps /<prompt-name> → ordered chain of shell commands to run on UserPromptSubmit.
    # Empty list = match the slash but don't run (useful for gating).
    "stage-1a-breadth": ["meta-compiler ingest --scope all"],
    "stage-2-dialog": ["meta-compiler elicit-vision --start"],
    "stage-3-scaffold": [
        "meta-compiler scaffold",
        "meta-compiler validate-stage --stage 3",
    ],
    "stage-4-finalize": [
        "meta-compiler phase4-finalize",
        "meta-compiler validate-stage --stage 4",
    ],
}

SUBAGENT_STOP_CHAINS: dict[str, list[str]] = {
    # Maps agent name → ordered chain of shell commands to run on SubagentStop.
    "ingest-orchestrator": [
        "meta-compiler ingest-validate",
        "meta-compiler research-breadth",
        "meta-compiler validate-stage --stage 1a",
    ],
    "stage-1a2-orchestrator": [
        "meta-compiler research-depth",
        "meta-compiler validate-stage --stage 1b",
        "meta-compiler review",
        "meta-compiler validate-stage --stage 1c",
    ],
}


def _run_chain(commands: list[str], cwd: Path) -> tuple[list[str], str | None]:
    """Run commands sequentially. Return (per-step output list, failure-message or None).
    On failure, stops; subsequent commands are not run."""
    outputs: list[str] = []
    env = dict(os.environ)
    env["META_COMPILER_SKIP_HOOK"] = "1"
    for cmd in commands:
        # Allow test override
        if os.environ.get("META_COMPILER_TEST_CHAIN"):
            test_cmds = os.environ["META_COMPILER_TEST_CHAIN"].split(";")
            for tc in test_cmds:
                try:
                    r = _subprocess.run(tc, shell=True, cwd=str(cwd), env=env,
                                        capture_output=True, text=True, timeout=60)
                    if r.returncode != 0:
                        return outputs, f"`{tc}` failed (rc={r.returncode}): {r.stderr.strip()}"
                    outputs.append(f"$ {tc}\n{r.stdout.strip()}")
                except Exception as e:
                    return outputs, f"`{tc}` raised: {e}"
            return outputs, None
        try:
            r = _subprocess.run(cmd, shell=True, cwd=str(cwd), env=env,
                                capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return outputs, f"`{cmd}` failed (rc={r.returncode}): {r.stderr.strip()}"
            outputs.append(f"$ {cmd}\n{r.stdout.strip()}")
        except Exception as e:
            return outputs, f"`{cmd}` raised: {e}"
    return outputs, None


def _detect_prompt_slash(prompt_text: str) -> str | None:
    """Extract /<name> at start of user prompt. Returns name or None."""
    stripped = prompt_text.lstrip()
    if not stripped.startswith("/"):
        return None
    first_token = stripped.split()[0][1:]  # drop leading /
    return first_token


@register("user_prompt_submit_dispatch")
def user_prompt_submit_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    prompt_text = (payload.get("tool_input") or {}).get("prompt") or ""
    slash = _detect_prompt_slash(prompt_text)
    if slash is None:
        return {}
    # Test-mode hook: match any /__test_chain__ or real triggers
    if slash == "__test_chain__" or slash in PROMPT_TRIGGERS:
        commands = PROMPT_TRIGGERS.get(slash, [])
        outputs, failure = _run_chain(commands, ws)
        body_parts: list[str] = []
        if outputs:
            body_parts.append("Auto-fired chain output:")
            for o in outputs:
                body_parts.append("```\n" + o + "\n```")
        result: dict[str, Any] = {}
        if body_parts:
            result["additionalContext"] = "\n\n".join(body_parts)
        if failure:
            result["systemMessage"] = f"Chain failed: {failure}"
        audit(ws, "user_prompt_submit_dispatch", "UserPromptSubmit",
              "chain_ok" if not failure else "chain_failed",
              reason=slash, extra={"failure": failure})
        return result
    return {}


@register("subagent_stop_dispatch")
def subagent_stop_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    # The agent name comes from the hook event; exact field name is
    # subagent_id or similar depending on runtime. Accept either.
    agent = (
        payload.get("subagent_id")
        or payload.get("agent_name")
        or (payload.get("tool_input") or {}).get("agent_name")
        or ""
    )
    if agent not in SUBAGENT_STOP_CHAINS:
        return {}
    commands = SUBAGENT_STOP_CHAINS[agent]
    outputs, failure = _run_chain(commands, ws)
    result: dict[str, Any] = {}
    if outputs:
        result["additionalContext"] = "Post-subagent chain output:\n" + "\n".join(
            "```\n" + o + "\n```" for o in outputs
        )
    if failure:
        result["systemMessage"] = f"Chain failed: {failure}"
    audit(ws, "subagent_stop_dispatch", "SubagentStop",
          "chain_ok" if not failure else "chain_failed",
          reason=agent, extra={"failure": failure})
    return result
```

- [ ] **Step 4: Run all tests.** `pytest .github/hooks/bin/tests/ -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add .github/hooks/bin/meta_hook.py .github/hooks/bin/tests/test_chain_runner.py .github/hooks/bin/tests/test_prompt_triggers.py
git commit -m "hooks: add chain runner + UserPromptSubmit/SubagentStop dispatchers"
```

---

## Phase 2 — CLI Hardening

Changes to `meta_compiler/` that stand alone (no hook dependency).

### Task 12: Add `_check_reentry_block_freshness` helper

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/meta_compiler/stages/elicit_stage.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/tests/test_stage2_reentry_freshness.py`

- [ ] **Step 1: Read the existing elicit_stage.py to identify insertion points**

Run: `grep -n "def " /Users/christianstokes/Downloads/META-COMPILER/meta_compiler/stages/elicit_stage.py | head -30`

Identify the location of `run_elicit_vision_finalize` and the `DecisionBlock` dataclass (or the parser's return type).

- [ ] **Step 2: Write failing test**

Create `tests/test_stage2_reentry_freshness.py`:

```python
"""Tests for _check_reentry_block_freshness in elicit_stage."""
from meta_compiler.stages.elicit_stage import _check_reentry_block_freshness


def _block(title, section):
    # Adjust attribute names to match the real DecisionBlock dataclass
    class _B:
        pass
    b = _B()
    b.title = title
    b.section = section
    return b


def test_fresh_block_in_every_revised_section_passes():
    parent = {
        "decision_log": {
            "architecture": [{"component": "old-comp", "approach": "x"}],
            "requirements": [{"description": "old req"}],
        }
    }
    cascade = {"cascade_report": {"revised_sections": ["architecture", "requirements"]}}
    blocks = [
        _block("new-comp", "architecture"),
        _block("REQ — new", "requirements"),
    ]
    issues = _check_reentry_block_freshness(blocks, cascade, parent)
    assert issues == []


def test_empty_revised_section_fails():
    parent = {
        "decision_log": {
            "architecture": [{"component": "old-comp"}],
        }
    }
    cascade = {"cascade_report": {"revised_sections": ["architecture"]}}
    blocks = [_block("old-comp", "architecture")]  # same title as parent — not fresh
    issues = _check_reentry_block_freshness(blocks, cascade, parent)
    assert len(issues) == 1
    assert "architecture" in issues[0]


def test_scope_revision_satisfied_by_scope_in_or_scope_out():
    parent = {"decision_log": {"scope": {"in_scope": [{"item": "old"}], "out_of_scope": []}}}
    cascade = {"cascade_report": {"revised_sections": ["scope"]}}
    blocks = [_block("new-item", "scope-in")]
    issues = _check_reentry_block_freshness(blocks, cascade, parent)
    assert issues == []


def test_mixed_fresh_and_stale_reports_only_stale():
    parent = {
        "decision_log": {
            "architecture": [{"component": "old-a"}],
            "conventions": [{"name": "old-c"}],
        }
    }
    cascade = {"cascade_report": {"revised_sections": ["architecture", "conventions"]}}
    blocks = [
        _block("new-a", "architecture"),
        _block("old-c", "conventions"),  # stale
    ]
    issues = _check_reentry_block_freshness(blocks, cascade, parent)
    assert len(issues) == 1
    assert "conventions" in issues[0]
```

- [ ] **Step 3: Run to verify failure.** `pytest tests/test_stage2_reentry_freshness.py -v` → FAIL (function missing).

- [ ] **Step 4: Implement `_check_reentry_block_freshness`**

Open `/Users/christianstokes/Downloads/META-COMPILER/meta_compiler/stages/elicit_stage.py`. Locate the `run_elicit_vision_finalize` function. Add this helper function immediately above it:

```python
def _check_reentry_block_freshness(
    transcript_blocks: list,
    cascade_report: dict,
    parent_log: dict,
) -> list[str]:
    """For each revised section in the cascade report, require >=1 block
    with a title that does NOT appear in the parent Decision Log.

    Returns list of issue strings, one per empty revised section. Empty
    list means pass. Called only when re-entry is detected.
    """
    revised = set(
        (cascade_report.get("cascade_report") or {}).get("revised_sections") or []
    )
    if not revised:
        return []

    dl = parent_log.get("decision_log") or {}
    issues: list[str] = []

    # Map revised-section name -> set of prior titles in that section.
    def _titles_for_revised(section: str) -> set[str]:
        titles: set[str] = set()
        if section == "conventions":
            for row in dl.get("conventions") or []:
                titles.add(str(row.get("name") or row.get("choice") or ""))
        elif section == "architecture":
            for row in dl.get("architecture") or []:
                titles.add(str(row.get("component") or ""))
        elif section == "scope":
            scope = dl.get("scope") or {}
            for row in (scope.get("in_scope") or []) + (scope.get("out_of_scope") or []):
                titles.add(str(row.get("item") or ""))
        elif section == "requirements":
            for row in dl.get("requirements") or []:
                titles.add(str(row.get("id") or row.get("description") or ""))
        elif section == "open_items":
            for row in dl.get("open_items") or []:
                titles.add(str(row.get("description") or ""))
        elif section == "agents_needed":
            for row in dl.get("agents_needed") or []:
                titles.add(str(row.get("role") or ""))
        return {t for t in titles if t}

    def _block_matches_revised(block_section: str, revised_name: str) -> bool:
        if revised_name == "scope":
            return block_section in {"scope-in", "scope-out"}
        return block_section == revised_name

    for section in sorted(revised):
        prior_titles = _titles_for_revised(section)
        has_fresh = False
        for b in transcript_blocks:
            b_section = getattr(b, "section", "") or ""
            b_title = getattr(b, "title", "") or ""
            if not _block_matches_revised(b_section, section):
                continue
            if b_title and b_title not in prior_titles:
                has_fresh = True
                break
        if not has_fresh:
            issues.append(
                f"Revised section '{section}' has no fresh decision block in the transcript. "
                f"Add at least one decision block under that section whose title differs from the prior log."
            )
    return issues
```

- [ ] **Step 5: Run tests.** `pytest tests/test_stage2_reentry_freshness.py -v` → all pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
git add meta_compiler/stages/elicit_stage.py tests/test_stage2_reentry_freshness.py
git commit -m "elicit: add _check_reentry_block_freshness for re-entry block diff"
```

---

### Task 13: Wire block-freshness check into `run_elicit_vision_finalize`

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/meta_compiler/stages/elicit_stage.py`
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/tests/test_stage2_reentry.py` (existing)

- [ ] **Step 1: Read existing `run_elicit_vision_finalize` to identify re-entry detection point**

Run: `grep -n "reentry\|re-entry\|2-reentry" /Users/christianstokes/Downloads/META-COMPILER/meta_compiler/stages/elicit_stage.py`

Identify where `last_completed_stage == "2-reentry-seeded"` is checked (or add detection).

- [ ] **Step 2: Write failing integration test**

Add to `/Users/christianstokes/Downloads/META-COMPILER/tests/test_stage2_reentry.py`:

```python
def test_finalize_fails_when_revised_section_has_no_fresh_block(tmp_path):
    """After stage2-reentry seeds the transcript, running --finalize without
    authoring new blocks under revised sections exits nonzero."""
    from meta_compiler.stages.stage2_reentry import run_stage2_reentry
    from meta_compiler.stages.elicit_stage import run_elicit_vision_finalize
    from meta_compiler.artifacts import build_paths, ensure_layout, save_manifest

    artifacts_root = tmp_path / "workspace-artifacts"
    ws_root = tmp_path

    # Seed a v1 decision log + manifest so re-entry has something to carry forward.
    # (Use existing test helpers in this file to construct the fixture.)
    # ... set up manifest to "2" and write a minimal decision_log_v1.yaml ...

    # Run stage2-reentry to seed the transcript
    run_stage2_reentry(
        artifacts_root=artifacts_root,
        workspace_root=ws_root,
        reason="test",
        sections=["architecture"],
    )

    # Do NOT modify transcript; finalize should fail
    import pytest
    with pytest.raises(RuntimeError) as exc:
        run_elicit_vision_finalize(
            artifacts_root=artifacts_root,
            workspace_root=ws_root,
        )
    assert "architecture" in str(exc.value)
```

*(Engineer: the test fixture setup is project-specific; reuse helpers from the existing `test_stage2_reentry.py`. If those helpers do not exist in reusable form, extract them into a shared `_build_v1_decision_log(tmp_path)` helper inside the test module as part of this step.)*

- [ ] **Step 3: Run to verify failure.** Test should error.

- [ ] **Step 4: Wire the check into `run_elicit_vision_finalize`**

In `elicit_stage.py`, inside `run_elicit_vision_finalize`, after parsing transcript blocks and before compiling the Decision Log, add:

```python
# Re-entry mode: enforce block-freshness against parent log and cascade report.
manifest = load_manifest(paths)
research = (manifest or {}).get("workspace_manifest", {}).get("research", {}) or {}
is_reentry = research.get("last_completed_stage") == "2-reentry-seeded"
if is_reentry:
    latest = latest_decision_log_path(paths)
    if latest is None:
        raise RuntimeError(
            "Re-entry state detected but no parent Decision Log exists."
        )
    _, parent_path = latest
    parent_log = load_yaml(parent_path) or {}
    cascade_path = paths.stage2_runtime_dir / f"cascade_report_v{research.get('reentry_version')}.yaml"
    cascade_report = load_yaml(cascade_path) if cascade_path.exists() else {}

    freshness_issues = _check_reentry_block_freshness(
        transcript_blocks=blocks,  # adjust variable name to local parser output
        cascade_report=cascade_report,
        parent_log=parent_log,
    )
    if freshness_issues:
        raise RuntimeError(
            "Re-entry block-freshness check failed:\n"
            + "\n".join(f"  - {issue}" for issue in freshness_issues)
        )
```

*(Engineer: the local variable name holding parsed blocks inside `run_elicit_vision_finalize` may be different. Adjust to match the actual name returned by `parse_decision_blocks` or equivalent.)*

- [ ] **Step 5: After `run_elicit_vision_finalize` succeeds, clear re-entry state**

Find the successful-return path in `run_elicit_vision_finalize`. After the manifest update, add:

```python
if is_reentry:
    wm = manifest["workspace_manifest"]
    research = wm.setdefault("research", {})
    research["last_completed_stage"] = "2"
    research.pop("reentry_version", None)
    save_manifest(paths, manifest)
```

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest tests/ -v`

Expected: all pass including the new freshness test.

- [ ] **Step 7: Commit**

```bash
git add meta_compiler/stages/elicit_stage.py tests/test_stage2_reentry.py
git commit -m "elicit: enforce block-freshness on --finalize when re-entry state detected"
```

---

### Task 14: Add `--from-request` flag and `brief.md` + `precheck_request.yaml` emission to `stage2-reentry`

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/meta_compiler/stages/stage2_reentry.py`
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/meta_compiler/cli.py`
- Create: `/Users/christianstokes/Downloads/META-COMPILER/tests/test_stage2_reentry_request.py`

- [ ] **Step 1: Identify where the `stage2-reentry` CLI args are parsed**

Run: `grep -n "stage2-reentry\|stage2_reentry" /Users/christianstokes/Downloads/META-COMPILER/meta_compiler/cli.py`

- [ ] **Step 2: Write failing tests**

Create `tests/test_stage2_reentry_request.py`:

```python
"""Tests for --from-request flag + brief/precheck_request emission."""
import hashlib
import yaml
from pathlib import Path

from meta_compiler.stages.stage2_reentry import run_stage2_reentry


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_v1_workspace(tmp_path):
    """Create minimal v1 fixture: manifest + decision_log_v1.yaml + PROBLEM_STATEMENT.md."""
    ws = tmp_path
    artifacts = ws / "workspace-artifacts"
    (artifacts / "manifests").mkdir(parents=True)
    (artifacts / "decision-logs").mkdir(parents=True)
    (artifacts / "runtime" / "stage2").mkdir(parents=True)
    ps = ws / "PROBLEM_STATEMENT.md"
    ps_content = "Problem v1\n"
    ps.write_text(ps_content)
    (artifacts / "manifests" / "workspace_manifest.yaml").write_text(
        "workspace_manifest:\n"
        "  project_name: test\n"
        "  problem_domain: x\n"
        "  project_type: research\n"
        "  research:\n"
        "    last_completed_stage: '2'\n"
        "  decision_logs: []\n"
        "  seeds: []\n"
    )
    (artifacts / "decision-logs" / "decision_log_v1.yaml").write_text(
        "decision_log:\n"
        "  meta:\n"
        "    version: 1\n"
        "  architecture:\n"
        "    - component: old-comp\n"
        "      approach: x\n"
        "  conventions: []\n"
        "  scope: {in_scope: [], out_of_scope: []}\n"
        "  requirements: []\n"
        "  open_items: []\n"
        "  agents_needed: []\n"
    )
    return ws, artifacts, _sha(ps_content)


def _write_request(ws, prev_sha, cur_sha, updated=False, reason="scope changed",
                   revised_sections=None):
    revised = revised_sections or ["architecture"]
    path = ws / "workspace-artifacts" / "runtime" / "stage2" / "reentry_request.yaml"
    path.write_text(
        "stage2_reentry_request:\n"
        "  parent_version: 1\n"
        f"  problem_change_summary: 'summary'\n"
        "  problem_statement:\n"
        f"    previously_ingested_sha256: {prev_sha}\n"
        f"    current_sha256: {cur_sha}\n"
        f"    updated: {'true' if updated else 'false'}\n"
        f"    update_rationale: rationale\n"
        f"  reason: '{reason}'\n"
        "  revised_sections:\n"
        + "".join(f"    - {s}\n" for s in revised)
        + "  carried_consistency_risks: []\n"
    )
    return path


def test_from_request_derives_reason_and_sections(tmp_path):
    ws, artifacts, sha = _make_v1_workspace(tmp_path)
    req = _write_request(ws, sha, sha, reason="derived-reason", revised_sections=["architecture"])
    result = run_stage2_reentry(
        artifacts_root=artifacts,
        workspace_root=ws,
        reason=None,
        sections=None,
        from_request=req,
    )
    assert result["sections_to_revise"] == ["architecture"]
    assert "derived-reason" in result.get("reason", "derived-reason")


def test_conflict_between_request_and_flags_errors(tmp_path):
    ws, artifacts, sha = _make_v1_workspace(tmp_path)
    req = _write_request(ws, sha, sha)
    import pytest
    with pytest.raises(RuntimeError):
        run_stage2_reentry(
            artifacts_root=artifacts,
            workspace_root=ws,
            reason="conflict",
            sections=["conventions"],  # conflicts with request's [architecture]
            from_request=req,
        )


def test_brief_and_precheck_request_written(tmp_path):
    ws, artifacts, sha = _make_v1_workspace(tmp_path)
    req = _write_request(ws, sha, sha)
    run_stage2_reentry(
        artifacts_root=artifacts,
        workspace_root=ws,
        reason=None,
        sections=None,
        from_request=req,
    )
    brief = artifacts / "runtime" / "stage2" / "brief.md"
    precheck = artifacts / "runtime" / "stage2" / "precheck_request.yaml"
    assert brief.exists()
    assert "Re-entry context" in brief.read_text()
    assert precheck.exists()
    precheck_data = yaml.safe_load(precheck.read_text())
    assert "reentry" in precheck_data.get("stage2_precheck_request", {})


def test_old_flag_signature_still_works(tmp_path):
    ws, artifacts, sha = _make_v1_workspace(tmp_path)
    result = run_stage2_reentry(
        artifacts_root=artifacts,
        workspace_root=ws,
        reason="legacy",
        sections=["architecture"],
    )
    assert result["sections_to_revise"] == ["architecture"]
```

- [ ] **Step 3: Run to verify failure.** FAIL.

- [ ] **Step 4: Update `run_stage2_reentry` signature and add artifact emission**

Edit `meta_compiler/stages/stage2_reentry.py`. Change the `run_stage2_reentry` signature:

```python
def run_stage2_reentry(
    artifacts_root: Path,
    workspace_root: Path,
    reason: str | None = None,
    sections: list[str] | None = None,
    from_request: Path | None = None,
) -> dict[str, Any]:
```

Add at the top of the function body, before the manifest check:

```python
    # If --from-request passed, derive reason and sections from the artifact.
    request_data: dict[str, Any] | None = None
    if from_request is not None:
        if not from_request.exists():
            raise RuntimeError(f"--from-request path does not exist: {from_request}")
        request_data = load_yaml(from_request) or {}
        req = request_data.get("stage2_reentry_request") or {}
        artifact_reason = req.get("reason")
        artifact_sections = req.get("revised_sections") or []
        if reason is not None and artifact_reason is not None and reason != artifact_reason:
            raise RuntimeError(
                f"--reason='{reason}' conflicts with request.reason='{artifact_reason}'. "
                "Pass one or the other, or align them."
            )
        if sections and artifact_sections and set(sections) != set(artifact_sections):
            raise RuntimeError(
                f"--sections={sections} conflicts with request.revised_sections={artifact_sections}."
            )
        reason = reason or artifact_reason
        sections = sections or list(artifact_sections)

    if not reason:
        raise RuntimeError("reason is required (via --reason or request.reason).")
    if not sections:
        raise RuntimeError("sections are required (via --sections or request.revised_sections).")
```

After the transcript is written (before updating the manifest), add:

```python
    # Emit brief.md for the orchestrator preflight
    brief_lines = [
        f"# Stage 2 Brief — Re-entry v{new_version}",
        "",
        f"Generated: {generated_at}",
        f"Decision Log version (parent): v{prior_version}",
        "",
        "## Where to look",
        "",
        "- PROBLEM_STATEMENT.md",
        "- workspace-artifacts/wiki/v2/index.md",
        "- workspace-artifacts/wiki/citations/index.yaml",
        f"- workspace-artifacts/decision-logs/decision_log_v{prior_version}.yaml (parent)",
        "",
        "## Re-entry context",
        "",
        f"- Revised sections: {', '.join(sections)}",
        f"- Revision reason: {reason}",
    ]
    if request_data is not None:
        req = request_data.get("stage2_reentry_request") or {}
        summary = req.get("problem_change_summary") or ""
        if summary:
            brief_lines += ["", "### Problem-change summary", "", summary]
        risks = req.get("carried_consistency_risks") or []
        if risks:
            brief_lines += ["", "### Carried consistency risks", ""]
            for r in risks:
                if isinstance(r, dict):
                    brief_lines.append(
                        f"- {r.get('prior_decision', '?')} ({r.get('section', '?')}): {r.get('concern', '')}"
                    )
    brief_lines += ["", "## Transcript path", "",
                    "workspace-artifacts/runtime/stage2/transcript.md"]
    paths.stage2_brief_path.write_text("\n".join(brief_lines) + "\n", encoding="utf-8")

    # Emit precheck_request.yaml for the orchestrator preflight
    precheck_payload: dict[str, Any] = {
        "stage2_precheck_request": {
            "generated_at": generated_at,
            "decision_log_version": new_version,
            "parent_version": prior_version,
            "mechanical_checks": [
                {"name": "parent_log_present", "result": "PASS"},
                {"name": "reentry_request_present",
                 "result": "PASS" if request_data else "SKIP"},
            ],
            "reentry": {
                "parent_version": prior_version,
                "revised_sections": sections,
                "reason": reason,
                "problem_change_summary": (
                    (request_data or {}).get("stage2_reentry_request", {}).get(
                        "problem_change_summary", ""
                    )
                ),
                "carried_consistency_risks": (
                    (request_data or {}).get("stage2_reentry_request", {}).get(
                        "carried_consistency_risks", []
                    )
                ),
            },
            "verdict_output_path": (
                "workspace-artifacts/runtime/stage2/precheck_verdict.yaml"
            ),
        }
    }
    dump_yaml(paths.stage2_precheck_request_path, precheck_payload)
```

Also update the `return` dict: include `reason` and point `next_step` at `stage2-reentry.prompt.md`:

```python
    return {
        "status": "transcript_seeded",
        "new_version": new_version,
        "parent_version": prior_version,
        "sections_to_revise": sections,
        "reason": reason,
        "cascade": cascade,
        "transcript_path": str(
            paths.stage2_transcript_path.relative_to(paths.root).as_posix()
        ),
        "brief_path": str(paths.stage2_brief_path.relative_to(paths.root).as_posix()),
        "precheck_request_path": str(
            paths.stage2_precheck_request_path.relative_to(paths.root).as_posix()
        ),
        "cascade_report_path": str(
            cascade_report_path.relative_to(paths.root).as_posix()
        ),
        "next_step": (
            "Open .github/prompts/stage2-reentry.prompt.md in your LLM runtime and walk "
            "its 6 steps. Step 1 is already complete (this call). "
            "Next: invoke @stage2-orchestrator mode=preflight."
        ),
    }
```

- [ ] **Step 5: Add `--from-request` to the CLI parser**

In `/Users/christianstokes/Downloads/META-COMPILER/meta_compiler/cli.py`, locate the `stage2-reentry` subparser definition. Add:

```python
p_stage2_reentry.add_argument(
    "--from-request",
    type=Path,
    default=None,
    help="Path to stage2 reentry_request.yaml. When passed, --reason and --sections are derived from the artifact.",
)
# Make --reason and --sections optional (they can come from the request)
# If they were previously required=True, change to required=False.
```

Update the dispatch:

```python
run_stage2_reentry(
    artifacts_root=artifacts_root,
    workspace_root=workspace_root,
    reason=args.reason,
    sections=(args.sections.split(",") if args.sections else None),
    from_request=args.from_request,
)
```

*(Engineer: if the existing code uses `argparse` with `required=True` on `--reason`/`--sections`, remove `required=True`. The new function body validates presence at the semantic level.)*

- [ ] **Step 6: Run tests.** `pytest tests/test_stage2_reentry_request.py tests/test_stage2_reentry.py -v` → all pass.

- [ ] **Step 7: Commit**

```bash
git add meta_compiler/stages/stage2_reentry.py meta_compiler/cli.py tests/test_stage2_reentry_request.py
git commit -m "stage2-reentry: add --from-request flag + emit brief.md + precheck_request.yaml"
```

---

## Phase 3 — Stage 2 Re-entry Prompt Rewrite

### Task 15: Rewrite `stage2-reentry.prompt.md` as a 6-step conductor prompt

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/.github/prompts/stage2-reentry.prompt.md`

- [ ] **Step 1: Replace the prompt wholesale**

Write to `/Users/christianstokes/Downloads/META-COMPILER/.github/prompts/stage2-reentry.prompt.md`:

```markdown
---
description: Stage 2 re-entry via prompt-as-conductor. Walk the six steps exactly. Step 0 produces reentry_request.yaml before any CLI fires. The CLI is the integrity layer; stage2-orchestrator audits both boundaries; you conduct the dialog and write new decision blocks only for revised sections.
---

# Stage 2 Re-entry: Scoped Revision

You are the Stage 2 re-entry conductor. Re-entry revises a prior Decision Log (v{N}) when scope, problem space, or decisions have shifted — without re-litigating settled choices. Revision is surgical: only sections flagged for change are revisited.

Walk this prompt top to bottom. Do not skip steps. Do not improvise sequencing. The `gate_reentry_request` hook will reject Step 1 if Step 0 has not produced a valid `reentry_request.yaml`.

## Prompt-as-Conductor Contract

Artifacts flow one direction: dialog → request artifact → CLI writes → you read → you converse → you write decision blocks → CLI compiles → agent audits. You never edit `decision_log_v{N+1}.yaml` directly.

---

## Step 0 — Re-ingest the problem space (LLM + human)

You must complete this step **before** invoking the CLI. No `META_COMPILER_SKIP_HOOK` override bypasses this.

### 0a. Orient

Read, in order:

- `PROBLEM_STATEMENT.md` (live intent)
- The latest Decision Log at `workspace-artifacts/decision-logs/decision_log_v{N}.yaml`
- `workspace-artifacts/wiki/v2/index.md` (current wiki state)

Establish what v{N} captured: conventions, architecture, scope, requirements, agents, open items.

### 0b. Dialog with the human

Ask, one at a time, narrowing the space:

- "What changed in your problem space since v{N}?"
- "Does `PROBLEM_STATEMENT.md` still describe what you're trying to build? Walk me through the parts that no longer fit."
- For each identified change: "Which decision areas does this touch — conventions, architecture, scope, requirements, agents, or open items?"
- "Are there carried-forward decisions from v{N} that might no longer be safe given this shift?"

Avoid forms. Avoid yes/no ladders. Surface specific prior decisions when you ask about sections.

### 0c. Update the problem statement if needed

If `PROBLEM_STATEMENT.md` needs edits, edit it in-session with the human's explicit approval. Record what you changed in `problem_change_summary`. Never edit seeds or the Decision Log directly.

### 0d. Write `reentry_request.yaml`

Write `workspace-artifacts/runtime/stage2/reentry_request.yaml`:

```yaml
stage2_reentry_request:
  generated_at: <current ISO timestamp>
  parent_version: <N>
  problem_change_summary: |
    <human's described change, in their own words as you understood them>
  problem_statement:
    previously_ingested_sha256: <sha256 of PROBLEM_STATEMENT.md at parent_version>
    current_sha256: <sha256 of PROBLEM_STATEMENT.md right now>
    updated: <true if you edited it in 0c; false otherwise>
    update_rationale: |
      <if updated=true: why. If false: affirmation that problem still stands.>
  revised_sections:
    - <one of: conventions | architecture | scope | requirements | open_items | agents_needed>
  reason: <short string; becomes --reason arg>
  carried_consistency_risks:
    - prior_decision: <title from parent log>
      section: <section>
      concern: <why carrying it forward may be unsafe>
```

Compute SHAs with `sha256sum PROBLEM_STATEMENT.md` or equivalent. The `gate_reentry_request` hook will verify them.

## Step 1 — Seed the transcript (CLI)

```bash
meta-compiler stage2-reentry --from-request workspace-artifacts/runtime/stage2/reentry_request.yaml
```

This writes:
- `workspace-artifacts/runtime/stage2/transcript.md` — seeded with v{N}'s decisions: carried-forward blocks under unchanged sections; prior-decision prose under revised sections.
- `workspace-artifacts/runtime/stage2/brief.md` — re-entry variant with the problem-change summary, revised sections, and carried consistency risks.
- `workspace-artifacts/runtime/stage2/precheck_request.yaml` — input for Step 2.
- `workspace-artifacts/runtime/stage2/cascade_report_v{N+1}.yaml` — downstream sections potentially affected.

On nonzero exit: **STOP**. Surface the failure to the human and return to Step 0 if the request was malformed.

## Step 2 — Orchestrator preflight (semantic readiness)

```
@stage2-orchestrator mode=preflight
```

Input: `workspace-artifacts/runtime/stage2/precheck_request.yaml` (includes the `reentry:` block).

Output: `workspace-artifacts/runtime/stage2/precheck_verdict.yaml`.

Re-entry-specific checks the orchestrator performs:
- Does `problem_change_summary` map plausibly to `revised_sections`?
- Do any `carried_consistency_risks` suggest sections the human did not list?

On `BLOCK`: surface the blocking reasons. Offer two paths: return to Step 0 and expand `revised_sections`, or iterate Stage 1B if the cascade opened new wiki coverage gaps. Do not enter Step 3 without `PROCEED`.

## Step 3 — Scoped dialog (LLM + human)

Read, in order:
- `PROBLEM_STATEMENT.md`
- `workspace-artifacts/runtime/stage2/brief.md`
- `workspace-artifacts/runtime/stage2/transcript.md` (seeded with prior decisions)
- `workspace-artifacts/runtime/stage2/cascade_report_v{N+1}.yaml`

Discuss **only** sections listed in `cascade_report.revised_sections`. For each revised section:

- Present the PRIOR decision (already in the transcript as reference prose): "v{N} committed to A because B. Given the change, does that still hold?"
- Query the wiki for alternatives not previously considered.
- Converse with the human.
- Append a **new** decision block whose title differs from the prior log's titles in that section. Blocks with identical titles fail the finalize-time freshness check.

For **unchanged** sections: the carried-forward blocks are already in the transcript. Note "Retained from v{N}" in prose if you want, but do not re-discuss.

Use the standard decision-block format (see `stage-2-dialog.prompt.md` § Step 3 for per-section required fields).

## Step 4 — Finalize (CLI)

```bash
meta-compiler elicit-vision --finalize
```

This:
- Parses decision blocks.
- **Re-entry block-freshness check**: every section in `cascade_report.revised_sections` must have ≥1 decision block whose title differs from the parent log's titles in that section.
- Assigns `REQ-NNN` IDs sequentially.
- Compiles `workspace-artifacts/decision-logs/decision_log_v{N+1}.yaml`.
- Writes `postcheck_request.yaml`.

On nonzero exit: **STOP**. Surface the named empty sections, return to Step 3, author the missing fresh blocks.

## Step 5 — Orchestrator postflight (fidelity audit + re-entry consistency)

```
@stage2-orchestrator mode=postflight
```

Standard fidelity audit, plus: carried-forward decisions from `parent_version` must remain internally consistent with the newly authored ones.

On `REVISE`: return to Step 3 with the discrepancies.

## Step 6 — Audit and handoff

On PROCEED:

```bash
meta-compiler audit-requirements
```

If scope or requirements changed, recommend re-running `meta-compiler scaffold`.

Record the audit output path in your final handoff message to the human.

---

## Out of scope

- You do not run `meta-compiler scaffold`. That's Stage 3, a separate prompt.
- You do not edit `decision_log_v{N+1}.yaml` directly.
- You do not discuss unchanged sections. Revise only what's listed in `revised_sections`.

## On refusal

If the human asks you to skip Step 0, refuse. The integrity layer exists for a reason. If the human asks to bypass `gate_reentry_request`, refuse — that hook is non-skippable by design.

## Guiding principles

- **Document everything** — every revision, cascade impact, retained decision is captured.
- **Data over folklore** — revised decisions cite specific evidence from the wiki.
- **Accessible to everyone** — explain what changed and why in plain language.
- **Knowledge should be shared** — v{N+1} preserves v{N}'s intent where the human confirmed it still holds.
```

- [ ] **Step 2: Verify the prompt file is well-formed markdown**

Run: `head -5 /Users/christianstokes/Downloads/META-COMPILER/.github/prompts/stage2-reentry.prompt.md`

Expected: opens with `---` frontmatter.

- [ ] **Step 3: Commit**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
git add .github/prompts/stage2-reentry.prompt.md
git commit -m "prompts: rewrite stage2-reentry as 6-step conductor with hardened Step 0"
```

---

## Phase 4 — Hook Registration (Progressive Enablement)

Each sub-task adds hook entries to `main.json` and/or agent frontmatter. Each requires a manual smoke test in VSCode Copilot Chat (not automatable).

### Task 16: Register `SessionStart` + `UserPromptSubmit` → `inject_state`

**Files:**
- Create: `/Users/christianstokes/Downloads/META-COMPILER/.github/hooks/main.json`
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/.gitignore` (add overrides.json)

- [ ] **Step 1: Create `.github/hooks/main.json`**

Write:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py inject_state",
        "timeout": 10
      }
    ],
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py inject_state",
        "timeout": 10
      }
    ]
  }
}
```

- [ ] **Step 2: Add `overrides.json` to `.gitignore`**

Append to `/Users/christianstokes/Downloads/META-COMPILER/.gitignore`:

```
# Meta-compiler hook kill-switch (local only)
.github/hooks/overrides.json
```

- [ ] **Step 3: Manual smoke test**

Open the META-COMPILER workspace in VSCode Copilot Chat. Start a new session. Observe that Copilot's context panel includes the `# Meta-compiler workspace state` section with the current `last_completed_stage` value. If absent, verify `chat.useCustomAgentHooks` is set (not required for workspace hooks per se, but worth confirming) and that `.github/hooks/main.json` is valid JSON.

- [ ] **Step 4: Commit**

```bash
git add .github/hooks/main.json .gitignore
git commit -m "hooks: register SessionStart + UserPromptSubmit inject_state"
```

---

### Task 17: Register `PostToolUse` → `capture_output`

**Files:**
- Modify: `.github/hooks/main.json`

- [ ] **Step 1: Add to main.json**

Edit the `hooks` object:

```json
{
  "hooks": {
    "SessionStart": [ /* existing */ ],
    "UserPromptSubmit": [ /* existing */ ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py capture_output",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

*(Engineer: verify the matcher nesting matches the Copilot hook schema; some versions use flat `"matcher": "Bash"` per entry, others use a nested `"matcher"` + `"hooks"` array. Check `.github/docs/hooks.md` once Phase 5 lands, or consult https://code.visualstudio.com/docs/copilot/customization/hooks.)*

- [ ] **Step 2: Manual smoke test**

In Copilot, run `meta-compiler validate-stage --stage 0` against a fresh workspace. Confirm the CLI output appears in the assistant's context as a code block via `additionalContext`. Confirm the LLM references actual values (not paraphrased).

- [ ] **Step 3: Commit**

```bash
git add .github/hooks/main.json
git commit -m "hooks: register PostToolUse capture_output on Bash"
```

---

### Task 18: Register `PreToolUse` denying hooks (`gate_cli`, `gate_artifact_writes`, `gate_reentry_request`)

**Files:**
- Modify: `.github/hooks/main.json`

- [ ] **Step 1: Add to main.json**

```json
"PreToolUse": [
  {
    "matcher": "Bash",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py gate_cli",
        "timeout": 10
      },
      {
        "type": "command",
        "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py gate_reentry_request",
        "timeout": 10
      }
    ]
  },
  {
    "matcher": "Write",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py gate_artifact_writes",
        "timeout": 5
      }
    ]
  },
  {
    "matcher": "Edit",
    "hooks": [
      {
        "type": "command",
        "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py gate_artifact_writes",
        "timeout": 5
      }
    ]
  }
]
```

- [ ] **Step 2: Manual smoke test — gate_cli**

Against a workspace at `last_completed_stage: "1a"`, run `meta-compiler scaffold`. Confirm Copilot shows the denial with `reason` + `remediation` + `audit_ref`.

- [ ] **Step 3: Manual smoke test — gate_reentry_request**

Against a workspace at `last_completed_stage: "2"`, attempt `meta-compiler stage2-reentry --reason x --sections architecture` without writing `reentry_request.yaml`. Confirm denial citing the missing Step 0.

- [ ] **Step 4: Manual smoke test — gate_artifact_writes**

Ask Copilot to write to `workspace-artifacts/decision-logs/decision_log_v1.yaml`. Confirm denial.

- [ ] **Step 5: Commit**

```bash
git add .github/hooks/main.json
git commit -m "hooks: register PreToolUse gate_cli + gate_artifact_writes + gate_reentry_request"
```

---

### Task 19: Add agent-scoped hooks — `stage2-orchestrator`

**Files:**
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/.github/agents/stage2-orchestrator.agent.md`
- Modify: `/Users/christianstokes/Downloads/META-COMPILER/.vscode/settings.json`

- [ ] **Step 1: Ensure `chat.useCustomAgentHooks` is enabled**

Read `/Users/christianstokes/Downloads/META-COMPILER/.vscode/settings.json`. If missing or lacking the key, set:

```json
{
  "chat.useCustomAgentHooks": true
}
```

(Merge with existing keys; do not overwrite.)

- [ ] **Step 2: Update the agent frontmatter**

Read `.github/agents/stage2-orchestrator.agent.md`. Its frontmatter is YAML between `---` fences at the top. Add a `hooks:` field:

```yaml
hooks:
  PreToolUse:
    - type: command
      command: "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py gate_orchestrator_mode_preflight"
      timeout: 10
  SubagentStop:
    - type: command
      command: "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py require_verdict_preflight"
      timeout: 10
```

*(Engineer: the orchestrator has two modes — preflight and postflight. There is no per-invocation event discriminator that lets ONE hook block decide which check to use. Options: (a) define two separate agents; (b) pass mode via environment/arg. For now, the hook dispatches to the preflight variant; the postflight-mode invocation must rely on the workspace-level `require_verdict_postflight` registered separately, or the hook must internally inspect the request artifact present to decide. Keep it simple: the orchestrator agent's hook points at `require_verdict_preflight`; postflight is covered by a workspace-level `SubagentStop` check that branches based on which verdict file exists. Revisit in Task 20 if brittleness emerges.)*

- [ ] **Step 3: Manual smoke test**

Invoke `@stage2-orchestrator mode=preflight` without writing `precheck_request.yaml` first. Confirm the agent refuses via the hook.

- [ ] **Step 4: Commit**

```bash
git add .github/agents/stage2-orchestrator.agent.md .vscode/settings.json
git commit -m "hooks: add stage2-orchestrator agent-scoped hooks (preflight gate + verdict requirement)"
```

---

### Task 20: Add agent-scoped hooks for `ingest-orchestrator`, `seed-reader`, `stage-1a2-orchestrator`

**Files:**
- Modify: `.github/agents/ingest-orchestrator.agent.md`
- Modify: `.github/agents/seed-reader.agent.md`
- Modify: `.github/agents/stage-1a2-orchestrator.agent.md`

- [ ] **Step 1: Add to `ingest-orchestrator.agent.md` frontmatter**

```yaml
hooks:
  PreToolUse:
    - type: command
      command: "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py gate_ingest_workplan"
      timeout: 10
  SubagentStop:
    - type: command
      command: "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py require_ingest_report"
      timeout: 10
```

- [ ] **Step 2: Add to `seed-reader.agent.md` frontmatter**

```yaml
hooks:
  PostToolUse:
    - matcher: Write
      hooks:
        - type: command
          command: "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py validate_findings_schema"
          timeout: 5
```

- [ ] **Step 3: Add to `stage-1a2-orchestrator.agent.md` frontmatter**

```yaml
hooks:
  SubagentStop:
    - type: command
      command: "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py require_handoff"
      timeout: 10
```

- [ ] **Step 4: Manual smoke tests**

- Invoke `@ingest-orchestrator` without `work_plan.yaml` → denied.
- Have `seed-reader` write malformed findings JSON → denied.
- Stop `stage-1a2-orchestrator` without `1a2_handoff.yaml` → blocked with reason.

- [ ] **Step 5: Commit**

```bash
git add .github/agents/ingest-orchestrator.agent.md .github/agents/seed-reader.agent.md .github/agents/stage-1a2-orchestrator.agent.md
git commit -m "hooks: add ingest-orchestrator, seed-reader, stage-1a2-orchestrator agent hooks"
```

---

### Task 21: Register auto-fire chains + strip corresponding CLI bullets from prompt bodies

**Files:**
- Modify: `.github/hooks/main.json`
- Modify: `.github/prompts/stage-1a-breadth.prompt.md`
- Modify: `.github/prompts/stage-2-dialog.prompt.md`
- Modify: `.github/prompts/stage-3-scaffold.prompt.md`
- Modify: `.github/prompts/stage-4-finalize.prompt.md`

- [ ] **Step 1: Register `user_prompt_submit_dispatch` in main.json**

Replace the `UserPromptSubmit` array with:

```json
"UserPromptSubmit": [
  {
    "type": "command",
    "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py inject_state",
    "timeout": 10
  },
  {
    "type": "command",
    "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py user_prompt_submit_dispatch",
    "timeout": 60
  }
]
```

- [ ] **Step 2: Register `subagent_stop_dispatch`**

Add to main.json:

```json
"SubagentStop": [
  {
    "type": "command",
    "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py subagent_stop_dispatch",
    "timeout": 120
  }
]
```

- [ ] **Step 3: Strip auto-fired CLI bullets from prompt bodies**

In `.github/prompts/stage-1a-breadth.prompt.md`: remove the `meta-compiler ingest --scope all`, `meta-compiler ingest-validate`, `meta-compiler research-breadth`, and `meta-compiler validate-stage --stage 1a` bullets (the first fires via UserPromptSubmit; the others via SubagentStop on ingest-orchestrator). Replace the "run these commands" sections with:

> Ingest runs automatically on prompt invocation. Conduct the seed fan-out via the `ingest-orchestrator` subagent. Validation and breadth-research commands run automatically after the subagent stops.

In `.github/prompts/stage-2-dialog.prompt.md`: remove the `meta-compiler elicit-vision --start` bullet from Step 1. Keep `--finalize` and `audit-requirements` in-prompt (they stay LLM-invoked).

In `.github/prompts/stage-3-scaffold.prompt.md`: remove `meta-compiler scaffold` and `meta-compiler validate-stage --stage 3` bullets. Replace with:

> Scaffold and validation run automatically when you invoke `/stage-3-scaffold`. Inspect the generated artifacts in `workspace-artifacts/scaffolds/v<N>/`.

In `.github/prompts/stage-4-finalize.prompt.md`: remove `meta-compiler phase4-finalize` and `meta-compiler validate-stage --stage 4` bullets.

- [ ] **Step 4: Manual smoke tests**

- Invoke `/stage-1a-breadth`. Confirm `meta-compiler ingest --scope all` fires automatically (observe `additionalContext`). Confirm the LLM proceeds to the orchestrator fan-out without re-running the CLI.
- Invoke `/stage-3-scaffold`. Confirm both commands auto-fire and the LLM does not attempt to re-run them.

- [ ] **Step 5: Commit**

```bash
git add .github/hooks/main.json .github/prompts/stage-1a-breadth.prompt.md .github/prompts/stage-2-dialog.prompt.md .github/prompts/stage-3-scaffold.prompt.md .github/prompts/stage-4-finalize.prompt.md
git commit -m "hooks: register auto-fire chains + strip auto-fired CLI bullets from prompts"
```

---

### Task 22: Register `Stop` → `nudge_finalize`

**Files:**
- Modify: `.github/hooks/main.json`

- [ ] **Step 1: Add to main.json**

```json
"Stop": [
  {
    "type": "command",
    "command": "python3 ${workspaceFolder}/.github/hooks/bin/meta_hook.py nudge_finalize",
    "timeout": 5
  }
]
```

- [ ] **Step 2: Manual smoke test**

In a workspace at `last_completed_stage: "2-reentry-seeded"`, end the Copilot session. Confirm the nudge appears ("Stage 2 re-entry is seeded but not finalized...") with a suggestion to either finalize or document the pause.

- [ ] **Step 3: Commit**

```bash
git add .github/hooks/main.json
git commit -m "hooks: register Stop nudge_finalize to block session-end on mid-flight Stage 2"
```

---

## Phase 5 — Documentation

### Task 23: Add `.github/docs/hooks.md`

**Files:**
- Create: `.github/docs/hooks.md`

- [ ] **Step 1: Write the doc**

Create `/Users/christianstokes/Downloads/META-COMPILER/.github/docs/hooks.md`:

```markdown
# Meta-Compiler Hooks

Source spec: `docs/superpowers/specs/2026-04-18-hooks-and-stage2-reentry-design.md`.

## What hooks are

VSCode Copilot hooks intercept lifecycle events (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStart/Stop, PreCompact) and can inject context, auto-fire commands, deny tool invocations, or block session end. Meta-compiler uses them to enforce pipeline-ordering discipline that was previously informal.

## Where hooks live

- **Workspace hooks:** `.github/hooks/main.json`. Apply to every session.
- **Agent-scoped hooks:** declared in each `.github/agents/*.agent.md` frontmatter under `hooks:`. Require `chat.useCustomAgentHooks: true` in `.vscode/settings.json`.
- **Shared machinery:** every hook entry invokes `python3 .github/hooks/bin/meta_hook.py <check-name>`. One module, stdlib-only, fully unit-tested under `.github/hooks/bin/tests/`.

## Checks

| Check | Event | Purpose |
|---|---|---|
| `inject_state` | SessionStart, UserPromptSubmit | Workspace stage summary + next action into context. |
| `gate_cli` | PreToolUse (Bash) | Denies `meta-compiler X` when manifest state precondition fails. |
| `gate_artifact_writes` | PreToolUse (Write/Edit) | Denies writes to `decision-logs/*.yaml` and `seeds/**`. |
| `gate_reentry_request` | PreToolUse (Bash) | Denies `stage2-reentry` without `reentry_request.yaml`. **Non-overridable.** |
| `capture_output` | PostToolUse (Bash) | Injects `meta-compiler` stdout as `additionalContext`. |
| `user_prompt_submit_dispatch` | UserPromptSubmit | Auto-fires CLI chain on `/stage-*-*` slash commands. |
| `subagent_stop_dispatch` | SubagentStop | Auto-fires post-subagent validation chain. |
| `nudge_finalize` | Stop | Blocks session end during mid-flight Stage 2. |
| `gate_orchestrator_mode_{preflight,postflight}` | Agent PreToolUse | Deny agent run without input artifact. |
| `require_verdict_{preflight,postflight}` | Agent SubagentStop | Block agent stop without verdict. |
| `gate_ingest_workplan` | Agent PreToolUse | Deny ingest-orchestrator fan-out without work_plan.yaml. |
| `require_ingest_report` / `require_handoff` | Agent SubagentStop | Block stop without output artifact. |
| `validate_findings_schema` | Agent PostToolUse (Write) | Deny malformed findings JSON at write time. |

## Override mechanisms

### Per-call env flag

Set `META_COMPILER_SKIP_HOOK=1` in the tool call env. Honored by `gate_cli`, `gate_artifact_writes`, and the auto-fire chain runner's child invocations. **Not honored by** `gate_reentry_request`, `gate_orchestrator_mode_*`, `require_verdict_*`, or `validate_findings_schema`.

### Config file

`.github/hooks/overrides.json` (gitignored). Time-bounded kill-switch:

```json
{
  "disable_checks": ["nudge_finalize"],
  "disable_until": "2026-05-01T00:00:00Z",
  "reason": "...",
  "approved_by": "human"
}
```

Listed checks short-circuit with an override `systemMessage`. Expired entries are ignored.

## Audit log

`workspace-artifacts/runtime/hook_audit.log`. One JSON line per event. Cleared only by `meta-compiler clean-workspace --target-stage 0`.

## Testing hooks

```bash
pytest .github/hooks/bin/tests/ -v
```

Runs unit tests for every check against fixture hook-input JSON. No VSCode required.

## Debugging

Set `META_COMPILER_HOOK_TEST=1` to suppress audit writes. Invoke `meta_hook.py <check>` directly with a JSON payload on stdin to reproduce denials:

```bash
echo '{"hookEventName":"PreToolUse","tool_input":{"command":"meta-compiler scaffold"}}' | python3 .github/hooks/bin/meta_hook.py gate_cli
```
```

- [ ] **Step 2: Commit**

```bash
git add .github/docs/hooks.md
git commit -m "docs: add .github/docs/hooks.md describing the hook layer"
```

---

### Task 24: Update `README.md`, `LLM_INSTRUCTIONS.md`, `CLAUDE.md`

**Files:**
- Modify: `README.md`
- Modify: `LLM_INSTRUCTIONS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: README.md — add "Hooks and Determinism" section**

Insert after the existing "Setup and Commands" section:

```markdown
## Hooks and Determinism

Meta-compiler uses VSCode Copilot hooks (`.github/hooks/main.json` + per-agent `hooks:` frontmatter) to enforce pipeline ordering and artifact integrity. Hooks gate out-of-order CLI calls, auto-fire deterministic steps at transition boundaries, and capture command output so the LLM cannot paraphrase it.

**Key points:**

- **Auto-fired steps:** Invoking a stage prompt (e.g., `/stage-1a-breadth`) auto-fires the pure-CLI calls for that stage. The prompt body describes only the semantic work (what the LLM is supposed to reason about).
- **Gated calls:** `meta-compiler` invocations are denied unless the manifest's `last_completed_stage` matches the command's precondition. Override with `META_COMPILER_SKIP_HOOK=1` only where integrity permits.
- **Stage 2 re-entry:** Non-overridable gate requires `reentry_request.yaml` (produced by Step 0 of `stage2-reentry.prompt.md`) before the CLI fires.

See `.github/docs/hooks.md` for the full check inventory, override mechanisms, and audit log format.
```

- [ ] **Step 2: LLM_INSTRUCTIONS.md — update stage entry notes**

Find the Stage 1A, Stage 2, Stage 3, Stage 4 sections. For each, replace "Run these commands in order:" with "Invoke the stage prompt (`/stage-1a-breadth` etc.). Pure-CLI steps auto-fire via hooks; your job is the semantic work."

Replace the "Stage 2 Re-entry" section with a reference to `.github/prompts/stage2-reentry.prompt.md` and call out Step 0 as the dialog-driven step that must complete before any CLI fires.

- [ ] **Step 3: CLAUDE.md — update stage-pipeline description**

Add a new paragraph at the end of the "Stage Pipeline" section:

```markdown
### Hook-enforced determinism

As of 2026-04, CLI calls in stage prompts are enforced by VSCode Copilot hooks (`.github/hooks/main.json` + per-agent `hooks:` frontmatter). Auto-fire chains eliminate the "LLM skips the CLI" failure for pure-CLI steps; `gate_cli` blocks out-of-order invocations; `gate_reentry_request` closes the Stage 2 re-entry dialog gap. See `.github/docs/hooks.md` for the full check inventory.
```

Also update the "Stage 2" paragraph to note the new `stage2-reentry.prompt.md` structure (6 steps including Step 0 problem-space re-ingestion).

- [ ] **Step 4: Commit**

```bash
git add README.md LLM_INSTRUCTIONS.md CLAUDE.md
git commit -m "docs: document hook layer in README, LLM_INSTRUCTIONS, CLAUDE"
```

---

## Phase 6 — Integration Tests

### Task 25: Hook integration smoke test

**Files:**
- Create: `/Users/christianstokes/Downloads/META-COMPILER/tests/test_hooks_integration.py`

- [ ] **Step 1: Write the integration test**

```python
"""End-to-end smoke test for meta_hook.py event sequences.

Does NOT require VSCode. Invokes meta_hook.py as a subprocess with
simulated hook-input JSON to replay a realistic Stage 2 re-entry scenario,
then asserts the audit log contains the expected decisions in order.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".github" / "hooks" / "bin" / "meta_hook.py"


def _invoke(check: str, payload: dict, cwd: Path) -> dict:
    env = {**os.environ}
    env.pop("META_COMPILER_HOOK_TEST", None)  # enable audit log
    proc = subprocess.run(
        [sys.executable, str(HOOK), check],
        input=json.dumps(payload),
        capture_output=True, text=True, cwd=str(cwd), env=env, timeout=10,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def _bash(cmd): return {"hookEventName": "PreToolUse", "tool_input": {"command": cmd}}


def test_stage2_reentry_sequence_audited(tmp_path):
    """Replay: SessionStart → denied scaffold → write reentry_request → allowed stage2-reentry."""
    # Build workspace at stage 2
    artifacts = tmp_path / "workspace-artifacts"
    (artifacts / "manifests").mkdir(parents=True)
    (artifacts / "manifests" / "workspace_manifest.yaml").write_text(
        "workspace_manifest:\n  research:\n    last_completed_stage: '2'\n"
    )
    ps = tmp_path / "PROBLEM_STATEMENT.md"
    ps.write_text("problem v1\n")
    sha = hashlib.sha256(ps.read_bytes()).hexdigest()

    # 1. SessionStart inject_state
    r = _invoke("inject_state", {"hookEventName": "SessionStart"}, tmp_path)
    assert "last_completed_stage" in (r.get("additionalContext") or "")

    # 2. Deny scaffold (wrong stage is NOT this test — stage is "2" which allows scaffold)
    # Instead: deny stage2-reentry without request
    r = _invoke("gate_reentry_request",
                _bash("meta-compiler stage2-reentry --reason x --sections architecture"),
                tmp_path)
    assert r.get("permissionDecision") == "deny"

    # 3. Author reentry_request.yaml
    (artifacts / "runtime" / "stage2").mkdir(parents=True)
    req_path = artifacts / "runtime" / "stage2" / "reentry_request.yaml"
    req_path.write_text(
        "stage2_reentry_request:\n"
        "  parent_version: 1\n"
        "  problem_change_summary: 'test'\n"
        "  problem_statement:\n"
        f"    previously_ingested_sha256: {sha}\n"
        f"    current_sha256: {sha}\n"
        "    updated: false\n"
        "    update_rationale: 'unchanged'\n"
        "  reason: 'test'\n"
        "  revised_sections:\n    - architecture\n"
        "  carried_consistency_risks: []\n"
    )

    # 4. Now stage2-reentry is allowed
    r = _invoke("gate_reentry_request",
                _bash("meta-compiler stage2-reentry --from-request workspace-artifacts/runtime/stage2/reentry_request.yaml"),
                tmp_path)
    assert r.get("permissionDecision") == "allow"

    # 5. Audit log contains both decisions
    audit_path = artifacts / "runtime" / "hook_audit.log"
    assert audit_path.exists()
    lines = audit_path.read_text().splitlines()
    decisions = [json.loads(l).get("decision") for l in lines if l.strip()]
    assert "deny" in decisions
    assert "allow" in decisions
```

- [ ] **Step 2: Run test**

Run: `cd /Users/christianstokes/Downloads/META-COMPILER && pytest tests/test_hooks_integration.py -v`

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_hooks_integration.py
git commit -m "tests: add hooks integration smoke test (subprocess, no VSCode)"
```

---

### Task 26: Agent-hooks frontmatter sanity test

**Files:**
- Create: `/Users/christianstokes/Downloads/META-COMPILER/tests/test_agent_hooks_frontmatter.py`

- [ ] **Step 1: Write the test**

```python
"""Parse each .github/agents/*.agent.md frontmatter; validate any hooks:
block against the known check names in meta_hook.py."""
import re
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO / ".github" / "agents"
HOOK_SCRIPT = REPO / ".github" / "hooks" / "bin" / "meta_hook.py"


def _known_checks() -> set[str]:
    text = HOOK_SCRIPT.read_text(encoding="utf-8")
    return set(re.findall(r'@register\("([^"]+)"\)', text))


def _parse_frontmatter(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    _, fm, _ = text.split("---", 2)
    return yaml.safe_load(fm) or {}


@pytest.mark.parametrize("agent_path", sorted(AGENTS_DIR.glob("*.agent.md")))
def test_agent_hooks_reference_known_checks(agent_path):
    fm = _parse_frontmatter(agent_path)
    if fm is None or "hooks" not in fm:
        pytest.skip(f"{agent_path.name} has no hooks block")
    known = _known_checks()
    for event, entries in (fm["hooks"] or {}).items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            cmd = entry.get("command", "") if isinstance(entry, dict) else ""
            # Extract the last token of the python3 meta_hook.py <check> command
            m = re.search(r"meta_hook\.py\s+(\S+)", cmd)
            if not m:
                continue
            check = m.group(1)
            # Strip any argparse junk
            check = check.split()[0]
            assert check in known, (
                f"{agent_path.name} event={event} references unknown check '{check}'. "
                f"Known: {sorted(known)}"
            )
```

- [ ] **Step 2: Run test**

`pytest tests/test_agent_hooks_frontmatter.py -v` → all pass (including any agent without hooks, which is skipped).

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_hooks_frontmatter.py
git commit -m "tests: validate agent frontmatter hook commands reference known checks"
```

---

## Final Verification

- [ ] **Run the full test suite**

```bash
cd /Users/christianstokes/Downloads/META-COMPILER
pytest tests/ .github/hooks/bin/tests/ -v
```

Expected: all pass.

- [ ] **Manual end-to-end walk in VSCode Copilot**

1. Fresh workspace → `/stage-0-init` → stage 0 reached.
2. `/stage-1a-breadth` → auto-fires ingest; conduct fan-out; post-subagent chain auto-fires; stage 1a reached.
3. Continue through 1B, 1C.
4. `/stage-2-dialog` → auto-fires `--start`; conduct dialog; manually run `--finalize`; stage 2 reached.
5. `/stage2-reentry` → Step 0 dialog; write `reentry_request.yaml`; Step 1 CLI fires; dialog; `--finalize` succeeds.
6. `/stage-3-scaffold` → auto-fires scaffold + validate; stage 3 reached.
7. `/stage-4-finalize` → auto-fires phase4 + validate; stage 4 reached.
8. Verify `workspace-artifacts/runtime/hook_audit.log` contains a clean sequence of `allow` / `inject` / `chain_ok` decisions.

- [ ] **Close the loop**

```bash
git log --oneline main..HEAD
```

Expected: ~26 commits, each revertable individually.
