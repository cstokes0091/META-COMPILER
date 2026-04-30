from __future__ import annotations

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
    fragments = final_output.get("fragments", []) if isinstance(final_output, dict) else []
    notes = final_output.get("execution_notes", []) if isinstance(final_output, dict) else []
    synthesis_status = final_output.get("synthesis_status") if isinstance(final_output, dict) else None
    final_dir_str = final_output.get("final_dir") if isinstance(final_output, dict) else None

    lines = [
        "## What I Built",
        "",
        f"- Decision Log Version: v{decision_log_version}",
        f"- Project type: {project_type}",
        f"- Output directory: {output_dir}",
    ]
    if synthesis_status:
        lines.append(f"- Synthesis status: {synthesis_status}")
    if final_dir_str and synthesis_status == "synthesized":
        lines.append(f"- Assembled deliverable root: {final_dir_str}")
    lines.extend(["", "### Final Deliverables"])

    if isinstance(deliverables, list) and deliverables:
        for row in deliverables:
            if not isinstance(row, dict):
                continue
            bucket = row.get("bucket")
            if bucket:
                lines.append(f"- [{bucket}] {row.get('kind')}: {row.get('path')}")
            else:
                lines.append(f"- {row.get('kind')}: {row.get('path')}")
    else:
        lines.append("- No deliverables recorded.")

    if synthesis_status == "synthesized" and isinstance(fragments, list) and fragments:
        lines.extend(["", "### Underlying Fragments"])
        lines.append(f"- {len(fragments)} per-capability fragment(s) under work/")

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


def _load_dispatch_hints(scaffold_root: Path) -> list[dict[str, Any]]:
    """Load capability-keyed dispatch hints. Replaces the legacy
    _load_agent_registry that read AGENT_REGISTRY.yaml. Commit 8 of the
    Stage-3 rearchitecture."""
    hints_path = scaffold_root / "DISPATCH_HINTS.yaml"
    if not hints_path.exists():
        return []
    payload = load_yaml(hints_path) or {}
    root = payload.get("dispatch_hints", {}) if isinstance(payload, dict) else {}
    assignments = root.get("assignments", []) if isinstance(root, dict) else []
    return [row for row in assignments if isinstance(row, dict)]


def _load_capability_graph_for_dispatch(
    scaffold_root: Path,
) -> dict[str, dict[str, Any]]:
    """Return {capability_name: capability_dict_from_yaml} for denormalization.

    Used by run_phase4_start to write per-capability `_dispatch.yaml`
    files with the full plan-extract field set. We index by name so the
    DISPATCH_HINTS row's `capability` field resolves directly.
    """
    capabilities_path = scaffold_root / "capabilities.yaml"
    if not capabilities_path.exists():
        return {}
    payload = load_yaml(capabilities_path) or {}
    graph = payload.get("capability_graph") or {}
    capabilities = graph.get("capabilities") or []
    out: dict[str, dict[str, Any]] = {}
    for cap in capabilities:
        if not isinstance(cap, dict):
            continue
        name = cap.get("name")
        if isinstance(name, str) and name:
            out[name] = cap
    return out


