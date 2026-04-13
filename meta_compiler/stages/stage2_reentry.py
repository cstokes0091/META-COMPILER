"""stage2-reentry command: Revise Decision Log when scope or use case changes.

Loads the latest Decision Log, creates a new version with revision metadata,
performs cascade analysis on changed sections, and produces a template for
the LLM assistant to fill via human dialog.
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
from ..validation import validate_decision_log
from ..wiki_interface import WikiQueryInterface


# Maps each section to downstream sections it can affect
CASCADE_MAP: dict[str, list[str]] = {
    "conventions": ["architecture", "scope", "requirements", "agents_needed"],
    "architecture": ["scope", "requirements", "agents_needed"],
    "scope": ["requirements", "agents_needed"],
    "requirements": ["agents_needed"],
    "agents_needed": [],
}

REVISABLE_SECTIONS = {"conventions", "architecture", "scope", "requirements", "open_items", "agents_needed"}


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
            flags.append(f"Changing '{section}' may invalidate '{downstream}' decisions.")

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


def _create_revision_template(
    prior_log: dict[str, Any],
    revised_sections: list[str],
    version: int,
    reason: str,
    workspace_root: Path,
) -> dict[str, Any]:
    """Create a new Decision Log version, marking revised sections for re-fill."""
    prior = prior_log["decision_log"]
    prior_meta = prior.get("meta", {})

    problem_path = workspace_root / "PROBLEM_STATEMENT.md"
    ps_hash = sha256_bytes(
        read_text_safe(problem_path).encode("utf-8")
    ) if problem_path.exists() else sha256_bytes(b"")

    new_meta = {
        "project_name": prior_meta.get("project_name", ""),
        "project_type": prior_meta.get("project_type", "algorithm"),
        "created": iso_now(),
        "version": version,
        "parent_version": version - 1,
        "reason_for_revision": reason,
        "problem_statement_hash": ps_hash,
        "wiki_version": prior_meta.get("wiki_version", ""),
        "use_case": prior_meta.get("use_case", ""),
    }

    new_log: dict[str, Any] = {"decision_log": {"meta": new_meta}}

    for section in ["conventions", "architecture", "scope", "requirements", "open_items", "agents_needed"]:
        if section in revised_sections:
            # Clear for re-fill, but preserve as _prior for reference
            if section == "scope":
                new_log["decision_log"][section] = {"in_scope": [], "out_of_scope": []}
            else:
                new_log["decision_log"][section] = []
            new_log["decision_log"][f"_prior_{section}"] = prior.get(section, [])
        else:
            # Retain unchanged
            new_log["decision_log"][section] = prior.get(section, [] if section != "scope" else {"in_scope": [], "out_of_scope": []})

    return new_log


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
        raise RuntimeError("No existing Decision Log found. Run elicit-vision first.")

    prior_version, prior_path = latest
    prior_log = load_yaml(prior_path)
    if not prior_log:
        raise RuntimeError(f"Decision Log is empty: {prior_path}")

    # Validate sections
    invalid = [s for s in sections if s not in REVISABLE_SECTIONS]
    if invalid:
        raise RuntimeError(
            f"Invalid sections for revision: {invalid}. "
            f"Valid: {sorted(REVISABLE_SECTIONS)}"
        )
    if not sections:
        raise RuntimeError("Must specify at least one section to revise.")

    new_version = prior_version + 1

    # Cascade analysis
    cascade = _cascade_analysis(sections)

    # Create revision template
    new_log = _create_revision_template(
        prior_log=prior_log,
        revised_sections=sections,
        version=new_version,
        reason=reason,
        workspace_root=workspace_root,
    )

    # Save the template (with _prior_ fields for reference)
    template_path = paths.runtime_dir / f"decision_log_v{new_version}_template.yaml"
    dump_yaml(template_path, new_log)

    # Save cascade report
    cascade_report = {
        "cascade_report": {
            "generated_at": iso_now(),
            "parent_version": prior_version,
            "new_version": new_version,
            "reason": reason,
            **cascade,
        }
    }
    dump_yaml(paths.runtime_dir / f"cascade_report_v{new_version}.yaml", cascade_report)

    # Also write a clean version (without _prior_ fields) for validation later
    clean_log = {"decision_log": {}}
    for key, value in new_log["decision_log"].items():
        if not key.startswith("_prior_"):
            clean_log["decision_log"][key] = value

    # Write the prompt context file for the LLM assistant
    wiki = WikiQueryInterface(paths=paths, prefer_v2=True)
    context_path = paths.runtime_dir / f"reentry_context_v{new_version}.md"
    _write_reentry_context(
        context_path=context_path,
        prior_log=prior_log,
        sections=sections,
        cascade=cascade,
        wiki=wiki,
        reason=reason,
        new_version=new_version,
    )

    # Update manifest
    wm = manifest["workspace_manifest"]
    research = wm.setdefault("research", {})
    research["last_completed_stage"] = "2-reentry"
    research["reentry_version"] = new_version
    save_manifest(paths, manifest)

    return {
        "status": "template_created",
        "new_version": new_version,
        "parent_version": prior_version,
        "sections_to_revise": sections,
        "cascade": cascade,
        "template_path": str(template_path),
        "context_path": str(context_path),
        "next_step": (
            "the LLM assistant should read the reentry context file and conduct "
            "an asymmetric dialog with the user to fill revised sections. "
            "Then run: meta-compiler finalize-reentry"
        ),
    }


def _write_reentry_context(
    context_path: Path,
    prior_log: dict[str, Any],
    sections: list[str],
    cascade: dict[str, Any],
    wiki: WikiQueryInterface,
    reason: str,
    new_version: int,
) -> None:
    """Write a context file that the LLM assistant reads to conduct the re-entry dialog."""
    prior = prior_log["decision_log"]
    lines = [
        "# Stage 2 Re-entry Context",
        "",
        f"## Revision: v{new_version}",
        f"**Reason:** {reason}",
        f"**Sections to revise:** {', '.join(sections)}",
        "",
        "## Cascade Analysis",
    ]
    for flag in cascade.get("cascade_flags", []):
        lines.append(f"- {flag}")
    affected = cascade.get("affected_downstream", [])
    if affected:
        lines.append(f"- **Also review:** {', '.join(affected)}")
    lines.append("")

    # Include prior decisions for each revised section
    for section in sections:
        lines.append(f"## Prior {section.replace('_', ' ').title()}")
        prior_data = prior.get(section, [])
        if isinstance(prior_data, list):
            for idx, item in enumerate(prior_data):
                if isinstance(item, dict):
                    lines.append(f"  {idx + 1}. {item}")
                else:
                    lines.append(f"  {idx + 1}. {item}")
        elif isinstance(prior_data, dict):
            for key, value in prior_data.items():
                lines.append(f"  - {key}: {value}")
        lines.append("")

    # Include wiki search hints
    lines.extend([
        "## Wiki Resources",
        "Use wiki tool interface to query for alternatives not previously considered.",
        "",
    ])
    open_questions = wiki.get_open_questions()
    if open_questions:
        lines.append("### Open Questions from Wiki")
        for q in open_questions[:10]:
            lines.append(f"- [{q['concept']}] {q['question']}")
        lines.append("")

    lines.extend([
        "## Instructions for the LLM assistant",
        "",
        "1. Read this context and the prior Decision Log",
        "2. For each section marked for revision, conduct asymmetric dialog:",
        "   - Present the prior decision and why it was made",
        "   - Query wiki for alternatives not previously considered",
        "   - Ask the user targeted questions to narrow the revised choice",
        "   - Capture the new decision with rationale and citations",
        "3. For unchanged sections, note 'Retained from v{N}'",
        "4. Check cascade analysis and confirm affected downstream sections",
        "5. Save the completed Decision Log and run validation:",
        f"   meta-compiler validate-stage --stage 2",
        "",
    ])

    context_path.write_text("\n".join(lines), encoding="utf-8")


def run_finalize_reentry(
    artifacts_root: Path,
    version: int | None = None,
) -> dict[str, Any]:
    """Finalize a re-entry Decision Log after the LLM assistant fills it."""
    paths = build_paths(artifacts_root)

    if version is None:
        # Find latest template
        templates = sorted(paths.runtime_dir.glob("decision_log_v*_template.yaml"))
        if not templates:
            raise RuntimeError("No re-entry template found. Run stage2-reentry first.")
        template_path = templates[-1]
        stem = template_path.stem
        version = int(stem.split("_v")[1].split("_")[0])
    else:
        template_path = paths.runtime_dir / f"decision_log_v{version}_template.yaml"

    if not template_path.exists():
        raise RuntimeError(f"Template not found: {template_path}")

    log = load_yaml(template_path)
    if not log:
        raise RuntimeError(f"Template is empty: {template_path}")

    # Remove _prior_ fields before validation
    clean_log: dict[str, Any] = {"decision_log": {}}
    for key, value in log.get("decision_log", {}).items():
        if not key.startswith("_prior_"):
            clean_log["decision_log"][key] = value

    issues = validate_decision_log(clean_log)
    if issues:
        raise RuntimeError("Decision Log validation failed:\n" + "\n".join(issues))

    # Save finalized Decision Log
    decision_log_path = paths.decision_logs_dir / f"decision_log_v{version}.yaml"
    dump_yaml(decision_log_path, clean_log)

    # Update manifest
    manifest = load_manifest(paths)
    if manifest:
        wm = manifest["workspace_manifest"]
        decision_logs = wm.setdefault("decision_logs", [])
        entry = {
            "version": version,
            "created": clean_log["decision_log"]["meta"]["created"],
            "parent_version": clean_log["decision_log"]["meta"].get("parent_version"),
            "reason_for_revision": clean_log["decision_log"]["meta"].get("reason_for_revision"),
            "use_case": clean_log["decision_log"]["meta"].get("use_case"),
            "scaffold_path": None,
        }
        existing = None
        for row in decision_logs:
            if isinstance(row, dict) and row.get("version") == version:
                existing = row
                break
        if existing is None:
            decision_logs.append(entry)
        else:
            existing.update(entry)

        research = wm.setdefault("research", {})
        research["last_completed_stage"] = "2"
        save_manifest(paths, manifest)

    # Clean up template
    template_path.unlink(missing_ok=True)

    return {
        "status": "finalized",
        "version": version,
        "decision_log_path": str(decision_log_path),
    }
