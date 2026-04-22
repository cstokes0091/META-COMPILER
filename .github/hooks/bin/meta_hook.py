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
    "add-code-seed": {"allowed_prior": ["0", "1a", "1b", "1c", "2"], "sets": None},
    "bind-code-seed": {"allowed_prior": ["0", "1a", "1b", "1c", "2"], "sets": None},
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


@register("gate_ingest_precheck")
def gate_ingest_precheck(payload):
    return _presence_check(
        payload, "gate_ingest_precheck",
        "workspace-artifacts/runtime/ingest/precheck_request.yaml",
        "PreToolUse",
        "ingest precheck_request.yaml is missing.",
        "Run `meta-compiler ingest-precheck --scope {all|new}` before invoking @ingest-orchestrator mode=preflight.",
    )


@register("gate_ingest_postcheck")
def gate_ingest_postcheck(payload):
    return _presence_check(
        payload, "gate_ingest_postcheck",
        "workspace-artifacts/runtime/ingest/postcheck_request.yaml",
        "PreToolUse",
        "ingest postcheck_request.yaml is missing.",
        "Run `meta-compiler ingest-postcheck` after the orchestrator fan-out completes.",
    )


@register("require_ingest_precheck_verdict")
def require_ingest_precheck_verdict(payload):
    return _presence_check(
        payload, "require_ingest_precheck_verdict",
        "workspace-artifacts/runtime/ingest/precheck_verdict.yaml",
        "SubagentStop",
        "ingest precheck_verdict.yaml was not written.",
        "Preflight must write its verdict before stopping.",
        require_field="verdict",
        deny_mode="block",
    )


@register("require_ingest_postcheck_verdict")
def require_ingest_postcheck_verdict(payload):
    return _presence_check(
        payload, "require_ingest_postcheck_verdict",
        "workspace-artifacts/runtime/ingest/postcheck_verdict.yaml",
        "SubagentStop",
        "ingest postcheck_verdict.yaml was not written.",
        "Postflight must write its verdict before stopping.",
        require_field="verdict",
        deny_mode="block",
    )


@register("gate_phase4_finalize")
def gate_phase4_finalize(payload):
    """Block phase4-finalize --finalize unless dispatch_plan exists and the
    work_dir holds at least one file. Lets --start through unconditionally.

    Pitch sub-loop awareness:
      - --pitch-step=evidence (and the alias --pitch-step=draft) are allowed
        as soon as work_dir is populated; they only produce the evidence pack
        and pitch_request that the @pitch-writer agent consumes.
      - --pitch-step=render is denied when slides.yaml is absent or older
        than evidence_pack.yaml — the deck must be drafted from a fresh pack.
    """
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str) or "phase4-finalize" not in cmd:
        return {"permissionDecision": "allow"}
    if "--start" in cmd:
        return {"permissionDecision": "allow"}
    # Look for any executions/v*/dispatch_plan.yaml; if none, block.
    executions_dir = ws / "workspace-artifacts" / "executions"
    if not executions_dir.exists():
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "phase4-finalize --finalize blocked: no executions/ directory. "
                "Run `meta-compiler phase4-finalize --start` first to write the "
                "dispatch plan, then conduct the LLM ralph loop per "
                "stage-4-finalize.prompt.md."
            ),
        }
    plans = list(executions_dir.glob("v*/dispatch_plan.yaml"))
    if not plans:
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "phase4-finalize --finalize blocked: no dispatch_plan.yaml in "
                "executions/. Run `meta-compiler phase4-finalize --start` first."
            ),
        }
    # Confirm at least one plan's work/ directory has content.
    populated = False
    for plan in plans:
        work_dir = plan.parent / "work"
        if work_dir.exists() and any(work_dir.rglob("*")):
            populated = True
            break
    if not populated:
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "phase4-finalize --finalize blocked: every executions/v*/work/ "
                "directory is empty. Conduct the Stage 4 ralph loop "
                "(stage-4-finalize.prompt.md Steps 2-3) so implementer agents "
                "populate work_dir before compiling the final manifest."
            ),
        }

    if "--pitch-step=render" in cmd or "--pitch-step render" in cmd:
        slides_path = ws / "workspace-artifacts" / "runtime" / "phase4" / "slides.yaml"
        if not slides_path.exists():
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "phase4-finalize --pitch-step=render blocked: slides.yaml is missing. "
                    "Run `meta-compiler phase4-finalize --pitch-step=evidence` first, then "
                    "invoke @pitch-writer to author "
                    "workspace-artifacts/runtime/phase4/slides.yaml. See "
                    ".github/prompts/pitch-writer.prompt.md for the conductor."
                ),
            }
        evidence_path = ws / "workspace-artifacts" / "runtime" / "phase4" / "evidence_pack.yaml"
        if evidence_path.exists() and slides_path.stat().st_mtime < evidence_path.stat().st_mtime:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "phase4-finalize --pitch-step=render blocked: slides.yaml is older "
                    "than evidence_pack.yaml. The deck would render against stale facts. "
                    "Re-invoke @pitch-writer to refresh slides.yaml against the current "
                    "evidence pack, then retry."
                ),
            }
    return {"permissionDecision": "allow"}