def run_phase4_start(
    artifacts_root: Path,
    workspace_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    """Stage 4 preflight: write dispatch plan + per-capability dispatch files
    + execution request, then stop.

    Change E rewrites this preflight to denormalize each capability's
    plan-extract fields into `executions/v{N}/work/<cap>/_dispatch.yaml`.
    The Stage 4 work loop (driven by stage-4-finalize.prompt.md) reads
    those files instead of an in-flight `_plan.yaml` produced by a Stage
    4 planner agent — that planner has been removed; planning lives
    upstream in Stage 2.5. The execution-orchestrator routes implementer
    → reviewer (always) → researcher (only on `gap_kind: knowledge_gap`).

    After the loop populates `executions/v{N}/work/`, the operator runs
    `meta-compiler phase4-finalize --finalize` to compile the final
    manifest and emit the pitch deck.
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

    capability_entries = _load_dispatch_hints(scaffold_root)
    capability_graph_by_name = _load_capability_graph_for_dispatch(scaffold_root)
    context_md_path = scaffold_root / "CONTEXT.md"
    context_md_relative = (
        str(context_md_path.relative_to(paths.root))
        if context_md_path.exists()
        else None
    )

    dispatch_assignments: list[dict[str, Any]] = []
    dispatch_files_written: list[str] = []
    for entry in capability_entries:
        capability = entry.get("capability")
        if not capability:
            continue
        capability_name = str(capability)
        capability_work_dir = work_dir / capability_name
        capability_work_dir.mkdir(parents=True, exist_ok=True)
        cap_dict = capability_graph_by_name.get(capability_name) or {}
        dispatch_path = capability_work_dir / "_dispatch.yaml"
        verification_hook_ids = list(entry.get("verification_hook_ids") or [])
        verification_spec_paths = list(entry.get("verification_spec_paths") or [])
        if not verification_spec_paths and verification_hook_ids:
            # Legacy DISPATCH_HINTS may have only the hook ids; reconstruct
            # the spec path so older scaffolds still drive the work loop.
            verification_spec_paths = [
                f"verification/{hook}_spec.yaml" for hook in verification_hook_ids
            ]
        dispatch_payload = {
            "dispatch": {
                "decision_log_version": version,
                "capability_id": capability_name,
                "work_dir": str(capability_work_dir.relative_to(paths.root)),
                "skill_path": entry.get("skill_path"),
                "contract_ref": entry.get("contract_ref"),
                "verification_hook_ids": verification_hook_ids,
                "verification_spec_paths": verification_spec_paths,
                "context_md_path": context_md_relative,
                # v2.1 fields denormalized from capabilities.yaml — feed the
                # implementer's Step 0 (acceptance test from spec) and the
                # reviewer's audit phases without a Stage 4 planner hop.
                "user_story": cap_dict.get("user_story"),
                "the_problem": cap_dict.get("the_problem"),
                "the_fix": cap_dict.get("the_fix"),
                "anti_patterns": list(cap_dict.get("anti_patterns") or []),
                "out_of_scope": list(cap_dict.get("out_of_scope") or []),
                "dispatch_kind": cap_dict.get("dispatch_kind") or entry.get("dispatch_kind"),
                "parallelizable": (
                    cap_dict.get("parallelizable")
                    if "parallelizable" in cap_dict
                    else entry.get("parallelizable")
                ),
                "implementation_steps": list(cap_dict.get("implementation_steps") or []),
                "acceptance_criteria": list(cap_dict.get("acceptance_criteria") or []),
                "explicit_triggers": list(cap_dict.get("explicit_triggers") or []),
                "evidence_refs": list(cap_dict.get("evidence_refs") or []),
                "rationale": cap_dict.get("rationale"),
                "phase": cap_dict.get("phase"),
                "objective": cap_dict.get("objective"),
                "deletion_test": cap_dict.get("deletion_test"),
            }
        }
        dump_yaml(dispatch_path, dispatch_payload)
        dispatch_files_written.append(str(dispatch_path.relative_to(paths.root)))

        dispatch_assignments.append(
            {
                "capability": capability_name,
                "skill_path": entry.get("skill_path"),
                "contract_ref": entry.get("contract_ref"),
                "verification_hook_ids": verification_hook_ids,
                "verification_spec_paths": verification_spec_paths,
                "expected_work_dir": str(capability_work_dir.relative_to(paths.root)),
                "dispatch_path": str(dispatch_path.relative_to(paths.root)),
                "dispatch_kind": dispatch_payload["dispatch"]["dispatch_kind"],
                "parallelizable": dispatch_payload["dispatch"]["parallelizable"],
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
                "context_md_path": context_md_relative,
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
            "context_md_path": context_md_relative,
            "verdict_output_path": str(paths.phase4_preflight_verdict_path.relative_to(paths.root)),
            "next_action": (
                "Invoke @execution-orchestrator to drive the work loop. "
                "For each capability, the orchestrator loads "
                "`work/<cap>/_dispatch.yaml` + SKILL.md + CONTEXT.md as a "
                "fresh read set, then runs implementer → reviewer "
                "(→ researcher only on `gap_kind: knowledge_gap`). The "
                "Stage 4 planner agent is gone — planning is upstream in "
                "Stage 2.5. After the loop populates the work_dir, run "
                "`meta-compiler phase4-finalize --finalize`."
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
        "capability_count": len(dispatch_assignments),
        "dispatch_files_written": dispatch_files_written,
        "context_md_path": context_md_relative,
    }


def _compile_final_output_manifest(
    output_dir: Path,
    work_dir: Path,
    *,
    decision_log_version: int,
    project_type: str,
    scaffold_root: Path,
    final_dir: Path | None = None,
) -> dict[str, Any]:
    """Compile FINAL_OUTPUT_MANIFEST.yaml.

    When `final_dir` exists and contains assembled output (the
    final-synthesis sub-stage has run), `deliverables[]` lists those
    artifacts as the canonical deliverable set, and per-capability
    work/<cap>/ files are demoted to `fragments[]` for audit.

    When `final_dir` is empty or absent, the legacy behavior applies:
    `deliverables[]` is populated from work/<cap>/ and `synthesis_status`
    reports `"fragments_only"`.
    """
    fragments: list[dict[str, Any]] = []
    if work_dir.exists():
        for capability_dir in sorted(work_dir.iterdir()):
            if not capability_dir.is_dir():
                continue
            for path in sorted(capability_dir.rglob("*")):
                if not path.is_file():
                    continue
                fragments.append(
                    {
                        "capability": capability_dir.name,
                        "kind": path.suffix.lstrip(".") or "file",
                        "path": str(path.relative_to(output_dir.parent.parent))
                        if path.is_relative_to(output_dir.parent.parent)
                        else str(path),
                    }
                )

    deliverables: list[dict[str, Any]] = []
    synthesis_status = "fragments_only"
    if final_dir is not None and final_dir.exists() and any(final_dir.rglob("*")):
        synthesis_status = "synthesized"
        for path in sorted(final_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(final_dir)
            except ValueError:
                relative = path
            bucket = relative.parts[0] if relative.parts else ""
            deliverables.append(
                {
                    "bucket": bucket,
                    "kind": path.suffix.lstrip(".") or "file",
                    "path": str(path.relative_to(output_dir.parent.parent))
                    if path.is_relative_to(output_dir.parent.parent)
                    else str(path),
                }
            )
    else:
        # Legacy: deliverables == fragments. Preserves backward compat for
        # workspaces predating the final-synthesis sub-stage.
        deliverables = list(fragments)

    if synthesis_status == "synthesized":
        execution_notes = [
            f"Assembled {len(deliverables)} file(s) under final/",
            f"Underlying work_dir produced {len(fragments)} fragment(s)",
            "Composed via final-synthesis.prompt.md → final-synthesize-finalize",
        ]
    else:
        execution_notes = [
            f"Compiled from {len(fragments)} file(s) in work_dir",
            "Conducted via stage-4-finalize.prompt.md ralph loop",
            "final-synthesis sub-stage NOT run — deliverables == fragments",
        ]

    manifest_payload = {
        "final_output": {
            "generated_at": iso_now(),
            "decision_log_version": decision_log_version,
            "project_type": project_type,
            "scaffold_root": str(scaffold_root),
            "work_dir": str(work_dir),
            "final_dir": str(final_dir) if final_dir is not None else None,
            "synthesis_status": synthesis_status,
            "deliverables": deliverables,
            "fragments": fragments,
            "execution_notes": execution_notes,
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
    if not execution_manifest_path.exists():
        raise RuntimeError(f"Execution manifest missing: {execution_manifest_path}")

    execution_payload = load_yaml(execution_manifest_path)
    execution_root = execution_payload.get("execution", {}) if isinstance(execution_payload, dict) else {}
    project_type = str(execution_root.get("project_type") or "algorithm")

    output_dir = paths.executions_dir / f"v{version}"
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "work"
    final_dir = paths.final_dir_for(version)

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
            final_dir=final_dir,
        )
    elif final_output_manifest_path.exists():
        # Manifest already on disk (e.g., LLM agent wrote it directly).
        final_output_manifest = load_yaml(final_output_manifest_path)
    else:
        # The legacy orchestrator/run_stage4.py subprocess fallback was deleted
        # in Commit 8 of the Stage-3 rearchitecture. Stage 4 is now LLM-conducted
        # only: the operator runs `meta-compiler phase4-finalize --start`, the
        # execution-orchestrator populates executions/v{N}/work/, and then
        # `phase4-finalize --finalize` compiles the manifest from work_dir.
        raise RuntimeError(
            f"executions/v{version}/work/ is empty and FINAL_OUTPUT_MANIFEST.yaml "
            "is absent. Run `meta-compiler phase4-finalize --start`, then let the "
            "@execution-orchestrator populate the work directory before calling "
            "--finalize."
        )

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

    # Primary location (Commit 6+): structured REQ trace under verification/.
    # Legacy location (pre-Commit 6): requirements/REQ_TRACE_MATRIX.md.
    req_trace_path = scaffold_root / "verification" / "REQ_TRACE.yaml"
    if not req_trace_path.exists():
        legacy = scaffold_root / "requirements" / "REQ_TRACE_MATRIX.md"
        if legacy.exists():
            req_trace_path = legacy
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
            final_dir=final_dir if final_dir.exists() and any(final_dir.rglob("*")) else None,
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