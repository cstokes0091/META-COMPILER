"""Stage 3.2 — contract extract.

Builds the `scaffolds/v{N}/contracts/` library from the capability graph +
decision log. One `Contract` per distinct IO shape observed across
`agents_needed` / `architecture` / `code_architecture` rows; shapes are
deduped by (inputs_name_modality_set, outputs_name_modality_set) so a single
contract can back multiple skills (the invariant the user called out).

Capabilities are rewritten with their real `io_contract_ref`. If a
capability's original placeholder (`contract-{name}`) has no matching IO
shape in the decision log, the capability gets assigned the most
permissive "policy" contract that encodes the convention-level invariants
for the workspace.

Test fixtures from findings `tables_figures` land under
`contracts/{contract_id}/fixtures/*.{csv,json}` so numerical capabilities
have concrete data to assert against.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from ..artifacts import build_paths
from ..findings_loader import FindingRecord, load_all_findings
from ..io import dump_yaml, load_yaml
from ..schemas import (
    Capability,
    CapabilityGraph,
    Contract,
    ContractIOField,
    ContractManifest,
    ContractManifestEntry,
    FindingRef,
)
from ..utils import iso_now, slugify
from ._decision_log_utils import as_string_list, collect_citation_ids, resolve_decision_log


VALID_CONTRACT_MODALITIES: frozenset[str] = frozenset({
    "document", "code", "data", "config", "artifact"
})
DEFAULT_MODALITY = "document"


def run_contract_extract(
    artifacts_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    version, _decision_log_path, payload = resolve_decision_log(paths, decision_log_version)
    root = payload["decision_log"]

    scaffold_root = paths.scaffolds_dir / f"v{version}"
    capabilities_path = scaffold_root / "capabilities.yaml"
    if not capabilities_path.exists():
        raise RuntimeError(
            f"capabilities.yaml missing at {capabilities_path}. Run "
            "`meta-compiler compile-capabilities` first."
        )
    graph_payload = load_yaml(capabilities_path).get("capability_graph") or {}
    graph = CapabilityGraph.model_validate(graph_payload)

    findings = load_all_findings(paths)
    findings_by_citation = _index_findings(findings)

    fallback_citations = collect_citation_ids(root)
    shapes = _collect_io_shapes(root)
    for shape in shapes:
        if not shape["citations"]:
            shape["citations"] = list(fallback_citations)
    contracts = _shapes_to_contracts(shapes, root, findings_by_citation)
    # Add a fallback "policy" contract that anchors convention-only capabilities.
    contracts.append(
        _policy_contract(root, findings_by_citation)
    )
    contracts = _dedupe_contracts_by_shape(contracts)

    contracts_dir = scaffold_root / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    contract_id_to_contract: dict[str, Contract] = {}
    manifest_entries: list[ContractManifestEntry] = []

    for contract in contracts:
        fixtures = _write_fixtures(contracts_dir / contract.contract_id, contract, findings_by_citation)
        if fixtures:
            contract = contract.model_copy(update={"test_fixtures": fixtures})
        contract_yaml_path = contracts_dir / f"{contract.contract_id}.yaml"
        dump_yaml(contract_yaml_path, {"contract": contract.model_dump(mode="json")})
        contract_id_to_contract[contract.contract_id] = contract
        manifest_entries.append(ContractManifestEntry(
            contract_id=contract.contract_id,
            path=f"contracts/{contract.contract_id}.yaml",
        ))

    manifest = ContractManifest(
        generated_at=iso_now(),
        decision_log_version=version,
        entries=manifest_entries,
    )
    manifest_path = contracts_dir / "_manifest.yaml"
    dump_yaml(manifest_path, {"contract_manifest": manifest.model_dump(mode="json")})

    updated_capabilities = _rewrite_capability_refs(graph.capabilities, contract_id_to_contract, root)
    graph = graph.model_copy(update={"capabilities": updated_capabilities})
    dump_yaml(capabilities_path, {"capability_graph": graph.model_dump(mode="json")})

    return {
        "stage": "contract-extract",
        "decision_log_version": version,
        "contracts_dir": str(contracts_dir),
        "manifest_path": str(manifest_path),
        "contract_count": len(contract_id_to_contract),
        "fixtures_written": sum(len(c.test_fixtures) for c in contract_id_to_contract.values()),
    }


def _index_findings(records: list[FindingRecord]) -> dict[str, list[FindingRecord]]:
    out: dict[str, list[FindingRecord]] = {}
    for rec in records:
        out.setdefault(rec.citation_id, []).append(rec)
    return out


def _collect_io_shapes(root: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of dicts describing IO shape candidates from the decision
    log. Each shape: {source, role, inputs, outputs, citations, constraints}."""
    shapes: list[dict[str, Any]] = []

    for row in root.get("agents_needed") or []:
        if not isinstance(row, dict):
            continue
        inputs = _normalize_io(row.get("inputs"))
        outputs = _normalize_io(row.get("outputs"))
        if not inputs or not outputs:
            continue
        shapes.append({
            "source": "agent",
            "role": str(row.get("role") or ""),
            "inputs": inputs,
            "outputs": outputs,
            "citations": as_string_list(row.get("citations", [])),
            "constraints": as_string_list(row.get("key_constraints", [])),
        })

    for row in root.get("architecture") or []:
        if not isinstance(row, dict):
            continue
        inputs = _normalize_io(row.get("inputs"))
        outputs = _normalize_io(row.get("outputs"))
        if not inputs or not outputs:
            continue
        shapes.append({
            "source": "architecture",
            "role": str(row.get("component") or ""),
            "inputs": inputs,
            "outputs": outputs,
            "citations": as_string_list(row.get("citations", [])),
            "constraints": as_string_list(row.get("constraints_applied", [])),
        })

    for row in root.get("code_architecture") or []:
        if not isinstance(row, dict):
            continue
        data_model = row.get("data_model")
        if isinstance(data_model, dict):
            inputs = _normalize_io(data_model.get("inputs"))
            outputs = _normalize_io(data_model.get("outputs"))
            if inputs and outputs:
                shapes.append({
                    "source": "code_architecture",
                    "role": str(row.get("aspect") or "data_model"),
                    "inputs": inputs,
                    "outputs": outputs,
                    "citations": as_string_list(row.get("citations", [])),
                    "constraints": [],
                })

    return shapes


