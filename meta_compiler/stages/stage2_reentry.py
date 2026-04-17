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
    "conventions": ["architecture", "scope", "requirements", "agents_needed"],
    "architecture": ["scope", "requirements", "agents_needed"],
    "scope": ["requirements", "agents_needed"],
    "requirements": ["agents_needed"],
    "agents_needed": [],
}

# Sections the human can ask to revise. "scope" is a single unit at the
# human-facing level; it splits into scope-in / scope-out Sections at the
# decision-block level.
REVISABLE_SECTIONS = {
    "conventions",
    "architecture",
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


def _render_agent_block(row: dict[str, Any], parent_version: int) -> list[str]:
    reads = row.get("reads") or []
    writes = row.get("writes") or []
    constraints = row.get("key_constraints") or []
    return [
        f"### Decision: {row.get('role', 'agent')}",
        f"- Section: agents_needed",
        f"- Role: {row.get('role', '')}",
        f"- Responsibility: {row.get('responsibility', '')}",
        f"- Reads: {', '.join(str(r) for r in reads) if reads else '(none)'}",
        f"- Writes: {', '.join(str(w) for w in writes) if writes else '(none)'}",
        (
            f"- Key constraints: "
            f"{', '.join(str(c) for c in constraints) if constraints else '(none)'}"
        ),
        f"- Rationale: Carried from v{parent_version}.",
        f"- Citations: {_format_citations(row.get('citations'))}",
        "",
    ]


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
    reason: str,
    sections: list[str],
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

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
        "cascade": cascade,
        "transcript_path": str(
            paths.stage2_transcript_path.relative_to(paths.root).as_posix()
        ),
        "cascade_report_path": str(
            cascade_report_path.relative_to(paths.root).as_posix()
        ),
        "next_step": (
            "Open .github/prompts/stage-2-dialog.prompt.md in your LLM runtime and walk "
            "its five steps. The seeded transcript already contains carried-forward "
            "decisions; the dialog only needs to author blocks for the revised "
            "sections. Then run `meta-compiler elicit-vision --finalize`."
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
