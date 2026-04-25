"""Stage 3.4 — workspace bootstrap.

Wires the static agent palette + verification harness + output buckets +
manifests. Consumes capabilities.yaml + contracts/_manifest.yaml + skills/
INDEX.md (all produced by Commits 3/4/5) and emits:
  - SCAFFOLD_MANIFEST.yaml (new shape)
  - EXECUTION_MANIFEST.yaml (new shape; Stage 4 dispatch_plan reads this)
  - DISPATCH_HINTS.yaml (replaces AGENT_REGISTRY.yaml)
  - verification/REQ_TRACE.yaml (REQ-NNN -> capability_id -> hook_id)
  - verification/{hook_id}.py (pytest stub per verification_hook_ids)
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


PALETTE_AGENTS: tuple[str, ...] = ("planner", "implementer", "reviewer", "researcher")


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
    scaffold_manifest_path = _write_scaffold_manifest(
        scaffold_root, graph, manifest, version, decision_log_path, hook_paths
    )

    _refresh_provenance(paths, graph, manifest, version)
    _advance_manifest_stage(paths)

    return {
        "stage": "workspace-bootstrap",
        "decision_log_version": version,
        "scaffold_manifest_path": str(scaffold_manifest_path),
        "execution_manifest_path": str(execution_manifest_path),
        "dispatch_hints_path": str(dispatch_hints_path),
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
    written: list[Path] = []
    seen: set[str] = set()
    for cap in capabilities:
        # Skip capabilities the planner marked as policy/config — they have no
        # verification hooks to honor and the test stub would be vacuous.
        if not cap.verification_required:
            continue
        contract = contracts_by_id.get(cap.io_contract_ref)
        for hook_id in cap.verification_hook_ids:
            if hook_id in seen:
                continue
            seen.add(hook_id)
            stub_path = verification_dir / f"{hook_id}.py"
            stub_path.write_text(
                _render_stub(hook_id, cap, contract),
                encoding="utf-8",
            )
            written.append(stub_path)
    return written


def _render_stub(hook_id: str, cap: Capability, contract: Contract | None) -> str:
    invariants = contract.invariants if contract is not None else []
    citations = ", ".join(repr(c) for c in cap.citation_ids)
    invariant_lines: list[str] = []
    if not invariants:
        invariant_lines.append(
            "    pytest.xfail(\"Capability has no contract invariants yet; "
            "implementer + reviewer must upgrade to real assertions.\")"
        )
    else:
        for inv in invariants:
            safe = inv.replace('"', "'")
            invariant_lines.append(f"    pytest.xfail(\"invariant: {safe}\")")
    return (
        "\"\"\"Auto-generated verification stub for "
        f"capability `{cap.name}` via hook `{hook_id}`.\n"
        "Reviewer agent upgrades these xfail markers into real assertions against\n"
        "the implementer's work-dir output before emitting a PROCEED verdict.\n"
        "\"\"\"\n"
        "from __future__ import annotations\n"
        "\n"
        "import pytest\n"
        "\n"
        f"CAPABILITY = {cap.name!r}\n"
        f"CONTRACT = {cap.io_contract_ref!r}\n"
        f"VERIFICATION_TYPE = {cap.verification_type.value!r}\n"
        f"CITATION_IDS = [{citations}]\n"
        "\n"
        f"def test_{_slugify_py(hook_id)}() -> None:\n"
        + "\n".join(invariant_lines)
        + "\n"
    )


def _slugify_py(value: str) -> str:
    out = []
    for ch in value.lower():
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    slug = "".join(out).strip("_") or "verify"
    # Collapse runs of underscores.
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug


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
                    "expected_work_dir_relative": f"work/{cap.name}/",
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
) -> Path:
    path = scaffold_root / "SCAFFOLD_MANIFEST.yaml"
    requirement_ids = sorted({rid for c in graph.capabilities for rid in c.requirement_ids})
    citation_ids = sorted({cid for c in graph.capabilities for cid in c.citation_ids})
    dump_yaml(path, {
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
    })
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
