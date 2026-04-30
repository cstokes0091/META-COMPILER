"""Stage 3.4 — workspace bootstrap.

Wires the static agent palette + verification harness + output buckets +
manifests. Consumes capabilities.yaml + contracts/_manifest.yaml + skills/
INDEX.md (all produced by Commits 3/4/5) and emits:
  - SCAFFOLD_MANIFEST.yaml (new shape)
  - EXECUTION_MANIFEST.yaml (new shape; Stage 4 dispatch_plan reads this)
  - DISPATCH_HINTS.yaml (replaces AGENT_REGISTRY.yaml)
  - verification/REQ_TRACE.yaml (REQ-NNN -> capability_id -> hook_id)
  - verification/{hook_id}_spec.yaml (machine-readable acceptance spec
    per verification_hook_ids — the implementer translates this into
    work/<cap>/tests/test_acceptance.py at Stage 4 step 0; the reviewer
    audits fidelity. Replaces the legacy pytest stub that the reviewer
    was instructed to enrich — see Change B in the v2.1 hardening plan)
  - Empty output buckets per `scaffold_subdirs_for(project_type)`
  - workspace_manifest.research.last_completed_stage advanced to '3'
  - wiki/provenance/what_i_built.md refreshed with capability counts

The palette agents (planner, implementer, reviewer, researcher) must
already exist at repo-level `.github/agents/` — they're installed by
`meta-compiler meta-init` via init_stage._provision_workspace_customizations.
This stage ASSERTS they're present and fails fast if not.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import build_paths, load_manifest, save_manifest
from ..io import dump_yaml, load_yaml
from ..project_types import scaffold_subdirs_for
from ..schemas import (
    Capability,
    CapabilityGraph,
    Contract,
    ContractManifest,
)
from ..utils import iso_now
from ._decision_log_utils import resolve_decision_log


PALETTE_AGENTS: tuple[str, ...] = ("implementer", "reviewer", "researcher")


def run_workspace_bootstrap(
    artifacts_root: Path,
    workspace_root: Path | None = None,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    version, decision_log_path, _payload = resolve_decision_log(paths, decision_log_version)

    scaffold_root = paths.scaffolds_dir / f"v{version}"
    capabilities_path = scaffold_root / "capabilities.yaml"
    contract_manifest_path = scaffold_root / "contracts" / "_manifest.yaml"
    skill_index_path = scaffold_root / "skills" / "INDEX.md"
    for required in (capabilities_path, contract_manifest_path, skill_index_path):
        if not required.exists():
            raise RuntimeError(
                f"workspace-bootstrap prerequisite missing: {required}. "
                "Run compile-capabilities, extract-contracts, and synthesize-skills first."
            )

    graph = CapabilityGraph.model_validate(
        (load_yaml(capabilities_path) or {}).get("capability_graph") or {}
    )
    manifest = ContractManifest.model_validate(
        (load_yaml(contract_manifest_path) or {}).get("contract_manifest") or {}
    )
    contracts_by_id = _load_contracts(scaffold_root, manifest)

    ws_root = _resolve_workspace_root(artifacts_root, workspace_root)
    _assert_palette_present(ws_root)

    project_type = graph.project_type
    _create_output_buckets(scaffold_root, project_type)

    verification_dir = scaffold_root / "verification"
    verification_dir.mkdir(parents=True, exist_ok=True)
    hook_paths = _write_verification_stubs(verification_dir, graph.capabilities, contracts_by_id)
    req_trace_path = _write_req_trace(verification_dir, graph.capabilities)

    execution_manifest_path = _write_execution_manifest(
        scaffold_root, graph, manifest, version, decision_log_path
    )
    dispatch_hints_path = _write_dispatch_hints(
        scaffold_root, graph, version
    )
    decision_log_payload = load_yaml(decision_log_path) or {}
    context_md_path = _write_context_md(
        scaffold_root,
        graph,
        contracts_by_id,
        decision_log_payload,
        paths,
    )
    scaffold_manifest_path = _write_scaffold_manifest(
        scaffold_root, graph, manifest, version, decision_log_path, hook_paths,
        context_md_path=context_md_path,
    )

    _refresh_provenance(paths, graph, manifest, version)
    _advance_manifest_stage(paths)

    return {
        "stage": "workspace-bootstrap",
        "decision_log_version": version,
        "scaffold_manifest_path": str(scaffold_manifest_path),
        "execution_manifest_path": str(execution_manifest_path),
        "dispatch_hints_path": str(dispatch_hints_path),
        "context_md_path": str(context_md_path),
        "verification_dir": str(verification_dir),
        "verification_hook_count": len(hook_paths),
        "req_trace_path": str(req_trace_path),
        "output_buckets": sorted(scaffold_subdirs_for(project_type)),
    }


def _resolve_workspace_root(artifacts_root: Path, workspace_root: Path | None) -> Path:
    if workspace_root is not None:
        return workspace_root.resolve()
    # Default: the repository containing workspace-artifacts/ is the ws root.
    ar = artifacts_root.resolve()
    if ar.name == "workspace-artifacts":
        return ar.parent
    return ar


def _assert_palette_present(workspace_root: Path) -> None:
    agents_dir = workspace_root / ".github" / "agents"
    missing: list[str] = []
    for name in PALETTE_AGENTS:
        candidate = agents_dir / f"{name}.agent.md"
        if not candidate.exists():
            missing.append(candidate.as_posix())
    if missing:
        raise RuntimeError(
            "Static agent palette not found. Expected one file per palette member "
            f"under {agents_dir}. Missing: {missing}. "
            "Run `meta-compiler meta-init --force` to reprovision."
        )


def _load_contracts(scaffold_root: Path, manifest: ContractManifest) -> dict[str, Contract]:
    out: dict[str, Contract] = {}
    for entry in manifest.entries:
        path = scaffold_root / entry.path
        payload = load_yaml(path) or {}
        out[entry.contract_id] = Contract.model_validate(payload.get("contract") or {})
    return out


def _create_output_buckets(scaffold_root: Path, project_type: str) -> None:
    for subdir in sorted(scaffold_subdirs_for(project_type)):
        (scaffold_root / subdir).mkdir(parents=True, exist_ok=True)
        gitkeep = scaffold_root / subdir / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")


def _write_verification_stubs(
    verification_dir: Path,
    capabilities: list[Capability],
    contracts_by_id: dict[str, Contract],
) -> list[Path]:
    """Emit one `{hook_id}_spec.yaml` per verification hook.

    These YAML specs replace the legacy pytest `.py` stubs (Change B).
    The Stage 4 implementer reads each spec at step 0 and writes
    `work/<cap>/tests/test_acceptance.py` from it; the reviewer runs that
    test and audits fidelity against the spec. Neither the implementer
    nor the reviewer modifies the spec file.

    When `cap.acceptance_spec` is unset (legacy or bootstrap path), the
    spec falls back to a `pending_planner_spec` placeholder so REQ_TRACE
    still resolves the hook_id and the reviewer can ITERATE with
    `gap_kind: knowledge_gap` until the planner re-runs Stage 2.5.
    """
    written: list[Path] = []
    seen: set[str] = set()
    for cap in capabilities:
        # Skip capabilities the planner marked as policy/config — they have no
        # verification hooks to honor and a spec would be vacuous.
        if not cap.verification_required:
            continue
        contract = contracts_by_id.get(cap.io_contract_ref)
        for hook_id in cap.verification_hook_ids:
            if hook_id in seen:
                continue
            seen.add(hook_id)
            spec_path = verification_dir / f"{hook_id}_spec.yaml"
            dump_yaml(spec_path, _render_acceptance_spec(hook_id, cap, contract))
            written.append(spec_path)
    return written


def _render_acceptance_spec(
    hook_id: str, cap: Capability, contract: Contract | None
) -> dict[str, Any]:
    """Build the YAML payload for verification/{hook_id}_spec.yaml.

    Carries the planner's `acceptance_spec` block verbatim (Change A
    output) plus a header that makes the spec self-describing for the
    Stage 4 implementer + reviewer.
    """
    contract_invariants = contract.invariants if contract is not None else []
    spec_body = cap.acceptance_spec
    if isinstance(spec_body, dict) and spec_body.get("scenarios"):
        scenarios = spec_body.get("scenarios") or []
        invariants = list(spec_body.get("invariants") or []) or list(
            contract_invariants
        )
        spec_status = "planner_provided"
        spec_format = spec_body.get("format") or "gherkin"
    else:
        # Legacy / bootstrap path: no acceptance_spec from the planner.
        # Mark the spec as pending so the reviewer ITERATEs with
        # gap_kind: knowledge_gap and the operator can re-run Stage 2.5.
        scenarios = []
        invariants = list(contract_invariants)
        spec_status = "pending_planner_spec"
        spec_format = "gherkin"
    return {
        "verification_spec": {
            "hook_id": hook_id,
            "capability": cap.name,
            "contract_ref": cap.io_contract_ref,
            "verification_type": cap.verification_type.value,
            "verification_required": cap.verification_required,
            "requirement_ids": list(cap.requirement_ids),
            "constraint_ids": list(cap.constraint_ids),
            "citation_ids": list(cap.citation_ids),
            "user_story": cap.user_story,
            "spec_status": spec_status,
            "format": spec_format,
            "scenarios": scenarios,
            "invariants": invariants,
            "implementer_instructions": (
                "Translate every scenario into one pytest function in "
                f"work/{cap.name}/tests/test_acceptance.py. Confirm RED "
                "before any implementation code is written. Do NOT modify "
                "this spec."
            ),
        }
    }


def _write_req_trace(verification_dir: Path, capabilities: list[Capability]) -> Path:
    trace: dict[str, list[dict[str, Any]]] = {}
    for cap in capabilities:
        for req_id in cap.requirement_ids:
            trace.setdefault(req_id, []).append({
                "capability": cap.name,
                "contract": cap.io_contract_ref,
                "verification_type": cap.verification_type.value,
                "verification_required": cap.verification_required,
                "hook_ids": list(cap.verification_hook_ids),
                "citation_ids": list(cap.citation_ids),
            })
        for con_id in cap.constraint_ids:
            entry = {
                "capability": cap.name,
                "contract": cap.io_contract_ref,
                "verification_type": cap.verification_type.value,
                "verification_required": cap.verification_required,
                "hook_ids": (
                    list(cap.verification_hook_ids)
                    if cap.verification_required
                    else []
                ),
                "citation_ids": list(cap.citation_ids),
            }
            trace.setdefault(con_id, []).append(entry)
    path = verification_dir / "REQ_TRACE.yaml"
    dump_yaml(path, {
        "requirement_trace": {
            "generated_at": iso_now(),
            "entries": [
                {"requirement_id": rid, "coverage": rows}
                for rid, rows in sorted(trace.items())
            ],
        }
    })
    return path


def _write_execution_manifest(
    scaffold_root: Path,
    graph: CapabilityGraph,
    contract_manifest: ContractManifest,
    version: int,
    decision_log_path: Path,
) -> Path:
    capability_ids = [c.name for c in graph.capabilities]
    requirement_ids = sorted({rid for c in graph.capabilities for rid in c.requirement_ids})
    citation_ids = sorted({cid for c in graph.capabilities for cid in c.citation_ids})
    path = scaffold_root / "EXECUTION_MANIFEST.yaml"
    dump_yaml(path, {
        "execution": {
            "generated_at": iso_now(),
            "decision_log_version": version,
            "decision_log_path": str(decision_log_path),
            "project_type": graph.project_type,
            "scaffold_root": str(scaffold_root),
            "capabilities_path": "capabilities.yaml",
            "contracts_dir": "contracts",
            "skills_dir": "skills",
            "verification_dir": "verification",
            "capability_ids": capability_ids,
            "contract_ids": [e.contract_id for e in contract_manifest.entries],
            "requirement_ids": requirement_ids,
            "citation_ids": citation_ids,
        }
    })
    return path


def _write_dispatch_hints(
    scaffold_root: Path,
    graph: CapabilityGraph,
    version: int,
) -> Path:
    path = scaffold_root / "DISPATCH_HINTS.yaml"
    dump_yaml(path, {
        "dispatch_hints": {
            "generated_at": iso_now(),
            "decision_log_version": version,
            "project_type": graph.project_type,
            "agent_palette": list(PALETTE_AGENTS),
            "agent_palette_source": ".github/agents",
            "skill_index_path": "skills/INDEX.md",
            "capabilities_path": "capabilities.yaml",
            "contracts_manifest_path": "contracts/_manifest.yaml",
            "verification_dir": "verification",
            "dispatch_policy": "capability-keyed",
            "assignments": [
                {
                    "capability": cap.name,
                    "skill_path": f"skills/{cap.name}/SKILL.md",
                    "contract_ref": cap.io_contract_ref,
                    "verification_hook_ids": list(cap.verification_hook_ids),
                    "verification_spec_paths": [
                        f"verification/{hook}_spec.yaml"
                        for hook in cap.verification_hook_ids
                    ] if cap.verification_required else [],
                    "expected_work_dir_relative": f"work/{cap.name}/",
                    # Change C: dispatch_kind + parallelizable feed the
                    # Stage 4 orchestrator's batch logic (AFK auto-loop vs
                    # HITL operator pause; parallel work-dir scheduling).
                    "dispatch_kind": cap.dispatch_kind,
                    "parallelizable": cap.parallelizable,
                }
                for cap in graph.capabilities
            ],
        }
    })
    return path


def _write_scaffold_manifest(
    scaffold_root: Path,
    graph: CapabilityGraph,
    contract_manifest: ContractManifest,
    version: int,
    decision_log_path: Path,
    hook_paths: list[Path],
    *,
    context_md_path: Path | None = None,
) -> Path:
    path = scaffold_root / "SCAFFOLD_MANIFEST.yaml"
    requirement_ids = sorted({rid for c in graph.capabilities for rid in c.requirement_ids})
    citation_ids = sorted({cid for c in graph.capabilities for cid in c.citation_ids})
    payload = {
        "scaffold": {
            "generated_at": iso_now(),
            "decision_log_version": version,
            "decision_log_path": str(decision_log_path),
            "project_type": graph.project_type,
            "capabilities_path": "capabilities.yaml",
            "contracts_dir": "contracts",
            "skills_dir": "skills",
            "verification_dir": "verification",
            "capability_count": len(graph.capabilities),
            "contract_count": len(contract_manifest.entries),
            "skill_count": len(graph.capabilities),
            "verification_hook_count": len(hook_paths),
            "agent_palette_source": ".github/agents",
            "agent_palette": list(PALETTE_AGENTS),
            "requirement_ids": requirement_ids,
            "citation_ids": citation_ids,
            "root": str(scaffold_root),
        }
    }
    if context_md_path is not None:
        payload["scaffold"]["context_md_path"] = str(
            context_md_path.relative_to(scaffold_root)
        )
    dump_yaml(path, payload)
    return path


# ---------------------------------------------------------------------------
# CONTEXT.md — shared glossary for Stage 4 palette agents (Change B).
# ---------------------------------------------------------------------------


_ARCHITECTURE_GLOSSARY: tuple[tuple[str, str], ...] = (
    ("Module", "Anything with an interface and an implementation."),
    (
        "Interface",
        "Everything a caller must know to use the module: types, "
        "invariants, error modes, ordering, config.",
    ),
    ("Implementation", "The code inside a module."),
    ("Depth", "Leverage at the interface: a lot of behaviour behind a small interface."),
    (
        "Seam",
        "Where an interface lives; a place behaviour can be altered "
        "without editing in place.",
    ),
    ("Adapter", "A concrete thing satisfying an interface at a seam."),
    ("Leverage", "What callers get from depth."),
    (
        "Locality",
        "What maintainers get from depth: change, bugs, knowledge "
        "concentrated in one place.",
    ),
)


def _concept_pages_for_glossary(paths) -> list[tuple[str, list[str], str]]:
    """Return [(concept_name, aliases, definition)] tuples sorted by source-count.

    Reads `wiki/v{1}/pages/concept-*.md`. Each page has YAML frontmatter
    with `name`, `aliases`, `sources`, and a `## Definition` body section.
    Returns the best-effort extraction; missing/unreadable pages are
    skipped silently (CONTEXT.md must always render).
    """
    pages_dir = paths.wiki_dir / "v1" / "pages"
    if not pages_dir.exists():
        return []
    out: list[tuple[str, list[str], str, int]] = []
    for page_path in sorted(pages_dir.glob("concept-*.md")):
        try:
            text = page_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end == -1:
            continue
        try:
            import yaml  # local import to keep module load light

            fm = yaml.safe_load(text[3:end]) or {}
        except yaml.YAMLError:
            continue
        body = text[end + 4 :]
        name = str(fm.get("name") or "").strip()
        if not name:
            continue
        aliases_raw = fm.get("aliases") or []
        if not isinstance(aliases_raw, list):
            aliases_raw = []
        aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
        sources = fm.get("sources") or []
        source_count = len(sources) if isinstance(sources, list) else 0
        # Pull first non-empty paragraph after `## Definition` heading.
        definition = ""
        if "## Definition" in body:
            tail = body.split("## Definition", 1)[1]
            for paragraph in tail.split("\n\n"):
                cleaned = paragraph.strip()
                if cleaned and not cleaned.startswith("##"):
                    definition = cleaned.split("\n")[0].strip()
                    break
        out.append((name, aliases, definition, source_count))
    out.sort(key=lambda row: (-row[3], row[0].lower()))
    return [(name, aliases, definition) for name, aliases, definition, _ in out]


def _write_context_md(
    scaffold_root: Path,
    graph: CapabilityGraph,
    contracts_by_id: dict[str, Contract],
    decision_log_payload: dict[str, Any],
    paths,
) -> Path:
    """Render `scaffolds/v{N}/CONTEXT.md`.

    Five sections (per Change B):
    1. Domain Glossary — concept pages + aliases + one-line definitions.
    2. Architecture Glossary — fixed mattpocock terms (Module/Interface/
       Implementation/Depth/Seam/Adapter/Leverage/Locality).
    3. Requirements & Constraints — REQ/CON tables from the decision log.
    4. Project Invariants & Out-of-Scope — union of capability invariants +
       out_of_scope (deduped).
    5. Anti-Patterns Index — every capability's anti-patterns with
       back-links to the SKILL.md.
    """
    inner = decision_log_payload.get("decision_log") or decision_log_payload
    lines: list[str] = [
        "# CONTEXT — Project Glossary",
        "",
        f"_Generated: {iso_now()}_",
        "",
        "All Stage 4 palette agents (implementer, reviewer, researcher) "
        "MUST read this file before doing per-capability work. It is the "
        "single source of vocabulary, requirements, invariants, and "
        "anti-patterns; the reviewer rejects synonym drift.",
        "",
        "## Domain Glossary",
        "",
    ]
    concept_rows = _concept_pages_for_glossary(paths)
    if concept_rows:
        lines.append("| Term | Aliases | Definition |")
        lines.append("|---|---|---|")
        for name, aliases, definition in concept_rows:
            alias_text = ", ".join(aliases) if aliases else "—"
            def_text = definition.replace("|", "\\|") if definition else "—"
            lines.append(f"| **{name}** | {alias_text} | {def_text} |")
    else:
        lines.append("_No canonical concept pages found in `wiki/v1/pages/`._")
    lines.extend(["", "## Architecture Glossary", ""])
    lines.append(
        "These terms are mandatory; reviewers reject synonym drift such "
        "as 'component', 'service', 'API', 'boundary'."
    )
    lines.append("")
    for term, definition in _ARCHITECTURE_GLOSSARY:
        lines.append(f"- **{term}** — {definition}")
    lines.extend(["", "## Requirements & Constraints", ""])
    requirements = inner.get("requirements") or []
    if requirements:
        lines.append("### Requirements")
        for row in requirements:
            if not isinstance(row, dict):
                continue
            rid = row.get("id", "")
            desc = (row.get("description") or "").strip()
            lines.append(f"- **{rid}** — {desc}")
        lines.append("")
    constraints = inner.get("constraints") or []
    if constraints:
        lines.append("### Constraints")
        for row in constraints:
            if not isinstance(row, dict):
                continue
            cid = row.get("id", "")
            kind = row.get("kind", "")
            desc = (row.get("description") or "").strip()
            lines.append(f"- **{cid}** [{kind}] — {desc}")
        lines.append("")
    if not requirements and not constraints:
        lines.append("_Decision log has no requirements or constraints._")
        lines.append("")
    lines.append("## Project Invariants & Out-of-Scope")
    lines.append("")
    invariants_seen: set[str] = set()
    invariants_ordered: list[str] = []
    for cap in graph.capabilities:
        contract = contracts_by_id.get(cap.io_contract_ref)
        if contract is None:
            continue
        for inv in contract.invariants:
            text = inv.strip()
            if text and text not in invariants_seen:
                invariants_seen.add(text)
                invariants_ordered.append(text)
    if invariants_ordered:
        lines.append("### Invariants (union across capabilities)")
        for inv in invariants_ordered:
            lines.append(f"- {inv}")
        lines.append("")
    out_of_scope_seen: set[str] = set()
    out_of_scope_ordered: list[tuple[str, str]] = []
    for cap in graph.capabilities:
        for item in cap.out_of_scope:
            text = item.strip()
            if text and text not in out_of_scope_seen:
                out_of_scope_seen.add(text)
                out_of_scope_ordered.append((text, cap.name))
    if out_of_scope_ordered:
        lines.append("### Out of Scope")
        for text, cap_name in out_of_scope_ordered:
            lines.append(f"- {text} _(declared by `{cap_name}`)_")
        lines.append("")
    if not invariants_ordered and not out_of_scope_ordered:
        lines.append(
            "_No invariants in contracts and no out_of_scope declared by any "
            "capability._"
        )
        lines.append("")
    lines.append("## Anti-Patterns Index")
    lines.append("")
    any_anti_pattern = False
    for cap in graph.capabilities:
        if not cap.anti_patterns:
            continue
        any_anti_pattern = True
        skill_link = f"skills/{cap.name}/SKILL.md"
        lines.append(f"### `{cap.name}` ([SKILL]({skill_link}))")
        for ap in cap.anti_patterns:
            text = ap.strip()
            if not text.lower().startswith("do not"):
                text = f"Do NOT {text[0].lower()}{text[1:]}" if text else text
            lines.append(f"- {text}")
        lines.append("")
    if not any_anti_pattern:
        lines.append(
            "_No capability declared anti-patterns yet. Stage 2.5 v2.1 "
            "plans require them for every verification-required capability._"
        )
        lines.append("")
    path = scaffold_root / "CONTEXT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _refresh_provenance(
    paths,
    graph: CapabilityGraph,
    contract_manifest: ContractManifest,
    version: int,
) -> None:
    provenance_path = paths.wiki_provenance_dir / "what_i_built.md"
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# What I Built — Decision Log v{version}",
        "",
        f"- Project type: `{graph.project_type}`",
        f"- Capabilities compiled: {len(graph.capabilities)}",
        f"- Contracts in library: {len(contract_manifest.entries)}",
        f"- Skills synthesized: {len(graph.capabilities)}",
        f"- Verification hooks: "
        + f"{sum(len(c.verification_hook_ids) for c in graph.capabilities)}",
        "",
        "## Capabilities",
    ]
    for cap in graph.capabilities:
        lines.append(f"- `{cap.name}` — {cap.description}")
        lines.append(
            f"  - requirements: {', '.join(cap.requirement_ids)}; "
            f"citations: {', '.join(cap.citation_ids)}"
        )
    lines.append("")
    provenance_path.write_text("\n".join(lines), encoding="utf-8")


def _advance_manifest_stage(paths) -> None:
    manifest = load_manifest(paths) or {}
    root = manifest.setdefault("workspace_manifest", {})
    research = root.setdefault("research", {})
    research["last_completed_stage"] = "3"
    save_manifest(paths, manifest)
