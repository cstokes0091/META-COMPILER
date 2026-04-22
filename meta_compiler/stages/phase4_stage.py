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
from . import pitch_render


VALID_PITCH_STEPS = {"all", "evidence", "draft", "verify", "render"}


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


def _resolve_template_path(
    *,
    cli_template: Path | None,
    manifest: dict[str, Any],
    workspace_root: Path,
) -> Path | None:
    """Return the resolved template path or None.

    Precedence: CLI flag > manifest field > unset. Relative paths resolve
    against `workspace_root`. The renderer further validates the suffix.
    """
    candidate: Path | None = None
    if cli_template is not None:
        candidate = cli_template
    else:
        wm = manifest.get("workspace_manifest", {}) if isinstance(manifest, dict) else {}
        configured = (wm.get("pitch") or {}).get("template_path") if isinstance(wm, dict) else ""
        if isinstance(configured, str) and configured.strip():
            candidate = Path(configured.strip())
    if candidate is None:
        return None
    if not candidate.is_absolute():
        candidate = (workspace_root / candidate).resolve()
    return candidate


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
    *,
    pitch_step: str = "all",
    pptx_template: Path | None = None,
) -> dict[str, Any]:
    """Stage 4 finalize — pitch sub-loop with four steps.

    `pitch_step` controls which sub-step(s) run:
      - `evidence`: build evidence_pack.yaml + pitch_request.yaml; stop.
      - `draft`: alias for `evidence` (the draft is authored by the LLM
                 agent, not the CLI).
      - `verify`: re-build evidence + verify slides.yaml fidelity; stop.
      - `render`: assume slides.yaml exists; verify + render the .pptx.
      - `all` (default): run every step end-to-end. When slides.yaml is
                 absent, stop at the evidence/draft handoff and surface
                 the pitch-writer instruction.
    """
    if pitch_step not in VALID_PITCH_STEPS:
        raise RuntimeError(
            f"Invalid --pitch-step={pitch_step!r}. "
            f"Choose from: {sorted(VALID_PITCH_STEPS)}."
        )

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

    template_path = _resolve_template_path(
        cli_template=pptx_template,
        manifest=manifest,
        workspace_root=workspace_root,
    )
    if template_path is not None and template_path.suffix.lower() not in {".pptx", ".potx"}:
        raise RuntimeError(
            f"--pptx-template must point to a .pptx or .potx file (got {template_path})"
        )

    pptx_path = paths.pitches_dir / f"pitch_v{version}.pptx"
    markdown_pitch_path = paths.pitches_dir / f"pitch_v{version}.md"
    metadata_path = paths.pitches_dir / f"pitch_v{version}.yaml"

    decision_log_path = paths.decision_logs_dir / f"decision_log_v{version}.yaml"
    decision_log_payload = load_yaml(decision_log_path) or {}

    citations_payload = (
        load_yaml(paths.citations_index_path) if paths.citations_index_path.exists() else {}
    ) or {}

    req_trace_path = scaffold_root / "requirements" / "REQ_TRACE_MATRIX.md"
    ralph_loop_log_path = output_dir / "ralph_loop_log.yaml"

    pitch_summary: dict[str, Any] = {}

    if pitch_step in {"all", "evidence", "draft", "verify"} or pitch_step == "render":
        # Evidence pack is cheap and deterministic — always rebuild so
        # downstream steps work against fresh state.
        evidence_pack = pitch_render.build_evidence_pack(
            decision_log=decision_log_payload,
            decision_log_version=version,
            project_type=project_type,
            workspace_root=workspace_root,
            final_output_manifest=final_output_manifest,
            work_dir=work_dir,
            citations_payload=citations_payload,
            req_trace_path=req_trace_path if req_trace_path.exists() else None,
            ralph_loop_log_path=ralph_loop_log_path if ralph_loop_log_path.exists() else None,
        )
        dump_yaml(paths.phase4_evidence_pack_path, evidence_pack)
        pitch_render.write_pitch_request(
            pitch_request_path=paths.phase4_pitch_request_path,
            evidence_pack_path=paths.phase4_evidence_pack_path,
            slides_path=paths.phase4_slides_path,
            pptx_output_path=pptx_path,
            template_path=template_path,
            decision_log_version=version,
        )
        pitch_summary["evidence_pack_path"] = str(paths.phase4_evidence_pack_path)
        pitch_summary["pitch_request_path"] = str(paths.phase4_pitch_request_path)

    if pitch_step in {"evidence", "draft"}:
        return _phase4_summary(
            paths=paths,
            version=version,
            project_type=project_type,
            output_dir=output_dir,
            what_i_built_path=what_i_built_path,
            pitch_summary=pitch_summary,
            pitch_status="pending_pitch_writer",
            template_path=template_path,
            stdout=completed_stdout,
            stderr=completed_stderr,
            extra_instruction=(
                "Invoke @pitch-writer to draft "
                f"{paths.phase4_slides_path.relative_to(paths.root).as_posix()}, "
                "then re-run `meta-compiler phase4-finalize --pitch-step=render`."
            ),
        )

    # Verify + render require slides.yaml.
    if not paths.phase4_slides_path.exists():
        if pitch_step == "render":
            raise RuntimeError(
                "Cannot --pitch-step=render: "
                f"{paths.phase4_slides_path.relative_to(paths.root).as_posix()} is missing. "
                "Run `meta-compiler phase4-finalize --pitch-step=evidence` and invoke "
                "@pitch-writer to author it first."
            )
        # `all` mode without an LLM-authored slides.yaml — this is the
        # normal handoff state at the end of the deterministic prep step.
        return _phase4_summary(
            paths=paths,
            version=version,
            project_type=project_type,
            output_dir=output_dir,
            what_i_built_path=what_i_built_path,
            pitch_summary=pitch_summary,
            pitch_status="pending_pitch_writer",
            template_path=template_path,
            stdout=completed_stdout,
            stderr=completed_stderr,
            extra_instruction=(
                "Invoke @pitch-writer to draft "
                f"{paths.phase4_slides_path.relative_to(paths.root).as_posix()}, "
                "then re-run `meta-compiler phase4-finalize --pitch-step=render`."
            ),
        )

    slides_payload = load_yaml(paths.phase4_slides_path) or {}
    evidence_for_verify = load_yaml(paths.phase4_evidence_pack_path) or {}
    fidelity_issues = pitch_render.verify_slides_fidelity(
        slides_payload=slides_payload,
        evidence_pack=evidence_for_verify,
    )
    if fidelity_issues:
        violation_block = "\n".join(f"  - {issue}" for issue in fidelity_issues)
        raise RuntimeError(
            "Pitch fidelity check failed. Deck NOT rendered.\n"
            f"{violation_block}\n"
            "Edit the offending bullets in "
            f"{paths.phase4_slides_path.relative_to(paths.root).as_posix()} so every "
            "evidence_ids[...] resolves to a known entry in "
            f"{paths.phase4_evidence_pack_path.relative_to(paths.root).as_posix()}."
        )
    pitch_summary["fidelity"] = "pass"

    if pitch_step == "verify":
        return _phase4_summary(
            paths=paths,
            version=version,
            project_type=project_type,
            output_dir=output_dir,
            what_i_built_path=what_i_built_path,
            pitch_summary=pitch_summary,
            pitch_status="verified_pending_render",
            template_path=template_path,
            stdout=completed_stdout,
            stderr=completed_stderr,
            extra_instruction=(
                "Re-run `meta-compiler phase4-finalize --pitch-step=render` to produce the .pptx."
            ),
        )

    pitch_render.render_pitch_deck(
        slides_payload=slides_payload,
        output_path=pptx_path,
        template_path=template_path,
    )
    markdown_pitch_path.write_text(
        pitch_render.render_pitch_markdown(slides_payload),
        encoding="utf-8",
    )
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
                "evidence_pack_path": str(paths.phase4_evidence_pack_path),
                "slides_path": str(paths.phase4_slides_path),
                "template_path": str(template_path) if template_path else None,
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
                "pitch_pptx_path": str(pptx_path.relative_to(paths.root)),
                "evidence_pack_path": str(paths.phase4_evidence_pack_path.relative_to(paths.root)),
                "slides_path": str(paths.phase4_slides_path.relative_to(paths.root)),
                "template_path": str(template_path) if template_path else None,
                "next_action": (
                    "Invoke @execution-orchestrator mode=postflight to spot-verify "
                    "deliverable fidelity against the dispatch plan and the rendered deck."
                ),
            }
        },
    )

    pitch_summary.update(
        {
            "pptx_path": str(pptx_path),
            "markdown_path": str(markdown_pitch_path),
            "metadata_path": str(metadata_path),
        }
    )

    return {
        "decision_log_version": version,
        "project_type": project_type,
        "execution_output_dir": str(output_dir),
        "pitch": pitch_summary,
        "pitch_status": "rendered",
        "pitch_pptx_path": str(pptx_path),
        "pitch_markdown_path": str(markdown_pitch_path),
        "what_i_built_path": str(what_i_built_path),
        "template_path": str(template_path) if template_path else None,
        "postcheck_request_path": str(paths.phase4_postcheck_request_path),
        "stdout": completed_stdout,
        "stderr": completed_stderr,
    }


def _phase4_summary(
    *,
    paths,
    version: int,
    project_type: str,
    output_dir: Path,
    what_i_built_path: Path,
    pitch_summary: dict[str, Any],
    pitch_status: str,
    template_path: Path | None,
    stdout: str,
    stderr: str,
    extra_instruction: str,
) -> dict[str, Any]:
    return {
        "decision_log_version": version,
        "project_type": project_type,
        "execution_output_dir": str(output_dir),
        "pitch_status": pitch_status,
        "pitch": pitch_summary,
        "what_i_built_path": str(what_i_built_path),
        "template_path": str(template_path) if template_path else None,
        "next_step": extra_instruction,
        "stdout": stdout,
        "stderr": stderr,
    }