@register("gate_migration_request")
def gate_migration_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Block `meta-compiler migrate-decision-log --apply` unless the proposal
    written by `--plan` exists.

    The workflow is:
      1. `decision-log-migrate-v2` prompt walks the human/LLM through Step 0
         (re-orient on the v1 Decision Log + author migration_request.yaml).
      2. `meta-compiler migrate-decision-log --plan` writes
         runtime/migration/proposal.yaml.
      3. The LLM/human refines the proposal modalities and (for
         algorithm/hybrid projects) authors
         runtime/migration/code_architecture_blocks.md.
      4. `meta-compiler migrate-decision-log --apply` compiles the new
         Decision Log — the step this gate protects.

    Skipping straight from the prompt to `--apply` would drop the typed-I/O
    review and silently emit a v{N+1} log with bad modalities.
    """
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str):
        return {"permissionDecision": "allow"}
    if "migrate-decision-log" not in cmd or "--apply" not in cmd:
        return {"permissionDecision": "allow"}
    if not cmd.strip().startswith("meta-compiler"):
        return {"permissionDecision": "allow"}

    disabled, ov_reason = is_disabled("gate_migration_request", ws)
    if disabled:
        audit(ws, "gate_migration_request", "PreToolUse", "allow_override",
              reason=ov_reason, extra={"command": cmd})
        return {
            "permissionDecision": "allow",
            "systemMessage": f"gate_migration_request disabled by override: {ov_reason}",
        }

    proposal_path = ws / "workspace-artifacts" / "runtime" / "migration" / "proposal.yaml"
    if not proposal_path.exists():
        line = audit(ws, "gate_migration_request", "PreToolUse", "deny",
                     reason="proposal.yaml missing",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": "Migration proposal.yaml is missing — --plan did not run.",
            "remediation": (
                "Run `meta-compiler migrate-decision-log --plan` first, review "
                "runtime/migration/proposal.yaml (and author "
                "runtime/migration/code_architecture_blocks.md for "
                "algorithm/hybrid projects), then retry --apply."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    audit(ws, "gate_migration_request", "PreToolUse", "allow", extra={"command": cmd})
    return {"permissionDecision": "allow"}


@register("gate_reconcile_request")
def gate_reconcile_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Block `meta-compiler wiki-apply-reconciliation` unless the preflight
    request and an orchestrator-written proposal both exist.

    The workflow is:
      1. `wiki-reconcile-concepts` (writes reconcile_request.yaml + work_plan)
      2. `wiki-concept-reconciliation` prompt fan-out (writes the proposal)
      3. `wiki-apply-reconciliation` (mutates v2 pages) — the step this gates.

    Preventing #3 from running without #1 or #2 catches the common failure
    of an LLM skipping straight to the apply step and silently mutating
    pages against a stale or missing proposal.
    """
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str):
        return {"permissionDecision": "allow"}
    if "wiki-apply-reconciliation" not in cmd or not cmd.strip().startswith(
        "meta-compiler"
    ):
        return {"permissionDecision": "allow"}

    disabled, ov_reason = is_disabled("gate_reconcile_request", ws)
    if disabled:
        audit(ws, "gate_reconcile_request", "PreToolUse", "allow_override",
              reason=ov_reason, extra={"command": cmd})
        return {
            "permissionDecision": "allow",
            "systemMessage": f"gate_reconcile_request disabled by override: {ov_reason}",
        }

    request_path = ws / "workspace-artifacts" / "runtime" / "wiki_reconcile" / "reconcile_request.yaml"
    if not request_path.exists():
        line = audit(ws, "gate_reconcile_request", "PreToolUse", "deny",
                     reason="reconcile_request.yaml missing",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": "reconcile_request.yaml is missing — preflight did not run.",
            "remediation": (
                "Run `meta-compiler wiki-reconcile-concepts --version 2` first, "
                "then invoke the wiki-concept-reconciliation prompt, then retry this CLI."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    reports_dir = ws / "workspace-artifacts" / "wiki" / "reports"
    proposals = sorted(reports_dir.glob("concept_reconciliation_v*.yaml")) if reports_dir.exists() else []
    if not proposals:
        line = audit(ws, "gate_reconcile_request", "PreToolUse", "deny",
                     reason="no concept_reconciliation_v*.yaml proposal",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": "No concept_reconciliation_v*.yaml proposal was written by the orchestrator.",
            "remediation": (
                "Invoke the `wiki-concept-reconciliation` prompt (it writes the "
                "proposal to workspace-artifacts/wiki/reports/) before running "
                "`meta-compiler wiki-apply-reconciliation`."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    audit(ws, "gate_reconcile_request", "PreToolUse", "allow",
          extra={"command": cmd, "proposal_count": len(proposals)})
    return {"permissionDecision": "allow"}


@register("gate_wiki_search_apply")
def gate_wiki_search_apply(payload: dict[str, Any]) -> dict[str, Any]:
    """Block `meta-compiler wiki-search --apply` unless the preflight wrote the
    request and at least one `T-*.yaml` topic file landed in the results dir.

    Workflow:
      1. `wiki-search --scope stage2` (preflight: writes work_plan + request)
      2. `@wiki-search-orchestrator` (fan-out: writes T-*.yaml per topic)
      3. `wiki-search --apply` (postflight: consolidates) — gated here.

    Catches the common failure of an LLM skipping the orchestrator and
    asking apply to consolidate an empty results directory.
    """
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str):
        return {"permissionDecision": "allow"}
    if "wiki-search" not in cmd or "--apply" not in cmd or not cmd.strip().startswith(
        "meta-compiler"
    ):
        return {"permissionDecision": "allow"}

    disabled, ov_reason = is_disabled("gate_wiki_search_apply", ws)
    if disabled:
        audit(ws, "gate_wiki_search_apply", "PreToolUse", "allow_override",
              reason=ov_reason, extra={"command": cmd})
        return {
            "permissionDecision": "allow",
            "systemMessage": f"gate_wiki_search_apply disabled by override: {ov_reason}",
        }

    runtime_dir = ws / "workspace-artifacts" / "runtime" / "stage2" / "wiki_search"
    request_path = runtime_dir / "wiki_search_request.yaml"
    if not request_path.exists():
        line = audit(ws, "gate_wiki_search_apply", "PreToolUse", "deny",
                     reason="wiki_search_request.yaml missing",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": "wiki_search_request.yaml is missing — preflight did not run.",
            "remediation": (
                "Run `meta-compiler wiki-search --scope stage2` first (or just "
                "`meta-compiler elicit-vision --start`, which auto-fires it), "
                "then invoke @wiki-search-orchestrator, then retry --apply."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    results_dir = runtime_dir / "results"
    topic_files = sorted(results_dir.glob("T-*.yaml")) if results_dir.exists() else []
    if not topic_files:
        line = audit(ws, "gate_wiki_search_apply", "PreToolUse", "deny",
                     reason="no T-*.yaml topic files",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": "No T-*.yaml topic results found in runtime/stage2/wiki_search/results/.",
            "remediation": (
                "Invoke @wiki-search-orchestrator first; it fans out wiki-searcher "
                "subagents that write one file per topic before --apply consolidates."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    audit(ws, "gate_wiki_search_apply", "PreToolUse", "allow",
          extra={"command": cmd, "topic_count": len(topic_files)})
    return {"permissionDecision": "allow"}


@register("validate_wiki_search_schema")
def validate_wiki_search_schema(payload: dict[str, Any]) -> dict[str, Any]:
    """PostToolUse: re-load runtime/stage2/wiki_search/results.yaml after a
    successful `wiki-search --apply` and surface schema errors back to the LLM.
    """
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str):
        return {}
    if "wiki-search" not in cmd or "--apply" not in cmd:
        return {}

    results_path = (
        ws / "workspace-artifacts" / "runtime" / "stage2" / "wiki_search" / "results.yaml"
    )
    if not results_path.exists():
        return {}

    try:
        import sys

        repo_root = Path(__file__).resolve().parents[3]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from meta_compiler.io import load_yaml
        from meta_compiler.stages.wiki_search_stage import validate_wiki_search_results

        issues = validate_wiki_search_results(load_yaml(results_path) or {})
    except Exception as exc:  # noqa: BLE001
        audit(ws, "validate_wiki_search_schema", "PostToolUse", "error",
              reason=str(exc), extra={"command": cmd})
        return {
            "systemMessage": (
                f"validate_wiki_search_schema raised {exc.__class__.__name__}: {exc}"
            )
        }
    if not issues:
        audit(ws, "validate_wiki_search_schema", "PostToolUse", "allow",
              extra={"command": cmd})
        return {}
    audit(ws, "validate_wiki_search_schema", "PostToolUse", "deny",
          reason="schema_issues", extra={"issues": issues[:5]})
    return {
        "systemMessage": "wiki_search/results.yaml failed schema validation:\n  - "
        + "\n  - ".join(issues[:10])
    }


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


FINDINGS_SCHEMA_REQUIRED: list[str] = ["source_id", "findings"]
FINDINGS_ITEM_REQUIRED: list[str] = ["claim", "quote", "location"]

CODE_FINDINGS_REQUIRED: list[str] = [
    "citation_id",
    "seed_path",
    "file_hash",
    "file_metadata",
    "symbols",
    "claims",
    "quotes",
    "dependencies",
]

DOC_FINDINGS_REQUIRED: list[str] = [
    "citation_id",
    "seed_path",
    "file_hash",
    "concepts",
    "quotes",
    "claims",
]


def _is_code_payload(data: dict[str, Any]) -> bool:
    if data.get("source_type") == "code":
        return True
    return isinstance(data.get("file_metadata"), dict)


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

    # Polymorphic dispatch. Code payloads use {file_metadata, symbols, ...};
    # modern doc payloads use {citation_id, concepts, ...}; legacy payloads
    # still carrying {source_id, findings[]} stay permitted for back-compat.
    if _is_code_payload(data):
        for field in CODE_FINDINGS_REQUIRED:
            if field not in data:
                return _deny(f"{fp.name} (code) missing required field '{field}'.")
        symbols = data.get("symbols")
        if not isinstance(symbols, list):
            return _deny(f"{fp.name} (code): 'symbols' must be a list.")
        for i, sym in enumerate(symbols):
            if not isinstance(sym, dict):
                return _deny(f"{fp.name} (code): symbols[{i}] is not an object.")
            loc = sym.get("locator")
            if not isinstance(loc, dict) or not loc.get("file") or not isinstance(loc.get("line_start"), int):
                return _deny(
                    f"{fp.name} (code): symbols[{i}].locator must include file + integer line_start."
                )
        return {"permissionDecision": "allow"}

    if "citation_id" in data and "concepts" in data:
        for field in DOC_FINDINGS_REQUIRED:
            if field not in data:
                return _deny(f"{fp.name} (doc) missing required field '{field}'.")
        return {"permissionDecision": "allow"}

    # Legacy shape — kept for back-compat with existing callers that haven't
    # migrated. Future doc writers should use the {citation_id, concepts, ...}
    # shape above.
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


REPO_MAP_REQUIRED: list[str] = [
    "repo_name",
    "commit_sha",
    "languages",
    "priority_files",
]


@register("validate_repo_map_schema")
def validate_repo_map_schema(payload: dict[str, Any]) -> dict[str, Any]:
    ws = resolve_workspace_root(payload)
    fp_str = (payload.get("tool_input") or {}).get("file_path") or ""
    if not fp_str:
        return {"permissionDecision": "allow"}
    try:
        fp = Path(fp_str).resolve()
        rel = fp.relative_to(ws).as_posix()
    except (ValueError, OSError):
        return {"permissionDecision": "allow"}
    if not rel.startswith("workspace-artifacts/runtime/ingest/repo_map/") or not rel.endswith(".yaml"):
        return {"permissionDecision": "allow"}

    def _deny(msg: str) -> dict[str, Any]:
        line = audit(ws, "validate_repo_map_schema", "PostToolUse", "deny",
                     reason=msg, extra={"file_path": rel})
        return {
            "permissionDecision": "deny",
            "reason": msg,
            "remediation": (
                "Repair the RepoMap YAML before the file is persisted. "
                "See .github/prompts/ingest-orchestrator.prompt.md § RepoMap Schema."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    try:
        text = fp.read_text(encoding="utf-8")
    except OSError as e:
        return _deny(f"{fp.name}: unreadable ({e})")

    try:
        data = _parse_yaml_subset(text)
    except ValueError as e:
        return _deny(f"{fp.name}: unparseable YAML ({e})")

    if not isinstance(data, dict):
        return _deny(f"{fp.name}: top-level must be a mapping.")

    for field in REPO_MAP_REQUIRED:
        if field not in data:
            return _deny(f"{fp.name}: missing required field '{field}'.")

    priority = data.get("priority_files")
    if not isinstance(priority, list) or not priority:
        return _deny(f"{fp.name}: priority_files must be a non-empty list.")
    for i, entry in enumerate(priority):
        if not isinstance(entry, dict):
            return _deny(f"{fp.name}: priority_files[{i}] is not an object.")
        for needed in ("path", "rank", "reason"):
            if needed not in entry:
                return _deny(f"{fp.name}: priority_files[{i}] missing '{needed}'.")

    languages = data.get("languages")
    if not isinstance(languages, list):
        return _deny(f"{fp.name}: languages must be a list.")

    return {"permissionDecision": "allow"}


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
    On failure, stops; subsequent commands are not run.

    If META_COMPILER_TEST_CHAIN is set, its ';'-separated commands replace
    the `commands` list entirely (used by unit tests to avoid invoking real
    meta-compiler)."""
    outputs: list[str] = []
    env = dict(os.environ)
    env["META_COMPILER_SKIP_HOOK"] = "1"
    test_chain = os.environ.get("META_COMPILER_TEST_CHAIN")
    effective_cmds = test_chain.split(";") if test_chain else commands
    for cmd in effective_cmds:
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
    outputs, failure = _run_chain(commands, cwd=ws)
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
