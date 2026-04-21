"""stage2-reentry command: Seed a Stage 2 transcript for scope revision.

Under the prompt-as-conductor model (see `.github/docs/stage-2-hardening.md`),
re-entry replaces the old "partial YAML template" flow with a **seeded
transcript**: decisions from the prior Decision Log are rendered as decision
blocks in `workspace-artifacts/runtime/stage2/transcript.md`, except for
sections the human has marked for revision — those are left empty, with the
prior decisions shown in prose for reference.

The operator then runs the `stage-2-dialog` prompt to conduct the revision
conversation, and `meta-compiler elicit-vision --finalize` to compile the
revised Decision Log as `decision_log_v{N+1}.yaml`.

`finalize-reentry` is preserved as a thin alias around
`elicit-vision --finalize` for backward compatibility.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import (
    ArtifactPaths,
    build_paths,
    ensure_layout,
    latest_decision_log_path,
    load_manifest,
    save_manifest,
)
from ..io import dump_yaml, load_yaml
from ..utils import iso_now, read_text_safe, sha256_bytes
from .elicit_stage import run_elicit_vision_finalize


# Maps each section to downstream sections it can affect
CASCADE_MAP: dict[str, list[str]] = {
    "conventions": ["architecture", "code_architecture", "scope", "requirements", "agents_needed"],
    "architecture": ["code_architecture", "scope", "requirements", "agents_needed"],
    "code_architecture": ["agents_needed"],
    "scope": ["requirements", "agents_needed"],
    "requirements": ["agents_needed"],
    "agents_needed": [],
}

# Sections the human can ask to revise. "scope" is a single unit at the
# human-facing level; it splits into scope-in / scope-out Sections at the
# decision-block level. "code_architecture" only applies to algorithm/hybrid
# projects; the run_stage2_reentry caller validates that.
REVISABLE_SECTIONS = {
    "conventions",
    "architecture",
    "code_architecture",
    "scope",
    "requirements",
    "open_items",
    "agents_needed",
}


def _cascade_analysis(revised_sections: list[str]) -> dict[str, Any]:
    """Identify downstream sections affected by revisions."""
    affected: set[str] = set()
    for section in revised_sections:
        for downstream in CASCADE_MAP.get(section, []):
            if downstream not in revised_sections:
                affected.add(downstream)

    flags: list[str] = []
    for section in revised_sections:
        for downstream in CASCADE_MAP.get(section, []):
            flags.append(
                f"Changing '{section}' may invalidate '{downstream}' decisions."
            )

    return {
        "revised_sections": revised_sections,
        "affected_downstream": sorted(affected),
        "cascade_flags": flags,
        "recommendation": (
            "Review affected downstream sections for consistency."
            if affected
            else "No downstream cascade detected."
        ),
    }


# ---------------------------------------------------------------------------
# Rendering the seeded transcript
# ---------------------------------------------------------------------------


def _format_citations(citations: Any) -> str:
    if not isinstance(citations, list) or not citations:
        return "(none)"
    return ", ".join(str(c) for c in citations if c)


def _render_convention_block(row: dict[str, Any], parent_version: int) -> list[str]:
    return [
        f"### Decision: {row.get('name', 'Convention')}",
        f"- Section: conventions",
        f"- Domain: {row.get('domain', 'code')}",
        f"- Choice: {row.get('choice', '')}",
        f"- Rationale: {row.get('rationale', '')} [carried from v{parent_version}]",
        f"- Citations: {_format_citations(row.get('citations'))}",
        "",
    ]


def _render_architecture_block(row: dict[str, Any], parent_version: int) -> list[str]:
    lines = [
        f"### Decision: {row.get('component', 'Component')}",
        f"- Section: architecture",
        f"- Component: {row.get('component', '')}",
        f"- Approach: {row.get('approach', '')}",
    ]
    alts = row.get("alternatives_rejected") or []
    if isinstance(alts, list) and alts:
        lines.append("- Alternatives rejected:")
        for alt in alts:
            if not isinstance(alt, dict):
                continue
            name = alt.get("name", "")
            reason = alt.get("reason", "")
            lines.append(f"  - {name}: {reason}")
    constraints = row.get("constraints_applied") or []
    if isinstance(constraints, list) and constraints:
        lines.append(f"- Constraints applied: {', '.join(str(c) for c in constraints)}")
    else:
        lines.append("- Constraints applied: (none)")
    rationale = row.get("rationale") or ""
    lines.append(f"- Rationale: {rationale} [carried from v{parent_version}]")
    lines.append(f"- Citations: {_format_citations(row.get('citations'))}")
    lines.append("")
    return lines


def _render_scope_in_block(row: dict[str, Any], parent_version: int) -> list[str]:
    return [
        f"### Decision: {row.get('item', 'Scope item')}",
        f"- Section: scope-in",
        f"- Item: {row.get('item', '')}",
        f"- Rationale: {row.get('rationale', '')} [carried from v{parent_version}]",
        f"- Citations: {_format_citations(row.get('citations'))}",
        "",
    ]


def _render_scope_out_block(row: dict[str, Any], parent_version: int) -> list[str]:
    return [
        f"### Decision: {row.get('item', 'Out-of-scope item')}",
        f"- Section: scope-out",
        f"- Item: {row.get('item', '')}",
        f"- Rationale: {row.get('rationale', '')} [carried from v{parent_version}]",
        f"- Revisit if: {row.get('revisit_if', '')}",
        f"- Citations: {_format_citations(row.get('citations'))}",
        "",
    ]


def _render_requirement_block(row: dict[str, Any], parent_version: int) -> list[str]:
    prior_id = row.get("id") or "REQ-???"
    return [
        f"### Decision: {prior_id} — {row.get('description', '')[:80]}",
        f"- Section: requirements",
        f"- Source: {row.get('source', 'derived')}",
        f"- Description: {row.get('description', '')}",
        f"- Verification: {row.get('verification', '')}",
        f"- Lens: {row.get('lens', 'functional')}",
        (
            f"- Rationale: {row.get('rationale') or f'Carried from v{parent_version} {prior_id}.'} "
            f"[carried from v{parent_version}]"
        ),
        f"- Citations: {_format_citations(row.get('citations'))}",
        "",
    ]


def _render_open_item_block(row: dict[str, Any], parent_version: int) -> list[str]:
    return [
        f"### Decision: {row.get('description', 'Open item')[:60]}",
        f"- Section: open_items",
        f"- Description: {row.get('description', '')}",
        f"- Deferred to: {row.get('deferred_to', 'implementation')}",
        f"- Owner: {row.get('owner', 'human')}",
        f"- Rationale: Carried from v{parent_version}.",
        f"- Citations: {_format_citations(row.get('citations'))}",
        "",
    ]


def _render_modality_sublist(items: Any, label: str) -> list[str]:
    if not isinstance(items, list) or not items:
        return [f"- {label}:", f"  - decision_log: document"]
    lines = [f"- {label}:"]
    for entry in items:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        modality = entry.get("modality", "document")
        if not name:
            continue
        lines.append(f"  - {name}: {modality}")
    if len(lines) == 1:
        lines.append(f"  - decision_log: document")
    return lines


def _render_agent_block(row: dict[str, Any], parent_version: int) -> list[str]:
    inputs = row.get("inputs") or []
    outputs = row.get("outputs") or []
    constraints = row.get("key_constraints") or []
    lines = [
        f"### Decision: {row.get('role', 'agent')}",
        f"- Section: agents_needed",
        f"- Role: {row.get('role', '')}",
        f"- Responsibility: {row.get('responsibility', '')}",
    ]
    lines.extend(_render_modality_sublist(inputs, "Inputs"))
    lines.extend(_render_modality_sublist(outputs, "Outputs"))
    lines.append(
        f"- Key constraints: "
        f"{', '.join(str(c) for c in constraints) if constraints else '(none)'}"
    )
    lines.append(f"- Rationale: Carried from v{parent_version}.")
    lines.append(f"- Citations: {_format_citations(row.get('citations'))}")
    lines.append("")
    return lines


def _render_code_arch_block(row: dict[str, Any], parent_version: int) -> list[str]:
    aspect = row.get("aspect", "language")
    lines = [
        f"### Decision: code-arch-{aspect}",
        f"- Section: code-architecture",
        f"- Aspect: {aspect}",
        f"- Choice: {row.get('choice', '')}",
    ]
    libraries = row.get("libraries") or []
    if isinstance(libraries, list) and libraries:
        lines.append("- Libraries:")
        for lib in libraries:
            if not isinstance(lib, dict):
                continue
            name = lib.get("name", "")
            description = lib.get("description", "")
            if name and description:
                lines.append(f"  - {name}: {description}")
    module_layout = row.get("module_layout")
    if isinstance(module_layout, str) and module_layout.strip():
        lines.append(f"- Module layout: {module_layout}")
    alts = row.get("alternatives_rejected") or []
    if isinstance(alts, list) and alts:
        lines.append("- Alternatives rejected:")
        for alt in alts:
            if not isinstance(alt, dict):
                continue
            name = alt.get("name", "")
            reason = alt.get("reason", "")
            if name:
                lines.append(f"  - {name}: {reason}")
    constraints = row.get("constraints_applied") or []
    if isinstance(constraints, list) and constraints:
        lines.append(
            f"- Constraints applied: {', '.join(str(c) for c in constraints)}"
        )
    rationale = row.get("rationale") or ""
    lines.append(f"- Rationale: {rationale} [carried from v{parent_version}]")
    lines.append(f"- Citations: {_format_citations(row.get('citations'))}")
    lines.append("")
    return lines


def _prior_section_prose(row: dict[str, Any], label: str) -> list[str]:
    """Render a prior decision as reference prose under a revised section."""
    return [
        f"_Prior {label} (from the previous Decision Log):_",
        "",
        f"- {row}",
        "",
    ]


def _render_seeded_transcript(
    prior_log: dict[str, Any],
    revised_sections: set[str],
    parent_version: int,
    new_version: int,
    reason: str,
    cascade: dict[str, Any],
    generated_at: str,
) -> str:
    prior = prior_log.get("decision_log", {})
    use_case = (
        prior.get("meta", {}).get("use_case")
        if isinstance(prior.get("meta"), dict)
        else None
    )

    lines: list[str] = []
    if use_case:
        lines.extend(
            [
                "---",
                f"use_case: Re-entry from v{parent_version} — {reason}",
                "---",
                "",
            ]
        )

    lines.extend(
        [
            f"# Stage 2 Transcript — v{new_version} (re-entry from v{parent_version})",
            "",
            f"Generated: {generated_at}",
            f"Revision reason: {reason}",
            f"Revised sections: {', '.join(sorted(revised_sections)) or '(none)'}",
            "",
        ]
    )
    if cascade.get("cascade_flags"):
        lines.append("## Cascade flags")
        lines.append("")
        for flag in cascade["cascade_flags"]:
            lines.append(f"- {flag}")
        lines.append("")
    if cascade.get("affected_downstream"):
        lines.append(
            f"_Downstream sections that may need review: "
            f"{', '.join(cascade['affected_downstream'])}._"
        )
        lines.append("")

    # --- Decision Area: Conventions ---
    lines.extend(["## Decision Area: Conventions", ""])
    if "conventions" in revised_sections:
        lines.extend(
            [
                "_Revising this section. Prior conventions are shown for reference; "
                "write new decision blocks below as the dialog progresses._",
                "",
            ]
        )
        for row in prior.get("conventions", []) or []:
            if isinstance(row, dict):
                lines.extend(_prior_section_prose(row, "convention"))
    else:
        for row in prior.get("conventions", []) or []:
            if isinstance(row, dict):
                lines.extend(_render_convention_block(row, parent_version))

    # --- Decision Area: Architecture ---
    lines.extend(["## Decision Area: Architecture", ""])
    if "architecture" in revised_sections:
        lines.extend(
            [
                "_Revising this section. Prior architecture is shown for reference._",
                "",
            ]
        )
        for row in prior.get("architecture", []) or []:
            if isinstance(row, dict):
                lines.extend(_prior_section_prose(row, "architecture component"))
    else:
        for row in prior.get("architecture", []) or []:
            if isinstance(row, dict):
                lines.extend(_render_architecture_block(row, parent_version))

    # --- Decision Area: Code Architecture (algorithm/hybrid only) ---
    code_arch = prior.get("code_architecture") or []
    if isinstance(code_arch, list) and code_arch:
        lines.extend(["## Decision Area: Code Architecture", ""])
        if "code_architecture" in revised_sections:
            lines.extend(
                [
                    "_Revising code architecture. Prior aspects shown for reference._",
                    "",
                ]
            )
            for row in code_arch:
                if isinstance(row, dict):
                    lines.extend(_prior_section_prose(row, "code-architecture aspect"))
        else:
            for row in code_arch:
                if isinstance(row, dict):
                    lines.extend(_render_code_arch_block(row, parent_version))

    # --- Decision Area: Scope (in) ---
    scope = prior.get("scope", {}) or {}
    in_scope_rows = (
        scope.get("in_scope", []) if isinstance(scope, dict) else []
    ) or []
    out_scope_rows = (
        scope.get("out_of_scope", []) if isinstance(scope, dict) else []
    ) or []

    lines.extend(["## Decision Area: Scope (in)", ""])
    if "scope" in revised_sections:
        lines.extend(
            [
                "_Revising scope. Prior in-scope items shown for reference._",
                "",
            ]
        )
        for row in in_scope_rows:
            if isinstance(row, dict):
                lines.extend(_prior_section_prose(row, "in-scope item"))
    else:
        for row in in_scope_rows:
            if isinstance(row, dict):
                lines.extend(_render_scope_in_block(row, parent_version))

    # --- Decision Area: Scope (out) ---
    lines.extend(["## Decision Area: Scope (out)", ""])
    if "scope" in revised_sections:
        lines.extend(
            [
                "_Revising scope. Prior out-of-scope items shown for reference._",
                "",
            ]
        )
        for row in out_scope_rows:
            if isinstance(row, dict):
                lines.extend(_prior_section_prose(row, "out-of-scope item"))
    else:
        for row in out_scope_rows:
            if isinstance(row, dict):
                lines.extend(_render_scope_out_block(row, parent_version))

    # --- Decision Area: Requirements ---
    lines.extend(["## Decision Area: Requirements", ""])
    if "requirements" in revised_sections:
        lines.extend(
            [
                "_Revising requirements. Prior REQ entries shown for reference; "
                "new REQ-NNN ids are assigned at --finalize time._",
                "",
            ]
        )
        for row in prior.get("requirements", []) or []:
            if isinstance(row, dict):
                lines.extend(_prior_section_prose(row, "requirement"))
    else:
        for row in prior.get("requirements", []) or []:
            if isinstance(row, dict):
                lines.extend(_render_requirement_block(row, parent_version))

    # --- Decision Area: Open Items ---
    lines.extend(["## Decision Area: Open Items", ""])
    if "open_items" in revised_sections:
        lines.extend(
            [
                "_Revising open items. Prior entries shown for reference._",
                "",
            ]
        )
        for row in prior.get("open_items", []) or []:
            if isinstance(row, dict):
                lines.extend(_prior_section_prose(row, "open item"))
    else:
        for row in prior.get("open_items", []) or []:
            if isinstance(row, dict):
                lines.extend(_render_open_item_block(row, parent_version))

    # --- Decision Area: Agents Needed ---
    lines.extend(["## Decision Area: Agents Needed", ""])
    if "agents_needed" in revised_sections:
        lines.extend(
            [
                "_Revising agent roster. Prior entries shown for reference._",
                "",
            ]
        )
        for row in prior.get("agents_needed", []) or []:
            if isinstance(row, dict):
                lines.extend(_prior_section_prose(row, "agent"))
    else:
        for row in prior.get("agents_needed", []) or []:
            if isinstance(row, dict):
                lines.extend(_render_agent_block(row, parent_version))

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def run_stage2_reentry(
    artifacts_root: Path,
    workspace_root: Path,
    reason: str | None = None,
    sections: list[str] | None = None,
    from_request: Path | None = None,
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

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
    if sections is None:
        raise RuntimeError("sections are required (via --sections or request.revised_sections).")

    manifest = load_manifest(paths)
    if not manifest:
        raise RuntimeError("Manifest not found. Run meta-init first.")

    latest = latest_decision_log_path(paths)
    if latest is None:
        raise RuntimeError(
            "No existing Decision Log found. Run `meta-compiler elicit-vision --start` / "
            "`--finalize` for the initial v1 first."
        )

    prior_version, prior_path = latest
    prior_log = load_yaml(prior_path)
    if not prior_log:
        raise RuntimeError(f"Decision Log is empty: {prior_path}")

    invalid = [s for s in sections if s not in REVISABLE_SECTIONS]
    if invalid:
        raise RuntimeError(
            f"Invalid sections for revision: {invalid}. "
            f"Valid: {sorted(REVISABLE_SECTIONS)}"
        )
    if not sections:
        raise RuntimeError("Must specify at least one section to revise.")

    new_version = prior_version + 1
    cascade = _cascade_analysis(sections)
    generated_at = iso_now()

    transcript_text = _render_seeded_transcript(
        prior_log=prior_log,
        revised_sections=set(sections),
        parent_version=prior_version,
        new_version=new_version,
        reason=reason,
        cascade=cascade,
        generated_at=generated_at,
    )
    paths.stage2_transcript_path.write_text(transcript_text, encoding="utf-8")

    cascade_report_path = (
        paths.stage2_runtime_dir / f"cascade_report_v{new_version}.yaml"
    )
    dump_yaml(
        cascade_report_path,
        {
            "cascade_report": {
                "generated_at": generated_at,
                "parent_version": prior_version,
                "new_version": new_version,
                "reason": reason,
                **cascade,
            }
        },
    )

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

    # Track re-entry state in the manifest so the next `--start` knows we're
    # mid-revision and `--finalize` can look up the prior version correctly.
    wm = manifest["workspace_manifest"]
    research = wm.setdefault("research", {})
    research["last_completed_stage"] = "2-reentry-seeded"
    research["reentry_version"] = new_version
    save_manifest(paths, manifest)

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


def run_finalize_reentry(
    artifacts_root: Path,
    version: int | None = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Finalize a re-entry revision.

    Under the new flow this is a thin alias for `elicit-vision --finalize`:
    the transcript produced by `stage2-reentry` lives at the same canonical
    path, so the same compile logic applies. The `version` argument is
    accepted for backward compatibility but ignored — the version is
    derived from the prior Decision Log automatically.
    """
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    ws_root = workspace_root if workspace_root is not None else paths.root.parent

    if version is not None:
        expected_next = None
        latest = latest_decision_log_path(paths)
        if latest is not None:
            expected_next = latest[0] + 1
        if expected_next is not None and expected_next != version:
            raise RuntimeError(
                f"Re-entry version mismatch: prior decision log is v{latest[0]}, "
                f"so finalize-reentry would produce v{expected_next}, but --version={version} "
                "was specified. Re-run without --version, or adjust your command."
            )

    return run_elicit_vision_finalize(
        artifacts_root=artifacts_root,
        workspace_root=ws_root,
    )
