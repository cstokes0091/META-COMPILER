"""Tests for meta_compiler.stages.contract_extract_stage.

Exercises:
- contract-per-agent extraction with deduped IO shapes
- invariants pulled from key_constraints + cited findings claims
- fixture emission from findings tables_figures
- capabilities.yaml rewrite with real io_contract_ref
- contract reuse across capabilities sharing IO shapes
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from meta_compiler.schemas import CapabilityGraph, Contract, ContractManifest
from meta_compiler.stages.capability_compile_stage import run_capability_compile
from meta_compiler.stages.contract_extract_stage import run_contract_extract


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def _write_manifest(artifacts_root: Path) -> None:
    _write(
        artifacts_root / "manifests" / "workspace_manifest.yaml",
        {
            "workspace_manifest": {
                "project_name": "Test",
                "project_type": "hybrid",
                "wiki_name": "Test Project Atlas",
                "problem_domain": "testing",
                "use_case": "unit-test",
                "research": {
                    "last_completed_stage": "2",
                    "problem_statement_hash": "abc",
                    "wiki_version": "xyz",
                },
            }
        },
    )


def _write_citations(artifacts_root: Path, ids: list[str]) -> None:
    _write(
        artifacts_root / "wiki" / "citations" / "index.yaml",
        {
            "citations_index": {
                "citations": {
                    cid: {
                        "human": cid,
                        "source": {"type": "document", "path": f"seeds/{cid}.md"},
                        "metadata": {"title": cid},
                        "status": "tracked",
                    }
                    for cid in ids
                }
            }
        },
    )


def _write_doc_finding(
    artifacts_root: Path,
    citation_id: str,
    file_hash: str,
    *,
    concepts: list[dict],
    claims: list[dict] | None = None,
    tables_figures: list[dict] | None = None,
) -> None:
    payload = {
        "citation_id": citation_id,
        "seed_path": f"seeds/{citation_id}.md",
        "file_hash": file_hash,
        "extracted_at": "2026-04-22T00:00:00+00:00",
        "extractor": "test",
        "document_metadata": {"title": citation_id},
        "concepts": concepts,
        "quotes": [],
        "equations": [],
        "claims": claims or [],
        "tables_figures": tables_figures or [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {},
    }
    target = artifacts_root / "wiki" / "findings" / f"{citation_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def _write_decision_log(
    artifacts_root: Path,
    *,
    version: int = 1,
    agents_needed: list[dict] | None = None,
    conventions: list[dict] | None = None,
    tables: list[dict] | None = None,
) -> None:
    """Write a two-agent decision log fixture so we can test shape dedup."""
    agents = agents_needed or [
        {
            "role": "scaffold-generator",
            "responsibility": "generate scaffold from decision log",
            "inputs": [{"name": "decision_log", "modality": "document"}],
            "outputs": [{"name": "scaffold", "modality": "document"}],
            "key_constraints": ["trace every instruction to decision log"],
        },
        {
            "role": "validator",
            "responsibility": "validate decision log",
            "inputs": [{"name": "decision_log", "modality": "document"}],
            "outputs": [{"name": "scaffold", "modality": "document"}],
            "key_constraints": ["report all schema violations"],
        },
    ]
    dl = {
        "decision_log": {
            "meta": {
                "project_name": "Test",
                "project_type": "hybrid",
                "created": "2026-04-22T00:00:00+00:00",
                "version": version,
                "parent_version": None,
                "reason_for_revision": None,
                "problem_statement_hash": "abc",
                "wiki_version": "xyz",
                "use_case": "unit-test",
            },
            "conventions": conventions or [],
            "architecture": [
                {
                    "component": "workflow-orchestrator",
                    "approach": "Artifact-driven stage transitions",
                    "alternatives_rejected": [{"name": "chat", "reason": "coupled"}],
                    "constraints_applied": ["fresh context", "strict validation"],
                    "citations": ["src-decision-seed"],
                }
            ],
            "scope": {
                "in_scope": [{"item": "test scope", "rationale": "needed"}],
                "out_of_scope": [],
            },
            "requirements": [
                {
                    "id": "REQ-001",
                    "description": "Decision log must be schema-valid and citation-traceable.",
                    "source": "derived",
                    "citations": ["src-decision-seed"],
                    "verification": "Run validate-stage --stage 2.",
                }
            ],
            "open_items": [],
            "agents_needed": agents,
            "code_architecture": [
                {
                    "aspect": "language",
                    "choice": "Python 3.11",
                    "rationale": "match runtime",
                    "citations": ["src-decision-seed"],
                },
                {
                    "aspect": "libraries",
                    "choice": "stdlib + pyyaml",
                    "rationale": "deterministic builds",
                    "citations": ["src-decision-seed"],
                    "libraries": [
                        {
                            "name": "PyYAML",
                            "version": ">=6.0",
                            "citation": "src-decision-seed",
                            "description": "YAML parsing",
                        }
                    ],
                },
            ],
        }
    }
    _write(artifacts_root / "decision-logs" / f"decision_log_v{version}.yaml", dl)


def _setup(artifacts_root: Path, *, tables: list[dict] | None = None) -> None:
    _write_manifest(artifacts_root)
    _write_citations(artifacts_root, ["src-decision-seed"])
    _write_decision_log(artifacts_root)
    _write_doc_finding(
        artifacts_root,
        "src-decision-seed",
        "seedhashA" * 2,
        concepts=[{"name": "Decision Log Schema"}, {"name": "Workflow Orchestrator"}],
        claims=[
            {"statement": "Every requirement has a citation.", "locator": {"page": 1}},
            {"statement": "Schema violations must be reported.", "locator": {"page": 2}},
        ],
        tables_figures=tables or [],
    )
    run_capability_compile(artifacts_root)


def test_contract_extracted_from_decision_log(tmp_path):
    _setup(tmp_path)
    result = run_contract_extract(tmp_path)

    contracts_dir = tmp_path / "scaffolds" / "v1" / "contracts"
    manifest_path = contracts_dir / "_manifest.yaml"
    assert manifest_path.exists()
    manifest_payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest = ContractManifest.model_validate(manifest_payload["contract_manifest"])
    assert manifest.entries, "manifest must list at least one contract"

    for entry in manifest.entries:
        path = tmp_path / "scaffolds" / "v1" / entry.path
        assert path.exists(), f"contract yaml missing: {entry.path}"
        Contract.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))["contract"])

    assert result["contract_count"] == len(manifest.entries)


def test_contract_reuse_dedupes_shared_shapes(tmp_path):
    # Two agents with identical (decision_log:document)->(scaffold:document)
    # shape should collapse into a single contract entry.
    _setup(tmp_path)
    run_contract_extract(tmp_path)

    manifest = ContractManifest.model_validate(
        yaml.safe_load(
            (tmp_path / "scaffolds" / "v1" / "contracts" / "_manifest.yaml").read_text(encoding="utf-8")
        )["contract_manifest"]
    )
    # Expect 2 contracts: one from the shared agent shape (dedupe) + policy.
    contract_ids = [e.contract_id for e in manifest.entries]
    assert "contract-policy" in contract_ids
    # Agent shapes dedupe down to 1 because both agents have the same IO shape.
    non_policy = [c for c in contract_ids if c != "contract-policy"]
    assert len(non_policy) == 1, f"expected shape dedup to 1 agent contract; got {contract_ids}"


def test_contract_invariants_include_key_constraints_and_claims(tmp_path):
    _setup(tmp_path)
    run_contract_extract(tmp_path)

    # Find the first non-policy contract file.
    contracts_dir = tmp_path / "scaffolds" / "v1" / "contracts"
    non_policy = next(
        p for p in contracts_dir.glob("*.yaml")
        if p.stem not in {"_manifest", "contract-policy"}
    )
    contract = Contract.model_validate(
        yaml.safe_load(non_policy.read_text(encoding="utf-8"))["contract"]
    )
    # key_constraints from the decision log AND claim statements from findings
    # should both show up in the invariants.
    joined = " | ".join(contract.invariants)
    assert "trace every instruction" in joined or "schema violations" in joined
    # Claim statement sourced from findings.claims[].statement.
    assert any("requirement has a citation" in inv for inv in contract.invariants)


def test_fixtures_written_from_tables_figures(tmp_path):
    tables = [
        {
            "label": "sample-table",
            "rows": [
                ["header-a", "header-b"],
                ["row1-a", "row1-b"],
                ["row2-a", "row2-b"],
            ],
        },
        {"label": "flat-fixture", "kind": "diagram", "value": "<svg>...</svg>"},
    ]
    _setup(tmp_path, tables=tables)
    result = run_contract_extract(tmp_path)

    # Fixture files should exist, linked via any contract that cites this finding.
    contracts_dir = tmp_path / "scaffolds" / "v1" / "contracts"
    found_csv = list(contracts_dir.rglob("sample-table.csv"))
    found_json = list(contracts_dir.rglob("flat-fixture.json"))
    assert found_csv, "CSV fixture not emitted"
    assert found_json, "JSON fixture not emitted"

    with found_csv[0].open("r", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["header-a", "header-b"]

    with found_json[0].open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["kind"] == "diagram"

    assert result["fixtures_written"] >= 2


def test_capabilities_rewritten_with_real_contract_refs(tmp_path):
    _setup(tmp_path)
    # Pre-extract: every capability carries its placeholder io_contract_ref.
    cap_path = tmp_path / "scaffolds" / "v1" / "capabilities.yaml"
    before = CapabilityGraph.model_validate(
        yaml.safe_load(cap_path.read_text(encoding="utf-8"))["capability_graph"]
    )
    assert all(cap.io_contract_ref.startswith("contract-") for cap in before.capabilities)
    assert any(
        "req-req-001" in cap.io_contract_ref  # placeholder uses the capability name
        for cap in before.capabilities
    )

    run_contract_extract(tmp_path)

    after = CapabilityGraph.model_validate(
        yaml.safe_load(cap_path.read_text(encoding="utf-8"))["capability_graph"]
    )
    manifest = ContractManifest.model_validate(
        yaml.safe_load(
            (tmp_path / "scaffolds" / "v1" / "contracts" / "_manifest.yaml").read_text(encoding="utf-8")
        )["contract_manifest"]
    )
    known_ids = {entry.contract_id for entry in manifest.entries}
    for cap in after.capabilities:
        assert cap.io_contract_ref in known_ids, (
            f"capability {cap.name} points at unknown contract {cap.io_contract_ref}"
        )


def test_contract_extract_requires_capability_graph(tmp_path):
    _write_manifest(tmp_path)
    _write_citations(tmp_path, ["src-decision-seed"])
    _write_decision_log(tmp_path)
    with pytest.raises(RuntimeError, match="capabilities.yaml missing"):
        run_contract_extract(tmp_path)
