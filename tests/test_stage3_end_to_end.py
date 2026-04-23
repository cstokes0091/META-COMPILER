"""Stage 3 end-to-end integration test.

Exercises `meta_compiler.stages.scaffold_stage.run_scaffold` (the thin composer
that invokes the four post-dialogue producers) against a realistic hybrid
fixture. Asserts:

- All four stages produced their artefacts.
- validate_scaffold returns [] (the new-shape validator with 11 checks).
- The OLD scaffold artifacts (ARCHITECTURE.md, AGENT_REGISTRY.yaml,
  orchestrator/run_stage4.py, agents/) are NOT produced.
- Re-running against the same inputs is idempotent modulo generated_at
  timestamps.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from meta_compiler.stages.scaffold_stage import run_scaffold
from meta_compiler.stages.workspace_bootstrap_stage import PALETTE_AGENTS
from meta_compiler.validation import validate_scaffold


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def _write_palette(ws_root: Path) -> None:
    agents_dir = ws_root / ".github" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for name in PALETTE_AGENTS:
        (agents_dir / f"{name}.agent.md").write_text(
            f"---\nname: {name}\ndescription: test\ntools: [read]\nagents: []\nuser-invocable: false\n---\n# {name}\n",
            encoding="utf-8",
        )


def _seed_hybrid_workspace(tmp_path: Path) -> tuple[Path, Path]:
    ws_root = tmp_path
    artifacts = ws_root / "workspace-artifacts"
    _write(
        artifacts / "manifests" / "workspace_manifest.yaml",
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
    _write(
        artifacts / "wiki" / "citations" / "index.yaml",
        {
            "citations_index": {
                "citations": {
                    "src-decision-seed": {
                        "human": "seed",
                        "source": {"type": "document", "path": "seeds/decision-seed.md"},
                        "metadata": {"title": "seed"},
                        "status": "tracked",
                    }
                }
            }
        },
    )
    _write(
        artifacts / "decision-logs" / "decision_log_v1.yaml",
        {
            "decision_log": {
                "meta": {
                    "project_name": "Test",
                    "project_type": "hybrid",
                    "created": "2026-04-22T00:00:00+00:00",
                    "version": 1,
                    "parent_version": None,
                    "reason_for_revision": None,
                    "problem_statement_hash": "abc",
                    "wiki_version": "xyz",
                    "use_case": "unit-test",
                },
                "conventions": [
                    {
                        "name": "Citation prefix",
                        "domain": "citation",
                        "choice": "src- prefix with kebab suffix",
                        "rationale": "uniform lookup",
                        "citations": ["src-decision-seed"],
                    }
                ],
                "architecture": [
                    {
                        "component": "workflow-orchestrator",
                        "approach": "Artifact-driven stage transitions",
                        "alternatives_rejected": [{"name": "chat", "reason": "coupled"}],
                        "constraints_applied": ["fresh context"],
                        "citations": ["src-decision-seed"],
                    }
                ],
                "scope": {
                    "in_scope": [{"item": "decision capture", "rationale": "needed"}],
                    "out_of_scope": [],
                },
                "requirements": [
                    {
                        "id": "REQ-001",
                        "description": "Decision log must be schema-valid and citation-traceable.",
                        "source": "derived",
                        "citations": ["src-decision-seed"],
                        "verification": "Run validate-stage --stage 2.",
                    },
                    {
                        "id": "REQ-002",
                        "description": "Scaffold must consume Decision Log and findings to produce capabilities.",
                        "source": "derived",
                        "citations": ["src-decision-seed"],
                        "verification": "Run validate-stage --stage 3.",
                    },
                ],
                "open_items": [],
                "agents_needed": [
                    {
                        "role": "scaffold-generator",
                        "responsibility": "generate scaffold",
                        "inputs": [{"name": "decision_log", "modality": "document"}],
                        "outputs": [{"name": "scaffold", "modality": "document"}],
                        "key_constraints": ["trace instructions"],
                    }
                ],
                "code_architecture": [
                    {"aspect": "language", "choice": "Python 3.11", "rationale": "runtime", "citations": ["src-decision-seed"]},
                    {
                        "aspect": "libraries",
                        "choice": "stdlib",
                        "rationale": "deterministic",
                        "citations": ["src-decision-seed"],
                        "libraries": [{"name": "PyYAML", "version": ">=6.0", "citation": "src-decision-seed", "description": "YAML parsing"}],
                    },
                ],
            }
        },
    )
    payload = {
        "citation_id": "src-decision-seed",
        "seed_path": "seeds/decision-seed.md",
        "file_hash": "seedhashAseedhashA",
        "extracted_at": "2026-04-22T00:00:00+00:00",
        "extractor": "test",
        "document_metadata": {"title": "seed"},
        "concepts": [
            {"name": "Decision Log Schema"},
            {"name": "Workflow Orchestrator"},
            {"name": "Citation Prefix"},
        ],
        "quotes": [
            {"text": "Decision logs must be schema-valid and citation-traceable.", "locator": {"page": 1}}
        ],
        "equations": [],
        "claims": [{"statement": "Every requirement has a citation.", "locator": {"page": 2}}],
        "tables_figures": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {},
    }
    finding_path = artifacts / "wiki" / "findings" / "src-decision-seed.json"
    finding_path.parent.mkdir(parents=True, exist_ok=True)
    finding_path.write_text(json.dumps(payload), encoding="utf-8")
    _write_palette(ws_root)
    return ws_root, artifacts


def test_run_scaffold_hybrid_e2e(tmp_path):
    ws_root, artifacts = _seed_hybrid_workspace(tmp_path)
    result = run_scaffold(artifacts)

    scaffold_root = artifacts / "scaffolds" / "v1"

    # All four sub-stages produced metadata.
    assert "capability_compile" in result
    assert "contract_extract" in result
    assert "skill_synthesis" in result
    assert "workspace_bootstrap" in result
    assert result["decision_log_version"] == 1

    # New-shape artefacts present.
    for rel in (
        "SCAFFOLD_MANIFEST.yaml",
        "EXECUTION_MANIFEST.yaml",
        "DISPATCH_HINTS.yaml",
        "capabilities.yaml",
        "contracts/_manifest.yaml",
        "skills/INDEX.md",
        "verification/REQ_TRACE.yaml",
    ):
        assert (scaffold_root / rel).exists(), f"missing {rel}"

    # Old-shape artefacts ABSENT.
    for rel in (
        "ARCHITECTURE.md",
        "CONVENTIONS.md",
        "REQUIREMENTS_TRACED.md",
        "CODE_ARCHITECTURE.md",
        "AGENT_REGISTRY.yaml",
        "orchestrator/run_stage4.py",
        "agents",
        ".github/agents",
        ".github/skills",
        ".github/instructions",
        "docs/skills",
        "docs/instructions",
        "requirements/REQ_TRACE_MATRIX.md",
    ):
        assert not (scaffold_root / rel).exists(), f"legacy artefact present: {rel}"

    # validate_scaffold returns [] for the fixture.
    issues = validate_scaffold(scaffold_root)
    assert issues == [], "\n".join(issues)


def test_run_scaffold_is_idempotent(tmp_path):
    ws_root, artifacts = _seed_hybrid_workspace(tmp_path)
    run_scaffold(artifacts)
    cap_path = artifacts / "scaffolds" / "v1" / "capabilities.yaml"
    first = yaml.safe_load(cap_path.read_text(encoding="utf-8"))
    del first["capability_graph"]["generated_at"]

    run_scaffold(artifacts)
    second = yaml.safe_load(cap_path.read_text(encoding="utf-8"))
    del second["capability_graph"]["generated_at"]

    assert first == second, "re-run produced a different capability graph"


def test_run_scaffold_covers_every_requirement(tmp_path):
    ws_root, artifacts = _seed_hybrid_workspace(tmp_path)
    run_scaffold(artifacts)
    cap_path = artifacts / "scaffolds" / "v1" / "capabilities.yaml"
    graph = yaml.safe_load(cap_path.read_text(encoding="utf-8"))["capability_graph"]
    covered: set[str] = set()
    for cap in graph["capabilities"]:
        for rid in cap.get("requirement_ids") or []:
            covered.add(rid)
    assert {"REQ-001", "REQ-002"}.issubset(covered)
