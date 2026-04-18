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
# Test-only checks (gated by env)
# ---------------------------------------------------------------------------

if os.environ.get("META_COMPILER_HOOK_TEST"):
    @register("_echo_stage_for_test")
    def _echo_stage_for_test(payload: dict[str, Any]) -> dict[str, Any]:
        return {"additionalContext": manifest_stage(resolve_workspace_root(payload))}

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
