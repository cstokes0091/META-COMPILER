from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from pptx import Presentation
except ImportError:  # pragma: no cover - exercised when dependency missing
    Presentation = None

from ..artifacts import (
    build_paths,
    ensure_layout,
    latest_decision_log_path,
    latest_scaffold_path,
    load_manifest,
    save_manifest,
)
from ..io import dump_yaml, load_yaml
from ..utils import iso_now, read_text_safe


def _require_pptx() -> None:
    if Presentation is None:
        raise RuntimeError(
            "python-pptx is required for Stage 4. Install dependencies with: "
            "python3 -m pip install -r requirements.txt"
        )


def _resolve_scaffold_root(paths, decision_log_version: int | None) -> tuple[int, Path]:
    if decision_log_version is not None:
        scaffold_root = paths.scaffolds_dir / f"v{decision_log_version}"
        if not scaffold_root.exists():
            raise RuntimeError(f"Scaffold root not found: {scaffold_root}")
        return decision_log_version, scaffold_root

    latest = latest_scaffold_path(paths)
    if latest is None:
        raise RuntimeError("No scaffold found. Run scaffold first.")
    return latest


def _resolve_decision_log_version(paths, requested_version: int | None) -> int:
    if requested_version is not None:
        return requested_version
    latest = latest_decision_log_path(paths)
    if latest is None:
        raise RuntimeError("No decision log found. Run elicit-vision first.")
    return latest[0]


def _write_what_i_built_refresh(
    paths,
    decision_log_version: int,
    project_type: str,
    output_dir: Path,
    execution_manifest: dict[str, Any],
) -> Path:
    final_output = execution_manifest.get("final_output", {}) if isinstance(execution_manifest, dict) else {}
    deliverables = final_output.get("deliverables", []) if isinstance(final_output, dict) else []
    notes = final_output.get("execution_notes", []) if isinstance(final_output, dict) else []

    lines = [
        "## What I Built",
        "",
        f"- Decision Log Version: v{decision_log_version}",
        f"- Project type: {project_type}",
        f"- Output directory: {output_dir}",
        "",
        "### Final Deliverables",
    ]

    if isinstance(deliverables, list) and deliverables:
        for row in deliverables:
            if not isinstance(row, dict):
                continue
            lines.append(f"- {row.get('kind')}: {row.get('path')}")
    else:
        lines.append("- No deliverables recorded.")

    lines.extend(["", "### Execution Notes"])
    if isinstance(notes, list) and notes:
        for note in notes:
            lines.append(f"- {note}")
    else:
        lines.append("- No execution notes recorded.")

    lines.extend(
        [
            "",
            "### Why This Matters",
            "- Stage 4 converts the scaffold execution contract into real final-output artifacts.",
            "- The resulting deliverables and the pitch deck share the same evidence trail.",
        ]
    )

    output_path = paths.wiki_provenance_dir / "what_i_built.md"
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _build_pitch_markdown(
    manifest: dict[str, Any],
    decision_log_version: int,
    project_type: str,
    output_dir: Path,
    execution_manifest: dict[str, Any],
) -> str:
    wm = manifest.get("workspace_manifest", {}) if isinstance(manifest, dict) else {}
    wiki = wm.get("wiki", {}) if isinstance(wm, dict) else {}
    final_output = execution_manifest.get("final_output", {}) if isinstance(execution_manifest, dict) else {}
    deliverables = final_output.get("deliverables", []) if isinstance(final_output, dict) else []
    deliverable_lines = [
        f"- {row.get('kind')}: {row.get('path')}"
        for row in deliverables
        if isinstance(row, dict)
    ] or ["- No deliverables recorded."]

    return "\n".join(
        [
            "# Stage 4 Pitch",
            "",
            "## Thesis",
            f"{wm.get('name', 'This project')} uses a fresh-context agentic loop to turn research into a traceable build and a final packaged output.",
            "",
            "## Why The Loop Works",
            "- Stage 1 separates breadth, depth, and fresh review so the wiki becomes durable instead of conversational.",
            "- Stage 2 captures creator judgment explicitly in the Decision Log instead of hiding it in chat state.",
            "- Stage 3 turns those decisions into an executable scaffold with reusable agents and skills.",
            "- Stage 4 executes the scaffold contract and packages the result into a product pitch.",
            "",
            "## Creator Role",
            "- The creator provides the problem statement, constraints, and decision checkpoints.",
            f"- The build remains anchored to Decision Log v{decision_log_version}.",
            "",
            "## Product Built",
            f"- Project type: {project_type}",
            f"- Wiki name: {wiki.get('name') or 'Not named yet'}",
            f"- Output directory: {output_dir}",
            *deliverable_lines,
            "",
            "## Sell",
            "- The loop is auditable: every major output has a file, schema, and manifest entry.",
            "- The loop is reusable: generated agents inherit the same research/explore subagent palette.",
            "- The loop is extensible: new seeds, re-entry, and final packaging are first-class stages.",
        ]
    ) + "\n"


