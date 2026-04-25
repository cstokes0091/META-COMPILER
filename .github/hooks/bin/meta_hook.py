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
    "compile-capabilities": {"allowed_prior": ["2", "3"], "sets": None},
    "extract-contracts": {"allowed_prior": ["2", "3"], "sets": None},
    "synthesize-skills": {"allowed_prior": ["2", "3"], "sets": None},
    "workspace-bootstrap": {"allowed_prior": ["2", "3"], "sets": "3"},
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

    # Stage 3 post-dialogue artefacts (capabilities.yaml + contracts/*.yaml).
    # Both are emitted by `meta-compiler compile-capabilities` / `extract-contracts`
    # from the Decision Log + findings; hand edits desynchronize them.
    import re as _re
    _SCAFFOLD_CAP_RE = _re.compile(r"^workspace-artifacts/scaffolds/v\d+/capabilities\.yaml$")
    _SCAFFOLD_CONTRACT_RE = _re.compile(
        r"^workspace-artifacts/scaffolds/v\d+/contracts/.+\.yaml$"
    )
    if _SCAFFOLD_CAP_RE.match(rel_posix) or _SCAFFOLD_CONTRACT_RE.match(rel_posix):
        line = audit(ws, "gate_artifact_writes", "PreToolUse", "deny",
                     reason="scaffolds/v*/capabilities.yaml and contracts/*.yaml are CLI-compiled",
                     extra={"file_path": rel_posix})
        return {
            "permissionDecision": "deny",
            "reason": (
                "workspace-artifacts/scaffolds/v*/capabilities.yaml and contracts/*.yaml are "
                "compiled by the post-dialogue stages. Hand edits desynchronize them from the "
                "Decision Log + findings."
            ),
            "remediation": (
                "Edit the source Decision Log (via stage2-reentry) or the wiki findings, then "
                "re-run `meta-compiler compile-capabilities` / `extract-contracts`."
            ),
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
        "2": (
            "Stage 3: run `/stage-3-scaffold` to compile capabilities.yaml, "
            "contracts/, skills/{name}/SKILL.md, and verification/ from the "
            "Decision Log + findings."
        ),
        "2-dialog-started": "Stage 2 preflight complete; open the Stage 2 dialog prompt and converse.",
        "2-reentry-seeded": "Stage 2 re-entry in progress; conduct the scoped dialog, then run `meta-compiler elicit-vision --finalize`.",
        "3": (
            "Stage 4: run `/stage-4-finalize`. The planner/implementer/reviewer "
            "palette executes against the capability graph."
        ),
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
    subagent_returns_dir = (
        ws / "workspace-artifacts" / "runtime" / "wiki_reconcile" / "subagent_returns"
    )
    has_returns = subagent_returns_dir.exists() and any(
        subagent_returns_dir.glob("*.json")
    )
    if not proposals and not has_returns:
        line = audit(ws, "gate_reconcile_request", "PreToolUse", "deny",
                     reason="no concept_reconciliation_v*.yaml proposal and no subagent_returns",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": (
                "No concept_reconciliation_v*.yaml proposal and no subagent JSON "
                "returns were written by the orchestrator."
            ),
            "remediation": (
                "Invoke the `wiki-concept-reconciliation` prompt — it writes "
                "per-bucket JSON to workspace-artifacts/runtime/wiki_reconcile/"
                "subagent_returns/ — before running "
                "`meta-compiler wiki-apply-reconciliation`."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    if proposals:
        try:
            import sys

            repo_root = Path(__file__).resolve().parents[3]
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            from meta_compiler.io import load_yaml as _load_yaml
            from meta_compiler.validation import (
                validate_concept_reconciliation_proposal as _validate_proposal,
            )

            issues = _validate_proposal(_load_yaml(proposals[-1]) or {})
        except Exception as exc:  # noqa: BLE001
            audit(ws, "gate_reconcile_request", "PreToolUse", "error",
                  reason=str(exc), extra={"command": cmd})
            return {
                "permissionDecision": "allow",
                "systemMessage": (
                    f"gate_reconcile_request: validator raised "
                    f"{exc.__class__.__name__}: {exc}. Allowing the apply step "
                    "to surface the error inline."
                ),
            }
        if issues:
            line = audit(ws, "gate_reconcile_request", "PreToolUse", "deny",
                         reason="proposal failed schema validation",
                         extra={"command": cmd, "issues": issues[:5]})
            return {
                "permissionDecision": "deny",
                "reason": (
                    f"{proposals[-1].name} failed schema validation: "
                    + "; ".join(issues[:5])
                ),
                "remediation": (
                    "Re-invoke the `wiki-concept-reconciliation` prompt — the "
                    "orchestrator's compile step has a bug or a subagent return "
                    "was malformed."
                ),
                "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
            }

    audit(ws, "gate_reconcile_request", "PreToolUse", "allow",
          extra={"command": cmd, "proposal_count": len(proposals),
                 "subagent_returns": has_returns})
    return {"permissionDecision": "allow"}


@register("gate_cross_source_synthesis_returns")
def gate_cross_source_synthesis_returns(payload: dict[str, Any]) -> dict[str, Any]:
    """Block `meta-compiler wiki-apply-cross-source-synthesis` unless the
    Phase B preflight has run AND at least one subagent return exists.

    The workflow is:
      1. `wiki-cross-source-synthesize` (writes work_plan + cross_source_request)
      2. `wiki-cross-source-synthesis` prompt fan-out (writes per-page JSON
         to runtime/wiki_cross_source/subagent_returns/)
      3. `wiki-apply-cross-source-synthesis` (rewrites v2 pages) — this gate.
    """
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str):
        return {"permissionDecision": "allow"}
    if "wiki-apply-cross-source-synthesis" not in cmd or not cmd.strip().startswith(
        "meta-compiler"
    ):
        return {"permissionDecision": "allow"}

    disabled, ov_reason = is_disabled("gate_cross_source_synthesis_returns", ws)
    if disabled:
        audit(ws, "gate_cross_source_synthesis_returns", "PreToolUse",
              "allow_override", reason=ov_reason, extra={"command": cmd})
        return {
            "permissionDecision": "allow",
            "systemMessage": (
                f"gate_cross_source_synthesis_returns disabled by override: {ov_reason}"
            ),
        }

    work_plan_path = (
        ws / "workspace-artifacts" / "runtime" / "wiki_cross_source" / "work_plan.yaml"
    )
    if not work_plan_path.exists():
        line = audit(ws, "gate_cross_source_synthesis_returns", "PreToolUse",
                     "deny", reason="work_plan.yaml missing",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": (
                "wiki_cross_source/work_plan.yaml is missing — Phase B preflight "
                "did not run."
            ),
            "remediation": (
                "Run `meta-compiler wiki-cross-source-synthesize --version 2` "
                "first, then invoke the wiki-cross-source-synthesis prompt, "
                "then retry this CLI."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    returns_dir = (
        ws / "workspace-artifacts" / "runtime" / "wiki_cross_source" / "subagent_returns"
    )
    return_files = list(returns_dir.glob("*.json")) if returns_dir.exists() else []
    if not return_files:
        line = audit(ws, "gate_cross_source_synthesis_returns", "PreToolUse",
                     "deny", reason="no subagent JSON returns",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": (
                "No subagent JSON returns at "
                "runtime/wiki_cross_source/subagent_returns/ — the orchestrator "
                "fan-out has not produced any output yet."
            ),
            "remediation": (
                "Invoke the `wiki-cross-source-synthesis` prompt — it writes "
                "per-page JSON to runtime/wiki_cross_source/subagent_returns/ — "
                "before running `meta-compiler wiki-apply-cross-source-synthesis`."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    audit(ws, "gate_cross_source_synthesis_returns", "PreToolUse", "allow",
          extra={"command": cmd, "subagent_return_count": len(return_files)})
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


# ---------------------------------------------------------------------------
# Stage 3 capability-compile hooks (Commit 3 of Stage-3 rearchitecture)
# ---------------------------------------------------------------------------


# Hand-rolled mirror of meta_compiler.schemas.Capability. Keeps meta_hook.py
# stdlib-only (its docstring contract) — DO NOT import pydantic here.
CAPABILITY_REQUIRED_SCALARS: list[str] = [
    "name",
    "description",
    "io_contract_ref",
    "verification_type",
]
CAPABILITY_REQUIRED_LISTS: list[str] = [
    "when_to_use",
    "required_finding_ids",
    "verification_hook_ids",
    "requirement_ids",
    "citation_ids",
]
CAPABILITY_GRAPH_REQUIRED: list[str] = [
    "generated_at",
    "decision_log_version",
    "project_type",
    "capabilities",
]
VALID_VERIFICATION_TYPES: frozenset[str] = frozenset({
    "unit_test",
    "numerical",
    "regression",
    "contract_fixture",
    "static_lint",
    "human_review",
})


# Stop-word list duplicates meta_compiler.findings_loader.GENERIC_TRIGGER_STOPWORDS
# (kept in sync manually; validated by tests/test_hooks_integration.py).
TRIGGER_STOPWORDS: frozenset[str] = frozenset({
    "use", "when", "implementing", "generating", "producing", "running",
    "executing", "the", "a", "an", "to", "for", "and", "or", "of", "in",
    "on", "with", "needed", "required", "any", "every", "this", "that",
    "is", "are", "be", "it", "at", "by", "as", "from", "into", "task",
    "work", "item",
})


import re as _hook_re


def _tokenize_trigger(text: str) -> set[str]:
    tokens = _hook_re.split(r"[^a-z0-9]+", text.lower())
    return {t for t in tokens if t}


def _trigger_content_tokens(text: str) -> set[str]:
    return _tokenize_trigger(text) - TRIGGER_STOPWORDS


def _concept_vocabulary_from_findings(findings_dir: Path) -> set[str]:
    vocab: set[str] = set()
    if not findings_dir.exists():
        return vocab
    for json_path in sorted(findings_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        # Doc/code shape: concepts at top-level.
        for concept in data.get("concepts") or []:
            if isinstance(concept, dict):
                name = str(concept.get("name") or "")
                vocab |= _tokenize_trigger(name)
                aliases = concept.get("aliases") or []
                if isinstance(aliases, list):
                    for alias in aliases:
                        vocab |= _tokenize_trigger(str(alias))
        # Legacy shape: findings[].claim text.
        for row in data.get("findings") or []:
            if isinstance(row, dict):
                claim = row.get("claim")
                if isinstance(claim, str):
                    vocab |= _tokenize_trigger(claim)
        for claim in data.get("claims") or []:
            if isinstance(claim, dict):
                statement = claim.get("statement")
                if isinstance(statement, str):
                    vocab |= _tokenize_trigger(statement)
    return vocab


def _decision_log_vocabulary(decision_log: dict[str, Any]) -> set[str]:
    # Kept in sync with meta_compiler.findings_loader.decision_log_vocabulary.
    vocab: set[str] = set()
    root = decision_log.get("decision_log") or {}
    for row in root.get("conventions") or []:
        if isinstance(row, dict):
            for key in ("name", "choice", "rationale"):
                vocab |= _tokenize_trigger(str(row.get(key) or ""))
    for row in root.get("architecture") or []:
        if isinstance(row, dict):
            for key in ("component", "approach"):
                vocab |= _tokenize_trigger(str(row.get(key) or ""))
    for row in root.get("code_architecture") or []:
        if isinstance(row, dict):
            for key in ("aspect", "choice", "rationale"):
                vocab |= _tokenize_trigger(str(row.get(key) or ""))
            for lib in row.get("libraries") or []:
                if isinstance(lib, dict):
                    for key in ("name", "description"):
                        vocab |= _tokenize_trigger(str(lib.get(key) or ""))
    for row in root.get("requirements") or []:
        if isinstance(row, dict):
            vocab |= _tokenize_trigger(str(row.get("description") or ""))
    scope = root.get("scope") or {}
    for key in ("in_scope", "out_of_scope"):
        for row in scope.get(key) or []:
            if isinstance(row, dict):
                vocab |= _tokenize_trigger(str(row.get("item") or ""))
    return vocab


@register("validate_capability_coverage")
def validate_capability_coverage(payload: dict[str, Any]) -> dict[str, Any]:
    """PreToolUse/Bash on `validate-stage --stage 3`.

    Enforces that every REQ-NNN in the latest decision log is covered by
    >=1 capability in the latest scaffold. Mirrors check #8 of the
    validator so the stage-3 gate fires even if the operator skips
    validate-stage.
    """
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""

    if os.environ.get("META_COMPILER_SKIP_HOOK") == "1":
        return {"permissionDecision": "allow"}

    sub = _parse_meta_compiler_command(cmd)
    if sub != "validate-stage":
        return {"permissionDecision": "allow"}
    if "--stage" not in cmd:
        return {"permissionDecision": "allow"}
    # Only fire for --stage 3 (exact match).
    if not _hook_re.search(r"--stage\s+3(?:\s|$)", cmd):
        return {"permissionDecision": "allow"}

    # Find latest scaffold version.
    scaffolds_dir = ws / "workspace-artifacts" / "scaffolds"
    if not scaffolds_dir.exists():
        return {"permissionDecision": "allow"}
    scaffold_versions: list[tuple[int, Path]] = []
    for entry in scaffolds_dir.iterdir():
        match = _hook_re.match(r"v(\d+)$", entry.name)
        if match and entry.is_dir():
            scaffold_versions.append((int(match.group(1)), entry))
    if not scaffold_versions:
        return {"permissionDecision": "allow"}
    scaffold_versions.sort(key=lambda x: x[0])
    latest_version, scaffold_root = scaffold_versions[-1]

    cap_path = scaffold_root / "capabilities.yaml"
    if not cap_path.exists():
        return {"permissionDecision": "allow"}
    dl_path = ws / "workspace-artifacts" / "decision-logs" / f"decision_log_v{latest_version}.yaml"
    if not dl_path.exists():
        return {"permissionDecision": "allow"}

    try:
        caps_data = _parse_yaml_subset(cap_path.read_text(encoding="utf-8"))
        dl_data = _parse_yaml_subset(dl_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"permissionDecision": "allow"}

    capabilities = ((caps_data or {}).get("capability_graph") or {}).get("capabilities") or []
    covered: set[str] = set()
    for cap in capabilities:
        if isinstance(cap, dict):
            for rid in cap.get("requirement_ids") or []:
                covered.add(str(rid))
    requirements = ((dl_data or {}).get("decision_log") or {}).get("requirements") or []
    missing: list[str] = []
    for row in requirements:
        if isinstance(row, dict):
            rid = str(row.get("id") or "")
            if rid and rid not in covered:
                missing.append(rid)

    if not missing:
        audit(ws, "validate_capability_coverage", "PreToolUse", "allow",
              extra={"capability_count": len(capabilities)})
        return {"permissionDecision": "allow"}

    line = audit(ws, "validate_capability_coverage", "PreToolUse", "deny",
                 reason="uncovered_requirements",
                 extra={"missing": missing[:12]})
    return {
        "permissionDecision": "deny",
        "reason": (
            f"Stage 3 capability coverage gate: {len(missing)} requirement(s) "
            f"uncovered: {', '.join(missing[:6])}."
        ),
        "remediation": (
            "Every decision-log requirement must map to >=1 capability. "
            "Add citations to the uncovered REQ rows and re-run compile-capabilities."
        ),
        "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
    }


@register("gate_capability_compile")
def gate_capability_compile(payload: dict[str, Any]) -> dict[str, Any]:
    """PreToolUse/Bash precondition for `compile-capabilities` and `scaffold`.

    Denies when:
    - workspace_manifest.research.last_completed_stage is not '2' (stage ordering).
    - no decision_log_v*.yaml exists (Stage 2 hasn't finalized).
    - wiki/findings/ is empty AND the latest decision log is v>1 (only v1
      bootstraps without findings).

    Operators can override for tests by passing --allow-empty-findings on
    `compile-capabilities`; that flag is detected in the command string and
    bypasses the findings check.
    """
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""

    if os.environ.get("META_COMPILER_SKIP_HOOK") == "1":
        return {"permissionDecision": "allow"}

    sub = _parse_meta_compiler_command(cmd)
    if sub not in {"compile-capabilities", "scaffold"}:
        return {"permissionDecision": "allow"}

    m = read_manifest(ws).get("workspace_manifest") or {}
    stage = (m.get("research") or {}).get("last_completed_stage")
    if stage != "2" and stage != "3":
        line = audit(ws, "gate_capability_compile", "PreToolUse", "deny",
                     reason=f"stage={stage}", extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": (
                f"{sub} requires last_completed_stage='2'; current='{stage}'."
            ),
            "remediation": "Complete Stage 2 (elicit-vision --finalize) first.",
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    decision_logs_dir = ws / "workspace-artifacts" / "decision-logs"
    logs = sorted(decision_logs_dir.glob("decision_log_v*.yaml")) if decision_logs_dir.exists() else []
    if not logs:
        line = audit(ws, "gate_capability_compile", "PreToolUse", "deny",
                     reason="no decision log", extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": "No decision_log_v*.yaml present.",
            "remediation": "Run `meta-compiler elicit-vision --finalize` to compile Stage 2.",
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    # Determine latest version by filename.
    def _version_of(p: Path) -> int:
        match = _hook_re.match(r"decision_log_v(\d+)\.yaml$", p.name)
        return int(match.group(1)) if match else -1
    latest_log = max(logs, key=_version_of)
    latest_version = _version_of(latest_log)

    if latest_version > 1 and "--allow-empty-findings" not in cmd:
        findings_dir = ws / "workspace-artifacts" / "wiki" / "findings"
        any_finding = findings_dir.exists() and any(findings_dir.glob("*.json"))
        if not any_finding:
            line = audit(ws, "gate_capability_compile", "PreToolUse", "deny",
                         reason="empty findings beyond v1", extra={"command": cmd})
            return {
                "permissionDecision": "deny",
                "reason": (
                    f"wiki/findings/ is empty but decision log is v{latest_version}. "
                    "Only v1 may bootstrap without findings."
                ),
                "remediation": (
                    "Run `meta-compiler ingest --scope all` to populate findings before "
                    "compile-capabilities, or pass --allow-empty-findings for testing."
                ),
                "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
            }

    # Stage 2.5 plan-extract gate. Plan-implementation is OPTIONAL for v1
    # bootstrap (legacy 1-to-1 capability compile still works); when a plan
    # extract DOES exist it must be consistent with the decision log.
    if sub == "compile-capabilities" and "--allow-no-plan" not in cmd:
        plan_extract_path = (
            ws
            / "workspace-artifacts"
            / "decision-logs"
            / f"plan_extract_v{latest_version}.yaml"
        )
        plan_md_path = (
            ws
            / "workspace-artifacts"
            / "decision-logs"
            / f"implementation_plan_v{latest_version}.md"
        )
        if plan_md_path.exists() and not plan_extract_path.exists():
            line = audit(ws, "gate_capability_compile", "PreToolUse", "deny",
                         reason="plan markdown present but extract missing",
                         extra={"command": cmd})
            return {
                "permissionDecision": "deny",
                "reason": (
                    f"{plan_md_path.name} exists but {plan_extract_path.name} "
                    "has not been generated."
                ),
                "remediation": (
                    "Run `meta-compiler plan-implementation --finalize` to "
                    "extract the capability_plan, or pass --allow-no-plan to "
                    "fall back to the legacy 1-to-1 capability mapping."
                ),
                "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
            }

    return {"permissionDecision": "allow"}


@register("gate_implementation_plan")
def gate_implementation_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """PreToolUse gate for `meta-compiler plan-implementation --finalize`.

    Denies when:
    - The decision log is missing.
    - `decision-logs/implementation_plan_v{N}.md` is missing for the latest
      decision log version (the agent hasn't written it yet).
    """
    ws = resolve_workspace_root(payload)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(cmd, str):
        return {"permissionDecision": "allow"}
    sub = _parse_meta_compiler_command(cmd)
    if sub != "plan-implementation" or "--finalize" not in cmd:
        return {"permissionDecision": "allow"}

    disabled, ov_reason = is_disabled("gate_implementation_plan", ws)
    if disabled:
        audit(ws, "gate_implementation_plan", "PreToolUse", "allow_override",
              reason=ov_reason, extra={"command": cmd})
        return {
            "permissionDecision": "allow",
            "systemMessage": f"gate_implementation_plan disabled by override: {ov_reason}",
        }

    decision_logs_dir = ws / "workspace-artifacts" / "decision-logs"
    logs = (
        sorted(decision_logs_dir.glob("decision_log_v*.yaml"))
        if decision_logs_dir.exists()
        else []
    )
    if not logs:
        line = audit(ws, "gate_implementation_plan", "PreToolUse", "deny",
                     reason="no decision log", extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": "No decision_log_v*.yaml present — Stage 2 has not finalized.",
            "remediation": "Run `meta-compiler elicit-vision --finalize` first.",
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    def _vof(p: Path) -> int:
        m = _hook_re.match(r"decision_log_v(\d+)\.yaml$", p.name)
        return int(m.group(1)) if m else -1

    latest_version = _vof(max(logs, key=_vof))
    plan_path = decision_logs_dir / f"implementation_plan_v{latest_version}.md"
    if not plan_path.exists():
        line = audit(ws, "gate_implementation_plan", "PreToolUse", "deny",
                     reason="implementation_plan markdown missing",
                     extra={"command": cmd})
        return {
            "permissionDecision": "deny",
            "reason": (
                f"{plan_path.name} is missing — the implementation-planner "
                "agent has not written the plan."
            ),
            "remediation": (
                "Run `meta-compiler plan-implementation --start` to render the "
                "brief, then invoke @implementation-planner. The agent must "
                f"write decision-logs/{plan_path.name} before --finalize can run."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    audit(ws, "gate_implementation_plan", "PreToolUse", "allow",
          extra={"command": cmd, "decision_log_version": latest_version})
    return {"permissionDecision": "allow"}


def _validate_capability_payload(
    cap: Any,
    idx: int,
    issues: list[str],
) -> None:
    if not isinstance(cap, dict):
        issues.append(f"capabilities[{idx}] is not a mapping")
        return
    for field in CAPABILITY_REQUIRED_SCALARS:
        value = cap.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"capabilities[{idx}].{field} must be a non-empty string")
    ver = cap.get("verification_type")
    if isinstance(ver, str) and ver not in VALID_VERIFICATION_TYPES:
        issues.append(
            f"capabilities[{idx}].verification_type='{ver}' not in {sorted(VALID_VERIFICATION_TYPES)}"
        )
    for field in CAPABILITY_REQUIRED_LISTS:
        value = cap.get(field)
        if not isinstance(value, list) or not value:
            issues.append(f"capabilities[{idx}].{field} must be a non-empty list")


@register("validate_capability_schema")
def validate_capability_schema(payload: dict[str, Any]) -> dict[str, Any]:
    """PostToolUse/Write on scaffolds/v*/capabilities.yaml.

    Hand-rolled subset validator mirroring meta_compiler.schemas.CapabilityGraph.
    Rejects missing top-level keys, empty capabilities list, non-string scalars,
    missing/empty required lists, and unknown verification_type values.
    """
    ws = resolve_workspace_root(payload)
    fp_str = (payload.get("tool_input") or {}).get("file_path") or ""
    if not fp_str:
        return {"permissionDecision": "allow"}
    try:
        fp = Path(fp_str).resolve()
        rel = fp.relative_to(ws).as_posix()
    except (ValueError, OSError):
        return {"permissionDecision": "allow"}

    if not _hook_re.match(r"^workspace-artifacts/scaffolds/v\d+/capabilities\.yaml$", rel):
        return {"permissionDecision": "allow"}

    def _deny(msg: str) -> dict[str, Any]:
        line = audit(ws, "validate_capability_schema", "PostToolUse", "deny",
                     reason=msg, extra={"file_path": rel})
        return {
            "permissionDecision": "deny",
            "reason": msg,
            "remediation": (
                "Re-run `meta-compiler compile-capabilities` against a schema-valid decision log."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    try:
        text = fp.read_text(encoding="utf-8")
    except OSError as exc:
        return _deny(f"{fp.name}: unreadable ({exc})")
    try:
        data = _parse_yaml_subset(text)
    except ValueError as exc:
        return _deny(f"{fp.name}: unparseable YAML ({exc})")
    if not isinstance(data, dict):
        return _deny(f"{fp.name}: top-level must be a mapping")

    graph = data.get("capability_graph")
    if not isinstance(graph, dict):
        return _deny(f"{fp.name}: missing capability_graph root object")

    issues: list[str] = []
    for field in CAPABILITY_GRAPH_REQUIRED:
        if field not in graph:
            issues.append(f"capability_graph.{field} missing")
    caps = graph.get("capabilities")
    if not isinstance(caps, list) or not caps:
        issues.append("capability_graph.capabilities must be a non-empty list")
    else:
        for idx, cap in enumerate(caps):
            _validate_capability_payload(cap, idx, issues)

    if issues:
        return _deny(f"{fp.name}: " + "; ".join(issues[:8]))

    audit(ws, "validate_capability_schema", "PostToolUse", "allow",
          extra={"file_path": rel, "capability_count": len(caps)})
    return {"permissionDecision": "allow"}


def _available_finding_ids(ws: Path) -> set[str]:
    """Return every finding_id present under wiki/findings/ (citation_id#hash[:12])."""
    findings_dir = ws / "workspace-artifacts" / "wiki" / "findings"
    out: set[str] = set()
    if not findings_dir.exists():
        return out
    for json_path in sorted(findings_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        cid = str(data.get("citation_id") or data.get("source_id") or "").strip()
        file_hash = str(data.get("file_hash") or "").strip()
        if cid and file_hash:
            out.add(f"{cid}#{file_hash[:12]}")
        # Legacy: each row under `findings[]` may have its own citation_id.
        for row in data.get("findings") or []:
            if isinstance(row, dict):
                row_cid = str(row.get("citation_id") or cid).strip()
                row_hash = str(row.get("file_hash") or file_hash or "legacy").strip()
                if row_cid:
                    out.add(f"{row_cid}#{row_hash[:12] if row_hash else 'legacy'}")
    return out


def _available_citation_ids(ws: Path) -> set[str]:
    idx_path = ws / "workspace-artifacts" / "wiki" / "citations" / "index.yaml"
    if not idx_path.exists():
        return set()
    try:
        data = _parse_yaml_subset(idx_path.read_text(encoding="utf-8"))
    except ValueError:
        return set()
    cits = (data.get("citations_index") or {}).get("citations") or {}
    if isinstance(cits, dict):
        return set(cits.keys())
    return set()


@register("validate_skill_finding_citations")
def validate_skill_finding_citations(payload: dict[str, Any]) -> dict[str, Any]:
    """PostToolUse/Write on SKILL.md or capabilities.yaml.

    Verifies every finding_id referenced in frontmatter (or in
    capabilities[*].required_finding_ids) resolves in wiki/findings/.
    Bootstrap exception: if wiki/findings/ is empty AND the file targets
    decision_log_version == 1, allow finding_ids that resolve against
    wiki/citations/index.yaml.
    """
    ws = resolve_workspace_root(payload)
    fp_str = (payload.get("tool_input") or {}).get("file_path") or ""
    if not fp_str:
        return {"permissionDecision": "allow"}
    try:
        fp = Path(fp_str).resolve()
        rel = fp.relative_to(ws).as_posix()
    except (ValueError, OSError):
        return {"permissionDecision": "allow"}

    is_capabilities = bool(_hook_re.match(
        r"^workspace-artifacts/scaffolds/v\d+/capabilities\.yaml$", rel
    ))
    is_skill = bool(_hook_re.match(
        r"^workspace-artifacts/scaffolds/v\d+/skills/[^/]+/SKILL\.md$", rel
    ))
    if not is_capabilities and not is_skill:
        return {"permissionDecision": "allow"}

    def _deny(msg: str) -> dict[str, Any]:
        line = audit(ws, "validate_skill_finding_citations", "PostToolUse", "deny",
                     reason=msg, extra={"file_path": rel})
        return {
            "permissionDecision": "deny",
            "reason": msg,
            "remediation": (
                "Ensure every referenced finding_id exists in wiki/findings/ "
                "(finding_id = citation_id#file_hash[:12]), or add the citation to "
                "wiki/citations/index.yaml for v1 bootstrap."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    try:
        text = fp.read_text(encoding="utf-8")
    except OSError as exc:
        return _deny(f"{fp.name}: unreadable ({exc})")

    referenced: list[tuple[str, str]] = []  # (owner_label, finding_id)
    target_version: int | None = None
    if is_capabilities:
        try:
            data = _parse_yaml_subset(text)
        except ValueError as exc:
            return _deny(f"{fp.name}: unparseable YAML ({exc})")
        graph = (data or {}).get("capability_graph")
        if not isinstance(graph, dict):
            return {"permissionDecision": "allow"}  # schema hook will flag
        target_version = graph.get("decision_log_version")
        for cap in graph.get("capabilities") or []:
            if not isinstance(cap, dict):
                continue
            name = str(cap.get("name") or "<unknown>")
            for fid in cap.get("required_finding_ids") or []:
                referenced.append((name, str(fid)))
    else:
        if not text.startswith("---"):
            return {"permissionDecision": "allow"}
        _, _, remainder = text.partition("---")
        fm_text, _, _ = remainder.partition("---")
        try:
            fm = _parse_yaml_subset(fm_text)
        except ValueError as exc:
            return _deny(f"{fp.name}: unparseable frontmatter ({exc})")
        if not isinstance(fm, dict):
            return _deny(f"{fp.name}: frontmatter must be a mapping")
        name = str(fm.get("name") or fp.parent.name)
        for fid in fm.get("required_finding_ids") or []:
            referenced.append((name, str(fid)))
        for finding in fm.get("findings") or []:
            if isinstance(finding, dict):
                fid = str(finding.get("finding_id") or "")
                if fid:
                    referenced.append((name, fid))
        # Discover target version from sibling capabilities.yaml.
        if target_version is None:
            cap_yaml = fp.parent.parent.parent / "capabilities.yaml"
            if cap_yaml.exists():
                try:
                    sib = _parse_yaml_subset(cap_yaml.read_text(encoding="utf-8"))
                    target_version = (sib.get("capability_graph") or {}).get("decision_log_version")
                except (OSError, ValueError):
                    pass

    known_findings = _available_finding_ids(ws)
    bootstrap_allowed = not known_findings and (
        isinstance(target_version, int) and target_version == 1
    )
    known_citations = _available_citation_ids(ws) if bootstrap_allowed else set()

    unresolved: list[str] = []
    for owner, fid in referenced:
        if fid in known_findings:
            continue
        if bootstrap_allowed and fid in known_citations:
            continue
        # Also allow if the citation-id prefix of a finding_id matches a known
        # citation and we're in bootstrap mode.
        if bootstrap_allowed and "#" in fid:
            cite_part = fid.split("#", 1)[0]
            if cite_part in known_citations:
                continue
        unresolved.append(f"{owner}:{fid}")

    if unresolved:
        return _deny(f"{fp.name}: unresolved finding_ids — " + "; ".join(unresolved[:8]))

    audit(ws, "validate_skill_finding_citations", "PostToolUse", "allow",
          extra={"file_path": rel, "referenced_count": len(referenced)})
    return {"permissionDecision": "allow"}


@register("validate_trigger_specificity")
def validate_trigger_specificity(payload: dict[str, Any]) -> dict[str, Any]:
    """PostToolUse/Write on capabilities.yaml or SKILL.md.

    A trigger passes if, after stripping stop-words, it contains at least one
    token present in the concept vocabulary from wiki/findings/*.json. If
    findings are empty, falls back to decision-log vocabulary (bootstrap mode)
    when the capabilities file targets decision_log_version == 1.
    """
    ws = resolve_workspace_root(payload)
    fp_str = (payload.get("tool_input") or {}).get("file_path") or ""
    if not fp_str:
        return {"permissionDecision": "allow"}
    try:
        fp = Path(fp_str).resolve()
        rel = fp.relative_to(ws).as_posix()
    except (ValueError, OSError):
        return {"permissionDecision": "allow"}

    is_capabilities = bool(_hook_re.match(
        r"^workspace-artifacts/scaffolds/v\d+/capabilities\.yaml$", rel
    ))
    is_skill = bool(_hook_re.match(
        r"^workspace-artifacts/scaffolds/v\d+/skills/[^/]+/SKILL\.md$", rel
    ))
    if not is_capabilities and not is_skill:
        return {"permissionDecision": "allow"}

    def _deny(msg: str) -> dict[str, Any]:
        line = audit(ws, "validate_trigger_specificity", "PostToolUse", "deny",
                     reason=msg, extra={"file_path": rel})
        return {
            "permissionDecision": "deny",
            "reason": msg,
            "remediation": (
                "Rewrite the trigger so it includes at least one token drawn from the "
                "cited finding's concept names or from the decision-log description."
            ),
            "audit_ref": f"workspace-artifacts/runtime/hook_audit.log:{line}" if line else None,
        }

    findings_dir = ws / "workspace-artifacts" / "wiki" / "findings"
    vocab = _concept_vocabulary_from_findings(findings_dir)
    bootstrap_vocab: set[str] = set()
    target_version: int | None = None

    if is_capabilities:
        try:
            data = _parse_yaml_subset(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return _deny(f"{fp.name}: could not parse ({exc})")
        graph = data.get("capability_graph") if isinstance(data, dict) else None
        if not isinstance(graph, dict):
            return {"permissionDecision": "allow"}  # schema hook will flag
        target_version = graph.get("decision_log_version")
        triggers_per_cap: list[tuple[str, list[str]]] = []
        caps = graph.get("capabilities") or []
        for cap in caps:
            if not isinstance(cap, dict):
                continue
            cap_name = str(cap.get("name") or "<unknown>")
            trigs = cap.get("when_to_use") or []
            if isinstance(trigs, list):
                triggers_per_cap.append((cap_name, [str(t) for t in trigs]))
    else:
        # Skill: parse YAML frontmatter between --- ... --- lines.
        try:
            text = fp.read_text(encoding="utf-8")
        except OSError as exc:
            return _deny(f"{fp.name}: unreadable ({exc})")
        if not text.startswith("---"):
            return {"permissionDecision": "allow"}
        _, _, remainder = text.partition("---")
        fm_text, _, _ = remainder.partition("---")
        try:
            fm = _parse_yaml_subset(fm_text)
        except ValueError as exc:
            return _deny(f"{fp.name}: unparseable frontmatter ({exc})")
        if not isinstance(fm, dict):
            return _deny(f"{fp.name}: frontmatter is not a mapping")
        trigs = fm.get("triggers") or []
        if not isinstance(trigs, list):
            return _deny(f"{fp.name}: frontmatter.triggers must be a list")
        triggers_per_cap = [(str(fm.get("name") or fp.parent.name), [str(t) for t in trigs])]

    if not vocab and target_version is None:
        # Skill files don't carry decision_log_version; look it up from the
        # sibling capabilities.yaml if possible.
        cap_yaml = fp.parent.parent.parent / "capabilities.yaml"
        if cap_yaml.exists():
            try:
                sibling = _parse_yaml_subset(cap_yaml.read_text(encoding="utf-8"))
                target_version = (sibling.get("capability_graph") or {}).get("decision_log_version")
            except (OSError, ValueError):
                pass

    if not vocab:
        # Bootstrap vocabulary from the latest decision log.
        if target_version:
            dl_path = ws / "workspace-artifacts" / "decision-logs" / f"decision_log_v{target_version}.yaml"
            if dl_path.exists():
                try:
                    dl_data = _parse_yaml_subset(dl_path.read_text(encoding="utf-8"))
                    bootstrap_vocab = _decision_log_vocabulary(dl_data)
                except (OSError, ValueError):
                    pass

    effective_vocab = vocab or bootstrap_vocab
    if not effective_vocab:
        # Degenerate case: no vocabulary available. Only reject pure stopword
        # triggers — permits bootstraps where the decision log is extremely sparse.
        pass

    offenders: list[str] = []
    for cap_name, triggers in triggers_per_cap:
        for trigger in triggers:
            content = _trigger_content_tokens(trigger)
            if not content:
                offenders.append(f"{cap_name}: '{trigger}' (all stopwords)")
                continue
            if not effective_vocab:
                continue
            if not (content & effective_vocab):
                offenders.append(f"{cap_name}: '{trigger}' (no domain tokens in vocabulary)")

    if offenders:
        return _deny(f"{fp.name}: generic triggers — " + "; ".join(offenders[:6]))

    audit(ws, "validate_trigger_specificity", "PostToolUse", "allow",
          extra={"file_path": rel, "trigger_count": sum(len(t) for _, t in triggers_per_cap)})
    return {"permissionDecision": "allow"}


# ---------------------------------------------------------------------------
# Auto-fire chains (existing infrastructure, unchanged below)
# ---------------------------------------------------------------------------

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
