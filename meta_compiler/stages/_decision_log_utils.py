"""Decision-log helpers shared across post-dialogue stages.

Extracted from `scaffold_stage.py` so `capability_compile_stage`,
`contract_extract_stage`, `skill_synthesis_stage`, and
`workspace_bootstrap_stage` can share one resolver and one citation
collector. The older helpers in `scaffold_stage.py` remain in place until
Commit 8 flips the composer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import ArtifactPaths, latest_decision_log_path
from ..io import load_yaml
from ..validation import validate_decision_log


def resolve_decision_log(
    paths: ArtifactPaths,
    decision_log_version: int | None,
) -> tuple[int, Path, dict[str, Any]]:
    """Return (version, path, parsed) for the requested decision log.

    Raises RuntimeError if the log is missing, empty, or fails schema
    validation — matching the behaviour of `scaffold_stage._resolve_decision_log`.
    """
    if decision_log_version is None:
        latest = latest_decision_log_path(paths)
        if latest is None:
            raise RuntimeError("No decision log found. Run elicit-vision first.")
        version, path = latest
    else:
        version = decision_log_version
        path = paths.decision_logs_dir / f"decision_log_v{version}.yaml"
        if not path.exists():
            raise RuntimeError(f"Decision log not found at {path}")

    payload = load_yaml(path)
    if not payload:
        raise RuntimeError(f"Decision log is empty: {path}")

    issues = validate_decision_log(payload)
    if issues:
        raise RuntimeError("Decision Log validation failed:\n" + "\n".join(issues))

    # Back-compat shim: v1 decision logs predate the constraints[] section.
    # Inject an empty list so downstream consumers (capability_compile_stage,
    # workspace_bootstrap_stage, stage2_reentry) can rely on the field being
    # present.
    decision_log = payload.get("decision_log") if isinstance(payload, dict) else None
    if isinstance(decision_log, dict) and decision_log.get("constraints") is None:
        decision_log["constraints"] = []

    return version, path, payload


def as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def collect_citation_ids(root: dict[str, Any]) -> list[str]:
    """Collect citation IDs from a decision-log root (the inner `decision_log` dict)."""
    citations: list[str] = []
    for section in (
        "conventions",
        "architecture",
        "code_architecture",
        "requirements",
        "constraints",
    ):
        for row in root.get(section, []) or []:
            if not isinstance(row, dict):
                continue
            citations.extend(as_string_list(row.get("citations", [])))
    return ordered_unique(citations)