def _add_slide(prs: Presentation, title: str, bullets: list[str]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    text_frame = slide.placeholders[1].text_frame
    text_frame.clear()
    for idx, bullet in enumerate(bullets):
        paragraph = text_frame.paragraphs[0] if idx == 0 else text_frame.add_paragraph()
        paragraph.text = bullet
        paragraph.level = 0


def _write_pitch_deck(
    pptx_path: Path,
    manifest: dict[str, Any],
    decision_log_version: int,
    project_type: str,
    output_dir: Path,
    execution_manifest: dict[str, Any],
) -> None:
    _require_pptx()
    wm = manifest.get("workspace_manifest", {}) if isinstance(manifest, dict) else {}
    wiki = wm.get("wiki", {}) if isinstance(wm, dict) else {}
    final_output = execution_manifest.get("final_output", {}) if isinstance(execution_manifest, dict) else {}
    deliverables = final_output.get("deliverables", []) if isinstance(final_output, dict) else []

    prs = Presentation()
    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    title_slide.shapes.title.text = f"{wm.get('name', 'META-COMPILER')} Stage 4 Pitch"
    title_slide.placeholders[1].text = "Why the loop works, what it built, and why the creator wins."

    _add_slide(
        prs,
        "Agentic Loop",
        [
            "Stage 0 defines the problem. Stage 1 builds and tests the wiki. Stage 2 captures judgment. Stage 3 scaffolds execution. Stage 4 executes and pitches.",
            "Fresh context between stages keeps the artifacts cleaner than a single long-running chat.",
        ],
    )
    _add_slide(
        prs,
        "Creator Advantage",
        [
            "The creator makes the narrow, high-value decisions while the system handles repeatable research and packaging work.",
            f"Decision Log v{decision_log_version} is the explicit contract that downstream stages respect.",
        ],
    )
    _add_slide(
        prs,
        "What Was Built",
        [
            f"Project type: {project_type}",
            f"Wiki name: {wiki.get('name') or 'Not named yet'}",
            f"Execution output directory: {output_dir}",
        ]
        + [
            f"{row.get('kind')}: {row.get('path')}"
            for row in deliverables
            if isinstance(row, dict)
        ],
    )
    _add_slide(
        prs,
        "Why This Loop Is Good",
        [
            "It is traceable: prompts, markdown, citations, and generated agents all resolve to files.",
            "It is extensible: review search, re-entry, and final packaging are stage-aware rather than ad hoc.",
            "It is portable: generated agents share the same explore/research delegation model.",
        ],
    )
    _add_slide(
        prs,
        "Final Sell",
        [
            "This is not just a scaffold generator. It is a governed build loop with explicit evidence, decisions, and execution handoff.",
            "The same pipeline that explains the work also produces the output and the pitch for it.",
        ],
    )
    pptx_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(pptx_path))


