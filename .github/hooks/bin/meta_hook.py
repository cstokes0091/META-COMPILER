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
# Test-only check (gated by env)
# ---------------------------------------------------------------------------

if os.environ.get("META_COMPILER_HOOK_TEST") == "1":
    @register("_echo_stage_for_test")
    def _echo_stage_for_test(payload: dict[str, Any]) -> dict[str, Any]:
        return {"additionalContext": manifest_stage(resolve_workspace_root(payload))}


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
