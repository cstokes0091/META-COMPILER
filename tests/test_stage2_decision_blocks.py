"""Tests for Stage 2 decision block parsing and compilation.

These are pure-function tests: no filesystem, no CLI wiring. They verify
that transcripts written per `.github/docs/stage-2-hardening.md` §7 parse
correctly and compile into a Decision Log that `validate_decision_log`
accepts.
"""
from __future__ import annotations

import pytest

from meta_compiler.stages.elicit_stage import (
    DecisionBlock,
    compile_decision_log,
    mechanical_fidelity_checks,
    parse_decision_blocks,
)
from meta_compiler.validation import validate_decision_log


PROJECT_META = {
    "project_name": "Test Project",
    "project_type": "hybrid",
}


# ---------------------------------------------------------------------------
# Parser — empty / prose-only inputs
# ---------------------------------------------------------------------------


def test_empty_transcript_returns_no_blocks_and_no_errors():
    blocks, errors = parse_decision_blocks("")
    assert blocks == []
    assert errors == []


def test_prose_only_transcript_returns_no_blocks():
    text = (
        "# Stage 2 Transcript\n"
        "\n"
        "## Decision Area: Conventions\n"
        "\n"
        "The human described a preference for black-formatted Python.\n"
        "No decision has been locked yet.\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert errors == []


# ---------------------------------------------------------------------------
# Parser — per-section happy paths
# ---------------------------------------------------------------------------


def test_conventions_block_happy_path():
    text = (
        "### Decision: Code style\n"
        "- Section: conventions\n"
        "- Domain: code\n"
        "- Choice: 4-space indent with type hints\n"
        "- Rationale: matches existing style\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    assert len(blocks) == 1
    block = blocks[0]
    assert block.name == "Code style"
    assert block.section == "conventions"
    assert block.fields["domain"] == "code"
    assert block.fields["choice"] == "4-space indent with type hints"
    assert block.rationale == "matches existing style"
    assert block.citations == ()


def test_architecture_block_with_alternatives():
    text = (
        "### Decision: Orchestrator role\n"
        "- Section: architecture\n"
        "- Component: stage2-orchestrator\n"
        "- Approach: boundary integrity audit only\n"
        "- Alternatives rejected:\n"
        "  - CLI-mediated dialog: too rigid\n"
        "  - Pure-prompt orchestration: no mechanical determinism\n"
        "- Constraints applied: fresh context, artifact-only handoff\n"
        "- Rationale: separates dialog from integrity\n"
        "- Citations: src-alpha, src-beta\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    assert len(blocks) == 1
    block = blocks[0]
    assert block.section == "architecture"
    assert block.fields["component"] == "stage2-orchestrator"
    assert len(block.alternatives_rejected) == 2
    assert block.alternatives_rejected[0] == {
        "name": "CLI-mediated dialog",
        "reason": "too rigid",
    }
    assert block.fields["constraints_applied"] == [
        "fresh context",
        "artifact-only handoff",
    ]
    assert block.citations == ("src-alpha", "src-beta")


def test_scope_in_block():
    text = (
        "### Decision: Ralph-loop implementers\n"
        "- Section: scope-in\n"
        "- Item: Stage 4 Python driver for implementer-reviewer cycles\n"
        "- Rationale: scaffold describes contract but nothing executes it\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    assert blocks[0].section == "scope-in"
    assert blocks[0].fields["item"] == (
        "Stage 4 Python driver for implementer-reviewer cycles"
    )


def test_scope_out_block_requires_revisit_if():
    text = (
        "### Decision: GUI front-end\n"
        "- Section: scope-out\n"
        "- Item: web UI for the meta-compiler CLI\n"
        "- Rationale: VSCode + Copilot Chat covers the interactive surface today\n"
        "- Revisit if: non-VSCode users adopt the tool at scale\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    assert blocks[0].section == "scope-out"
    assert blocks[0].fields["revisit_if"] == (
        "non-VSCode users adopt the tool at scale"
    )


def test_requirements_block_captures_lens_and_verification():
    text = (
        "### Decision: Fast preflight\n"
        "- Section: requirements\n"
        "- Source: derived\n"
        "- Description: When the user invokes elicit-vision --start, the system shall write brief.md within 2 seconds.\n"
        "- Verification: Benchmark on a workspace with 50 wiki pages and confirm wall clock < 2s.\n"
        "- Lens: performance\n"
        "- Rationale: fast CLI keeps the prompt responsive\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    req = blocks[0]
    assert req.section == "requirements"
    assert req.fields["source"] == "derived"
    assert req.fields["lens"] == "performance"
    assert "shall write brief.md" in req.fields["description"]


def test_open_items_block():
    text = (
        "### Decision: Dual prompt source\n"
        "- Section: open_items\n"
        "- Description: Decide whether to consolidate on .github/prompts/ only\n"
        "- Deferred to: future_work\n"
        "- Owner: human\n"
        "- Rationale: not load-bearing for this release\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    assert blocks[0].section == "open_items"
    assert blocks[0].fields["deferred_to"] == "future_work"


def test_agents_needed_block():
    text = (
        "### Decision: stage2-orchestrator\n"
        "- Section: agents_needed\n"
        "- Role: stage2-orchestrator\n"
        "- Responsibility: preflight + postflight integrity audit\n"
        "- Inputs:\n"
        "  - precheck_request: document\n"
        "  - postcheck_request: document\n"
        "  - decision_log: document\n"
        "  - transcript: document\n"
        "- Outputs:\n"
        "  - precheck_verdict: document\n"
        "  - postcheck_verdict: document\n"
        "- Key constraints: read-only against the transcript, never edit the decision log\n"
        "- Rationale: keeps the CLI deterministic while preserving semantic checks\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    block = blocks[0]
    assert block.fields["role"] == "stage2-orchestrator"
    assert block.fields["inputs"] == [
        {"name": "precheck_request", "modality": "document"},
        {"name": "postcheck_request", "modality": "document"},
        {"name": "decision_log", "modality": "document"},
        {"name": "transcript", "modality": "document"},
    ]
    assert block.fields["outputs"] == [
        {"name": "precheck_verdict", "modality": "document"},
        {"name": "postcheck_verdict", "modality": "document"},
    ]


def test_agents_needed_rejects_invalid_modality():
    text = (
        "### Decision: bad-agent\n"
        "- Section: agents_needed\n"
        "- Role: bad-agent\n"
        "- Responsibility: fails modality enum\n"
        "- Inputs:\n"
        "  - decision_log: binary\n"
        "- Outputs:\n"
        "  - scaffold: code\n"
        "- Key constraints: (none)\n"
        "- Rationale: test case\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert any("modality 'binary'" in e for e in errors)


def test_agents_needed_requires_inputs_and_outputs():
    text = (
        "### Decision: missing-outputs\n"
        "- Section: agents_needed\n"
        "- Role: missing-outputs\n"
        "- Responsibility: forgot outputs\n"
        "- Inputs:\n"
        "  - decision_log: document\n"
        "- Key constraints: (none)\n"
        "- Rationale: test\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert any("outputs" in e for e in errors)


def test_code_architecture_block_with_libraries():
    text = (
        "### Decision: numerical-libraries\n"
        "- Section: code-architecture\n"
        "- Aspect: libraries\n"
        "- Choice: numpy + pyarrow\n"
        "- Libraries:\n"
        "  - numpy: PSF math (>=1.26)\n"
        "  - pyarrow: columnar IO (>=15)\n"
        "- Alternatives rejected:\n"
        "  - pandas: PSF ops need zero-copy arrow buffers\n"
        "- Constraints applied: permissive license\n"
        "- Rationale: stable and documented\n"
        "- Citations: src-numpy, src-pyarrow\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    block = blocks[0]
    assert block.section == "code-architecture"
    assert block.fields["aspect"] == "libraries"
    assert block.fields["libraries"] == [
        {"name": "numpy", "description": "PSF math (>=1.26)"},
        {"name": "pyarrow", "description": "columnar IO (>=15)"},
    ]
    assert block.alternatives_rejected[0]["name"] == "pandas"


def test_code_architecture_rejects_unknown_aspect():
    text = (
        "### Decision: bad-aspect\n"
        "- Section: code-architecture\n"
        "- Aspect: cosmos\n"
        "- Choice: whatever\n"
        "- Rationale: nope\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert any("Aspect 'cosmos'" in e for e in errors)


def test_code_architecture_libraries_aspect_requires_library_sublist():
    text = (
        "### Decision: missing-libs\n"
        "- Section: code-architecture\n"
        "- Aspect: libraries\n"
        "- Choice: something\n"
        "- Rationale: missing libs\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert any("Aspect=libraries" in e for e in errors)


# ---------------------------------------------------------------------------
# Parser — error cases
# ---------------------------------------------------------------------------


def test_unknown_section_produces_error():
    text = (
        "### Decision: Mystery\n"
        "- Section: freeform\n"
        "- Rationale: whatever\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert len(errors) == 1
    assert "unknown Section" in errors[0]


def test_missing_required_field_produces_error():
    # conventions block missing Domain
    text = (
        "### Decision: Code style\n"
        "- Section: conventions\n"
        "- Choice: black-formatted\n"
        "- Rationale: consistency\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert len(errors) == 1
    assert "domain" in errors[0]


def test_missing_citations_field_is_an_error():
    text = (
        "### Decision: Code style\n"
        "- Section: conventions\n"
        "- Domain: code\n"
        "- Choice: black-formatted\n"
        "- Rationale: consistency\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert any("Citations" in e for e in errors)


def test_missing_rationale_is_an_error():
    text = (
        "### Decision: Code style\n"
        "- Section: conventions\n"
        "- Domain: code\n"
        "- Choice: black-formatted\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert any("Rationale" in e for e in errors)


def test_invalid_convention_domain_is_an_error():
    text = (
        "### Decision: Bogus convention\n"
        "- Section: conventions\n"
        "- Domain: vibes\n"
        "- Choice: whatever\n"
        "- Rationale: (empty)\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert any("Domain" in e and "vibes" in e for e in errors)


def test_invalid_requirement_source_is_an_error():
    text = (
        "### Decision: Bad source\n"
        "- Section: requirements\n"
        "- Source: hallucinated\n"
        "- Description: The system shall do things.\n"
        "- Verification: Look at it.\n"
        "- Lens: functional\n"
        "- Rationale: because\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert blocks == []
    assert any("Source" in e for e in errors)


# ---------------------------------------------------------------------------
# Parser — REQ-NNN assignment at compile time
# ---------------------------------------------------------------------------


def test_req_ids_assigned_sequentially_on_compile():
    text = (
        "### Decision: First req\n"
        "- Section: requirements\n"
        "- Source: derived\n"
        "- Description: When x, the system shall do A.\n"
        "- Verification: Try x.\n"
        "- Lens: functional\n"
        "- Rationale: because\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: Second req\n"
        "- Section: requirements\n"
        "- Source: derived\n"
        "- Description: When y, the system shall do B.\n"
        "- Verification: Try y.\n"
        "- Lens: reliability\n"
        "- Rationale: because\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: Third req\n"
        "- Section: requirements\n"
        "- Source: user\n"
        "- Description: While z, the system shall do C.\n"
        "- Verification: Try z.\n"
        "- Lens: performance\n"
        "- Rationale: because\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    compiled = compile_decision_log(blocks, PROJECT_META)
    req_ids = [row["id"] for row in compiled["decision_log"]["requirements"]]
    assert req_ids == ["REQ-001", "REQ-002", "REQ-003"]


# ---------------------------------------------------------------------------
# Parser — mixed prose and blocks
# ---------------------------------------------------------------------------


def test_prose_between_blocks_is_ignored():
    text = (
        "## Decision Area: Conventions\n"
        "\n"
        "We discussed indentation at length. The user pushed back on 2-space.\n"
        "\n"
        "### Decision: Code style\n"
        "- Section: conventions\n"
        "- Domain: code\n"
        "- Choice: 4-space indent\n"
        "- Rationale: matches repo norms\n"
        "- Citations: (none)\n"
        "\n"
        "Some more prose about why 4-space is better than 8-space.\n"
        "\n"
        "## Decision Area: Architecture\n"
        "\n"
        "### Decision: Orchestrator role\n"
        "- Section: architecture\n"
        "- Component: stage2-orchestrator\n"
        "- Approach: boundary integrity audit only\n"
        "- Constraints applied: fresh context\n"
        "- Rationale: keeps dialog clean\n"
        "- Citations: (none)\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    assert len(blocks) == 2
    assert [b.name for b in blocks] == ["Code style", "Orchestrator role"]


# ---------------------------------------------------------------------------
# Compile — produces schema-valid decision log
# ---------------------------------------------------------------------------


def _full_transcript_all_sections() -> str:
    return (
        "### Decision: Code style\n"
        "- Section: conventions\n"
        "- Domain: code\n"
        "- Choice: black-formatted, 4-space\n"
        "- Rationale: matches existing style\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: Core architecture\n"
        "- Section: architecture\n"
        "- Component: stage-2-orchestrator\n"
        "- Approach: boundary integrity audit\n"
        "- Alternatives rejected:\n"
        "  - CLI-mediated dialog: too rigid\n"
        "- Constraints applied: fresh context, artifact-only handoff\n"
        "- Rationale: separates dialog from integrity\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: language-choice\n"
        "- Section: code-architecture\n"
        "- Aspect: language\n"
        "- Choice: Python 3.11\n"
        "- Rationale: matches existing meta-compiler toolchain\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: libraries-choice\n"
        "- Section: code-architecture\n"
        "- Aspect: libraries\n"
        "- Choice: pyyaml + pytest\n"
        "- Libraries:\n"
        "  - pyyaml: config serialization (>=6.0)\n"
        "  - pytest: unit tests (>=8.0)\n"
        "- Rationale: stable and already vendored\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: Ralph-loop Python driver\n"
        "- Section: scope-in\n"
        "- Item: Stage 4 Python runner\n"
        "- Rationale: scaffold describes contract but nothing executes it\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: GUI client\n"
        "- Section: scope-out\n"
        "- Item: standalone GUI client\n"
        "- Rationale: VSCode + Copilot Chat covers the interactive surface today\n"
        "- Revisit if: non-VSCode users adopt the tool at scale\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: Fast preflight\n"
        "- Section: requirements\n"
        "- Source: derived\n"
        "- Description: When the user invokes elicit-vision --start, the system shall write brief.md within 2 seconds.\n"
        "- Verification: Benchmark on a workspace with 50 wiki pages and confirm wall clock < 2s.\n"
        "- Lens: performance\n"
        "- Rationale: fast CLI keeps the prompt responsive\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: Dual prompt source\n"
        "- Section: open_items\n"
        "- Description: Decide whether to consolidate on .github/prompts/ only\n"
        "- Deferred to: future_work\n"
        "- Owner: human\n"
        "- Rationale: not load-bearing for this release\n"
        "- Citations: (none)\n"
        "\n"
        "### Decision: stage2-orchestrator\n"
        "- Section: agents_needed\n"
        "- Role: stage2-orchestrator\n"
        "- Responsibility: preflight + postflight integrity audit\n"
        "- Inputs:\n"
        "  - precheck_request: document\n"
        "  - decision_log: document\n"
        "  - transcript: document\n"
        "- Outputs:\n"
        "  - precheck_verdict: document\n"
        "  - postcheck_verdict: document\n"
        "- Key constraints: read-only against transcript\n"
        "- Rationale: keeps CLI deterministic\n"
        "- Citations: (none)\n"
    )


def test_compile_produces_schema_valid_decision_log():
    blocks, errors = parse_decision_blocks(_full_transcript_all_sections())
    assert errors == []
    compiled = compile_decision_log(
        blocks,
        project_meta=PROJECT_META,
        prior_version=None,
        problem_statement_hash="abc",
        wiki_version="def",
        use_case="smoke test",
    )
    schema_issues = validate_decision_log(compiled)
    assert schema_issues == [], schema_issues

    root = compiled["decision_log"]
    assert root["meta"]["version"] == 1
    assert root["meta"]["parent_version"] is None
    assert len(root["conventions"]) == 1
    assert len(root["architecture"]) == 1
    assert len(root["code_architecture"]) == 2
    assert len(root["scope"]["in_scope"]) == 1
    assert len(root["scope"]["out_of_scope"]) == 1
    assert len(root["requirements"]) == 1
    assert len(root["open_items"]) == 1
    assert len(root["agents_needed"]) == 1


def test_compile_version_bump_for_reentry():
    blocks, errors = parse_decision_blocks(_full_transcript_all_sections())
    assert errors == []
    compiled = compile_decision_log(
        blocks,
        project_meta=PROJECT_META,
        prior_version=3,
        reason_for_revision="scope expanded",
    )
    meta = compiled["decision_log"]["meta"]
    assert meta["version"] == 4
    assert meta["parent_version"] == 3
    assert meta["reason_for_revision"] == "scope expanded"


# ---------------------------------------------------------------------------
# Fidelity checks
# ---------------------------------------------------------------------------


def test_fidelity_checks_all_pass_for_clean_compile():
    blocks, errors = parse_decision_blocks(_full_transcript_all_sections())
    assert errors == []
    compiled = compile_decision_log(blocks, project_meta=PROJECT_META)
    checks = mechanical_fidelity_checks(blocks, compiled, known_citation_ids=set())
    results = {check["name"]: check["result"] for check in checks}
    assert results["block_count_matches_entry_count"] == "PASS"
    assert results["citation_ids_resolve"] == "PASS"  # all citations are (none)
    assert results["req_ids_sequential"] == "PASS"
    assert results["schema_validates"] == "PASS"


def test_fidelity_check_flags_unresolved_citations():
    text = (
        "### Decision: Code style\n"
        "- Section: conventions\n"
        "- Domain: code\n"
        "- Choice: black-formatted\n"
        "- Rationale: consistency\n"
        "- Citations: src-bogus, src-also-bogus\n"
    )
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    compiled = compile_decision_log(blocks, project_meta=PROJECT_META)
    checks = mechanical_fidelity_checks(
        blocks, compiled, known_citation_ids={"src-real"}
    )
    citation_check = next(c for c in checks if c["name"] == "citation_ids_resolve")
    assert citation_check["result"] == "FAIL"
    assert "src-bogus" in citation_check["evidence"]
