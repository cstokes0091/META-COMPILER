"""Tests for meta_compiler.validation.validate_scaffold (new capability-driven shape).

After Commit 7, validate_scaffold runs 11 ordered checks against the
post-dialogue layout:
  1. Manifest + top-level files
  2. CapabilityGraph schema
  3. Contract library schema
  4. Finding-citation integrity (with v1 bootstrap exception)
  5. Skill <-> capability symmetry + no stub sections
  6. Trigger specificity
  7. Contract reuse
  8. Capability coverage vs Stage 2 requirements
  9. Verification harness presence
 10. Repo palette
 11. Empty output buckets
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from meta_compiler.stages.capability_compile_stage import run_capability_compile
from meta_compiler.stages.contract_extract_stage import run_contract_extract
from meta_compiler.stages.skill_synthesis_stage import run_skill_synthesis
from meta_compiler.stages.workspace_bootstrap_stage import (
    PALETTE_AGENTS,
    run_workspace_bootstrap,
)
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


def _seed_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Build a complete happy-path workspace and return (ws_root, artifacts_root)."""
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
                "conventions": [],
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
                    }
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
        "concepts": [{"name": "Decision Log Schema"}, {"name": "Workflow Orchestrator"}],
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
    run_capability_compile(artifacts)
    run_contract_extract(artifacts)
    run_skill_synthesis(artifacts)
    run_workspace_bootstrap(artifacts, workspace_root=ws_root)
    return ws_root, artifacts


def _scaffold_root(artifacts: Path) -> Path:
    return artifacts / "scaffolds" / "v1"


def test_validate_scaffold_happy_path(tmp_path):
    ws_root, artifacts = _seed_fixture(tmp_path)
    issues = validate_scaffold(_scaffold_root(artifacts))
    assert issues == [], "\n".join(issues)


def test_missing_manifest_returns_single_issue(tmp_path):
    scaffold = tmp_path / "scaffolds" / "v1"
    scaffold.mkdir(parents=True)
    issues = validate_scaffold(scaffold)
    assert issues == ["scaffold missing file: SCAFFOLD_MANIFEST.yaml"]


def test_missing_capabilities_yaml_is_flagged(tmp_path):
    _, artifacts = _seed_fixture(tmp_path)
    scaffold = _scaffold_root(artifacts)
    (scaffold / "capabilities.yaml").unlink()
    issues = validate_scaffold(scaffold)
    assert any("capabilities.yaml" in msg for msg in issues)


def test_missing_contract_file_is_flagged(tmp_path):
    _, artifacts = _seed_fixture(tmp_path)
    scaffold = _scaffold_root(artifacts)
    target = next((scaffold / "contracts").glob("contract-*.yaml"))
    target.unlink()
    issues = validate_scaffold(scaffold)
    assert any("contracts" in msg or "schema invalid" in msg for msg in issues)


def test_stub_skill_section_rejected(tmp_path):
    _, artifacts = _seed_fixture(tmp_path)
    scaffold = _scaffold_root(artifacts)
    skill = next((scaffold / "skills").glob("*/SKILL.md"))
    text = skill.read_text(encoding="utf-8")
    # Blank out the Evidence section's body.
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == "## Evidence":
            j = idx + 1
            while j < len(lines) and not lines[j].startswith("## "):
                lines[j] = ""
                j += 1
            break
    skill.write_text("\n".join(lines), encoding="utf-8")
    issues = validate_scaffold(scaffold)
    assert any("empty section" in msg and "## Evidence" in msg for msg in issues)


def test_generic_trigger_rejected(tmp_path):
    _, artifacts = _seed_fixture(tmp_path)
    scaffold = _scaffold_root(artifacts)
    cap_path = scaffold / "capabilities.yaml"
    data = yaml.safe_load(cap_path.read_text(encoding="utf-8"))
    data["capability_graph"]["capabilities"][0]["when_to_use"] = ["use when implementing"]
    with cap_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)
    # Also update the matching skill so symmetry check doesn't short-circuit before
    # trigger check.
    cap_name = data["capability_graph"]["capabilities"][0]["name"]
    skill_path = scaffold / "skills" / cap_name / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    text = text.replace(
        "triggers:\n", "triggers:\n- use when implementing\n", 1
    )
    skill_path.write_text(text, encoding="utf-8")
    issues = validate_scaffold(scaffold)
    assert any("generic" in msg for msg in issues)