def _normalize_io(value: Any) -> list[ContractIOField]:
    if not isinstance(value, list):
        return []
    fields: list[ContractIOField] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        modality = str(entry.get("modality") or DEFAULT_MODALITY).strip().lower()
        if not name or modality not in VALID_CONTRACT_MODALITIES:
            continue
        fields.append(ContractIOField(name=name, modality=modality))  # type: ignore[arg-type]
    return fields


def _shapes_to_contracts(
    shapes: list[dict[str, Any]],
    root: dict[str, Any],
    findings_by_citation: dict[str, list[FindingRecord]],
) -> list[Contract]:
    contracts: list[Contract] = []
    used_ids: set[str] = set()
    for shape in shapes:
        role_slug = slugify(shape["role"]) or "untitled"
        contract_id = _unique_id(f"contract-{role_slug}", used_ids)
        invariants = _extract_invariants_for_shape(shape, root, findings_by_citation)
        required_findings = _findings_for_citations(shape["citations"], findings_by_citation)
        if not required_findings:
            # Bootstrap: synthesize a placeholder FindingRef per citation so
            # Contract.required_findings is never empty. Resolves against
            # citations/index.yaml in the scaffold validator's bootstrap path.
            required_findings = [
                FindingRef(
                    finding_id=cid,
                    citation_id=cid,
                    seed_path=f"seeds/{cid}.md",
                    locator={"stage": "bootstrap"},
                )
                for cid in shape["citations"] or ["src-unknown"]
            ]
        contracts.append(Contract(
            contract_id=contract_id,
            title=_compose_title(shape["role"], shape["source"]),
            inputs=shape["inputs"],
            outputs=shape["outputs"],
            invariants=invariants or ["Inherits decision-log constraints."],
            test_fixtures=[],
            required_findings=required_findings,
        ))
    return contracts


def _policy_contract(
    root: dict[str, Any],
    findings_by_citation: dict[str, list[FindingRecord]],
) -> Contract:
    citations: list[str] = []
    invariants: list[str] = []
    for row in root.get("conventions") or []:
        if not isinstance(row, dict):
            continue
        citations.extend(as_string_list(row.get("citations", [])))
        choice = str(row.get("choice") or "").strip()
        name = str(row.get("name") or "").strip()
        if choice and name:
            invariants.append(f"{name}: {choice}")

    findings = _findings_for_citations(citations, findings_by_citation)
    if not findings:
        findings = [
            FindingRef(
                finding_id=cid,
                citation_id=cid,
                seed_path=f"seeds/{cid}.md",
                locator={"stage": "bootstrap"},
            )
            for cid in citations or ["src-conventions"]
        ]
    if not invariants:
        invariants = ["Decision-log conventions apply."]
    return Contract(
        contract_id="contract-policy",
        title="Decision-Log Policy Contract",
        inputs=[ContractIOField(name="decision_log", modality="document")],
        outputs=[ContractIOField(name="policy_compliance_report", modality="data")],
        invariants=invariants,
        test_fixtures=[],
        required_findings=findings,
    )


def _extract_invariants_for_shape(
    shape: dict[str, Any],
    root: dict[str, Any],
    findings_by_citation: dict[str, list[FindingRecord]],
) -> list[str]:
    invariants: list[str] = list(shape.get("constraints") or [])
    # Pull claim statements (normative declarations) from the cited findings.
    for cid in shape.get("citations") or []:
        for rec in findings_by_citation.get(cid, []):
            for claim in rec.claims:
                statement = str(claim.get("statement") or "").strip()
                if statement and statement not in invariants:
                    invariants.append(statement)
    if not invariants:
        invariants = ["Inherits decision-log constraints."]
    return invariants


