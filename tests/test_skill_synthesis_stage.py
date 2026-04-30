"""Tests for meta_compiler.stages.skill_synthesis_stage.

Exercises:
- one SKILL.md per capability
- body contains domain tokens drawn from cited findings (not slot placeholders)
- frontmatter round-trips through Pydantic SkillFrontmatter
- INDEX.md entries map 1:1 to skill files
- generic trigger in a capability is flagged by validate_trigger_specificity
  (even though synthesis itself still produces the file)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from meta_compiler.io import parse_frontmatter
from meta_compiler.schemas import SkillFrontmatter, SkillIndex
from meta_compiler.stages.capability_compile_stage import run_capability_compile
from meta_compiler.stages.contract_extract_stage import run_contract_extract
from meta_compiler.stages.skill_synthesis_stage import run_skill_synthesis


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def _setup_fixture(tmp_path: Path) -> None:
    # Minimal happy-path workspace: one requirement, one agent, one finding with
    # concept names + claim statements that land in both capabilities and contract.
    _write(
        tmp_path / "manifests" / "workspace_manifest.yaml",
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
        tmp_path / "wiki" / "citations" / "index.yaml",
        {
            "citations_index": {
                "citations": {
                    "src-decision-seed": {
                        "human": "Decision seed",
                        "source": {"type": "document", "path": "seeds/decision-seed.md"},
                        "metadata": {"title": "seed"},
                        "status": "tracked",
                    }
                }
            }
        },
    )
    _write(
        tmp_path / "decision-logs" / "decision_log_v1.yaml",
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
    # Finding with concepts + claims that will surface in the skill body.
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
        "claims": [
            {"statement": "Every requirement has a citation.", "locator": {"page": 2}}
        ],
        "tables_figures": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {},
    }
    target = tmp_path / "wiki" / "findings" / "src-decision-seed.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def test_skill_per_capability(tmp_path):
    _setup_fixture(tmp_path)
    run_capability_compile(tmp_path)
    run_contract_extract(tmp_path)
    result = run_skill_synthesis(tmp_path)

    skills_dir = tmp_path / "scaffolds" / "v1" / "skills"
    cap_yaml = yaml.safe_load(
        (tmp_path / "scaffolds" / "v1" / "capabilities.yaml").read_text(encoding="utf-8")
    )
    names = [c["name"] for c in cap_yaml["capability_graph"]["capabilities"]]
    for name in names:
        assert (skills_dir / name / "SKILL.md").exists(), f"missing SKILL for {name}"

    assert result["skill_count"] == len(names)
    assert (skills_dir / "INDEX.md").exists()


def test_skill_body_has_domain_terms_not_placeholders(tmp_path):
    _setup_fixture(tmp_path)
    run_capability_compile(tmp_path)
    run_contract_extract(tmp_path)
    run_skill_synthesis(tmp_path)

    skill_path = tmp_path / "scaffolds" / "v1" / "skills" / "req-req-001-decision-log-must-be-schema-valid-and-ci" / "SKILL.md"
    if not skill_path.exists():
        # Name may differ — pick whatever req-* skill exists.
        req_skills = list((tmp_path / "scaffolds" / "v1" / "skills").glob("req-*/SKILL.md"))
        assert req_skills, "no req-* skill produced"
        skill_path = req_skills[0]

    text = skill_path.read_text(encoding="utf-8")

    # Sections non-empty
    for heading in ("## Goal", "## Procedure", "## Inputs and Outputs", "## Invariants", "## Evidence"):
        assert heading in text
        start = text.index(heading) + len(heading)
        next_heading = text.find("\n## ", start)
        section = text[start: next_heading if next_heading != -1 else len(text)]
        assert section.strip(), f"Empty section under {heading} in {skill_path}"

    # Domain tokens from findings
    lowered = text.lower()
    assert "schema-valid" in lowered or "schema valid" in lowered
    assert "citation-traceable" in lowered or "citation" in lowered
    # Quote from findings surfaces in Evidence section
    assert "decision logs must be schema-valid" in lowered

    # No placeholder slot syntax (e.g., "{algorithm_name}", "TODO", "Fill in")
    for placeholder in ("{algorithm_name}", "{method}", "TODO", "Fill in", "<PLACEHOLDER>"):
        assert placeholder.lower() not in lowered


def test_skill_body_uses_planner_steps_when_plan_extract_exists(tmp_path):
    _setup_fixture(tmp_path)
    _write(
        tmp_path / "decision-logs" / "plan_extract_v1.yaml",
        {
            "plan_extract": {
                "generated_at": "2026-04-22T00:00:00+00:00",
                "decision_log_version": 1,
                "source": "decision-logs/implementation_plan_v1.md",
                "version": 2,
                "capabilities": [
                    {
                        "name": "schema-dispatch",
                        "phase": "dispatch",
                        "objective": "Produce citation-traced schema dispatch outputs.",
                        "description": "Compile schema dispatch outputs from the decision log.",
                        "requirement_ids": ["REQ-001"],
                        "constraint_ids": [],
                        "verification_required": True,
                        "composes": [],
                        "explicit_triggers": ["decision log schema workflow"],
                        "evidence_refs": ["src-decision-seed"],
                        "implementation_steps": [
                            "Load the decision log and cited finding records",
                            "Write schema dispatch output with citation trace metadata",
                        ],
                        "acceptance_criteria": [
                            "Output includes citation trace metadata for REQ-001",
                        ],
                        "parallelizable": False,
                        "rationale": "The planner provided concrete execution guidance.",
                    }
                ],
            }
        },
    )
    run_capability_compile(tmp_path)
    run_contract_extract(tmp_path)
    run_skill_synthesis(tmp_path)

    skill_path = tmp_path / "scaffolds" / "v1" / "skills" / "schema-dispatch" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")

    assert "Produce citation-traced schema dispatch outputs." in text
    assert "Load the decision log and cited finding records." in text
    assert "Write schema dispatch output with citation trace metadata." in text
    assert "## Acceptance Criteria" in text
    assert "Output includes citation trace metadata for REQ-001" in text


def test_skill_frontmatter_roundtrip(tmp_path):
    _setup_fixture(tmp_path)
    run_capability_compile(tmp_path)
    run_contract_extract(tmp_path)
    run_skill_synthesis(tmp_path)

    for skill_path in (tmp_path / "scaffolds" / "v1" / "skills").glob("*/SKILL.md"):
        fm, body = parse_frontmatter(skill_path.read_text(encoding="utf-8"))
        model = SkillFrontmatter.model_validate(fm)
        assert model.name == skill_path.parent.name
        assert model.triggers
        assert body.strip()


def test_skill_index_entry_matches_skill(tmp_path):
    _setup_fixture(tmp_path)
    run_capability_compile(tmp_path)
    run_contract_extract(tmp_path)
    run_skill_synthesis(tmp_path)

    index_path = tmp_path / "scaffolds" / "v1" / "skills" / "INDEX.md"
    raw = index_path.read_text(encoding="utf-8")
    fm, _body = parse_frontmatter(raw)
    index = SkillIndex.model_validate(fm["skill_index"])

    for entry in index.entries:
        skill_abs = tmp_path / "scaffolds" / "v1" / entry.skill_path
        assert skill_abs.exists(), f"INDEX references missing {entry.skill_path}"
        sk_fm, _ = parse_frontmatter(skill_abs.read_text(encoding="utf-8"))
        assert sk_fm["name"] == entry.capability_name


def test_skill_renders_v2_1_failure_mode_sections(tmp_path):
    """Change B: SKILL.md leads with User Story / The Problem / The Fix and
    has Anti-Patterns + Out of Scope sections rendered from the planner's
    failure-mode framing. Acceptance Criteria points at the runnable spec.
    """
    _setup_fixture(tmp_path)
    _write(
        tmp_path / "decision-logs" / "plan_extract_v1.yaml",
        {
            "plan_extract": {
                "generated_at": "2026-04-30T00:00:00+00:00",
                "decision_log_version": 1,
                "source": "decision-logs/implementation_plan_v1.md",
                "version": 2,
                "capabilities": [
                    {
                        "name": "schema-dispatch",
                        "phase": "dispatch",
                        "objective": "Produce citation-traced schema dispatch outputs.",
                        "description": "Compile schema dispatch outputs from the decision log.",
                        "requirement_ids": ["REQ-001"],
                        "constraint_ids": [],
                        "verification_required": True,
                        "composes": [],
                        "explicit_triggers": ["decision log schema workflow"],
                        "evidence_refs": ["src-decision-seed"],
                        "implementation_steps": [
                            "Load the decision log and cited findings",
                            "Write schema dispatch output with citation trace metadata",
                        ],
                        "acceptance_criteria": [
                            "Output includes citation trace metadata for REQ-001",
                        ],
                        "parallelizable": False,
                        "rationale": "The planner provided concrete execution guidance.",
                        "dispatch_kind": "afk",
                        "user_story": (
                            "As a planner reviewer, I want every dispatch "
                            "row to carry citation trace metadata, so that "
                            "audits stay traceable."
                        ),
                        "the_problem": "Dispatch rows ship without trace metadata and audits drift.",
                        "the_fix": "Compile every row with a trace metadata field that the schema validates.",
                        "anti_patterns": [
                            "silently strip citation IDs when reshaping rows",
                            "Do NOT skip fields when the contract is unclear",
                        ],
                        "out_of_scope": ["Real-time streaming dispatch"],
                        "deletion_test": (
                            "Deleting this leaves three audit pipelines with "
                            "no trace metadata across N callers."
                        ),
                        "acceptance_spec": {
                            "format": "gherkin",
                            "scenarios": [
                                {
                                    "name": "trace_emitted",
                                    "given": "decision log REQ-001 loaded",
                                    "when": "the dispatch compiler runs",
                                    "then": "every row has citation trace metadata for audits",
                                }
                            ],
                        },
                    }
                ],
            }
        },
    )
    run_capability_compile(tmp_path)
    run_contract_extract(tmp_path)
    run_skill_synthesis(tmp_path)

    skill_path = tmp_path / "scaffolds" / "v1" / "skills" / "schema-dispatch" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")

    # Section ordering: User Story / The Problem / The Fix appear before Goal.
    user_story_idx = text.index("## User Story")
    problem_idx = text.index("## The Problem")
    fix_idx = text.index("## The Fix")
    goal_idx = text.index("## Goal")
    assert user_story_idx < problem_idx < fix_idx < goal_idx

    assert "every dispatch row to carry citation trace metadata" in text
    assert "Dispatch rows ship without trace metadata" in text
    assert "trace metadata field that the schema validates" in text

    # Anti-Patterns rendered with "Do NOT" prefix on items that don't already have it.
    anti_idx = text.index("## Anti-Patterns")
    assert "Do NOT silently strip citation IDs" in text
    assert "Do NOT skip fields" in text
    assert anti_idx > goal_idx

    # Out of Scope section
    oos_idx = text.index("## Out of Scope")
    assert "Real-time streaming dispatch" in text
    assert oos_idx > anti_idx

    # Acceptance Criteria points at the runnable spec
    accept_idx = text.index("## Acceptance Criteria")
    accept_section = text[accept_idx:]
    assert "_spec.yaml" in accept_section
    assert "verifies the User Story above" in accept_section
    assert oos_idx < accept_idx


def test_skill_requires_capability_graph(tmp_path):
    _setup_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="capabilities.yaml missing"):
        run_skill_synthesis(tmp_path)


def test_skill_requires_contract_manifest(tmp_path):
    _setup_fixture(tmp_path)
    run_capability_compile(tmp_path)
    with pytest.raises(RuntimeError, match="contracts/_manifest.yaml missing"):
        run_skill_synthesis(tmp_path)