def test_uncovered_requirement_flagged(tmp_path):
    _, artifacts = _seed_fixture(tmp_path)
    # Add REQ-999 to the decision log but no capability covers it.
    dl_path = artifacts / "decision-logs" / "decision_log_v1.yaml"
    dl = yaml.safe_load(dl_path.read_text(encoding="utf-8"))
    dl["decision_log"]["requirements"].append(
        {
            "id": "REQ-999",
            "description": "Orphan requirement with no capability.",
            "source": "derived",
            "citations": ["src-decision-seed"],
            "verification": "manual",
        }
    )
    _write(dl_path, dl)
    issues = validate_scaffold(_scaffold_root(artifacts))
    assert any("REQ-999" in msg and "coverage gate" in msg for msg in issues)


def test_missing_verification_stub_flagged(tmp_path):
    _, artifacts = _seed_fixture(tmp_path)
    scaffold = _scaffold_root(artifacts)
    stub = next((scaffold / "verification").glob("ver-*.py"))
    stub.unlink()
    issues = validate_scaffold(scaffold)
    assert any("verification hook missing" in msg for msg in issues)


def test_missing_palette_agent_flagged(tmp_path):
    ws_root, artifacts = _seed_fixture(tmp_path)
    (ws_root / ".github" / "agents" / "planner.agent.md").unlink()
    issues = validate_scaffold(_scaffold_root(artifacts))
    assert any("planner.agent.md" in msg for msg in issues)


def test_missing_output_bucket_flagged(tmp_path):
    _, artifacts = _seed_fixture(tmp_path)
    scaffold = _scaffold_root(artifacts)
    import shutil
    shutil.rmtree(scaffold / "code")
    issues = validate_scaffold(scaffold)
    assert any("output bucket" in msg and "code" in msg for msg in issues)


def test_unreferenced_contract_flagged(tmp_path):
    _, artifacts = _seed_fixture(tmp_path)
    scaffold = _scaffold_root(artifacts)
    # Inject an orphan contract: add a new file + manifest entry.
    orphan_path = scaffold / "contracts" / "contract-orphan.yaml"
    orphan_path.write_text(
        yaml.safe_dump({
            "contract": {
                "contract_id": "contract-orphan",
                "title": "Orphan",
                "inputs": [{"name": "x", "modality": "data"}],
                "outputs": [{"name": "y", "modality": "data"}],
                "invariants": ["orphan invariant"],
                "test_fixtures": [],
                "required_findings": [{
                    "finding_id": "src-decision-seed#seedhashAsee",
                    "citation_id": "src-decision-seed",
                    "seed_path": "seeds/decision-seed.md",
                    "locator": {},
                }],
            }
        }, sort_keys=False),
        encoding="utf-8",
    )
    manifest_path = scaffold / "contracts" / "_manifest.yaml"
    m = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    m["contract_manifest"]["entries"].append({
        "contract_id": "contract-orphan",
        "path": "contracts/contract-orphan.yaml",
    })
    manifest_path.write_text(yaml.safe_dump(m, sort_keys=False), encoding="utf-8")
    issues = validate_scaffold(scaffold)
    assert any("contract-orphan" in msg and "unreferenced" in msg for msg in issues)


def test_unresolvable_finding_id_flagged(tmp_path):
    _, artifacts = _seed_fixture(tmp_path)
    scaffold = _scaffold_root(artifacts)
    cap_path = scaffold / "capabilities.yaml"
    data = yaml.safe_load(cap_path.read_text(encoding="utf-8"))
    data["capability_graph"]["capabilities"][0]["required_finding_ids"] = [
        "src-nonexistent#deadbeefdead"
    ]
    with cap_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)
    issues = validate_scaffold(scaffold)
    assert any("src-nonexistent" in msg for msg in issues)