def _load_agent_registry(scaffold_root: Path) -> list[dict[str, Any]]:
    registry_path = scaffold_root / "AGENT_REGISTRY.yaml"
    if not registry_path.exists():
        return []
    payload = load_yaml(registry_path) or {}
    registry = payload.get("agent_registry", {}) if isinstance(payload, dict) else {}
    entries = registry.get("entries", []) if isinstance(registry, dict) else []
    return [row for row in entries if isinstance(row, dict)]


def run_phase4_start(
    artifacts_root: Path,
    workspace_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    """Stage 4 preflight: write dispatch plan + execution request, then stop.

    The LLM ralph loop (driven by stage-4-finalize.prompt.md) consumes the
    dispatch plan to fan out scaffold-generated implementer agents. After the
    loop populates `executions/v{N}/work/`, the operator runs
    `meta-compiler phase4-finalize --finalize` to compile the final manifest
    and emit the pitch deck.
    """
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    version = _resolve_decision_log_version(paths, decision_log_version)
    scaffold_version, scaffold_root = _resolve_scaffold_root(paths, decision_log_version=version)
    if scaffold_version != version:
        raise RuntimeError(
            f"Latest scaffold version (v{scaffold_version}) does not match Decision Log v{version}."
        )

    execution_manifest_path = scaffold_root / "EXECUTION_MANIFEST.yaml"
    if not execution_manifest_path.exists():
        raise RuntimeError(f"Execution manifest missing: {execution_manifest_path}")

    execution_payload = load_yaml(execution_manifest_path)
    execution_root = execution_payload.get("execution", {}) if isinstance(execution_payload, dict) else {}
    project_type = str(execution_root.get("project_type") or "algorithm")

    output_dir = paths.executions_dir / f"v{version}"
    work_dir = output_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    agent_entries = _load_agent_registry(scaffold_root)
    dispatch_assignments: list[dict[str, Any]] = []
    for entry in agent_entries:
        slug = entry.get("slug") or entry.get("role")
        if not slug:
            continue
        agent_work_dir = work_dir / str(slug)
        dispatch_assignments.append(
            {
                "agent": slug,
                "role": entry.get("role"),
                "responsibility": entry.get("responsibility"),
                "output_kind": entry.get("output_kind"),
                "outputs": entry.get("outputs", []),
                "expected_work_dir": str(agent_work_dir.relative_to(paths.root)),
                "max_cycles": entry.get("max_cycles", 3),
                "status": "pending",
            }
        )

    dispatch_plan_path = output_dir / "dispatch_plan.yaml"
    dump_yaml(
        dispatch_plan_path,
        {
            "dispatch_plan": {
                "generated_at": iso_now(),
                "decision_log_version": version,
                "project_type": project_type,
                "scaffold_root": str(scaffold_root.relative_to(paths.root.parent)) if scaffold_root.is_relative_to(paths.root.parent) else str(scaffold_root),
                "execution_output_dir": str(output_dir.relative_to(paths.root)),
                "work_dir": str(work_dir.relative_to(paths.root)),
                "assignments": dispatch_assignments,
            }
        },
    )

    request_payload = {
        "phase4_execution_request": {
            "generated_at": iso_now(),
            "decision_log_version": version,
            "project_type": project_type,
            "dispatch_plan_path": str(dispatch_plan_path.relative_to(paths.root)),
            "work_dir": str(work_dir.relative_to(paths.root)),
            "verdict_output_path": str(paths.phase4_preflight_verdict_path.relative_to(paths.root)),
            "next_action": (
                "Invoke @execution-orchestrator (or per-agent implementers from "
                "the dispatch plan) to populate the work_dir, then run "
                "meta-compiler phase4-finalize --finalize."
            ),
        }
    }
    dump_yaml(paths.phase4_execution_request_path, request_payload)

    return {
        "status": "ready_for_orchestrator",
        "decision_log_version": version,
        "project_type": project_type,
        "dispatch_plan_path": str(dispatch_plan_path),
        "execution_request_path": str(paths.phase4_execution_request_path),
        "work_dir": str(work_dir),
        "agent_count": len(dispatch_assignments),
    }


def _compile_final_output_manifest(
    output_dir: Path,
    work_dir: Path,
    *,
    decision_log_version: int,
    project_type: str,
    scaffold_root: Path,
) -> dict[str, Any]:
    """Compile FINAL_OUTPUT_MANIFEST.yaml from LLM-populated work/ directory.

    Walks each per-agent subdirectory, records every output file as a
    deliverable, and writes the manifest.
    """
    deliverables: list[dict[str, Any]] = []
    if work_dir.exists():
        for agent_dir in sorted(work_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            for path in sorted(agent_dir.rglob("*")):
                if not path.is_file():
                    continue
                deliverables.append(
                    {
                        "agent": agent_dir.name,
                        "kind": path.suffix.lstrip(".") or "file",
                        "path": str(path.relative_to(output_dir.parent.parent))
                        if path.is_relative_to(output_dir.parent.parent)
                        else str(path),
                    }
                )

    manifest_payload = {
        "final_output": {
            "generated_at": iso_now(),
            "decision_log_version": decision_log_version,
            "project_type": project_type,
            "scaffold_root": str(scaffold_root),
            "work_dir": str(work_dir),
            "deliverables": deliverables,
            "execution_notes": [
                f"Compiled from {len(deliverables)} file(s) in work_dir",
                "Conducted via stage-4-finalize.prompt.md ralph loop",
            ],
        }
    }
    manifest_path = output_dir / "FINAL_OUTPUT_MANIFEST.yaml"
    dump_yaml(manifest_path, manifest_payload)
    return manifest_payload


def run_phase4_finalize(
    artifacts_root: Path,
    workspace_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    version = _resolve_decision_log_version(paths, decision_log_version)
    scaffold_version, scaffold_root = _resolve_scaffold_root(paths, decision_log_version=version)
    if scaffold_version != version:
        raise RuntimeError(
            f"Latest scaffold version (v{scaffold_version}) does not match Decision Log v{version}."
        )

    execution_manifest_path = scaffold_root / "EXECUTION_MANIFEST.yaml"
    orchestrator_path = scaffold_root / "orchestrator" / "run_stage4.py"
    if not execution_manifest_path.exists():
        raise RuntimeError(f"Execution manifest missing: {execution_manifest_path}")

    execution_payload = load_yaml(execution_manifest_path)
    execution_root = execution_payload.get("execution", {}) if isinstance(execution_payload, dict) else {}
    project_type = str(execution_root.get("project_type") or "algorithm")

    output_dir = paths.executions_dir / f"v{version}"
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "work"

    completed_stdout = ""
    completed_stderr = ""
    final_output_manifest_path = output_dir / "FINAL_OUTPUT_MANIFEST.yaml"

    work_populated = work_dir.exists() and any(work_dir.rglob("*"))

    if work_populated:
        # Conductor mode: LLM ralph loop populated work_dir; compile manifest.
        final_output_manifest = _compile_final_output_manifest(
            output_dir,
            work_dir,
            decision_log_version=version,
            project_type=project_type,
            scaffold_root=scaffold_root,
        )
    elif final_output_manifest_path.exists():
        # Manifest already on disk (e.g., LLM agent wrote it directly).
        final_output_manifest = load_yaml(final_output_manifest_path)
    else:
        # Legacy fallback: invoke the scaffold-generated subprocess.
        if not orchestrator_path.exists():
            raise RuntimeError(
                f"Stage 4 orchestrator missing: {orchestrator_path}. "
                "Either populate executions/v{N}/work/ via the LLM conductor "
                "(meta-compiler phase4-finalize --start, then run the prompt) "
                "or regenerate the scaffold."
            )
        command = [sys.executable, str(orchestrator_path), "--output-dir", str(output_dir)]
        completed = subprocess.run(
            command,
            cwd=str(scaffold_root),
            capture_output=True,
            text=True,
            check=True,
        )
        completed_stdout = completed.stdout.strip()
        completed_stderr = completed.stderr.strip()
        if not final_output_manifest_path.exists():
            raise RuntimeError("Stage 4 orchestrator completed without FINAL_OUTPUT_MANIFEST.yaml")
        final_output_manifest = load_yaml(final_output_manifest_path)

    manifest = load_manifest(paths)
    if not manifest:
        raise RuntimeError("Manifest not found. Run meta-init first.")

    what_i_built_path = _write_what_i_built_refresh(
        paths,
        decision_log_version=version,
        project_type=project_type,
        output_dir=output_dir,
        execution_manifest=final_output_manifest,
    )

    markdown_pitch_path = paths.pitches_dir / f"pitch_v{version}.md"
    markdown_pitch_path.write_text(
        _build_pitch_markdown(
            manifest,
            decision_log_version=version,
            project_type=project_type,
            output_dir=output_dir,
            execution_manifest=final_output_manifest,
        ),
        encoding="utf-8",
    )

    pptx_path = paths.pitches_dir / f"pitch_v{version}.pptx"
    _write_pitch_deck(
        pptx_path,
        manifest,
        decision_log_version=version,
        project_type=project_type,
        output_dir=output_dir,
        execution_manifest=final_output_manifest,
    )

    metadata_path = paths.pitches_dir / f"pitch_v{version}.yaml"
    dump_yaml(
        metadata_path,
        {
            "pitch": {
                "generated_at": iso_now(),
                "decision_log_version": version,
                "pptx_path": str(pptx_path),
                "markdown_path": str(markdown_pitch_path),
                "what_i_built_path": str(what_i_built_path),
                "execution_output_dir": str(output_dir),
            }
        },
    )

    wm = manifest["workspace_manifest"]
    wm["status"] = "active"
    research = wm.setdefault("research", {})
    research["last_completed_stage"] = "4"

    executions = wm.setdefault("executions", [])
    execution_entry = {
        "version": version,
        "created": iso_now(),
        "output_dir": str(output_dir),
    }
    executions = [row for row in executions if not (isinstance(row, dict) and row.get("version") == version)]
    executions.append(execution_entry)
    wm["executions"] = executions

    pitches = wm.setdefault("pitches", [])
    pitch_entry = {
        "version": version,
        "created": iso_now(),
        "pptx_path": str(pptx_path),
    }
    pitches = [row for row in pitches if not (isinstance(row, dict) and row.get("version") == version)]
    pitches.append(pitch_entry)
    wm["pitches"] = pitches
    save_manifest(paths, manifest)

    dump_yaml(
        paths.phase4_postcheck_request_path,
        {
            "phase4_postcheck_request": {
                "generated_at": iso_now(),
                "decision_log_version": version,
                "execution_output_dir": str(output_dir.relative_to(paths.root)),
                "final_output_manifest_path": str(final_output_manifest_path.relative_to(paths.root)),
                "verdict_output_path": str(paths.phase4_postcheck_verdict_path.relative_to(paths.root)),
                "next_action": (
                    "Invoke @execution-orchestrator mode=postflight to spot-verify "
                    "deliverable fidelity against the dispatch plan."
                ),
            }
        },
    )

    return {
        "decision_log_version": version,
        "project_type": project_type,
        "execution_output_dir": str(output_dir),
        "pitch_pptx_path": str(pptx_path),
        "pitch_markdown_path": str(markdown_pitch_path),
        "what_i_built_path": str(what_i_built_path),
        "postcheck_request_path": str(paths.phase4_postcheck_request_path),
        "stdout": completed_stdout,
        "stderr": completed_stderr,
    }