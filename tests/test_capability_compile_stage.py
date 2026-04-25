"""Tests for meta_compiler.stages.capability_compile_stage.

Exercises the four paths:
- happy path: decision log with findings -> capabilities.yaml valid against Pydantic
- bootstrap: v1 decision log, empty findings -> succeeds, citation IDs used as
  placeholder finding IDs
- missing findings (v>1): raises RuntimeError unless allow_empty_findings=True
- coverage: every requirement in the decision log maps to >=1 capability
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from meta_compiler.artifacts import build_paths
from meta_compiler.schemas import CapabilityGraph
from meta_compiler.stages.capability_compile_stage import run_capability_compile


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
                        "human": cid.replace("-", " ").title(),
                        "source": {"type": "document", "path": f"seeds/{cid}.md"},
                        "metadata": {"title": cid},
                        "status": "tracked",
                    }
                    for cid in ids
                }
            }
        },
    )


def _write_decision_log(artifacts_root: Path, version: int, *, requirements: list[dict], architecture: list[dict] | None = None, conventions: list[dict] | None = None, project_type: str = "hybrid", code_architecture: list[dict] | None = None) -> None:
    dl = {
        "decision_log": {
            "meta": {
                "project_name": "Test",
                "project_type": project_type,
                "created": "2026-04-22T00:00:00+00:00",
                "version": version,
                "parent_version": None,
                "reason_for_revision": None,
                "problem_statement_hash": "abc",
                "wiki_version": "xyz",
                "use_case": "unit-test",
            },
            "conventions": conventions or [],
            "architecture": architecture or [],
            "scope": {
                "in_scope": [{"item": "test scope", "rationale": "needed"}],
                "out_of_scope": [],
            },
            "requirements": requirements,
            "open_items": [],
            "agents_needed": [
                {
                    "role": "scaffold-generator",
                    "responsibility": "generate scaffold",
                    "inputs": [{"name": "decision_log", "modality": "document"}],
                    "outputs": [{"name": "scaffold", "modality": "document"}],
                    "key_constraints": ["trace citations"],
                }
            ],
        }
    }
    if project_type in {"algorithm", "hybrid"}:
        dl["decision_log"]["code_architecture"] = code_architecture or [
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
                        "description": "YAML parsing stdlib complement",
                    },
                ],
            },
        ]
    _write(artifacts_root / "decision-logs" / f"decision_log_v{version}.yaml", dl)


def _write_doc_finding(artifacts_root: Path, citation_id: str, file_hash: str, concepts: list[dict], claims: list[dict] | None = None) -> None:
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
        "tables_figures": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {},
    }
    target = artifacts_root / "wiki" / "findings" / f"{citation_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def _setup_fixture(artifacts_root: Path, *, version: int = 1, with_findings: bool = True) -> None:
    _write_manifest(artifacts_root)
    _write_citations(artifacts_root, ["src-decision-seed", "src-sample-seed"])
    _write_decision_log(
        artifacts_root,
        version=version,
        requirements=[
            {
                "id": "REQ-001",
                "description": "Decision log must be schema-valid and citation-traceable.",
                "source": "derived",
                "citations": ["src-decision-seed", "src-sample-seed"],
                "verification": "Run validate-stage --stage 2 with zero issues.",
            },
            {
                "id": "REQ-002",
                "description": "Scaffold generator must consume Decision Log only.",
                "source": "derived",
                "citations": ["src-decision-seed"],
                "verification": "Run scaffold command and verify traces.",
            },
        ],
        architecture=[
            {
                "component": "workflow-orchestrator",
                "approach": "Artifact-driven stage transitions with strict schema checks",
                "alternatives_rejected": [{"name": "chat-coupled", "reason": "fresh-context"}],
                "constraints_applied": ["fresh context", "strict validation"],
                "citations": ["src-decision-seed"],
            }
        ],
        conventions=[
            {
                "name": "Citation prefix",
                "domain": "citation",
                "choice": "src- prefix with kebab suffix",
                "rationale": "uniform lookup",
                "citations": ["src-sample-seed"],
            }
        ],
    )
    if with_findings:
        _write_doc_finding(
            artifacts_root,
            "src-decision-seed",
            "seedhashA" * 2,
            concepts=[
                {"name": "Decision Log Schema"},
                {"name": "Workflow Orchestrator", "aliases": ["Conductor"]},
            ],
            claims=[
                {"statement": "Every requirement has a citation.", "locator": {"page": 1}},
            ],
        )
        _write_doc_finding(
            artifacts_root,
            "src-sample-seed",
            "seedhashB" * 2,
            concepts=[{"name": "Citation Prefix"}],
        )


def test_capability_compile_happy_path(tmp_path):
    artifacts_root = tmp_path
    _setup_fixture(artifacts_root, version=1, with_findings=True)
    result = run_capability_compile(artifacts_root)

    out = tmp_path / "scaffolds" / "v1" / "capabilities.yaml"
    assert out.exists()
    payload = yaml.safe_load(out.read_text(encoding="utf-8"))
    graph = CapabilityGraph.model_validate(payload["capability_graph"])

    # Requirements, architecture, and conventions each produce at least one capability.
    assert graph.decision_log_version == 1
    assert graph.project_type == "hybrid"
    names = [c.name for c in graph.capabilities]
    assert any(n.startswith("req-req-001") for n in names)
    assert any(n.startswith("req-req-002") for n in names)
    assert any(n.startswith("arch-workflow-orchestrator") for n in names)
    assert any(n.startswith("convention-citation-prefix") for n in names)

    # Every capability has triggers that contain a domain token.
    for cap in graph.capabilities:
        assert cap.when_to_use, cap.name
        # findings-traced capabilities should have finding IDs of the form cite#hash
        assert all("#" in fid or fid.startswith("src-") for fid in cap.required_finding_ids)

    # Result metadata
    assert result["capability_count"] == len(graph.capabilities)
    assert result["findings_considered"] == 2


def test_capability_compile_every_requirement_covered(tmp_path):
    artifacts_root = tmp_path
    _setup_fixture(artifacts_root, version=1, with_findings=True)
    run_capability_compile(artifacts_root)

    payload = yaml.safe_load(
        (tmp_path / "scaffolds" / "v1" / "capabilities.yaml").read_text(encoding="utf-8")
    )
    graph = CapabilityGraph.model_validate(payload["capability_graph"])
    covered = set()
    for cap in graph.capabilities:
        covered |= set(cap.requirement_ids)
    assert {"REQ-001", "REQ-002"}.issubset(covered)


def test_capability_compile_empty_findings_bootstrap(tmp_path):
    artifacts_root = tmp_path
    _setup_fixture(artifacts_root, version=1, with_findings=False)
    run_capability_compile(artifacts_root)

    payload = yaml.safe_load(
        (tmp_path / "scaffolds" / "v1" / "capabilities.yaml").read_text(encoding="utf-8")
    )
    graph = CapabilityGraph.model_validate(payload["capability_graph"])
    # With no findings, required_finding_ids falls back to citation IDs.
    for cap in graph.capabilities:
        for fid in cap.required_finding_ids:
            assert "#" not in fid  # citation IDs are unhashed
            assert fid.startswith("src-")


def test_capability_compile_empty_findings_nonbootstrap_raises(tmp_path):
    artifacts_root = tmp_path
    _setup_fixture(artifacts_root, version=2, with_findings=False)
    with pytest.raises(RuntimeError, match="bootstrap without findings"):
        run_capability_compile(artifacts_root)


def test_capability_compile_empty_findings_nonbootstrap_allowed_with_flag(tmp_path):
    artifacts_root = tmp_path
    _setup_fixture(artifacts_root, version=2, with_findings=False)
    run_capability_compile(artifacts_root, allow_empty_findings=True)
    # When allow_empty_findings is set, bootstrap-style citation placeholders
    # are used regardless of version — validator picks this up in Commit 7.
    out = tmp_path / "scaffolds" / "v2" / "capabilities.yaml"
    assert out.exists()


def test_capability_compile_raises_when_no_capabilities_buildable(tmp_path):
    # When every source row has empty citations, `_extract_capabilities`
    # drops them all and raises — guard against silent empty-graph output.
    artifacts_root = tmp_path
    _write_manifest(artifacts_root)
    _write_citations(artifacts_root, ["src-x"])
    _write_decision_log(
        artifacts_root,
        version=1,
        project_type="report",  # skips code_architecture gate
        requirements=[
            {
                "id": "REQ-001",
                "description": "Stub requirement",
                "source": "derived",
                "citations": [],  # no citations → no capability
                "verification": "manual",
            }
        ],
    )
    # validate_decision_log will raise on the empty citations list before we
    # reach our no-capabilities check — either RuntimeError is acceptable.
    with pytest.raises(RuntimeError):
        run_capability_compile(artifacts_root)


def test_capability_compile_composition_links_sibling_requirements(tmp_path):
    artifacts_root = tmp_path
    _setup_fixture(artifacts_root, version=1, with_findings=True)
    run_capability_compile(artifacts_root)
    payload = yaml.safe_load(
        (tmp_path / "scaffolds" / "v1" / "capabilities.yaml").read_text(encoding="utf-8")
    )
    graph = CapabilityGraph.model_validate(payload["capability_graph"])
    # Architecture and convention capabilities inherit both requirement IDs
    # via the fallback, so they compose with both req capabilities.
    arch = next(c for c in graph.capabilities if c.name.startswith("arch-"))
    assert any(n.startswith("req-req-001") for n in arch.composes)
    assert any(n.startswith("req-req-002") for n in arch.composes)


def test_capability_compile_consumes_plan_extract(tmp_path):
    """When plan_extract_v{N}.yaml is present, capabilities follow the planner
    not the per-row fallback — N-to-M REQ/CON mappings, verification_required
    propagates."""
    artifacts_root = tmp_path
    _setup_fixture(artifacts_root, version=1, with_findings=True)

    # Add a constraint to the decision log (the fixture didn't include any).
    decision_log_path = artifacts_root / "decision-logs" / "decision_log_v1.yaml"
    payload = yaml.safe_load(decision_log_path.read_text(encoding="utf-8"))
    payload["decision_log"]["constraints"] = [
        {
            "id": "CON-001",
            "description": "Latency budget < 250ms p95",
            "kind": "performance_target",
            "verification_required": True,
            "citations": ["src-decision-seed"],
            "rationale": "SLA",
        }
    ]
    _write(decision_log_path, payload)

    # Plan that bundles REQ-001 + REQ-002 into a single capability and adds a
    # constraint-only capability.
    plan_path = artifacts_root / "decision-logs" / "plan_extract_v1.yaml"
    _write(
        plan_path,
        {
            "plan_extract": {
                "generated_at": "2026-04-22T00:00:00+00:00",
                "decision_log_version": 1,
                "source": "decision-logs/implementation_plan_v1.md",
                "version": 1,
                "capabilities": [
                    {
                        "name": "shared-pipeline",
                        "description": "Bundle REQ-001 and REQ-002 into one pipeline",
                        "requirement_ids": ["REQ-001", "REQ-002"],
                        "constraint_ids": [],
                        "verification_required": True,
                        "composes": [],
                        "rationale": "Sibling REQs share the ingest path",
                    },
                    {
                        "name": "latency-gate",
                        "description": "Enforce CON-001 at runtime",
                        "requirement_ids": [],
                        "constraint_ids": ["CON-001"],
                        "verification_required": False,
                        "composes": [],
                        "rationale": "Policy-only — runtime check, no pytest stub",
                    },
                ],
            }
        },
    )

    run_capability_compile(artifacts_root)
    out = yaml.safe_load(
        (tmp_path / "scaffolds" / "v1" / "capabilities.yaml").read_text(encoding="utf-8")
    )
    graph = CapabilityGraph.model_validate(out["capability_graph"])
    by_name = {c.name: c for c in graph.capabilities}
    assert set(by_name) == {"shared-pipeline", "latency-gate"}

    # N-to-M REQ mapping preserved.
    assert sorted(by_name["shared-pipeline"].requirement_ids) == ["REQ-001", "REQ-002"]
    assert by_name["shared-pipeline"].verification_required is True

    # Constraint-only capability has empty requirement_ids and no verification.
    latency = by_name["latency-gate"]
    assert latency.requirement_ids == []
    assert latency.constraint_ids == ["CON-001"]
    assert latency.verification_required is False
    # Hook ID is allocated for traceability even when no pytest stub will be
    # written; workspace_bootstrap is what gates stub generation on
    # verification_required.
    assert latency.verification_hook_ids == ["ver-latency-gate-001"]


def test_capability_compile_falls_back_when_plan_extract_absent(tmp_path):
    """No plan_extract_v{N}.yaml → legacy 1-to-1 row mapping is used."""
    artifacts_root = tmp_path
    _setup_fixture(artifacts_root, version=1, with_findings=True)
    # No plan_extract written — should use legacy path.
    run_capability_compile(artifacts_root)
    out = yaml.safe_load(
        (tmp_path / "scaffolds" / "v1" / "capabilities.yaml").read_text(encoding="utf-8")
    )
    graph = CapabilityGraph.model_validate(out["capability_graph"])
    # Each REQ produced its own capability (legacy 1-to-1 path).
    req_caps = [c for c in graph.capabilities if c.name.startswith("req-req-")]
    assert len(req_caps) == 2
    for cap in req_caps:
        assert len(cap.requirement_ids) == 1