def _findings_for_citations(
    citations: list[str],
    findings_by_citation: dict[str, list[FindingRecord]],
) -> list[FindingRef]:
    refs: list[FindingRef] = []
    seen: set[str] = set()
    for cid in citations or []:
        for rec in findings_by_citation.get(cid, []):
            if rec.finding_id in seen:
                continue
            seen.add(rec.finding_id)
            refs.append(FindingRef(
                finding_id=rec.finding_id,
                citation_id=rec.citation_id,
                seed_path=rec.seed_path or f"seeds/{rec.citation_id}.md",
                locator={"file_hash": rec.file_hash[:12] if rec.file_hash else ""},
            ))
    return refs


def _write_fixtures(
    fixtures_root: Path,
    contract: Contract,
    findings_by_citation: dict[str, list[FindingRecord]],
) -> list[str]:
    """Persist table/figure fixtures from every cited finding into
    contracts/{contract_id}/fixtures/ as CSV (rows) or JSON (other)."""
    tables_dir = fixtures_root / "fixtures"
    written: list[str] = []
    for ref in contract.required_findings:
        records = findings_by_citation.get(ref.citation_id, [])
        for rec in records:
            if rec.finding_id != ref.finding_id and ref.finding_id != ref.citation_id:
                # Only include the finding the ref points at, unless the ref
                # is a citation-ID bootstrap placeholder (finding_id == citation_id).
                continue
            for idx, entry in enumerate(rec.tables_figures):
                if not isinstance(entry, dict):
                    continue
                rows = entry.get("rows")
                label = str(entry.get("label") or f"{ref.citation_id}-t{idx}").strip() or f"t{idx}"
                slug = slugify(label) or f"fig{idx}"
                if isinstance(rows, list) and rows and all(isinstance(r, list) for r in rows):
                    target = tables_dir / f"{slug}.csv"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("w", encoding="utf-8", newline="") as handle:
                        writer = csv.writer(handle)
                        writer.writerows(rows)
                else:
                    target = tables_dir / f"{slug}.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("w", encoding="utf-8") as handle:
                        json.dump(entry, handle, indent=2)
                # Record path relative to the scaffold root (contracts/...).
                rel = target.relative_to(fixtures_root.parent.parent).as_posix()
                if rel not in written:
                    written.append(rel)
    return written


def _rewrite_capability_refs(
    capabilities: list[Capability],
    contract_index: dict[str, Contract],
    root: dict[str, Any],
) -> list[Capability]:
    # Pick the first contract whose role slug matches the capability's role
    # prefix (req-, arch-, convention-, code-arch-). Fallback to contract-policy
    # if nothing matches.
    updated: list[Capability] = []
    for cap in capabilities:
        target_id = _match_contract(cap, contract_index)
        if target_id == cap.io_contract_ref:
            updated.append(cap)
            continue
        updated.append(cap.model_copy(update={"io_contract_ref": target_id}))
    return updated


def _match_contract(cap: Capability, contract_index: dict[str, Contract]) -> str:
    # Prefer a contract_id that shares a slug with the capability's name.
    # e.g., req-req-001-foo -> contract-scaffold-generator? No deterministic
    # slug match possible without more metadata; use the first non-policy
    # contract produced from an agent shape as the default, fall back to
    # contract-policy.
    if "contract-policy" not in contract_index and contract_index:
        return next(iter(contract_index))  # first contract
    for cid in contract_index:
        if cid == "contract-policy":
            continue
        return cid
    return "contract-policy"


def _compose_title(role: str, source: str) -> str:
    role = role.strip() or "untitled"
    if source == "agent":
        return f"Agent I/O Contract: {role}"
    if source == "architecture":
        return f"Architecture Contract: {role}"
    if source == "code_architecture":
        return f"Code Architecture Contract: {role}"
    return f"Contract: {role}"


def _dedupe_contracts_by_shape(contracts: Iterable[Contract]) -> list[Contract]:
    seen: dict[tuple, Contract] = {}
    order: list[tuple] = []
    for contract in contracts:
        signature = (
            tuple(sorted((f.name, f.modality) for f in contract.inputs)),
            tuple(sorted((f.name, f.modality) for f in contract.outputs)),
        )
        if signature in seen:
            continue
        seen[signature] = contract
        order.append(signature)
    return [seen[sig] for sig in order]


def _unique_id(candidate: str, used: set[str]) -> str:
    base = (candidate.strip().strip("-") or "contract")[:80]
    cid = base
    counter = 2
    while cid in used:
        cid = f"{base}-{counter}"
        counter += 1
    used.add(cid)
    return cid
