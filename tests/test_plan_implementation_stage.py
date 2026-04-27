"""Integration tests for `meta-compiler plan-implementation --start | --finalize`."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from meta_compiler.artifacts import build_paths
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.plan_implementation_stage import (
    parse_plan_markdown,
    run_plan_implementation_finalize,
    run_plan_implementation_start,
    validate_plan_extract,
)
from tests.test_elicit_vision_start import _seed_workspace


def _seed_decision_log(
    paths,
    *,
    requirements: list[dict] | None = None,
    constraints: list[dict] | None = None,
    architecture: list[dict] | None = None,
) -> Path:
    """Write a v1 decision log with optional REQ/CON tables."""
    paths.decision_logs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "decision_log": {
            "meta": {
                "project_name": "Plan Test",
                "project_type": "algorithm",
                "created": "2026-01-01T00:00:00Z",
                "version": 1,
                "parent_version": None,
                "reason_for_revision": None,
                "problem_statement_hash": "x",
                "wiki_version": "v1",
            },
            "conventions": [],
            "architecture": architecture or [],
            "scope": {"in_scope": [], "out_of_scope": []},
            "requirements": requirements or [],
            "constraints": constraints or [],
            "open_items": [],
            "agents_needed": [],
            "code_architecture": [
                {
                    "aspect": "language",
                    "choice": "Python 3.11",
                    "rationale": "match toolchain",
                    "citations": ["src-test"],
                    "alternatives_rejected": [],
                    "constraints_applied": [],
                },
                {
                    "aspect": "libraries",
                    "choice": "pyyaml",
                    "rationale": "stable",
                    "citations": ["src-test"],
                    "alternatives_rejected": [],
                    "constraints_applied": [],
                    "libraries": [{"name": "pyyaml", "description": "x (>=6.0)"}],
                },
            ],
        }
    }
    path = paths.decision_logs_dir / "decision_log_v1.yaml"
    dump_yaml(path, payload)
    return path


def _seed_citation(paths) -> None:
    paths.citations_index_path.parent.mkdir(parents=True, exist_ok=True)
    dump_yaml(
        paths.citations_index_path,
        {
            "citations": {
                "src-test": {
                    "human": "Test citation",
                    "source": {"type": "doc", "path": "seeds/test.md"},
                    "metadata": {},
                    "status": "tracked",
                }
            }
        },
    )


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_plan_start_renders_brief_with_requirements_and_constraints(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    _seed_citation(paths)
    _seed_decision_log(
        paths,
        requirements=[
            {
                "id": "REQ-001",
                "description": "When a request arrives, the system shall respond.",
                "source": "user",
                "citations": ["src-test"],
                "verification": "test",
                "lens": "functional",
                "rationale": "core",
            }
        ],
        constraints=[
            {
                "id": "CON-001",
                "description": "Latency < 250 ms p95",
                "kind": "performance_target",
                "verification_required": True,
                "citations": ["src-test"],
                "rationale": "SLA",
            },
            {
                "id": "CON-002",
                "description": "Python 3.11 only",
                "kind": "tooling",
                "verification_required": False,
                "citations": [],
                "rationale": "toolchain",
            },
        ],
    )

    result = run_plan_implementation_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        decision_log_version=1,
    )

    assert result["status"] == "ready_for_planner"
    assert result["decision_log_version"] == 1
    brief = paths.plan_brief_path.read_text(encoding="utf-8")
    assert "REQ-001" in brief
    assert "CON-001" in brief
    assert "performance_target" in brief
    assert "Python 3.11 only" in brief
    assert "capability_plan:" in brief  # schema doc included


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _decision_log_dict() -> dict:
    return {
        "decision_log": {
            "requirements": [
                {
                    "id": "REQ-001",
                    "description": "Decision log schema must validate citation traces.",
                    "citations": ["src-test"],
                },
                {
                    "id": "REQ-002",
                    "description": "Scaffold dispatch must preserve capability outputs.",
                    "citations": ["src-test"],
                },
            ],
            "constraints": [
                {
                    "id": "CON-001",
                    "description": "Planner output must be auditable.",
                    "citations": ["src-test"],
                },
            ],
        }
    }


def test_validate_plan_extract_happy_path():
    payload = {
        "capability_plan": {
            "version": 1,
            "capabilities": [
                {
                    "name": "ingest-pipeline",
                    "description": "Drive ingest fan-out.",
                    "requirement_ids": ["REQ-001", "REQ-002"],
                    "constraint_ids": ["CON-001"],
                    "verification_required": True,
                    "composes": [],
                    "rationale": "Bundles related ingest work.",
                }
            ],
        }
    }
    issues = validate_plan_extract(payload, decision_log=_decision_log_dict())
    assert issues == []


def test_validate_plan_extract_rejects_unknown_req():
    payload = {
        "capability_plan": {
            "version": 1,
            "capabilities": [
                {
                    "name": "rogue",
                    "description": "x",
                    "requirement_ids": ["REQ-001", "REQ-999"],
                    "constraint_ids": [],
                    "verification_required": True,
                    "composes": [],
                    "rationale": "x",
                },
                {
                    "name": "covers-002",
                    "description": "y",
                    "requirement_ids": ["REQ-002"],
                    "constraint_ids": [],
                    "verification_required": True,
                    "composes": [],
                    "rationale": "y",
                },
            ],
        }
    }
    issues = validate_plan_extract(payload, decision_log=_decision_log_dict())
    assert any("REQ-999" in issue for issue in issues)


def test_validate_plan_extract_requires_every_req_covered():
    payload = {
        "capability_plan": {
            "version": 1,
            "capabilities": [
                {
                    "name": "only",
                    "description": "x",
                    "requirement_ids": ["REQ-001"],
                    "constraint_ids": [],
                    "verification_required": True,
                    "composes": [],
                    "rationale": "x",
                }
            ],
        }
    }
    issues = validate_plan_extract(payload, decision_log=_decision_log_dict())
    assert any("REQ-002" in issue and "not covered" in issue for issue in issues)


def test_validate_plan_extract_rejects_self_loop():
    payload = {
        "capability_plan": {
            "version": 1,
            "capabilities": [
                {
                    "name": "loops",
                    "description": "x",
                    "requirement_ids": ["REQ-001", "REQ-002"],
                    "constraint_ids": [],
                    "verification_required": True,
                    "composes": ["loops"],
                    "rationale": "x",
                }
            ],
        }
    }
    issues = validate_plan_extract(payload, decision_log=_decision_log_dict())
    assert any("self-loop" in issue for issue in issues)


def test_validate_plan_extract_v2_requires_concrete_execution_fields():
    payload = {
        "capability_plan": {
            "version": 2,
            "capabilities": [
                {
                    "name": "schema-dispatch",
                    "description": "Validate schema dispatch.",
                    "requirement_ids": ["REQ-001", "REQ-002"],
                    "constraint_ids": ["CON-001"],
                    "verification_required": True,
                    "composes": [],
                    "rationale": "Keeps planning auditable.",
                }
            ],
        }
    }

    issues = validate_plan_extract(payload, decision_log=_decision_log_dict())

    assert any("implementation_steps" in issue for issue in issues)
    assert any("acceptance_criteria" in issue for issue in issues)
    assert any("explicit_triggers" in issue for issue in issues)
    assert any("evidence_refs" in issue for issue in issues)


def test_validate_plan_extract_v2_accepts_step_by_step_capability():
    payload = {
        "capability_plan": {
            "version": 2,
            "capabilities": [
                {
                    "name": "schema-dispatch",
                    "phase": "dispatch",
                    "objective": "Compile a citation-traced dispatch artifact.",
                    "description": "Compile schema-valid dispatch outputs with citation traces.",
                    "requirement_ids": ["REQ-001", "REQ-002"],
                    "constraint_ids": ["CON-001"],
                    "verification_required": True,
                    "composes": [],
                    "explicit_triggers": ["schema dispatch citation traces"],
                    "evidence_refs": ["src-test"],
                    "implementation_steps": [
                        "Load the decision log requirements and dispatch contract",
                        "Write the dispatch artifact with citation trace fields",
                    ],
                    "acceptance_criteria": [
                        "Dispatch artifact validates against the schema",
                        "Every output row includes a citation trace",
                    ],
                    "parallelizable": False,
                    "rationale": "The same output proves both requirements.",
                }
            ],
        }
    }

    issues = validate_plan_extract(payload, decision_log=_decision_log_dict())

    assert issues == []


def test_validate_plan_extract_rejects_dangling_compose():
    payload = {
        "capability_plan": {
            "version": 1,
            "capabilities": [
                {
                    "name": "a",
                    "description": "x",
                    "requirement_ids": ["REQ-001", "REQ-002"],
                    "constraint_ids": [],
                    "verification_required": True,
                    "composes": ["nope"],
                    "rationale": "x",
                }
            ],
        }
    }
    issues = validate_plan_extract(payload, decision_log=_decision_log_dict())
    assert any("nope" in issue for issue in issues)


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------


_VALID_PLAN_MD = dedent(
    """\
    # Implementation Plan v1

    ## Overview
    Sets up the ingest pipeline for the test workspace.

    ## Phases
    1. Ingest plumbing
    2. Verification harness

    ## Capabilities
    Two capabilities cover both REQs and the testable constraint.

    ```yaml
    capability_plan:
      version: 1
      capabilities:
        - name: ingest-pipeline
          description: Run ingest fan-out
          requirement_ids: [REQ-001, REQ-002]
          constraint_ids: [CON-001]
          verification_required: true
          composes: []
          rationale: shared back-pressure layer
    ```

    ## Dependencies
    Stage 1A findings must be present.

    ## Risks
    Cross-source schema drift.

    ## Open Questions
    - Should the ingest pipeline emit a manifest?
    """
)


def test_parse_plan_markdown_extracts_capability_plan():
    extracted, issues = parse_plan_markdown(_VALID_PLAN_MD, decision_log=_decision_log_dict())
    assert issues == []
    assert extracted is not None
    assert extracted["capability_plan"]["capabilities"][0]["name"] == "ingest-pipeline"


def test_parse_plan_markdown_flags_missing_section():
    bad = _VALID_PLAN_MD.replace("## Risks\nCross-source schema drift.\n\n", "")
    _, issues = parse_plan_markdown(bad, decision_log=_decision_log_dict())
    assert any("Risks" in issue for issue in issues)


def test_parse_plan_markdown_flags_empty_section():
    bad = _VALID_PLAN_MD.replace(
        "## Risks\nCross-source schema drift.\n", "## Risks\n"
    )
    _, issues = parse_plan_markdown(bad, decision_log=_decision_log_dict())
    assert any("Risks" in issue and "empty" in issue for issue in issues)


def test_parse_plan_markdown_requires_fenced_yaml():
    bad = _VALID_PLAN_MD.replace("```yaml", "```").replace("```\n", "```\n", 1)
    _, issues = parse_plan_markdown(bad, decision_log=_decision_log_dict())
    assert any("fenced" in issue.lower() for issue in issues)


# ---------------------------------------------------------------------------
# Postflight
# ---------------------------------------------------------------------------


def test_finalize_writes_plan_extract(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    _seed_citation(paths)
    _seed_decision_log(
        paths,
        requirements=[
            {
                "id": "REQ-001",
                "description": "x",
                "source": "user",
                "citations": ["src-test"],
                "verification": "t",
                "lens": "functional",
                "rationale": "r",
            },
            {
                "id": "REQ-002",
                "description": "y",
                "source": "derived",
                "citations": ["src-test"],
                "verification": "t",
                "lens": "data",
                "rationale": "r",
            },
        ],
        constraints=[
            {
                "id": "CON-001",
                "description": "Latency budget",
                "kind": "performance_target",
                "verification_required": True,
                "citations": ["src-test"],
                "rationale": "r",
            }
        ],
    )

    paths.implementation_plan_path(1).write_text(_VALID_PLAN_MD, encoding="utf-8")

    result = run_plan_implementation_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        decision_log_version=1,
    )

    assert result["status"] == "extracted"
    assert result["capability_count"] == 1
    extract = load_yaml(paths.plan_extract_path(1))
    assert extract["plan_extract"]["capabilities"][0]["name"] == "ingest-pipeline"
    assert extract["plan_extract"]["capabilities"][0]["requirement_ids"] == [
        "REQ-001",
        "REQ-002",
    ]


def test_finalize_raises_when_plan_md_missing(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    _seed_citation(paths)
    _seed_decision_log(
        paths,
        requirements=[
            {
                "id": "REQ-001",
                "description": "x",
                "source": "user",
                "citations": ["src-test"],
                "verification": "t",
                "lens": "functional",
                "rationale": "r",
            }
        ],
    )

    with pytest.raises(FileNotFoundError):
        run_plan_implementation_finalize(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            decision_log_version=1,
        )


def test_finalize_raises_when_plan_invalid(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    _seed_citation(paths)
    _seed_decision_log(
        paths,
        requirements=[
            {
                "id": "REQ-001",
                "description": "x",
                "source": "user",
                "citations": ["src-test"],
                "verification": "t",
                "lens": "functional",
                "rationale": "r",
            }
        ],
    )

    invalid = dedent(
        """\
        ## Overview
        plan
        ## Phases
        p
        ## Capabilities
        ```yaml
        capability_plan:
          version: 1
          capabilities:
            - name: covers
              description: x
              requirement_ids: [REQ-NEVER]
              constraint_ids: []
              verification_required: true
              composes: []
              rationale: x
        ```
        ## Dependencies
        d
        ## Risks
        r
        ## Open Questions
        q
        """
    )
    paths.implementation_plan_path(1).write_text(invalid, encoding="utf-8")
    with pytest.raises(RuntimeError, match="REQ-NEVER"):
        run_plan_implementation_finalize(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            decision_log_version=1,
        )
